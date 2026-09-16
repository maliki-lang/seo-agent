# Tracking runbook

Goal: a second person can recover tracking within 30 minutes.

## Prerequisites / access checklist

- Repo checkout + `.venv` with `pip install -r data_sources/requirements.txt`
- Secrets in `data_sources/config/.env` (never commit): GSC/GA4 credentials paths, `SERPER_API_KEY`, optional OpenAI/Perplexity, Lark app + base token + webhook
- Service accounts granted on GSC property and GA4 property
- `python -m data_sources.tracking.cli doctor --json`

## Sample pulls

```bash
python -m data_sources.tracking.cli collect --source gsc --dry-run --json
python -m data_sources.tracking.cli collect --source serper --limit 1 --json
```

Do not run full AI visibility (120 calls) without keys and cost approval.

## Daily / backfill / weekly

```bash
python -m data_sources.tracking.cli daily --as-of-date YYYY-MM-DD --json
python -m data_sources.tracking.cli backfill --sources gsc,ga4 --start-date YYYY-MM-DD --end-date YYYY-MM-DD --json
python -m data_sources.tracking.cli check --run-id <id> --json
python -m data_sources.tracking.cli baseline --end-date YYYY-MM-DD --lock --json
python -m data_sources.tracking.cli weekly --period-end YYYY-MM-DD --no-publish --json
python -m data_sources.tracking.cli weekly --period-end YYYY-MM-DD --publish --json
```

## Inspect a failed run

1. `run_log` row for `run_id` / status / `failed_collectors`
2. `collector_state` for error_code/message
3. `quality_check_log` for gate failures
4. `alerts` for deduplicated notifications

## Failure classes

| Class | Meaning | Typical fix |
|---|---|---|
| AuthenticationError | Bad/expired key | Rotate secret in env; do not commit |
| PermissionDenied | SA lacks property access | Grant access in GSC/GA4 |
| RateLimitError | Provider throttle | Wait / reduce concurrency |
| SchemaMismatchError | Unexpected storage/API shape | Migrate or fix parser |
| DataQualityError | Catalogue/quality gate | Fix data; re-run checks |
| CostLimitExceeded | Daily cap hit | Raise cap only with approval |

## Safe retry

Rerun the same as-of date. Upserts are idempotent on natural keys. Do not delete locked baselines.

## Rotate credentials

1. Create new secret in provider
2. Update env / secret manager
3. Restart scheduler identity
4. `doctor` + one sample pull
5. Revoke old secret

## Verify Lark writeback

Published weekly rows should have `weekly_reports.lark_record_id`. Re-run `--publish` should upsert by deterministic `report_id` / period key, not duplicate operational meaning.

## Stale run locks

If a process died, locks expire after `run_timeout_seconds`. Confirm no active runner, then rerun. Do not manually delete locks unless expiry failed and the run is confirmed dead.

## Rollback

1. Stop scheduler workflow/timer
2. Redeploy last known good commit on `main`
3. Keep SQLite DB; do not wipe production `data/tracking.db` without approval
4. Re-run `doctor` and a dry-run collect

## Escalation roles

- Tracking owner (engineering)
- Analytics owner (GSC/GA4 access)
- Lark Base admin (table/app token)
- Brand/SEO owner (catalogue and report sign-off)
