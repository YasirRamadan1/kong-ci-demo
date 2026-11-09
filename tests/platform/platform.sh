# Platform guarantees that hold for every team. Owned by the platform team.

check "unknown path is not routed" 404 "$(status "$BASE/no-such-endpoint")"

# Every service gets the platform defaults and a team tag
for svc in $(curl -s "$ADMIN/services" | jq -r '.data[].name'); do
  check "$svc: platform timeout"  30000 \
        "$(curl -s "$ADMIN/services/$svc" | jq -r .read_timeout)"
  check "$svc: has a team tag"    yes \
        "$(curl -s "$ADMIN/services/$svc" | jq -r '[.tags[]? | startswith("team:")] | any | if . then "yes" else "no" end')"
done