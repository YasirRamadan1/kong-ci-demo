# Shared test helpers. Owned by the platform team. Sourced, not executed.
BASE="${BASE:-http://localhost:8000}"
ADMIN="${ADMIN:-http://localhost:8001}"
KEY="${KEY:-demo-secret-key}"
fail=0

check() {   # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then
    echo "  PASS  $1"
  else
    echo "  FAIL  $1 - expected '$2', got '$3'"
    fail=1
  fi
}

status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }