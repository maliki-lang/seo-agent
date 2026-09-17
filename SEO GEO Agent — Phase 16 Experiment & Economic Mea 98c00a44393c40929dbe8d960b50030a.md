# SEO/GEO Agent — Phase 16 Experiment & Economic Measurement Implementation Spec

<aside>
🎯

**Decision:** Keep the current tracking and opportunity architecture. Do not restart it. The remaining gap is the closed business-learning loop: approved opportunity → implemented change → costed experiment → adjusted incremental traffic → outcome → scoring feedback.

</aside>

## Executive comparison

The implementation is now materially closer to the recommended solution than the earlier review suggested.

| Capability | Current implementation | Alignment | Remaining work |
| --- | --- | --- | --- |
| First-party collection | GSC and GA4 collectors with normalized storage | Strong | Complete production cadence and freshness checks |
| SERP and GEO evidence | Serper and AI visibility collectors exist | Partial | Production evidence remains sparse |
| Baselines | Immutable 28-day baseline service exists | Code-complete | No production baseline is locked |
| Catalogue governance | Versioning, provenance, LLM semantics, target-page states, review and activation exist | Strong | Evidence build is not approved/activated; cluster output is incomplete |
| Opportunity intelligence | Phase 15 creates action-centric, multi-signal, constrained portfolios | Strong | Thresholds and false-positive controls need tightening |
| Economic prioritization | Estimated clicks, estimated cost field and cost-aware formula exist | Partial | Costs are null; actual cost and actual cost/click are absent |
| Human approval | Opportunity export/import workflow exists | Strong | No approved opportunity has become an experiment |
| Experiment ledger | Not implemented | Missing | Add Phase 16 schema and services |
| Incremental measurement | Not implemented per change | Missing | Add counterfactual, checkpoints and outcome classification |
| Learning feedback | Not implemented | Missing | Feed measured outcomes into action-type priors |
| Weekly operations | Weekly report exists | Partial | It still invokes legacy v1 opportunities, not Phase 15 v2 |

**Overall assessment:** the system is a strong evidence and recommendation platform, but not yet an economic optimization agent. Phase 15 closed much of the recommendation gap. Phase 16 must prove what happened after a recommendation was implemented.

## Verified implementation snapshot — 17 September 2026

| Dataset/state | Observed |
| --- | --- |
| GSC daily rows | 43,406 |
| GA4 daily rows | 52,250 |
| Serper daily rows | 1 |
| AI answer runs | 0 |
| Quality-check rows | 62 |
| Keyword candidates | 5,290 |
| LLM assessments | 4,403 |
| Active provisional keyword catalogue | 55 |
| Active provisional AI-question catalogue | 20 |
| Phase 15 opportunities | 10 |
| Locked baseline rows | 0 |
| Weekly reports | 0 |
| Alerts | 0 |
| Experiment tables | 0 |

The latest evidence build is in review, with 47 selected keyword candidates. It has no derived catalogue clusters in the database, and 4,050 candidates remain in `homepage_unresolved`. The current Phase 15 portfolio passes its implemented gates, but all ten opportunities await human review.

## Material quality findings

1. **The architecture is ahead of live proof.**
The code contains collectors, governance, semantic review, opportunity detectors and approval workflows, but production outputs have not completed the baseline → approved action → measured outcome chain.
2. **The portfolio currently forces weak tail items.**
Six of the ten generated opportunities are cannibalization actions; several have estimated click gains at or near zero. “Up to ten” must replace “fill ten.”
3. **Homepage handling is too permissive.**
The highest-ranked opportunity targets `http://sunnystep.com/`. Homepage targets should be blocked unless a reviewer explicitly approves them, and canonical HTTPS URLs should be required.
4. **Cost-aware ranking is present but inactive.**
`estimated_cost` exists, but all generated values are null, so the scorer always falls back to S/M/L effort.
5. **Weekly reporting is on the legacy path.**
`WeeklyReportService` and `TrackingRunner` still use the v1 `OpportunityBuilder` rather than the Phase 15 action-centric portfolio.
6. **LLM diagnosis is stored but not operationally surfaced.**
Phase 15 attaches an assessment ID, but the review artifact should expose the diagnosis, recommended actions, assumptions and risk flags as structured fields.
7. **A Phase 15 test contains a vacuous assertion.**
The expression ending in `or True` cannot fail and must be replaced with a real invariant.
8. **Repository syntax is valid.**
`compileall` passed for the tracking package and tests. The full test suite could not be executed in the review environment because `pytest` was unavailable.

---

## Implementation objective

Build Phase 16 so that every approved SEO action can be executed as a controlled, costed experiment and evaluated at 14, 28 and 56 days.

The system must answer:

> How many additional non-branded organic clicks did this exact change likely generate, what did it cost, and should this action type be scaled, revised or stopped?
> 

## North star and guardrails

### Primary outcome

$$
Adjusted incremental non-branded organic clicks
$$

### Efficiency outcome

$$
Actual cost per incremental non-branded organic click = actual intervention cost / adjusted incremental clicks
$$

### Business guardrails

- Organic sessions
- Engaged organic sessions
- Product-detail clicks, when available
- Purchases
- Organic revenue
- Data quality and tracking completeness

### Diagnostic metrics

- Impressions
- CTR
- Average position
- Ranking URL
- Site-wide non-brand trend
- Control-page trend
- AI mention and citation rates

## Scope

### In scope

- Tighten Phase 15 opportunity gates.
- Add experiment, change, cost and measurement persistence.
- Create approved-opportunity-to-experiment workflow.
- Record publication evidence and exact intervention type.
- Measure page/query outcomes at 14, 28 and 56 days.
- Adjust for site-wide or control-page trends.
- Calculate estimated and actual cost per incremental click.
- Classify winner, loss, inconclusive or tracking failure.
- Integrate Phase 15 opportunities and Phase 16 outcomes into weekly reporting and Lark.
- Feed outcomes into action-type historical priors without allowing autonomous publishing.

### Out of scope

- Autonomous content publication.
- New data collectors unless required to calculate an existing guardrail.
- Full causal inference or Bayesian experimentation framework.
- Treating GEO mention rate as traffic without observed referral evidence.
- Replacing human approval.
- Large-scale content generation.

---

## Required workflow

```
Phase 15 opportunity
→ human review
→ approved opportunity
→ create experiment
→ capture pre-change evidence
→ implement and publish
→ record exact change and cost
→ measure at 14 / 28 / 56 days
→ estimate counterfactual traffic
→ calculate incremental clicks and cost/click
→ classify outcome
→ update action-type learning prior
→ recommend scale / revise / stop
```

## State machines

### Opportunity

```
awaiting_human_review
→ approved | rejected | deferred
→ converted_to_experiment
```

### Experiment

```
draft
→ approved
→ implementing
→ published
→ measuring
→ winner | likely_winner | inconclusive | likely_loss | tracking_failure
→ closed
```

Rules:

- Only an opportunity with `review_status=approved`, reviewer identity and review timestamp may create an experiment.
- One opportunity may create only one active experiment unless the prior experiment is closed and the new experiment declares `supersedes_experiment_id`.
- Publication and measurement are idempotent.
- An experiment cannot become a winner merely because observed clicks rose.

---

## Database migration

Create `data_sources/tracking/migrations/013_experiments.sql`. Do not modify historical migrations.

### Table: `seo_experiments`

```sql
CREATE TABLE seo_experiments (
    experiment_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    supersedes_experiment_id TEXT,
    status TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_page TEXT NOT NULL,
    target_queries_json TEXT NOT NULL DEFAULT '[]',
    control_pages_json TEXT NOT NULL DEFAULT '[]',
    primary_metric TEXT NOT NULL,
    guardrail_metrics_json TEXT NOT NULL DEFAULT '[]',
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    owner TEXT NOT NULL,
    planned_publish_at TEXT,
    published_at TEXT,
    baseline_start TEXT,
    baseline_end TEXT,
    estimated_incremental_clicks REAL,
    estimated_cost REAL,
    actual_cost REAL NOT NULL DEFAULT 0,
    cost_currency TEXT NOT NULL,
    counterfactual_method TEXT NOT NULL DEFAULT 'sitewide_adjusted',
    content_before_hash TEXT,
    content_after_hash TEXT,
    implementation_reference TEXT,
    catalogue_version TEXT,
    source_report_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(opportunity_id, status) ON CONFLICT IGNORE
);
```

Do not rely on the proposed unique constraint alone for active-experiment enforcement. Add service-level validation covering all non-terminal statuses.

### Table: `experiment_changes`

```sql
CREATE TABLE experiment_changes (
    change_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    target_asset TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    evidence_reference TEXT,
    implemented_by TEXT,
    implemented_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Each experiment must use one primary intervention type. Secondary changes may be recorded, but if several independent changes are bundled, set `isolation_quality=low` in the experiment metadata and prevent a “confirmed winner” classification.

### Table: `experiment_costs`

```sql
CREATE TABLE experiment_costs (
    cost_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    cost_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_cost REAL NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    incurred_at TEXT NOT NULL,
    evidence_reference TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
```

Allowed initial cost types:

```
model_api
engineering
writing
editing
review
seo_tool
contractor
other
```

### Table: `experiment_measurements`

```sql
CREATE TABLE experiment_measurements (
    measurement_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    checkpoint_days INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    baseline_clicks REAL,
    observed_clicks REAL,
    expected_clicks_without_change REAL,
    adjusted_incremental_clicks REAL,
    sitewide_trend_factor REAL,
    control_trend_factor REAL,
    nonbranded_impressions REAL,
    ctr REAL,
    average_position REAL,
    organic_sessions REAL,
    engaged_sessions REAL,
    purchases REAL,
    revenue REAL,
    ai_referral_sessions REAL,
    confidence_label TEXT NOT NULL,
    outcome TEXT NOT NULL,
    adjustment_method TEXT NOT NULL,
    source_references_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    quality_status TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    UNIQUE(experiment_id, checkpoint_days)
);
```

### Table: `action_type_priors`

```sql
CREATE TABLE action_type_priors (
    action_type TEXT PRIMARY KEY,
    completed_experiments INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    inconclusive INTEGER NOT NULL DEFAULT 0,
    median_incremental_clicks REAL,
    median_cost_per_incremental_click REAL,
    empirical_confidence REAL,
    updated_at TEXT NOT NULL
);
```

Priors are advisory. They may adjust future confidence only after at least five completed experiments of the same action type.

---

## Python package layout

Create:

```
data_sources/tracking/experiments/
├── __init__.py
├── service.py
├── costs.py
├── measurement.py
├── counterfactual.py
├── outcomes.py
├── learning.py
└── gates.py
```

Extend:

```
data_sources/tracking/storage.py
data_sources/tracking/enums.py
data_sources/tracking/cli.py
data_sources/tracking/runner.py
data_sources/tracking/reports/weekly.py
data_sources/tracking/sinks/lark_base.py
data_sources/tracking/opportunities/gates.py
data_sources/tracking/opportunities/portfolio.py
data_sources/tracking/opportunities/review.py
config/tracking.example.yaml
docs/tracking-runbook.md
docs/metric-definitions.md
```

## Service contracts

### `create_experiment_from_opportunity()`

Inputs:

```python
opportunity_id: str
approved_by: str
owner: str
hypothesis: str | None
control_pages: list[str] | None
planned_publish_at: date | None
cost_currency: str | None
```

Required behavior:

- Load and validate the opportunity.
- Require `review_status=approved` and matching reviewer fields.
- Reject zero/negative expected-click opportunities unless action type is an explicitly permitted measurement, technical or commerce diagnostic.
- Reject non-canonical, non-HTTPS and unresolved homepage targets unless explicitly approved with a reason.
- Copy evidence lineage, benchmarks, action type, target page, catalogue version and expected impact.
- Derive baseline dates as the 28 complete days immediately before publication; if publication date is unknown, keep dates null until recorded.
- Return the created experiment and gate results.

### `record_publication()`

Inputs:

```python
experiment_id: str
published_at: datetime
content_before_hash: str
content_after_hash: str
implementation_reference: str
changes: list[ExperimentChange]
```

Required behavior:

- Require approved or implementing state.
- Require different before/after hashes.
- Require at least one change row.
- Set baseline window and checkpoints.
- Move state to `published`.
- Do not publish content; only record evidence of an externally completed change.

### `add_experiment_cost()`

- Recalculate cost from `quantity × unit_cost`; never trust a caller-supplied amount.
- Recalculate `seo_experiments.actual_cost` transactionally.
- Reject mixed currencies for the same experiment unless a conversion record and rate date are provided.
- Default currency from configuration, not a hardcoded value.

### `measure_experiment()`

Inputs:

```python
experiment_id: str
checkpoint_days: Literal[14, 28, 56]
as_of_date: date
force: bool = False
```

Required behavior:

- Require enough complete GSC and GA4 dates.
- Use only non-branded GSC rows for the target page and target query family.
- Preserve page-level and query-level values separately in source references.
- Exclude incomplete recent GSC days according to configured lag.
- Upsert by `(experiment_id, checkpoint_days)`.
- Never overwrite a completed measurement unless `force` is explicit and an audit reason is supplied.

---

## Counterfactual method — MVP

Use a transparent hierarchy rather than claiming precise causality.

### Method A: matched controls

Use when two or more reviewer-approved control pages have sufficient baseline and post-period data.

```
control_trend_factor = median(control_post_clicks / control_baseline_clicks)
expected_target_clicks = target_baseline_clicks × control_trend_factor
```

### Method B: site-wide adjustment

Use when controls are unavailable.

Calculate non-brand clicks across the site while excluding the target page.

```
sitewide_trend_factor = comparable_site_post_clicks / comparable_site_baseline_clicks
expected_target_clicks = target_baseline_clicks × sitewide_trend_factor
```

### Method C: before/after only

Use only when neither controls nor site-wide adjustment is valid. Confidence cannot exceed `low`.

```
expected_target_clicks = target_baseline_clicks
```

### Incremental result

```
adjusted_incremental_clicks = observed_target_clicks - expected_target_clicks
```

Rules:

- Do not divide by a zero baseline.
- A zero or sparse baseline produces `inconclusive`, not a large percentage gain.
- Preserve negative incremental clicks.
- Store the chosen method, factors and assumptions.
- Do not convert GEO mention gains into clicks. Use observed AI-referral sessions only.

## Outcome classification

Initial configurable rules:

| Outcome | Rule |
| --- | --- |
| `winner` | 28- or 56-day adjusted clicks ≥ +15%, incremental clicks > 0, data quality passes, and cost/click is below threshold |
| `likely_winner` | Positive adjusted clicks with medium confidence, but one economic or control criterion is incomplete |
| `inconclusive` | Sparse data, mixed signals, isolation quality low, or confidence low |
| `likely_loss` | Adjusted incremental clicks < 0 at 28 or 56 days with adequate data |
| `tracking_failure` | Missing source windows, failed quality gate, URL mismatch or broken tracking |

A 14-day result is provisional and cannot finalize a winner. A final winner/loss requires a 28- or 56-day checkpoint.

## Cost metrics

Calculate and store:

```
estimated_cost_per_incremental_click = estimated_cost / expected_incremental_clicks
actual_cost_per_incremental_click = actual_cost / adjusted_incremental_clicks
```

Rules:

- When incremental clicks are zero or negative, actual cost/click is null and the outcome explains why.
- Preserve the original cost currency.
- Add configuration:

```yaml
tracking:
  economics:
    default_currency: SGD
    max_cost_per_incremental_click: null
    minimum_incremental_clicks: 1
    minimum_priority_score: 0.01
    minimum_experiments_for_learning: 5
```

Do not assume an economic threshold until the owner supplies one.

---

## Phase 15 hardening required before Phase 16 activation

### Opportunity portfolio gates

Update `opportunities/portfolio.py` and `opportunities/gates.py`:

- Return fewer than ten items when fewer than ten pass absolute eligibility gates.
- Require one of:
    - expected incremental clicks ≥ configured minimum;
    - approved technical/commerce diagnostic;
    - GEO opportunity with observable website citation/referral metric.
- Block `http://` targets.
- Canonicalize query strings and product-variant URLs before page deduplication.
- Block homepage targets unless `target_page_status` is explicitly human-approved.
- Require `priority_score ≥ minimum_priority_score`.
- Add a concentration warning when one source type exceeds 40% of the selected portfolio.
- Require cannibalization evidence from at least two distinct Sunnystep URLs for the same query/family and a minimum impression threshold.
- Do not create click-impact estimates for technical or commerce diagnostics.

### LLM diagnosis

- Join validated `llm_assessments` into the review export.
- Add columns for diagnosis, recommended action list, evidence refs, assumptions, risk flags and LLM confidence.
- Keep deterministic metrics authoritative.
- Never allow LLM output to set impressions, rankings, traffic gain, cost or approval status.

### Fix weak test

Replace the Phase 15 assertion containing `or True` with explicit rules by category and action type.

---

## CLI contract

Add commands:

```bash
python -m data_sources.tracking.cli experiments create \
  --opportunity-id OPP_ID \
  --approved-by Ting \
  --owner Maliki \
  --json

python -m data_sources.tracking.cli experiments record-publication \
  --experiment-id EXP_ID \
  --published-at 2026-09-20T09:00:00+08:00 \
  --before-hash HASH \
  --after-hash HASH \
  --implementation-reference URL_OR_COMMIT \
  --changes-json changes.json \
  --json

python -m data_sources.tracking.cli experiments add-cost \
  --experiment-id EXP_ID \
  --cost-type review \
  --quantity 1.5 \
  --unit-cost 45 \
  --currency SGD \
  --json

python -m data_sources.tracking.cli experiments measure \
  --experiment-id EXP_ID \
  --checkpoint 28 \
  --as-of-date 2026-10-18 \
  --json

python -m data_sources.tracking.cli experiments list --status measuring --json
python -m data_sources.tracking.cli experiments show --experiment-id EXP_ID --json
```

All commands must have deterministic JSON output and non-zero exit codes on failed critical gates.

## Weekly report integration

Replace legacy v1 opportunity generation in `reports/weekly.py` and `runner.py` with Phase 15 v2.

The weekly report must include:

1. Primary site-wide non-brand performance.
2. Opportunity portfolio with gate status.
3. Experiments due for 14-, 28- and 56-day measurement.
4. Newly classified winners, losses, inconclusive results and tracking failures.
5. Actual intervention spend and cost per incremental click.
6. Blocked sources such as unavailable GEO evidence.
7. A clear distinction between observed movement and attributed experiment impact.

Do not silently generate v1 opportunities as a fallback. If no approved evidence catalogue exists, report a blocked state.

## Lark operational views

Extend the sink with idempotent upserts for:

- Experiment register
- Experiment costs
- Experiment measurements
- Outcome summary

Use stable external keys:

```
experiment_id
cost_id
experiment_id:checkpoint_days
experiment_id:latest_outcome
```

Lark is an operational view, not the source of truth. SQLite remains authoritative.

---

## Test plan

Create:

```
tests/tracking/unit/test_experiment_service.py
tests/tracking/unit/test_experiment_costs.py
tests/tracking/unit/test_counterfactual.py
tests/tracking/unit/test_experiment_outcomes.py
tests/tracking/integration/test_phase16_closed_loop.py
tests/tracking/integration/test_phase16_weekly_lark.py
```

Required cases:

### Approval and state

- Unapproved opportunity cannot create an experiment.
- Approved opportunity creates exactly one active experiment.
- Duplicate create is idempotent or rejected without duplicate rows.
- Invalid state transitions fail.
- Publication requires changed hashes and at least one change.

### Costs

- Amount is recomputed from quantity and unit cost.
- Actual experiment cost equals the sum of cost rows.
- Mixed currencies fail without conversion metadata.
- Zero incremental clicks never cause division by zero.

### Measurement

- Page/query non-brand filters are correct.
- Branded traffic is excluded.
- Target page is excluded from site-wide control.
- Matched control median is reproducible.
- Sparse/zero baseline becomes inconclusive.
- Negative outcomes are preserved.
- 14-, 28- and 56-day upserts are idempotent.
- GSC lag prevents premature measurement.

### Opportunity hardening

- Portfolio may return fewer than ten.
- Zero-impact cannibalization items do not fill the queue.
- HTTP, variant-URL and unapproved homepage targets are rejected or canonicalized.
- Cannibalization requires two real URLs.
- Source concentration warning fires above 40%.

### Closed loop

Seed GSC and GA4 before/after rows for one approved opportunity, create an experiment, record costs and publication, measure day 28, classify the result, update the prior and render the weekly report. Assert all lineage references and calculations.

### Existing test correction

Remove every vacuous assertion and ensure all Phase 15 invariants can fail when broken.

## Acceptance criteria

Phase 16 is complete only when all are true:

- [ ]  Migration 013 applies cleanly to a copy of the current database.
- [ ]  An approved Phase 15 opportunity can create exactly one active experiment.
- [ ]  Exact changes, before/after hashes and costs are persisted.
- [ ]  Day-14, day-28 and day-56 measurements are idempotent.
- [ ]  Non-brand clicks are separated from branded clicks.
- [ ]  Counterfactual method and assumptions are stored and reproducible.
- [ ]  Actual cost per incremental click is calculated safely.
- [ ]  Outcomes distinguish winner, likely winner, inconclusive, likely loss and tracking failure.
- [ ]  Weekly reporting uses v2 opportunities and includes experiments.
- [ ]  Lark writes are idempotent and SQLite remains authoritative.
- [ ]  Portfolio can contain fewer than ten actions.
- [ ]  Homepage, HTTP and product-variant target issues are gated.
- [ ]  Full test suite passes in the supported Python environment.
- [ ]  Existing migration, catalogue and collector tests remain green.
- [ ]  No secrets or raw customer content are written to reports or logs.

---

## Cursor execution plan

### Pull request 1 — Phase 15 hardening

- Add economic threshold configuration.
- Canonicalize and validate targets.
- Tighten cannibalization and zero-impact gates.
- Permit portfolios smaller than ten.
- Surface LLM diagnosis in review artifacts.
- Fix weak tests.

### Pull request 2 — Phase 16 persistence and state machine

- Add migration 013.
- Add storage methods and enums.
- Implement experiment creation, publication recording and costs.
- Add unit tests.

### Pull request 3 — Measurement and outcomes

- Implement query/page metric extraction.
- Implement matched-control and site-wide counterfactuals.
- Implement checkpoints and outcome classification.
- Add end-to-end integration tests.

### Pull request 4 — Weekly and Lark integration

- Replace v1 opportunity use.
- Add experiment sections and operational views.
- Add idempotency tests and runbook documentation.

### Pull request 5 — Production proof

- Run a production-quality daily collection.
- Approve and activate an evidence catalogue.
- Lock the first 28-day baseline.
- Approve one low-cost existing-page opportunity.
- Record one real publication and all intervention costs.
- Measure it at day 14, 28 and 56.
- Publish the first economic outcome.

## Cursor guardrails

```
Do not rewrite the existing tracking layer.
Do not modify old migrations.
Do not invent traffic, rank, cost, conversion or causal evidence.
Do not let an LLM approve an opportunity or experiment.
Do not auto-publish content.
Do not force ten opportunities.
Do not translate GEO visibility into clicks without referral evidence.
Keep all writes idempotent and all calculations reproducible.
Preserve raw evidence and source references.
Make each pull request independently testable and reversible.
```

<aside>
✅

**Definition of success:** one approved existing-page change reaches day 28 with complete lineage, recorded total cost, an adjusted estimate of incremental non-branded clicks, a defensible outcome classification and a scale/revise/stop decision.

</aside>