# SEO/GEO Agent — Data & Tracking Layer Implementation Specification

**Project:** Sunnystep SEO/GEO Agent  
**Implementation window:** 15–18 September 2026  
**Owner:** Maliki  
**Approver:** Ting  
**Document role:** Source-of-truth implementation specification for Cursor  
**Status:** Ready for repository inspection and phased implementation

---

## 0. Instructions to the coding agent

Treat this file as the implementation contract. Inspect the repository before changing code, then implement the requirements in small, reviewable commits.

### Operating rules

1. Extend the existing system; do not rewrite working content-generation workflows.
2. Verify the repository's real interfaces, dependency versions, configuration patterns, and naming before coding.
3. If this specification conflicts with working repository behavior, preserve backward compatibility and document the conflict.
4. Do not invent credentials, API responses, table IDs, account access, production data, or evidence of completed runs.
5. Never mark an external integration complete without a successful authenticated sample pull or an explicitly documented blocker.
6. Do not expose secrets in source, fixtures, logs, reports, screenshots, or documentation.
7. Make collectors idempotent. A rerun for the same date must update or ignore the same natural keys, not create duplicates.
8. Preserve raw external responses when needed for replay or audit, while keeping normalized operational tables queryable.
9. Add tests with every implementation unit. Do not delete stale tests without replacing their intended coverage.
10. Run formatting, static checks, unit tests, integration tests that can run safely, and a same-day rerun test before declaring completion.
11. Use UTC for stored timestamps. Interpret reporting dates in the configured business timezone, defaulting to `Asia/Singapore`.
12. Keep source adapters, transformations, storage, validation, reporting, and orchestration separate.
13. If an API capability is uncertain, add a capability probe and store `unsupported` or `unverified`; do not fabricate a value.
14. Pause and report a blocker when a change could destroy production data, rotate credentials without authorization, alter a public schema incompatibly, or incur unapproved API cost.

### Required first response from Cursor

Before editing, return:

- detected project structure and Python version;
- dependency and test commands found in the repository;
- relevant existing modules and interfaces;
- differences between this specification and current code;
- implementation sequence;
- blockers requiring human access or decisions;
- files expected to be created or modified.

Then proceed phase by phase. After each phase, state what changed, tests run, results, unresolved risks, and the next phase.

---

## 1. Objective

Add an operational SEO/GEO measurement layer to the existing SEO content-production agent.

The target loop is:

```text
collect → normalize → store → validate → baseline → report → prioritize → alert → rerun safely
```

The inherited system already includes useful foundations such as content research, drafting, evaluation, Shopify publishing, Search Console and GA4 connectors, a Serper-backed SERP client, post-publish ledgers, opportunity scoring, and Lark review code. This project must preserve those capabilities and add repeatable site-wide measurement.

### Required outcomes

- Daily collection from Google Search Console, GA4, Serper, and AI-answer engines.
- Ninety-day GSC and GA4 backfill.
- Fixed catalogues containing at least 50 tracked keywords and exactly or at least 20 approved non-branded AI questions.
- Durable normalized history with raw-answer retention.
- Brand/non-brand and AI-referral classification.
- Automated quality checks with auditable results.
- Immutable 28-day baseline snapshots.
- Weekly report and evidence-backed prioritized action list.
- Lark Base operational writeback and failure alerts.
- Unattended server/cloud scheduling independent of a developer laptop.
- Safe same-day reruns with no duplicate natural keys.

### Non-goals for this delivery

- Replacing the existing content-writing pipeline.
- Rebuilding Shopify publishing beyond fixing confirmed defects and adding tests.
- Building a full BI platform.
- Scraping providers in violation of their terms.
- Claiming causal SEO impact from correlations alone.
- Storing secrets or unrestricted raw customer data in Lark Base.
- Supporting every search engine, locale, country, or device in the first release.

---

## 2. Repository baseline and mandatory discovery

Inspect these known paths if present:

```text
HANDOVER.md
CLAUDE.md
docs/seo-agent-strategy-and-eval-plan.md
docs/agent-contracts.md
data_sources/modules/eval/
data_sources/modules/google_analytics.py
data_sources/modules/google_search_console.py
data_sources/modules/dataforseo.py
data_sources/modules/shopify_publisher.py
data_sources/modules/ledger.py
track_published.py
daily_sync.py
tests/
```

### Discovery checklist

- Confirm Python version and package manager.
- Locate CLI conventions, logging configuration, configuration loader, and test fixtures.
- Locate existing GSC, GA4, Serper/DataForSEO, Shopify, OpenAI, Perplexity, and Lark clients.
- Identify current persistent storage and migration mechanism.
- Search for hardcoded tokens, passwords, API keys, base IDs, and webhook URLs without printing their values.
- Confirm whether `.git` history is available.
- Run the repository's documented tests before edits and record failures.
- If `pytest` is not installed, install dependencies only through the repository's declared mechanism; do not silently change dependency policy.
- Run `python3 -m compileall -q .` where applicable.
- Distinguish pre-existing failures from regressions introduced by this work.

### Known issues to verify

1. `data_sources/modules/dataforseo.py` may actually implement Serper.
2. `tests/test_dataforseo_resilience.py` may target a stale `_post` interface while the implementation uses `session.post`.
3. Search Console dependencies or permissions may be missing.
4. `daily_sync.py` may contain a hardcoded Lark Base token.
5. Shopify URL construction may include literal braces, for example:

```python
f"{{https://{self.shop}}}/admin/api/{self.api_version}"
```

Correct form should be equivalent to:

```python
f"https://{self.shop}/admin/api/{self.api_version}"
```

Do not assume these findings are still true; confirm them in the current repository.

---

## 3. Target architecture

Prefer this package layout unless the repository has a clearly established equivalent:

```text
data_sources/
└── tracking/
    ├── __init__.py
    ├── cli.py
    ├── config.py
    ├── enums.py
    ├── exceptions.py
    ├── logging.py
    ├── models.py
    ├── runner.py
    ├── storage.py
    ├── retries.py
    ├── costs.py
    ├── migrations/
    ├── collectors/
    │   ├── __init__.py
    │   ├── base.py
    │   ├── gsc.py
    │   ├── ga4.py
    │   ├── serper.py
    │   ├── ai_visibility.py
    │   └── shopify.py
    ├── transforms/
    │   ├── __init__.py
    │   ├── brand_label.py
    │   ├── ai_referrals.py
    │   ├── normalize.py
    │   └── metrics.py
    ├── checks/
    │   ├── __init__.py
    │   ├── base.py
    │   ├── completeness.py
    │   ├── freshness.py
    │   ├── duplicates.py
    │   ├── reconciliation.py
    │   └── cost.py
    ├── reports/
    │   ├── __init__.py
    │   ├── baseline.py
    │   ├── weekly.py
    │   └── opportunities.py
    └── sinks/
        ├── __init__.py
        ├── lark.py
        └── alerts.py
```

Supporting assets:

```text
config/
├── tracking.example.yaml
├── tracked_keywords.csv
├── ai_questions.csv
├── brand_terms.txt
└── ai_referrers.yaml

docs/
├── tracking-architecture.md
├── tracking-runbook.md
├── metric-definitions.md
└── gate-demo.md

tests/
├── tracking/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/
└── ...
```

### Component boundaries

- **Collectors:** retrieve source data and return typed source records; no reporting logic.
- **Transforms:** normalize fields and apply deterministic labels.
- **Storage:** migrations, transactions, upserts, snapshots, and queries.
- **Checks:** evaluate stored run data and persist every result.
- **Reports:** calculate baselines, weekly metrics, and opportunities from validated data.
- **Sinks:** write approved operational output to Lark and send alerts.
- **Runner:** orchestrate stages, state transitions, retries, and failure behavior.
- **CLI:** expose reproducible commands; contain no business logic.

---

## 4. Configuration and secrets

Use environment variables for secrets and a checked-in example configuration for non-secret defaults. Reuse the repository's configuration framework if one exists.

### Proposed environment variables

```text
TRACKING_ENV=development|staging|production
TRACKING_DATABASE_URL=sqlite:///... or supported production DSN
TRACKING_TIMEZONE=Asia/Singapore
TRACKING_LOG_LEVEL=INFO
TRACKING_DAILY_COST_CAP_USD=<decimal>
TRACKING_RUN_TIMEOUT_SECONDS=<integer>

GSC_PROPERTY=sc-domain:sunnystep.com
GOOGLE_APPLICATION_CREDENTIALS=<path, never checked in>
GA4_PROPERTY_ID=<id>

SERPER_API_KEY=<secret>
SERPER_GL=sg
SERPER_HL=en
SERPER_LOCATION=Singapore
SERPER_RESULTS_LIMIT=100

OPENAI_API_KEY=<secret>
OPENAI_VISIBILITY_MODEL=<approved model>
PERPLEXITY_API_KEY=<secret>
PERPLEXITY_VISIBILITY_MODEL=<approved model>

LARK_APP_ID=<secret or managed identity setting>
LARK_APP_SECRET=<secret>
LARK_BASE_APP_TOKEN=<secret>
LARK_ALERT_WEBHOOK_URL=<secret, if webhooks are approved>
LARK_*_TABLE_ID=<non-secret or environment-specific identifier>

SHOPIFY_SHOP_DOMAIN=<domain>
SHOPIFY_ACCESS_TOKEN=<secret>
SHOPIFY_API_VERSION=<version>
```

### Configuration model

Configuration must be typed and validated at startup. Fail fast for missing values required by the selected command, but do not require unrelated integrations for unit tests or partial local runs.

Suggested sections:

```yaml
tracking:
  timezone: Asia/Singapore
  storage_url: sqlite:///data/tracking.db
  daily_cost_cap_usd: 25
  run_timeout_seconds: 3600
  retry:
    max_attempts: 3
    base_delay_seconds: 2
    max_delay_seconds: 60
    jitter: true
  freshness:
    gsc_days: 3
    ga4_days: 2
    serper_hours: 30
    ai_visibility_hours: 30
  baseline_days: 28
  backfill_days: 90
```

Do not commit production identifiers if company policy treats them as sensitive. Provide `.env.example` with placeholders only.

### Secret remediation

If a hardcoded Lark token is confirmed:

1. Remove it from source and documentation.
2. Replace it with environment-based loading.
3. Add a secret-scanning CI step using the repository's preferred tool.
4. Notify the owner that the exposed token must be rotated; code cannot rotate it without authorized access.
5. Avoid repeating the token in commits, test output, or reports.

---

## 5. Data contracts

Use explicit typed models. Dataclasses or Pydantic are acceptable if consistent with the repository.

### Fields required on every normalized source row

```text
run_id          UUID/string identifying one orchestration run
as_of_date      Business date represented by the data
source          Stable source enum
collected_at    UTC timestamp when fetched
natural_key     Stable deterministic key
row_hash        Hash of canonical normalized payload
```

Recommended additional fields:

```text
schema_version
raw_record_id or raw_payload_path
created_at
updated_at
```

### Natural-key policy

- Generate natural keys from canonical normalized values, not Python object representations.
- Trim whitespace, normalize hostnames, preserve meaningful case rules, and normalize URLs consistently.
- Use a stable hash such as SHA-256 for `natural_key` and `row_hash` where a composite string would be unwieldy.
- `row_hash` must exclude ingestion metadata such as `collected_at` so unchanged source data remains unchanged.

### Required logical tables

#### `run_log`

```text
run_id              primary key
run_type            daily|backfill|weekly|baseline|manual|demo
as_of_date
started_at
finished_at
status               pending|running|partial|succeeded|failed|cancelled
requested_collectors JSON/list
completed_collectors JSON/list
failed_collectors    JSON/map
attempt_number
parent_run_id        nullable
code_version         commit SHA/build id when available
config_fingerprint   non-secret configuration hash
row_counts           JSON/map
cost_usd             decimal
error_code           nullable
error_message        redacted nullable
created_at
updated_at
```

#### `keyword_catalog`

```text
keyword_id           stable primary key
keyword              unique normalized keyword
cluster
target_page
country
device
language
active
valid_from
valid_to              nullable
created_at
updated_at
```

Require at least 50 active keywords before production daily runs pass validation.

#### `ai_question_catalog`

```text
question_id          stable primary key
question             approved non-branded question
cluster
target_page
locale
active
valid_from
valid_to              nullable
created_at
updated_at
```

Require at least 20 active questions. Changes must be versioned or date-effective so historical comparisons remain interpretable.

#### `gsc_daily`

```text
run_id
as_of_date
date
query
page
country
clicks
impressions
ctr
position
is_brand
brand_rule_version
source
collected_at
natural_key
row_hash
```

Unique key:

```text
date + query + page + country
```

The exact dimensions must include `query` and `page`. Device may be added if approved, but changing grain requires migration and metric documentation.

#### `ga4_daily`

```text
run_id
as_of_date
date
session_source
session_medium
landing_page
channel_class         organic_search|ai_referral|other
sessions
engaged_sessions
purchases
total_revenue
source
collected_at
natural_key
row_hash
```

Unique key:

```text
date + session_source + session_medium + landing_page
```

Currency must be documented. Use decimal-safe handling for revenue.

#### `serp_daily`

```text
run_id
as_of_date
date
keyword_id
keyword
cluster
target_page
country
device
sunnystep_position    integer; 0 means absent in inspected top 100
result_count_inspected
top_10_domains        ordered JSON array
ai_overview_status    present|absent|unsupported|unverified
ai_overview_citations ordered JSON array
raw_record_id         nullable
source
collected_at
natural_key
row_hash
```

Unique key:

```text
date + keyword_id + country + device
```

#### `ai_answer_runs`

```text
run_id
as_of_date
engine                chatgpt|perplexity
question_id
question
repetition_number     1..3
raw_answer
mentioned_sunnystep
cited_urls             ordered JSON array
named_competitors      ordered JSON array
target_cluster
target_page
api_cost_usd
latency_ms
model
search_enabled
parser_version
source
collected_at
natural_key
row_hash
```

Unique key:

```text
as_of_date + engine + question_id + repetition_number
```

For 20 questions, two engines, and three repetitions, a complete run contains 120 answer rows.

#### `raw_records`

Recommended for replayable payloads:

```text
raw_record_id
run_id
source
endpoint_or_operation
request_fingerprint
payload_compressed_or_path
content_type
received_at
retention_class
checksum
```

Redact credentials, authorization headers, cookies, and unnecessary personal data before persistence.

#### `quality_check_log`

```text
check_id
run_id
check_name
scope
status                pass|warn|fail|skipped
severity              info|warning|error|critical
threshold
observed_value
details_json
checked_at
```

Every executed or skipped check must produce a row.

#### `baseline`

```text
baseline_id
baseline_name
period_start
period_end
locked_at
locked_by
status                draft|locked|superseded
metric_name
segment_json
metric_value
source_query_version
input_fingerprint
notes
```

Locked rows are immutable. Corrections create a new baseline version with lineage; they do not overwrite the original.

#### `opportunities`

```text
opportunity_id
report_id
category              seo|geo
problem
supporting_evidence_json
source_row_references_json
target_query_or_question
target_page
proposed_action
owner
impact_score
impact_estimate
confidence_label
confidence_value
effort_label
effort_value
priority_score
metric_to_watch
status
created_at
```

#### `weekly_reports`

```text
report_id
period_start
period_end
baseline_id
status                draft|published|failed
summary_json
quality_status
opportunity_count
lark_record_id         nullable
created_at
published_at           nullable
```

#### `alerts`

```text
alert_id
run_id
severity
alert_type
summary
details_redacted
status                pending|sent|failed|acknowledged
attempts
created_at
sent_at                 nullable
external_reference     nullable
```

### Storage requirements

- Use schema migrations; do not create production tables ad hoc on every run.
- Enforce unique constraints at the database level.
- Upserts must run in transactions.
- A failed collector must not partially commit an indistinguishable successful dataset.
- Store collector completion state independently so partial runs are explicit.
- Support SQLite locally if appropriate, but keep SQL portable or provide a documented production database choice.
- Provide repository methods rather than scattering raw SQL throughout collectors.

---

## 6. Source collector specifications

### 6.1 Google Search Console

#### Purpose

Collect daily site-wide query-by-page performance.

#### Required dimensions

```text
date, query, page, country
```

#### Required metrics

```text
clicks, impressions, ctr, position
```

#### Behavior

- Query the configured property, expected to be `sc-domain:sunnystep.com`.
- Paginate until all rows are retrieved or the API's documented limit is reached.
- Use explicit date ranges.
- Handle GSC data delay; do not treat recent unavailable dates as zero without documentation.
- Apply brand classification after normalization.
- Record request range, row count, pagination count, and delay assumptions without logging query content at noisy levels.
- Backfill the last 90 available days.
- Upsert by the defined natural key.

#### Access blocker

If the service account cannot access the property, fail with a typed `PermissionDenied`/equivalent error, persist the failed collector state, run other independent collectors if allowed, and send an actionable alert. Never insert placeholder GSC rows.

Suggested human escalation:

> The tracking service account needs read access to `sc-domain:sunnystep.com`. The schema and other collectors can proceed, but GSC backfill and the complete four-source demo remain blocked until a real sample pull succeeds.

### 6.2 GA4

#### Required dimensions

Use API equivalents of:

```text
date
sessionSource
sessionMedium
landingPagePlusQueryString
```

#### Required metrics

```text
sessions
engagedSessions
purchases
totalRevenue
```

#### Behavior

- Use the configured property ID.
- Paginate as required.
- Normalize `(not set)`, direct, missing, and empty values consistently.
- Classify each row as `organic_search`, `ai_referral`, or `other`.
- Document timezone and revenue currency behavior.
- Backfill 90 days.
- Reconcile totals against a less granular API query for the same period within an approved tolerance.

### 6.3 Serper

The existing module named `dataforseo.py` appears to be a Serper wrapper. Prefer one of these compatibility approaches:

1. Introduce `SerperClient` in `serper.py` and leave a deprecated import alias in `dataforseo.py`; or
2. Keep the existing module path temporarily but rename classes and documentation accurately.

Do not break callers without a migration path and tests.

#### Daily behavior

For every active keyword:

- request enough results to determine Sunnystep position through the top 100 where provider limits allow;
- canonicalize result URLs and domains;
- capture ordered top-10 domains;
- record position `0` when no Sunnystep domain result appears in inspected results;
- detect AI Overview only from verified fields in the real response;
- store raw responses or replayable fixtures with sensitive fields removed;
- enforce daily API cost limits.

#### AI Overview

Use four states:

```text
present, absent, unsupported, unverified
```

`absent` is valid only if the account and response schema have been verified to support detection. Otherwise use `unsupported` or `unverified`.

### 6.4 AI visibility

#### Catalogue

Use 20 approved, fixed, non-branded questions. Each question must have a stable `question_id`, target cluster, and target page.

#### Execution matrix

```text
20 questions × 2 engines × 3 repetitions = 120 answers
```

Run ChatGPT with approved web/search functionality and Perplexity with its approved search-enabled model. Record the exact model and whether search was enabled.

#### Parsing

For each answer, derive:

- whether Sunnystep is explicitly named;
- cited URLs, canonicalized;
- whether a `sunnystep.com` URL is cited;
- named competitors from an approved alias catalogue;
- latency and estimated or provider-reported cost.

Retain the raw answer for every parsed row. The parser must be deterministic and versioned. Add fixtures for punctuation, casing, markdown links, URL parameters, bare domains, and false-positive brand substrings.

#### Aggregate metrics

For each question and engine:

```text
stable mention = Sunnystep named in at least 2 of 3 repetitions
stable citation = a sunnystep.com URL cited in at least 2 of 3 repetitions
```

Overall:

```text
AI mention rate = questions with a stable mention ÷ total eligible questions
AI citation rate = questions with a stable citation ÷ total eligible questions
```

Report engine-specific and combined rates. Document how a question is counted if one repetition fails; default behavior is incomplete and excluded from published rates until all three repetitions exist.

### 6.5 Shopify

This project does not require a new publishing workflow. It does require:

- confirming and fixing malformed base/token/edit URL construction;
- unit tests asserting exact URLs;
- no live write tests against production by default;
- compatibility with existing publish behavior.

---

## 7. Classification rules

### 7.1 Brand vs non-brand

Create a versioned classifier using an explicit brand-term configuration.

Initial rules should cover approved variants such as:

```text
sunnystep
sunny step
common spacing, punctuation, and typo variants approved by the owner
approved product or model names only if business considers them branded
```

Normalization may include Unicode normalization, lowercase conversion, punctuation-to-space conversion, whitespace collapse, and approved typo aliases. Avoid fuzzy matching that creates unreviewable false positives unless separately measured.

#### Required test set

- At least 200 hand-labelled queries.
- Include positives, negatives, near matches, competitor names, ambiguous phrases, misspellings, multilingual examples where relevant, and substrings that should not match.
- Required accuracy: at least 95%.
- Also report precision, recall, false positives, and false negatives so accuracy is not misleading.
- Store classifier/rule version on each GSC row.

### 7.2 GA4 channel classification

Use ordered deterministic rules.

1. `ai_referral` when normalized source/host matches the approved AI-referrer list.
2. `organic_search` when GA4 medium/source matches approved organic search rules and the row is not an AI referral.
3. `other` otherwise.

Initial AI domains should be reviewed and may include:

```text
chatgpt.com
perplexity.ai
gemini.google.com
approved Copilot-related domains
```

Store aliases in configuration, not hardcoded across multiple modules. Test subdomains, URL-like source values, mixed casing, source/medium disagreements, and unknown AI referrers.

---

## 8. Run orchestration

### Daily run state machine

```text
PENDING
→ RUNNING
→ COLLECTING
→ NORMALIZING
→ PERSISTING
→ VALIDATING
→ REPORTING (when scheduled)
→ PUBLISHING (when scheduled)
→ SUCCEEDED | PARTIAL | FAILED
```

The stored external status may use the simpler `run_log.status`, while detailed stage transitions go to structured logs or a stage table.

### Daily sequence

1. Validate command-specific configuration.
2. Acquire a run lock for `as_of_date` and run type.
3. Create `run_log` row.
4. Load active catalogues and validate minimum sizes.
5. Execute independent collectors with controlled concurrency.
6. Apply retries only to retryable errors.
7. Normalize and persist each collector atomically.
8. Run quality checks.
9. Determine final status.
10. Publish approved operational rows/report when quality gates allow.
11. Send failure or warning alerts as configured.
12. Persist final counts, cost, duration, and status.
13. Release lock even on failure.

### Error taxonomy

At minimum:

```text
ConfigurationError       non-retryable
AuthenticationError      non-retryable until credentials change
PermissionDenied         non-retryable until access changes
RateLimitError           retryable using Retry-After
TransientNetworkError    retryable
ProviderServerError      retryable with cap
InvalidResponseError     usually non-retryable; preserve redacted sample
SchemaMismatchError      non-retryable and critical
CostLimitExceeded        non-retryable for current run
DataQualityError         no source retry unless check indicates incomplete fetch
RunLockError             skip or fail without duplicate execution
```

### Retry policy

- Default maximum: three attempts total.
- Exponential backoff with jitter.
- Honor provider `Retry-After`.
- Never retry authentication/permission/configuration failures automatically.
- Ensure retries do not duplicate rows or API-side mutations.
- Persist attempt count and final error class.
- Include a safe simulated-failure mode for the gate demo.

### Timeouts and cancellation

- Apply connect/read timeouts to every HTTP call.
- Enforce per-collector and overall run timeouts.
- On cancellation, persist `cancelled` or `failed` state and committed collector outcomes.
- Do not leave a permanent lock after process termination; use expiring or recoverable locks.

### Concurrency

- Parallelize independent source collectors only where provider limits and existing architecture allow.
- Bound worker count.
- Preserve deterministic per-keyword/question result ordering in stored/report output.
- Do not execute multiple repetitions of the same AI question so aggressively that rate limits or correlated transient failures invalidate results.

---

## 9. Idempotency and backfill

### Idempotency acceptance rule

Running the same command twice for the same `as_of_date` and unchanged source data must produce:

- zero additional rows for all natural-key tables;
- unchanged business values and row hashes;
- a separate run record or explicit reuse policy;
- clear update counts if source values changed;
- no duplicated Lark records.

### Upsert behavior

- Insert when natural key is new.
- Update mutable source metrics when natural key exists and `row_hash` changes.
- Keep audit lineage through timestamps and run references; if full history of corrections is required, add a revision table rather than duplicate logical rows.
- Do not overwrite locked baseline rows.

### Backfill command

Support a bounded command such as:

```bash
python -m data_sources.tracking.cli backfill \
  --sources gsc,ga4 \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD
```

Requirements:

- validate maximum range or require an explicit override;
- process in provider-appropriate chunks;
- resume after interruption;
- display counts and failures without secrets;
- preserve existing rows by upsert;
- stop before cost cap where relevant;
- generate a final reconciliation summary.

---

## 10. Automated data-quality checks

Implement at least the following checks. Every check writes to `quality_check_log`.

| Check | Default severity | Pass condition |
|---|---:|---|
| Source present | Error | 100% normalized rows have a valid source |
| Timestamp present | Error | 100% have `collected_at` |
| As-of date present | Error | 100% have `as_of_date` |
| Natural-key uniqueness | Critical | Zero duplicate natural keys per table |
| Collector completion | Critical | All required collectors succeeded, or failure is explicit |
| Keyword catalogue size | Error | At least 50 active keywords |
| AI question catalogue size | Error | At least 20 active questions |
| AI repetition completeness | Error | Three rows per question/engine |
| Brand classifier quality | Error | Accuracy ≥95%; precision/recall reported |
| Freshness | Error | Within configured source delay |
| Non-negative metrics | Error | No negative counts, revenue, cost, or latency |
| CTR range | Error | `0 ≤ ctr ≤ 1` after normalization |
| Position validity | Error | GSC position non-negative; SERP integer in `0..100` |
| API cost cap | Critical | Run cost ≤ configured cap |
| Run duration | Warning/Error | Duration ≤ configured timeout |
| Backfill preservation | Critical | Existing natural keys are not duplicated |
| Raw AI answer presence | Critical | Every parsed AI row has raw answer or raw record reference |
| GA4 reconciliation | Warning/Error | Detailed totals within approved tolerance |
| GSC reconciliation | Warning | Pagination and aggregate totals internally consistent |
| URL validity | Warning | Target/cited/page URLs parse and use allowed schemes |
| Lark publish parity | Error | Published record counts/keys match intended set |

### Quality gate

- Critical failure: final run status `failed`; do not publish weekly conclusions as valid.
- Error failure: final status `partial` or `failed` according to configured policy; publish only a clearly marked operational failure report if required.
- Warning: run may succeed, but report must expose the warning.
- Skipped checks require an explicit reason, not silent omission.

---

## 11. Baseline specification

Create a 28-day site-wide baseline after validated historical data is available.

### Baseline period

Use the latest complete 28-day window ending on the latest date that satisfies source freshness for all required metrics. Record exact start/end dates.

### Minimum metrics

SEO:

- GSC clicks, impressions, weighted CTR, and weighted average position;
- branded vs non-branded clicks and impressions;
- GA4 organic sessions, engaged sessions, purchases, revenue, engagement rate, and conversion rate where derivable;
- tracked-keyword visibility: count/rate in top 3, top 10, top 20, and absent;
- optional cluster and target-page segments when data is complete.

GEO:

- ChatGPT mention rate and citation rate;
- Perplexity mention rate and citation rate;
- combined mention and citation rate, with methodology stated;
- competitor mention frequency if catalogue coverage is approved.

### Baseline rules

- Persist numerator and denominator where a rate is stored.
- Store metric definitions/query version and input fingerprint.
- Lock the baseline only if required quality checks pass.
- A locked baseline cannot be updated in place.
- Display missing values as unavailable, not zero.
- Do not mix date ranges silently across sources.

---

## 12. Weekly report

Generate a weekly report from validated data and write an operational version to Lark Base.

### Required sections

1. Reporting period and data freshness.
2. Run and quality status.
3. Executive summary with evidence, not generic advice.
4. SEO performance vs baseline and previous comparable period.
5. GEO visibility by engine vs baseline.
6. Branded vs non-branded movement.
7. Keyword distribution and meaningful movers.
8. Organic and AI-referral traffic/conversion metrics.
9. Data issues, failed collectors, and caveats.
10. Top ten prioritized actions.
11. Links or IDs that trace actions to source rows.

### Metric calculation rules

- Weighted CTR = total clicks / total impressions.
- Do not average row-level CTR values.
- Weighted GSC position = impression-weighted position unless metric definitions approve another method.
- Rate changes should show both percentage-point and relative change where useful.
- Protect division by zero and label unavailable comparisons.
- Use comparable complete windows.
- Separate observed facts from inferred recommendations.

### Lark publishing

- Use deterministic external keys such as `report_id` or `period_start + period_end`.
- Upsert, do not append duplicates on rerun.
- Keep raw answer text and unrestricted query-level data out of Lark unless explicitly approved.
- Publish concise summaries and evidence references.
- Verify write response and persist returned Lark record IDs.
- Retry only safe, idempotent operations.

---

## 13. Opportunity model

Every action must include:

```text
problem and evidence
source-row references
target query or AI question
target Sunnystep page
proposed action
owner
estimated impact
effort
confidence
metric to watch
priority score
```

### Scoring

```text
Priority = Impact × Confidence ÷ Effort
```

Default mappings:

```text
Confidence: High = 0.9, Medium = 0.6, Low = 0.3
Effort: S = 1, M = 2, L = 3
```

Normalize impact onto a documented comparable scale before ranking. Do not rank a raw click estimate directly against a raw AI-mention estimate.

### SEO impact

Estimate from evidence such as impressions, current rank, target rank, current clicks, and an approved CTR curve.

```text
expected_clicks = monthly_impressions × expected_ctr_at_target_rank
estimated_click_gain = max(0, expected_clicks - current_clicks)
```

Store inputs and assumptions. Do not present estimates as guaranteed results.

### GEO impact

Express as expected additional stable mentions or citations across the fixed question catalogue. Keep GEO impact separate from SEO impact in reports, even if both are normalized for prioritization.

### Recommendation constraints

- No action without traceable evidence.
- Avoid recommending a new page when an existing target page should be improved.
- Detect multiple keywords/questions mapping to the same target page and consolidate overlapping actions.
- Include confidence penalties for incomplete data, unverified AI Overview support, weak sample size, or freshness warnings.
- Produce at most ten primary weekly actions, sorted by score with deterministic tie-breaking.

---

## 14. CLI contract

Adapt names to repository conventions, but provide equivalent capabilities.

```bash
# Validate configuration without external calls
python -m data_sources.tracking.cli doctor

# Probe one source safely
python -m data_sources.tracking.cli collect --source gsc --as-of-date YYYY-MM-DD --dry-run

# Daily collection
python -m data_sources.tracking.cli daily --as-of-date YYYY-MM-DD

# Backfill
python -m data_sources.tracking.cli backfill --sources gsc,ga4 --start-date YYYY-MM-DD --end-date YYYY-MM-DD

# Run checks for an existing run
python -m data_sources.tracking.cli check --run-id <id>

# Create and lock baseline
python -m data_sources.tracking.cli baseline --end-date YYYY-MM-DD --lock

# Generate weekly report without publishing
python -m data_sources.tracking.cli weekly --period-end YYYY-MM-DD --no-publish

# Generate and publish weekly report
python -m data_sources.tracking.cli weekly --period-end YYYY-MM-DD --publish

# Safe gate-demo failure injection
python -m data_sources.tracking.cli daily --as-of-date YYYY-MM-DD --simulate-failure serper:authentication
```

### CLI behavior

- `--help` for every command.
- Non-zero exit codes for failed required operations.
- Human-readable default output plus optional `--json`.
- `--dry-run` must not write source rows or call mutation endpoints.
- No secrets in errors or verbose mode.
- Date validation in configured business timezone.
- Clear summary of inserted, updated, unchanged, failed, and skipped rows.

---

## 15. Logging, observability, and alerts

Use structured logs where the repository supports them.

### Required log context

```text
run_id
stage
source
as_of_date
attempt
operation
row_count
duration_ms
status
error_class
```

Never log authorization headers, API keys, full credential paths containing secrets, raw cookies, or unredacted sensitive payloads.

### Alerts

Send alerts for:

- required collector failure;
- authentication or permission denial;
- critical quality-check failure;
- cost-cap breach;
- run timeout;
- weekly-report publication failure;
- stale data beyond expected delay.

Alert content:

- environment;
- run ID;
- affected source/stage;
- redacted error class and concise message;
- attempts made;
- recommended recovery action;
- link/reference to logs or runbook if available.

Deduplicate repeated alerts for the same run/source/failure class.

---

## 16. Scheduling and deployment

The production scheduler must run independently of a developer laptop. Use the deployment platform already approved for the repository. If none exists, provide deployable examples for one cloud/server scheduler but do not provision infrastructure without authorization.

### Schedule expectations

- Daily source collection after expected GSC/GA4 availability.
- Weekly report after the relevant daily collection and validation complete.
- Business timezone: `Asia/Singapore` unless approved otherwise.
- Overlap prevention using run locks.
- Retries handled by application policy; scheduler retries must not cause duplicate data.

### Deployment requirements

- Secrets supplied through managed environment configuration.
- Pinned dependencies/lockfile.
- Migration step before first run.
- Health/doctor command.
- Documented rollback.
- Logs retained according to company policy.
- Scheduler identity has least privilege.

Do not rely solely on macOS `launchd` for the final unattended system.

---

## 17. Testing strategy

### Unit tests

Cover:

- configuration validation;
- URL/domain normalization;
- natural-key and row-hash stability;
- brand classifier and hand-labelled evaluation;
- AI-referrer classification;
- GSC/GA4/Serper/AI response parsing;
- AI mention/citation extraction;
- priority-score calculations;
- baseline arithmetic;
- retry classification and delay bounds;
- cost accounting;
- Shopify URL construction;
- redaction of errors/logs.

### Contract tests

Use sanitized recorded fixtures or mocked HTTP boundaries for each provider. Assert required request dimensions, pagination, response parsing, and capability handling. Never commit real tokens or sensitive production payloads.

### Integration tests

Using a temporary database:

- migrations apply from empty state;
- rows upsert correctly;
- duplicate constraints hold;
- partial collector failure is represented correctly;
- quality checks persist results;
- baseline locking is immutable;
- Lark sink upserts by external key through a mock/fake;
- weekly report references source rows.

### End-to-end fixture test

Create deterministic fixtures producing:

```text
≥50 SERP keyword rows
120 AI answer rows
200 hand-labelled brand queries
0 duplicate natural keys
100% source/timestamp/as-of-date coverage
```

Run daily ingestion twice and assert no logical row-count increase on the second run.

### Live smoke tests

Guard behind explicit flags and credentials. Default test runs must not spend money, publish Shopify content, or write to production Lark Base.

### Required commands before completion

Use repository-standard equivalents of:

```bash
python3 -m compileall -q .
pytest -q
ruff check .          # if configured
mypy ...              # if configured
<secret scan command>
```

Record pre-existing failures separately. No new failures are acceptable.

---

## 18. Security and privacy

- Rotate any confirmed exposed credential.
- Use least-privilege service accounts.
- Do not store secrets in Lark Base.
- Redact request/response headers before raw persistence.
- Minimize storage of query-level or referral data outside the durable internal store.
- Restrict live destructive tests.
- Add secret scanning to CI.
- Validate outbound URLs and do not follow arbitrary URLs derived from provider content.
- Pin or constrain dependencies according to repository policy.
- Document retention and deletion rules for raw AI answers and API responses.
- Ensure error traces presented to users cannot reveal credentials.

---

## 19. Documentation deliverables

### `docs/tracking-architecture.md`

Include:

- context and goals;
- component diagram;
- data flow;
- logical schema and natural keys;
- scheduling model;
- retry/failure behavior;
- secret boundaries;
- raw vs normalized data;
- Lark operational-view boundary;
- known limitations.

### `docs/metric-definitions.md`

Define every published metric, grain, source, formula, timezone, freshness expectation, and caveat.

### `docs/tracking-runbook.md`

A second person must be able to recover service within 30 minutes. Include:

- prerequisites and access checklist;
- how to run doctor/sample pulls;
- how to run daily/backfill/weekly commands;
- how to inspect a failed run;
- how to distinguish permission, rate-limit, schema, quality, and cost failures;
- how to retry safely;
- how to rotate credentials without committing them;
- how to verify Lark writeback;
- how to unlock/recover stale run locks;
- rollback steps;
- escalation contacts represented by roles, not invented names.

### `docs/gate-demo.md`

Provide exact commands and expected evidence for the demo sequence in Section 21.

---

## 20. Implementation phases and commit plan

### Phase 0 — Baseline and safety

- Clone/access real repository and create branch.
- Install declared dependencies.
- Record baseline tests.
- Confirm source-access checklist.
- Remove confirmed hardcoded credential and add secret scan.
- Fix Shopify URL construction and stale resilience tests.

Suggested commits:

```text
chore: bootstrap environment and record baseline tests
fix: remove credentials from daily sync
fix: repair Shopify URL construction
```

### Phase 1 — Core tracking framework

- Typed configuration and enums.
- Data contracts.
- Migrations/storage repositories.
- Run log, locks, upsert behavior.
- CLI skeleton.

```text
feat: add tracking configuration and data contracts
feat: add idempotent tracking storage and run log
```

### Phase 2 — GSC and GA4

- Daily collectors.
- Classifiers.
- 90-day backfill.
- Reconciliation checks.

```text
feat: add GSC daily collector and backfill
feat: add GA4 landing-page collector and channel labels
```

### Phase 3 — SERP and AI visibility

- Serper compatibility layer.
- Keyword collector and AI Overview capability states.
- ChatGPT and Perplexity collectors.
- Three-repetition model and deterministic parser.
- Cost accounting.

```text
feat: add Serper keyword rank collection
feat: add AI visibility collectors
```

### Phase 4 — Quality, baseline, and opportunities

- Minimum 15 quality checks.
- Brand test set and ≥95% target.
- Locked 28-day baseline.
- Opportunity scoring.

```text
feat: add data quality checks and baseline locking
feat: add evidence-backed opportunity scoring
```

### Phase 5 — Reporting, alerts, and scheduling

- Weekly report.
- Lark Base upsert.
- Alerts.
- Unattended scheduler/deployment config.
- Runbook and gate-demo fixtures.

```text
feat: add Lark reports and failure alerts
ops: add unattended tracking schedule
docs: add tracking architecture and runbook
test: add gate demo fixtures
```

Do not force this exact commit split if repository conventions differ. Keep each commit coherent and tests green.

---

## 21. Gate demo

The implementation is accepted only when the following can be demonstrated with real access where required and deterministic fixtures where a safe simulated failure is intended.

1. Show access checklist and one sample pull from each available source.
2. Show a scheduled run completed without manual initiation.
3. Show representative normalized rows with source, date, timestamp, run ID, natural key, and row hash.
4. Show the locked 28-day baseline for core metrics.
5. Show the 200-query brand classifier evaluation with accuracy ≥95%, plus precision and recall.
6. Induce a safe simulated credential failure without exposing or changing a real credential.
7. Show retry classification and a deduplicated Lark alert.
8. Show the automatically generated weekly report.
9. Show the top ten evidence-backed actions.
10. Rerun the same date and prove zero duplicated natural keys.
11. Show the runbook and perform a second-person recovery walkthrough targeting less than 30 minutes.

### Required evidence bundle

```text
baseline test report
post-change test report
migration output
sample pull summaries
run_log record
quality_check_log records
50+ keyword rows
120 AI-answer rows
brand-classifier evaluation
baseline records
weekly report
opportunity rows
alert record/message
same-day rerun row-count and duplicate comparison
scheduler execution evidence
secret-scan result
```

Production evidence must be redacted before sharing outside approved systems.

---

## 22. Definition of done

All items below must be true or explicitly marked blocked with owner, reason, evidence, and next action.

### Code

- [ ] Existing content pipeline remains functional.
- [ ] Tracking package follows repository conventions.
- [ ] GSC daily query-by-page collector implemented.
- [ ] GA4 daily source/medium/landing-page collector implemented.
- [ ] Serper daily collector covers 50+ keywords.
- [ ] AI visibility covers 20 questions, two engines, and three repetitions.
- [ ] Shopify URL defect fixed if confirmed.
- [ ] DataForSEO/Serper naming compatibility handled.

### Data

- [ ] Required tables and migrations exist.
- [ ] Unique constraints enforce natural keys.
- [ ] Ninety-day GSC and GA4 backfill completed or access-blocked honestly.
- [ ] Raw AI answers retained.
- [ ] Every normalized row has source, as-of date, collection timestamp, natural key, and row hash.
- [ ] Same-day rerun creates zero duplicates.

### Quality

- [ ] At least 15 automated checks implemented.
- [ ] Every check writes a result.
- [ ] Brand classifier tested on at least 200 labelled queries.
- [ ] Brand accuracy is at least 95% with precision/recall reported.
- [ ] Cost and duration gates enforced.
- [ ] No new test failures.

### Reporting

- [ ] A validated 28-day baseline is locked.
- [ ] Weekly report is generated from stored data.
- [ ] Top-ten actions include source references and scoring inputs.
- [ ] Lark upsert is idempotent.
- [ ] Failure alert is demonstrated.

### Operations and security

- [ ] No hardcoded credentials remain in tracked files.
- [ ] Confirmed exposed tokens are flagged for rotation.
- [ ] Secret scanning runs in CI.
- [ ] Unattended scheduler does not depend on a laptop.
- [ ] Runbook enables recovery in under 30 minutes.
- [ ] Logs and evidence are redacted.

---

## 23. Blocker protocol

When blocked, do not substitute fake data in a real-source run. Record:

```text
blocker_id
source or component
date detected
exact failing operation
redacted error class/message
impact on acceptance criteria
work that can continue independently
required human action
owner role
verification step after resolution
```

Known likely blocker:

```text
Source: Google Search Console
Issue: tracking service account lacks access to sc-domain:sunnystep.com
Required action: property admin grants required read access
Verification: authenticated sample query returns real rows or a valid empty result for an approved date
```

Fixtures are acceptable only for automated tests and the explicitly simulated failure demo. They must be clearly labelled and must never be included in production baselines or reports.

---

## 24. Cursor completion report format

At the end of each implementation phase, respond with:

```text
Phase:
Status: complete | partial | blocked

Changes:
- ...

Files changed:
- ...

Tests run:
- command — result

Data/API evidence:
- ...

Security notes:
- ...

Known limitations/blockers:
- ...

Next phase:
- ...
```

At final completion, include:

- commit list;
- architecture summary;
- schema/migration summary;
- test results before and after;
- live source verification status;
- acceptance checklist;
- exact remaining blockers;
- demo commands;
- rollback instructions.

---

## 25. Final engineering principles

- Correctness before apparent completeness.
- Real access verification before claiming an integration works.
- Deterministic transformations before model-based interpretation.
- Database constraints in addition to application checks.
- Raw evidence retained where parsing may evolve.
- Metrics defined before conclusions are published.
- Missing data is unavailable, not zero.
- Recommendations are traceable to source rows.
- Reruns are safe by design, not by operator caution.
- A collector returning data is not completion; the unattended, validated, reportable loop is completion.
