#!/usr/bin/env bash
# Materialize Google credential JSON secrets to temp files (mode 0600),
# export GSC_CREDENTIALS_PATH / GA4_CREDENTIALS_PATH, then run a command.
# Secrets are removed on exit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

CLEANUP_FILES=()
cleanup() {
  for f in "${CLEANUP_FILES[@]:-}"; do
    rm -f "$f" || true
  done
}
trap cleanup EXIT

materialize() {
  local env_name="$1"
  local out_var="$2"
  local payload="${!env_name:-}"
  if [[ -z "$payload" ]]; then
    return 0
  fi
  local tmp
  tmp="$(mktemp -t "${out_var}.XXXXXX.json")"
  CLEANUP_FILES+=("$tmp")
  printf '%s' "$payload" >"$tmp"
  chmod 600 "$tmp"
  python -c "import json,sys; json.load(open(sys.argv[1]))" "$tmp"
  export "$out_var=$tmp"
}

materialize GSC_CREDENTIALS_JSON GSC_CREDENTIALS_PATH
materialize GA4_CREDENTIALS_JSON GA4_CREDENTIALS_PATH

if [[ $# -eq 0 ]]; then
  echo "usage: materialize-credentials.sh <command...>" >&2
  exit 2
fi
exec "$@"
