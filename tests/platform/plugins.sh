# Platform plugins behave as promised. Owned by the platform team.

has_header() {   # has_header <header> <curl args...>
  curl -s -D - -o /dev/null "${@:2}" | grep -qi "^$1:" && echo yes || echo no
}

# Global: every response carries a request id, public or private
check "request id on public route"  yes "$(has_header X-Request-ID "$BASE/echo/get")"
check "request id on private route" yes "$(has_header X-Request-ID -H "apikey: $KEY" "$BASE/private/get")"

# Private profile: rate limit headers are present
check "private route is rate limited" yes \
      "$(has_header RateLimit-Limit -H "apikey: $KEY" "$BASE/private/get")"
check "public route is not rate limited" no "$(has_header RateLimit-Limit "$BASE/echo/get")"

# Private profile: the limit actually trips (separate consumer, own quota)
got=none
for i in $(seq 1 130); do
  code=$(status -H "apikey: rate-limit-probe-key" "$BASE/private/get")
  [[ "$code" == 429 ]] && { got=429; break; }
done
check "limit trips with 429" 429 "$got"