#!/usr/bin/env bash
# Runs platform tests, then every team's tests.
#   tests/platform/*.sh      - owned by the platform team
#   tests/teams/<team>/*.sh  - owned by each team
set -u
cd "$(dirname "$0")/.."
source tests/lib.sh

echo "== platform"
for t in tests/platform/*.sh; do source "$t"; done

for dir in tests/teams/*/; do
  echo "== $(basename "$dir")"
  for t in "$dir"*.sh; do source "$t"; done
done

exit $fail