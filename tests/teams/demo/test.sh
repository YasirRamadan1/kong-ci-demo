# Tests for the demo team's endpoints. Owned by the demo team.

# public route
check "public route returns 200"  200 "$(status "$BASE/echo/get")"
check "path prefix is stripped"   "http://httpbin:8080/get" \
      "$(curl -s "$BASE/echo/get" | jq -r .url)"

# private by default
check "no key is rejected"        401 "$(status "$BASE/private/get")"
check "wrong key is rejected"     401 "$(status -H 'apikey: wrong' "$BASE/private/get")"
check "valid key passes"          200 "$(status -H "apikey: $KEY" "$BASE/private/get")"

body=$(curl -s -H "apikey: $KEY" "$BASE/private/headers")
check "backend knows the consumer" "demo-app" \
      "$(jq -r '.headers["X-Consumer-Username"][0]' <<<"$body")"
check "key is not forwarded"       "false" \
      "$(jq -r '.headers | has("Apikey")' <<<"$body")"