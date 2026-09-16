#!/usr/bin/env bash
# Persistent-host daily runner (approved production model #1: VM/server disk + systemd).
# Do NOT treat GitHub Actions ephemeral checkout SQLite as the durable database.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
export TRACKING_ENV="${TRACKING_ENV:-production}"
# Require an explicit durable DB path on the host.
if [[ -z "${TRACKING_DATABASE_URL:-}" ]]; then
  echo "TRACKING_DATABASE_URL must point at persistent disk sqlite, e.g. sqlite:////var/lib/seo-tracking/tracking.db" >&2
  exit 2
fi
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
bash ops/tracking/materialize-credentials.sh python -m data_sources.tracking.cli doctor --json
bash ops/tracking/materialize-credentials.sh python -m data_sources.tracking.cli daily --json
