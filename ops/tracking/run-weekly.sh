#!/usr/bin/env bash
# Weekly report generation. Pass --publish only when Lark secrets are configured.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
PUBLISH_FLAG="${TRACKING_WEEKLY_PUBLISH:-}"
if [[ "$PUBLISH_FLAG" == "1" || "$PUBLISH_FLAG" == "true" ]]; then
  python -m data_sources.tracking.cli weekly --publish --json
else
  python -m data_sources.tracking.cli weekly --no-publish --json
fi
