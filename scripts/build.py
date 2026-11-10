#!/usr/bin/env python3
"""Build the final Kong config from team files + platform policy.

Teams describe their endpoints in teams/<team>/ in one of two forms:
  *.openapi.yaml - an OpenAPI 3 spec; one route per documented operation
  *.yaml         - the simple form; only SERVICE_FIELDS and ROUTE_FIELDS

Everything else comes from the platform:
  platform/defaults.yaml         - default settings for services and routes
  platform/profiles.yaml         - which plugins each access profile gets
  platform/global-plugins.yaml   - plugins on every request
  platform/consumers.yaml        - who may call private routes
  platform/public-endpoints.yaml - which routes are approved to be public

Routes are private by default. Public needs two keys: the team declares it
(tag 'access:public', or 'security: []' in OpenAPI) AND the platform approves
it in platform/public-endpoints.yaml.

All problems are collected first and reported together; the build fails
(exit 1) if there is at least one.
"""
import copy
import glob
import os
import subprocess
import sys

import yaml

# ---------------------------------------------------------------------------
# The developer-facing contract
# ---------------------------------------------------------------------------

# Simple form
TOP_FIELDS = {"_format_version", "services"}
SERVICE_FIELDS = {"name", "url", "routes"}
ROUTE_FIELDS = {"name", "paths", "methods", "tags"}
ACCESS_TAGS = {"access:public"}

# OpenAPI form
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}

# ---------------------------------------------------------------------------
# Error collection
# ---------------------------------------------------------------------------

errors = []


def fail(path, msg):
    """Record a problem. Never stops the build; main() decides at the end."""
    errors.append(f"{path}: {msg}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(path):
    """Read a YAML file. An empty file becomes an empty dict."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_team_file(path):
    """Read a team file; report YAML errors and non-mappings as build errors."""
    try:
        doc = load(path)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        fail(path, f"invalid YAML: {where}{getattr(e, 'problem', e)}")
        return None
    if not isinstance(doc, dict):
        fail(path, "must be a YAML mapping (key: value) at the top level")
        return None
    return doc


def load_platform():
    return {
        "defaults": load("platform/defaults.yaml"),
        "profiles": load("platform/profiles.yaml")["profiles"],
        "global_plugins": load("platform/global-plugins.yaml").get("plugins", []),
        "consumers": load("platform/consumers.yaml").get("consumers", []),
        "approved": load("platform/public-endpoints.yaml").get("public") or {},
    }


# ---------------------------------------------------------------------------
# Simple form: teams/<team>/*.yaml
# ---------------------------------------------------------------------------

def guard_simple(path, doc):
    for key in doc.keys() - TOP_FIELDS:
        fail(path, f"top-level '{key}' is not allowed in team files")
    for svc in doc.get("services", []):
        name = svc.get("name", "?")
        for key in svc.keys() - SERVICE_FIELDS:
            fail(path, f"service '{name}': '{key}' is not allowed (platform-owned)")
        for field in ("name", "url"):
            if field not in svc:
                fail(path, f"service '{name}': '{field}' is required")
        for route in svc.get("routes", []):
            rname = route.get("name", "?")
            for key in route.keys() - ROUTE_FIELDS:
                fail(path, f"route '{rname}': '{key}' is not allowed (platform-owned)")
            if not route.get("paths"):
                fail(path, f"route '{rname}': 'paths' is required")
            for tag in route.get("tags", []):
                if tag.startswith("access:") and tag not in ACCESS_TAGS:
                    fail(path, f"route '{rname}': unknown access tag '{tag}'")


def from_simple(doc):
    """Return [(service, {route_name: (profile, label)})]."""
    result = []
    for svc in doc.get("services", []):
        declared = {}
        for route in svc.get("routes", []):
            public = "access:public" in route.get("tags", [])
            declared[route.get("name")] = ("public" if public else "private", route.get("name"))
        result.append((svc, declared))
    return result


# ---------------------------------------------------------------------------
# OpenAPI form: teams/<team>/*.openapi.yaml
# ---------------------------------------------------------------------------

def find_kong_extensions(node, where="spec"):
    """Yield the location of every x-kong-* key anywhere in the spec."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower().startswith("x-kong"):
                yield f"{where}.{key}"
            yield from find_kong_extensions(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from find_kong_extensions(value, f"{where}[{i}]")


def operations(spec):
    """Yield (path, method, operation) for every documented operation."""
    for p, item in (spec.get("paths") or {}).items():
        if isinstance(item, dict):
            for method, op in item.items():
                if method in HTTP_METHODS and isinstance(op, dict):
                    yield p, method, op


def guard_openapi(path, spec):
    # Kong extensions would let a team add plugins through the back door
    for where in find_kong_extensions(spec):
        fail(path, f"'{where}': Kong extensions are platform-owned")

    if not str(spec.get("openapi", "")).startswith("3."):
        fail(path, "'openapi: 3.x' is required at the top")

    servers = spec.get("servers") or [{}]
    if not servers[0].get("url"):
        fail(path, "servers[0].url (the backend) is required")

    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        fail(path, "'paths' is missing or empty - nothing would be routed")
        return

    # Every path needs at least one HTTP method directly under it
    for p, item in paths.items():
        methods = [m for m in (item or {}) if m in HTTP_METHODS]
        if not methods:
            fail(path, f"path '{p}' has no operations (get, post, ...) directly under it")

    # Operations placed outside 'paths' (e.g. under 'components') are silently ignored
    for section, content in spec.items():
        if section != "paths" and isinstance(content, dict):
            for key in content:
                if isinstance(key, str) and key.startswith("/"):
                    fail(path, f"'{key}' is under '{section}', not under 'paths' - "
                               f"check its indentation")

    for p, method, op in operations(spec):
        if not op.get("operationId"):
            fail(path, f"{method.upper()} {p}: operationId is required")


def from_openapi(path, spec):
    """Generate services with decK; profile per operation from 'security'."""
    default_security = spec.get("security")
    declared_by_op = {}
    for _, _, op in operations(spec):
        security = op.get("security", default_security)
        profile = "public" if security == [] else "private"
        declared_by_op[op["operationId"].lower()] = (profile, op["operationId"])

    result = subprocess.run(
        ["deck", "file", "openapi2kong", "-s", path, "-o", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(path, f"openapi2kong failed: {result.stderr.strip()}")
        return []

    services = []
    for svc in yaml.safe_load(result.stdout).get("services", []):
        svc.pop("plugins", None)
        svc.pop("tags", None)
        declared = {}
        for route in svc.get("routes", []):
            route.pop("plugins", None)
            route.pop("tags", None)
            op_id = route["name"].split("_", 1)[1]
            declared[route["name"]] = declared_by_op.get(op_id, ("private", op_id))
        services.append((svc, declared))
    return services


# ---------------------------------------------------------------------------
# Applying platform policy
# ---------------------------------------------------------------------------

def build_service(svc, declared, team, path, platform):
    defaults = platform["defaults"]
    out_svc = {**svc, **copy.deepcopy(defaults.get("service", {}))}
    out_svc["tags"] = [f"team:{team}"]
    out_svc["routes"] = []

    for route in svc.get("routes", []):
        profile, label = declared.get(route.get("name"), ("private", route.get("name")))

        # Second key: public must be approved by the platform
        if profile == "public" and label not in (platform["approved"].get(team) or []):
            fail(path, f"'{label}' is declared public but not approved "
                       f"in platform/public-endpoints.yaml")

        own_tags = [t for t in route.get("tags", []) if not t.startswith("access:")]
        # Team/generated values win over defaults (e.g. strip_path: false from OpenAPI)
        out_route = {**copy.deepcopy(defaults.get("route", {})), **route}
        out_route["tags"] = (["access:public"] if profile == "public" else []) \
                            + own_tags + [f"team:{team}"]
        out_route["plugins"] = [dict(p) for p in platform["profiles"][profile]]
        out_svc["routes"].append(out_route)

    return out_svc


def check_unique_names(svc, path, seen):
    for obj in [svc] + svc.get("routes", []):
        name = obj.get("name")
        if name in seen:
            fail(path, f"name '{name}' already used in {seen[name]}")
        seen[name] = path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(out_path):
    platform = load_platform()
    services, seen = [], {}

    for path in sorted(glob.glob("teams/*/*.yaml")):
        team = path.split(os.sep)[1]
        doc = load_team_file(path)
        if doc is None:
            continue

        if path.endswith(".openapi.yaml"):
            before = len(errors)
            guard_openapi(path, doc)
            if len(errors) > before:
                continue  # don't generate from a spec that failed its guard
            team_services = from_openapi(path, doc)
        else:
            guard_simple(path, doc)
            team_services = from_simple(doc)

        for svc, declared in team_services:
            check_unique_names(svc, path, seen)
            services.append(build_service(svc, declared, team, path, platform))

    if errors:
        print("Guard failed:", *errors, sep="\n  ")
        sys.exit(1)

    out = {
        "_format_version": "3.0",
        "services": services,
        "plugins": platform["global_plugins"],
        "consumers": platform["consumers"],
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("# GENERATED by scripts/build.py - do not edit\n")
        yaml.safe_dump(out, f, sort_keys=False)
    routes = sum(len(s["routes"]) for s in services)
    print(f"Built {out_path}: {len(services)} services, {routes} routes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/kong.yaml")