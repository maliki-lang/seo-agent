# SEO/GEO Delivery Tracker — Ting-accessible evidence exports

These CSVs are the human-readable mirror of the local tracking DB for delivery review.
**System of record remains SQLite** (`data/tracking.db`, gitignored). These files exist so Ting can open exact evidence without local DB access.

| Export | Rows | Vera requirement |
| --- | ---: | --- |
| [keyword_catalogue_55.csv](./keyword_catalogue_55.csv) | 55 | Keyword Catalogue — one row per keyword |
| [ai_questions_20.csv](./ai_questions_20.csv) | 20 | AI Questions — one row per question |
| [approved_lists.csv](./approved_lists.csv) | brand + AI referrers | Approved Lists |
| [run_logs.csv](./run_logs.csv) | recent runs | Run Logs |
| [architecture_and_configuration.csv](./architecture_and_configuration.csv) | components | Architecture & Configuration |

Related review artifacts:

- `research/ting_selected_approved_776935e6.csv` — Ting-approved selected keyword sheet
- `research/ting_review_v2_ting_approved_776935e6.csv` — full review export

## Honest status (do not overclaim)

- Keyword Catalogue export: 55 rows (Lark shows approved).
- AI Questions export: 20 rows (16 selected / 4 rejected) with `source_candidate_id` keyword lineage + decision reasons.
- Phase 16 first experiment is **logged** (mules answer section), but the Shopify collection description may still be empty — no incremental-click proof yet.
