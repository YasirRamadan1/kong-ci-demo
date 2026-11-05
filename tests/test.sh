#!/usr/bin/env bash
# Behavioural tests against a running Kong. Same script locally and in CI.
set -u
BASE="${BASE:-http://localhost:8000}"
KEY="demo-secret-key"
fail=0

check() {   # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then
    echo "PASS  $1"
  else
    echo "FAIL  $1 — expected '$2', got '$3'"
    fail=1
  fi
}

status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# --- public route ---
check "public route returns 200"  200 "$(status "$BASE/echo/get")"
check "strip_path removes /echo"  "http://httpbin:8080/get" \
      "$(curl -s "$BASE/echo/get" | jq -r .url)"

# --- key-auth on /private ---
check "no key is rejected"        401 "$(status "$BASE/private/get")"
check "wrong key is rejected"     401 "$(status -H 'apikey: wrong' "$BASE/private/get")"
check "valid key passes"          200 "$(status -H "apikey: $KEY" "$BASE/private/get")"

body=$(curl -s -H "apikey: $KEY" "$BASE/private/headers")
check "backend knows the consumer" "demo-app" \
      "$(jq -r '.headers["X-Consumer-Username"][0]' <<<"$body")"
check "key is not forwarded"       "false" \
      "$(jq -r '.headers | has("Apikey")' <<<"$body")"

exit $fail