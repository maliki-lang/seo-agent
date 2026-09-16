# Tracking architecture

## Context and goals

Sunnystep’s SEO/GEO tracking layer measures non-branded organic performance and AI visibility for the Singapore comfort-footwear brand. It extends the existing content agent with repeatable collection, validation, baseline locking, weekly reporting, and operational alerts.

North-star inputs: GSC clicks/impressions, GA4 organic + AI-referral sessions, Serper keyword ranks, and ChatGPT/Perplexity mention/citation rates.

## Components

```text
CLI / scheduler
    → TrackingRunner
        → collectors (gsc, ga4, serper, ai_visibility)
        → transforms (brand, channel, AI parser, metrics)
        → TrackingStore (SQLite)
        → QualityCheckSuite
        → BaselineService / OpportunityBuilder / WeeklyReportService
        → sinks (Lark Base, alert webhook)
```

## Data flow

1. Catalogue sync (keywords ≥50, AI questions ≥20)
2. Collect source rows for the as-of/data date
3. Normalize + upsert by natural key / row hash
4. Persist redacted raw payloads where applicable
5. Quality checks → `quality_check_log`
6. Optional baseline lock / opportunity scoring / weekly report
7. Optional Lark publish + failure alerts

## Logical schema and natural keys

| Table | Natural key grain |
|---|---|
| `gsc_daily` | date, query, page, country |
| `ga4_daily` | date, session_source, session_medium, landing_page |
| `serp_daily` | date, keyword_id, country, device |
| `ai_answer_runs` | as_of_date, engine, question_id, repetition_number |

Row hashes exclude `collected_at` / run metadata so same-day reruns are idempotent.

## Scheduling model

Business timezone: `Asia/Singapore`.

- Daily collection after expected GSC/GA4 availability (`ops/tracking/run-daily.sh`, systemd timer, or GitHub Actions `tracking-schedule.yml`)
- Weekly report after validated data exists (`ops/tracking/run-weekly.sh`)
- Overlap prevention via `run_locks`
- Application retries own policy; scheduler must not invent duplicate rows

Laptop `launchd` is not the production control plane.

## Retry / failure behavior

HTTP failures map to typed errors (`AuthenticationError`, `RateLimitError`, etc.). Collector failures mark `collector_state`, keep sibling collectors running when possible, finalize as `partial`/`failed`, and emit a deduplicated alert keyed by `(run_id, alert_type)`.

Safe gate demo: `--simulate-failure serper:authentication`.

## Secret boundaries

Secrets live in environment / `.env` (gitignored). Never commit tokens, webhook URLs with secrets, or service-account JSON. Logs redact authorization headers and secret-like keys. Lark Base receives summaries and IDs only — not raw AI answers or unrestricted query dumps.

## Raw vs normalized

- Raw: `raw_records` (redacted JSON, retention class `raw-api`)
- Normalized: `*_daily` / `ai_answer_runs` with source, as_of_date, collected_at, natural_key, row_hash

## Lark operational-view boundary

Approved: weekly summary fields, opportunity rankings, alert text.  
Not approved by default: raw model answers, full SERP payloads, credential material.

## Known limitations

- Serper AI Overview `absent` only when the `aiOverview` key is present and empty; otherwise `unverified`
- AI mention/citation rates require complete 3-repetition sets
- Live Lark publish requires configured app credentials and table IDs
- Python 3.9 environments may hit Google client EOL warnings
