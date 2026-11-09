# Tests for the orders team's endpoints. Owned by the orders team.

# public catalog
check "catalog is public"          200 "$(status "$BASE/orders/catalog/items")"
check "catalog reaches backend"    "http://httpbin:8080/anything/items" \
      "$(curl -s "$BASE/orders/catalog/items" | jq -r .url)"

# orders API is private by default
check "orders need a key"          401 "$(status "$BASE/orders")"
check "GET orders with key"        200 "$(status -H "apikey: $KEY" "$BASE/orders")"
check "POST orders with key"       200 "$(status -X POST -H "apikey: $KEY" "$BASE/orders")"
check "DELETE is not routed"       404 "$(status -X DELETE -H "apikey: $KEY" "$BASE/orders")"