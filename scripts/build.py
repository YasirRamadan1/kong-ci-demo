#!/usr/bin/env python3
"""Build the final Kong config from team files + platform policy.

Teams describe their endpoints in teams/<team>/ in one of two forms:
  *.openapi.yaml - an OpenAPI 3 spec; one route per documented operation
  *.yaml         - the simple form; only SERVICE_FIELDS and ROUTE_FIELDS
Everything else comes from the platform:
  platform/defaults.yaml       - default settings for services and routes
  platform/profiles.yaml       - which plugins each access profile gets
  platform/consumers.yaml      - who may call private routes
  platform/global-plugins.yaml - plugins on every request
Routes are private by default. Public needs two keys: the team declares it
(tag 'access:public', or 'security: []' in OpenAPI) AND the platform approves
it in platform/public-endpoints.yaml.
"""
import copy
import glob
import os
import subprocess
import sys

import yaml

# The developer-facing contract for the simple form.
TOP_FIELDS = {"_format_version", "services"}
SERVICE_FIELDS = {"name", "url", "routes"}
ROUTE_FIELDS = {"name", "paths", "methods", "tags"}
ACCESS_TAGS = {"access:public"}
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}

errors = []


def fail(path, msg):
    errors.append(f"{path}: {msg}")


def load(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------- simple form ----------

def guard(path, doc):
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


def from_simple(path, doc):
    """Return [(service, {route_name: profile})] from a simple team file."""
    out = []
    for svc in doc.get("services", []):
        profiles = {}
        for route in svc.get("routes", []):
            public = "access:public" in route.get("tags", [])
            profiles[route.get("name")] = ("public" if public else "private", route.get("name"))
        out.append((svc, profiles))
    return out


# ---------- OpenAPI form ----------

def find_kong_extensions(node, where="spec"):
    """Yield every x-kong-* key anywhere in the spec."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower().startswith("x-kong"):
                yield f"{where}.{key}"
            yield from find_kong_extensions(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from find_kong_extensions(value, f"{where}[{i}]")


def guard_openapi(path, spec):
    for where in find_kong_extensions(spec):
        fail(path, f"'{where}': Kong extensions are platform-owned")
    if not (spec.get("servers") or [{}])[0].get("url"):
        fail(path, "servers[0].url (the backend) is required")
    for p, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if method in HTTP_METHODS and not op.get("operationId"):
                fail(path, f"{method.upper()} {p}: operationId is required")


def from_openapi(path, spec):
    """Generate services with decK; profile per operation from 'security'."""
    default_security = spec.get("security")
    profiles = {}
    for item in (spec.get("paths") or {}).values():
        for method, op in item.items():
            if method in HTTP_METHODS and op.get("operationId"):
                security = op.get("security", default_security)
                public = security == []
                profiles[op["operationId"].lower()] = ("public" if public else "private", op["operationId"])

    result = subprocess.run(
        ["deck", "file", "openapi2kong", "-s", path, "-o", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(path, f"openapi2kong failed: {result.stderr.strip()}")
        return []

    out = []
    for svc in yaml.safe_load(result.stdout).get("services", []):
        svc.pop("plugins", None)
        svc.pop("tags", None)
        route_profiles = {}
        for route in svc.get("routes", []):
            route.pop("plugins", None)
            route.pop("tags", None)
            op_id = route["name"].split("_", 1)[1]
            route_profiles[route["name"]] = profiles.get(op_id, ("private", op_id))
        out.append((svc, route_profiles))
    return out


# ---------- build ----------

def main(out_path):
    defaults = load("platform/defaults.yaml")
    profiles = load("platform/profiles.yaml")["profiles"]
    consumers = load("platform/consumers.yaml").get("consumers", [])
    global_plugins = load("platform/global-plugins.yaml").get("plugins", [])
    approved = load("platform/public-endpoints.yaml").get("public") or {}

    services, seen = [], {}
    for path in sorted(glob.glob("teams/*/*.yaml")):
        team = path.split(os.sep)[1]
        doc = load(path)
        if path.endswith(".openapi.yaml"):
            guard_openapi(path, doc)
            if errors:
                continue
            team_services = from_openapi(path, doc)
        else:
            guard(path, doc)
            team_services = from_simple(path, doc)

        for svc, route_profiles in team_services:
            for obj in [svc] + svc.get("routes", []):
                key = obj.get("name")
                if key in seen:
                    fail(path, f"name '{key}' already used in {seen[key]}")
                seen[key] = path

            out_svc = {**svc, **copy.deepcopy(defaults.get("service", {}))}
            out_svc["tags"] = [f"team:{team}"]
            out_svc["routes"] = []
            for route in svc.get("routes", []):
                profile, label = route_profiles.get(route.get("name"), ("private", route.get("name")))
                if profile == "public" and label not in (approved.get(team) or []):
                    fail(path, f"'{label}' is declared public but not approved "
                               f"in platform/public-endpoints.yaml")
                tags = [t for t in route.get("tags", []) if not t.startswith("access:")]
                # team/generated values win over defaults (e.g. strip_path: false from OpenAPI)
                out_route = {**copy.deepcopy(defaults.get("route", {})), **route}
                out_route["tags"] = (["access:public"] if profile == "public" else []) \
                                    + tags + [f"team:{team}"]
                out_route["plugins"] = [dict(p) for p in profiles[profile]]
                out_svc["routes"].append(out_route)
            services.append(out_svc)

    if errors:
        print("Guard failed:", *errors, sep="\n  ")
        sys.exit(1)

    out = {
        "_format_version": "3.0",
        "services": services,
        "plugins": global_plugins,
        "consumers": consumers,
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("# GENERATED by scripts/build.py - do not edit\n")
        yaml.safe_dump(out, f, sort_keys=False)
    print(f"Built {out_path}: {len(services)} services")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/kong.yaml")