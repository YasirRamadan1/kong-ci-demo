# Tests for the cards team's endpoints. Owned by the cards team.
# Endpoints come from teams/cards/cards.openapi.yaml.

# public: security: [] in the spec
check "products are public"          200 "$(status "$BASE/cards/products")"
check "full path reaches backend"    "http://httpbin:8080/anything/cards/products" \
      "$(curl -s "$BASE/cards/products" | jq -r .url)"

# private: inherits the spec's global security
check "cards need a key"             401 "$(status "$BASE/cards")"
check "list cards with key"          200 "$(status -H "apikey: $KEY" "$BASE/cards")"
check "one card needs a key"         401 "$(status "$BASE/cards/123")"
check "get one card with key"        200 "$(status -H "apikey: $KEY" "$BASE/cards/123")"
check "card id reaches backend"      "http://httpbin:8080/anything/cards/123" \
      "$(curl -s -H "apikey: $KEY" "$BASE/cards/123" | jq -r .url)"