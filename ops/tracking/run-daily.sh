#!/usr/bin/env bash
# Unattended daily SEO/GEO tracking collection (Asia/Singapore).
# Intended for a server/CI runner — not a developer laptop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
python -m data_sources.tracking.cli doctor --json
python -m data_sources.tracking.cli daily --json
