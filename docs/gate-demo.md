# Gate demo

Commands assume repo root and configured secrets. Fixtures are labelled; never mix fixture rows into production baselines.

## 1. Access checklist + sample pulls

```bash
python -m data_sources.tracking.cli doctor --json
python -m data_sources.tracking.cli collect --source gsc --limit 0 --json   # or --dry-run
python -m data_sources.tracking.cli collect --source serper --limit 1 --json
```

Evidence: doctor integrations booleans; one successful sample summary per available source.

## 2. Scheduled run without laptop initiation

Trigger GitHub Action `tracking-schedule` (workflow_dispatch `daily`) or enable systemd timer `seo-tracking-daily.timer`.

Evidence: Action/timer log + `run_log` row.

## 3. Normalized row shape

Inspect SQLite `gsc_daily` / `serp_daily` for `source`, date, `collected_at`, `run_id`, `natural_key`, `row_hash`.

## 4. Locked 28-day baseline

```bash
python -m data_sources.tracking.cli baseline --end-date YYYY-MM-DD --lock --json
```

Evidence: `baseline.status=locked`; update attempt raises immutability error.

## 5. Brand classifier (≥200, ≥95%)

```bash
python -m pytest tests/tracking/unit/test_brand_eval.py -q
```

Evidence: accuracy/precision/recall in check details / test output.

## 6. Simulated credential failure

```bash
python -m data_sources.tracking.cli daily --as-of-date YYYY-MM-DD --simulate-failure serper:authentication --json
```

Evidence: serper failed with `AuthenticationError`; no real credential changed.

## 7. Deduplicated alert

Re-emit the same failure path / alert type for the same `run_id`. Second call returns `deduplicated=true`. Evidence in `alerts` table.

## 8. Weekly report

```bash
python -m data_sources.tracking.cli weekly --period-end YYYY-MM-DD --no-publish --json
```

Evidence: `weekly_reports` row + summary sections.

## 9. Top ten actions

```bash
python -m data_sources.tracking.cli opportunities --end-date YYYY-MM-DD --json
```

Evidence: ≤10 rows with source references and scores.

## 10. Idempotent rerun

Rerun daily/collect for the same date; natural key counts unchanged; upsert stats show `unchanged`.

## 11. Runbook walkthrough

Follow `docs/tracking-runbook.md` recovery path end-to-end in <30 minutes.

## 12. Catalogue provenance (Phases 6–9)

```bash
python -m data_sources.tracking.cli catalogue build-keywords --gsc-start-date YYYY-MM-DD --gsc-end-date YYYY-MM-DD --json
python -m data_sources.tracking.cli catalogue derive-clusters --build-id BUILD --json
python -m data_sources.tracking.cli catalogue build-ai-questions --keyword-build-id BUILD --count 20 --json
python -m data_sources.tracking.cli catalogue export-review --build-id BUILD --output /tmp/review.csv --json
python -m data_sources.tracking.cli catalogue approve --build-id BUILD --approved-by "Ting" --keyword-minimum N --json
python -m data_sources.tracking.cli catalogue activate --build-id BUILD --confirm --json
python -m data_sources.tracking.cli catalogue check --build-id BUILD --json
python -m data_sources.tracking.cli catalogue report --build-id BUILD --output docs/catalogue-provenance-v1.md --json
```

Evidence: build fingerprint + funnel; lineage sources; 20 questions; approve≠activate; provisional `candidate-v0.1` still queryable; report counts parity.

## 13. Persistent unattended execution

Prefer systemd on persistent disk (`ops/tracking/run-daily-persistent.sh`). GitHub Actions may probe with materialize-credentials but must not be treated as the durable DB.
