# Tests for the cards team's endpoints. Owned by the cards team.
# Endpoints come from teams/cards/cards.openapi.yaml; responses come from
# the mock backend (Prism), which serves the examples in that spec.

# the mock takes a few seconds to start
for i in $(seq 1 20); do
  [[ "$(status "$BASE/cards/products")" == 200 ]] && break
  sleep 1
done

# public: security: [] in the spec
check "products are public"          200 "$(status "$BASE/cards/products")"
check "products come from the spec"  "Gold Card" \
      "$(curl -s "$BASE/cards/products" | jq -r '.[0].name')"

# private: inherits the spec's global security
check "cards need a key"             401 "$(status "$BASE/cards")"
check "list cards with key"          200 "$(status -H "apikey: $KEY" "$BASE/cards")"
check "one card needs a key"         401 "$(status "$BASE/cards/123")"
check "card comes from the spec"     "4417" \
      "$(curl -s -H "apikey: $KEY" "$BASE/cards/123" | jq -r .last4)"

# error responses from the spec, chosen by the caller
check "spec's 404 on request"        "card_not_found" \
      "$(curl -s -H "apikey: $KEY" -H "Prefer: code=404" "$BASE/cards/999" | jq -r .error)"

# only documented operations are reachable
check "undocumented method: 404"     404 "$(status -X DELETE -H "apikey: $KEY" "$BASE/cards/123")"
check "undocumented path: 404"       404 "$(status -H "apikey: $KEY" "$BASE/cards/123/pin")"