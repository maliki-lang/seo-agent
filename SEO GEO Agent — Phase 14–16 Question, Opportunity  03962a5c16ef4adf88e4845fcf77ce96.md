# SEO/GEO Agent — Phase 14–16 Question, Opportunity & Experiment Intelligence Spec

<aside>
🎯

**Purpose:** continue after the existing Phase 10–13 Catalogue Selection Quality specification. Add mandatory but non-authoritative LLM assessment at final catalogue review and opportunity diagnosis, validated AI-question benchmarks, multi-signal top-ten opportunities and costed post-change experiments without rebuilding catalogue-selection work already assigned to Phases 10–13.

**Owner:** Maliki
**Approver:** Ting
**Status:** Continuation contract after Phase 13
**Primary outcome:** incremental non-branded organic clicks at the lowest defensible cost

</aside>

## 0. Specification sequence

Cursor must treat the specifications in this exact order:

```
SEO_GEO_DATA_TRACKING_IMPLEMENTATION_SPEC.md
  → Phases 0–5

SEO_GEO_CATALOGUE_PROVENANCE_IMPLEMENTATION_SPEC.md
  → Phases 6–9

SEO_GEO_CATALOGUE_SELECTION_QUALITY_IMPLEMENTATION_SPEC.md
  → Phases 10–13

This specification
  → Phases 14–16
```

This document supersedes the earlier Phase 10–13 numbering in its previous draft. It does not supersede the contents of the three predecessor specifications.

## 1. Instructions to Cursor

1. Inspect the live repository, branch, HEAD, working tree, migrations, database, CLI and tests before editing.
2. Read all three predecessor specifications before this document.
3. Determine whether Phases 0–13 are complete, partial or blocked from Git history, code, migrations and test evidence. Do not infer completion from the presence of a specification.
4. If Phase 10–13 work is incomplete, stop after the inspection report and identify the predecessor phase that must be completed first. Do not implement Phase 14 on an unstable or unapproved catalogue-selection foundation.
5. Preserve all working collectors, provenance, classification, keyword-family, target-page, Serper preselection, score-v2, portfolio, review, approval and activation behavior.
6. Do not reimplement functionality already owned by the Phase 10–13 specification.
7. Choose migration identifiers only after inspecting the live migration history. Use the next available additive migration number; do not assume a fixed number.
8. Do not let an LLM invent search volume, GSC/GA4 metrics, ranking, page existence, conversion, revenue, customer frequency, evidence references or approval status.
9. Use deterministic code for facts, calculations, validation, cost, quality gates and final ordering. Use an LLM only for bounded semantic interpretation with strict schemas and stored provenance.
10. Paid Serper and AI calls require explicit commands, dry-run cost estimates and enforcement of existing cost caps.
11. Require human approval before activating AI questions, approving a content action or publishing a content change.
12. Preserve historical catalogue versions and all Phase 0–13 behavior.
13. Add tests for every schema and behavior change.
14. After each phase, stop and report results before continuing.

### Required first response

Before editing, report:

- current branch, HEAD and working-tree status;
- detected completion status for Phases 0–5, 6–9 and 10–13;
- commits and migrations associated with each completed phase;
- baseline test commands and results;
- active and draft keyword catalogue versions;
- whether the Phase 10–13 v2 portfolio has been approved and activated;
- number of approved primary keyword benchmarks, alternates and unresolved exceptions;
- current AI-question generation, review and activation behavior;
- current opportunity-builder behavior and evidence sources;
- whether any content-change or experiment ledger exists;
- existing model-provider/client abstractions that can be reused;
- exact files and next migration expected to change;
- paid-call cost implications;
- blockers requiring Maliki or Ting.

Do not start Phase 14 coding until this report is complete.

## 2. Scope boundary with Phases 10–13

The predecessor Phase 10–13 specification already owns:

- keyword routing and search-intent classification;
- improved business-relevance rules;
- conservative keyword-family derivation;
- distinction between keyword family and content cluster;
- target-page actionability;
- protection versus opportunity scoring;
- lane-balanced Serper preselection;
- catalogue score v2;
- quota/cap portfolio selection;
- 55 proposals, alternates and exceptions;
- review import/export;
- approval and activation hardening.

Do not duplicate these features here.

This specification begins only after that selection-quality foundation is available. It owns:

1. auditable, mandatory and non-authoritative LLM assessment at final catalogue review;
2. evidence-backed and pilot-tested AI-question benchmarks;
3. action-centric opportunities from multiple evidence types;
4. top-ten opportunity portfolio construction;
5. costed experiments and post-change measurement;
6. learning from measured winners, losses and inconclusive results.

## 3. Terminology

```
candidate = one exact normalized source query with lineage
keyword family = conservative variants representing substantially the same intent
content cluster = broader topic or page-ownership grouping
keyword benchmark = an approved representative phrase measured repeatedly
AI-question benchmark = an approved natural question measured repeatedly
opportunity = an evidence-backed problem and proposed action
experiment = an approved action whose cost and outcome are measured
```

A benchmark is primarily a measurement anchor. It is not an instruction to repeat an exact phrase in content.

## 4. Objective

Extend the completed Phase 10–13 flow:

```
source-backed candidates
→ routing and intent
→ keyword families
→ target-page actionability
→ Serper preselection
→ score v2
→ coverage-constrained portfolio
→ review
→ approval
→ activation
```

with:

```
approved keyword portfolio
→ mandatory non-authoritative LLM assessment at final catalogue review
→ deterministic validation
→ human approval
→ evidence-backed question candidates
→ low-cost AI-question pilot
→ reviewed 20-question benchmark
→ multi-signal problem detection
→ LLM-assisted diagnosis where enabled
→ deterministic impact and cost calculation
→ deduplicated top-ten action portfolio
→ human approval
→ costed experiment
→ 14/28/56-day measurement
→ winner/loss/inconclusive learning
```

The final system must answer:

> Which evidence identified this problem, what exact action was approved, how much did it cost, how many incremental non-branded organic clicks did it produce, and should the activity be scaled or stopped?
> 

## 5. Phase 14 — Bounded LLM assistance and validated AI questions

### 5.1 LLM boundary

The LLM may:

- explain customer need and intent from supplied evidence;
- identify possible semantic duplicates not caught by deterministic family rules;
- recommend a target page from a supplied allowlist;
- explain business relevance using a supplied rubric;
- rewrite question candidates into natural customer language;
- identify expected answer elements;
- diagnose a page/SERP gap from supplied evidence;
- propose a structured action and assumptions.

The LLM must not create or modify measured facts. It must not invent:

- search volume;
- GSC clicks, impressions, CTR or position;
- GA4 sessions, purchases or revenue;
- Serper ranking or result composition;
- page URLs outside the supplied allowlist;
- customer/support frequency;
- source references;
- reviewer decisions;
- final priority scores.

### 5.2 Required LLM stages and failure behavior

LLM assessment is mandatory for:

```
final keyword-catalogue review
AI-question natural-language review
expected-answer-element drafting
opportunity diagnosis
```

It remains non-authoritative. Deterministic validators and human approval decide whether an item advances.

The core system must continue collecting, validating, reporting and measuring when the LLM is unavailable. Only the affected review item is blocked:

```
evidence_ready
→ awaiting_llm_assessment
→ llm_assessed
→ assessment_validated
→ awaiting_human_review
→ approved|rejected
```

An unavailable or invalid model response produces `awaiting_llm_assessment` or `llm_output_invalid`. It must never silently fall back to automatic approval.

### 5.3 Additive audit contract

Add a table or repository-equivalent structure using the next available migration.

#### `llm_assessments`

```
assessment_id             primary key
assessment_type           semantic_review|question_rewrite|answer_rubric|opportunity_diagnosis
subject_type              family|cluster|keyword_candidate|question_candidate|opportunity
subject_id
build_id                  nullable
prompt_version
provider
model
input_fingerprint
input_evidence_refs_json
redacted_input_json
output_json
validation_status         valid|invalid|rejected
validation_errors_json
cost_usd
latency_ms
created_at
```

Requirements:

- strict input and output schemas;
- deterministic input fingerprints;
- no secrets in stored inputs;
- private customer text redacted or excluded according to policy;
- invalid output cannot influence a benchmark or opportunity;
- retries must not duplicate logical assessments;
- prompt/model changes produce a new assessment version.

### 5.4 LLM assessment CLI

Add an explicit command equivalent to:

```bash
python -m data_sources.tracking.cli catalogue assess-llm \
  --build-id BUILD_ID \
  --assessment-type semantic_review \
  --scope reviewed_shortlist \
  --limit N \
  --dry-run \
  --json
```

The dry run must show:

```
subjects eligible
subjects blocked
estimated calls
estimated tokens where available
estimated maximum cost
provider/model
prompt version
```

No provider call is allowed during `--dry-run`.

### 5.5 AI-question source policy

Question candidates may come from:

```
gsc_question_query
approved_keyword_family
customer_support
site_search
product_review
people_also_ask
competitor_faq
business_nominated
synthetic_template
llm_rewrite
```

A template or LLM output is wording assistance, not proof of customer demand.

Questions produced only by the current deterministic templates must be labelled `synthetic_draft`. They cannot become core production questions without human validation.

When customer/support, site-search or review sources are unavailable, store the source as blocked/deferred. Do not claim customer validation.

### 5.6 Extend AI-question candidates

Add or repository-equivalent fields:

```
source_type
source_reference             nullable
source_evidence_refs_json
persona                      nullable
situation                    nullable
decision_to_make             nullable
constraints_json
expected_answer_elements_json
naturalness_status           pending|passed|failed
distinctness_status          pending|passed|failed
safety_status                pending|passed|failed
pilot_status                 not_run|passed|failed|inconclusive
pilot_results_json
llm_assessment_id            nullable
reviewed_by                  nullable
reviewed_at                  nullable
```

### 5.7 Question portfolio

Default target remains 20 approved questions, distributed across decision families:

| Family | Default target |
| --- | --- |
| Problem/situation | 5 |
| Product selection | 4 |
| Comparison/trade-off | 3 |
| Feature education | 3 |
| Work/lifestyle context | 3 |
| Brand-neutral recommendation | 2 |

Also enforce cluster coverage so one family, page or customer need cannot dominate.

Every proposed production question must have:

- natural customer wording;
- non-branded framing unless explicitly routed to a separate brand benchmark;
- one approved cluster or keyword-family relationship;
- one valid target page or reviewed content gap;
- source evidence or explicit `human_validated_hypothesis` status;
- expected answer elements;
- safe medical/YMYL framing;
- a reviewer decision and reason.

### 5.8 Question pilot

Before full three-repetition production measurement, support an explicit one-run pilot:

```bash
python -m data_sources.tracking.cli catalogue pilot-ai-questions \
  --question-build-id BUILD_ID \
  --engines chatgpt,perplexity \
  --repetitions 1 \
  --limit 30 \
  --json
```

The pilot evaluates:

- whether the engine interprets the question consistently;
- whether the answer concerns relevant footwear categories;
- whether results contain usable citations;
- which competitors recur;
- whether Sunnystep could credibly qualify;
- whether the target page covers expected answer elements;
- whether question wording creates safety or ambiguity problems.

Only reviewed pilot-passed questions may be activated. An override requires approver, timestamp and reason.

### 5.9 Phase 14 quality gates

| Check | Severity | Pass condition |
| --- | --- | --- |
| LLM schema validity | Critical | Only schema-valid outputs influence decisions |
| LLM fact immutability | Critical | No measured field is sourced from model output |
| LLM target allowlist | Critical | Model-selected pages come only from supplied valid targets |
| Question source claim | Critical | Source type and evidence are truthful |
| Question naturalness | Error | Every production question is reviewed as natural |
| Question distinctness | Error | No unresolved near-duplicate pair |
| Question safety | Critical | No unsafe or unsupported medical framing |
| Question target | Error | Every production question has a valid target or reviewed gap |
| Question pilot | Error | Every production question passed or has approved override |
| Question approval | Critical | Approver and timestamp exist before activation |
| Question count | Critical | Exactly configured production count is approved |

### 5.10 Phase 14 deliverables

- additive migration;
- LLM audit repository/model;
- versioned prompt and strict schemas;
- dry-run cost estimator;
- hallucinated-ID and target-page guards;
- evidence-backed question-source contract;
- question review artifact;
- question pilot command;
- activation gates;
- unit and integration tests.

Suggested commits:

```
feat: add auditable llm assessment contracts
feat: add evidence-backed ai question review workflow
feat: add low-cost ai question pilot and gates
```

## 6. Phase 15 — Multi-signal top-ten opportunity intelligence

### 6.1 Opportunity is an action, not a keyword

A keyword or question may contribute evidence, but it is not itself an opportunity.

Every opportunity must contain:

```
problem
source evidence
cluster or family
benchmark references
target page or technical asset
specific action
estimated impact
estimated cost or effort
confidence
metric to watch
measurement window
assumptions
```

### 6.2 Opportunity detectors

Generate action candidates from at least:

1. **CTR underperformance:** strong impressions/position with CTR below an approved position-relative expectation.
2. **Ranking improvement:** meaningful demand at positions 11–20 or another configured opportunity band.
3. **Page-one underperformance:** positions 4–10 with measurable upside.
4. **Cannibalization:** multiple Sunnystep pages competing for the same family/intent.
5. **Wrong target:** observed ranking page differs from approved owner page.
6. **Traffic-to-commerce gap:** strong organic sessions with weak product navigation or conversion, while respecting GA4 page-level attribution limitations.
7. **Existing-page expansion:** validated demand where a relevant page exists but coverage is weak.
8. **New-page gap:** validated demand with no credible existing page and explicit review.
9. **GEO evidence gap:** suitable Google visibility but weak stable AI mention/citation.
10. **Technical/manual investigation:** trusted evidence indicates indexability, freshness or measurement failure.

Do not create detectors for unavailable evidence. Mark them blocked until the source exists.

### 6.3 Opportunity schema

Extend `opportunities` additively or add a versioned v2 table with:

```
opportunity_id
opportunity_version
report_id
source_type
cluster_id                    nullable
family_id                     nullable
benchmark_ids_json
action_type
problem
supporting_evidence_json
source_row_references_json
target_page                   nullable
target_asset                  nullable
target_page_status
proposed_action
expected_incremental_clicks   nullable
expected_geo_gain             nullable
estimated_cost                nullable
cost_currency                 nullable
confidence_label
confidence_value
effort_label                  nullable
effort_value                  nullable
metric_to_watch
measurement_window_json
assumptions_json
llm_assessment_id             nullable
review_status
reviewed_by                   nullable
reviewed_at                   nullable
owner
status
created_at
updated_at
```

Allowed `action_type` values:

```
title_meta_rewrite
internal_linking
answer_section
content_expansion
product_mapping
content_consolidation
technical_fix
new_page
geo_evidence_upgrade
manual_investigation
```

### 6.4 Deterministic impact and priority

For suitable CTR/ranking opportunities:

```
estimated_click_gain = impressions × expected_ctr_at_target − current_clicks
```

Preserve the raw estimated click gain and assumptions. Do not use only a score normalized against the largest current candidate.

When monetary cost is available:

```
priority = estimated_incremental_clicks × confidence ÷ estimated_cost
```

Otherwise:

```
priority = estimated_incremental_clicks × confidence ÷ effort
```

SEO and GEO impact remain separate. GEO-only impact must not be translated into clicks without an evidence-backed conversion model.

### 6.5 Required LLM opportunity diagnosis

Every opportunity that advances to final review must receive a schema-valid LLM diagnosis. The diagnosis is mandatory but non-authoritative: it may explain the problem and propose actions, while deterministic code retains measured facts, calculates impact/cost and applies eligibility and ordering rules.

The LLM may receive only a bounded evidence packet:

```
approved cluster/family
representative and supporting benchmarks
GSC metrics
GA4 page metrics
Serper result evidence
approved target-page content or summary
competitor/SERP observations
earlier recorded page changes
```

Required output:

```json
{
  "problem": "...",
  "diagnosis": ["..."],
  "proposed_action_type": "content_expansion",
  "proposed_actions": ["..."],
  "evidence_refs": ["..."],
  "assumptions": ["..."],
  "risk_flags": [],
  "confidence": "medium"
}
```

Reject invented references, unsupported targets and unavailable facts. Deterministic code calculates impact, cost efficiency and order.

### 6.6 Top-ten portfolio construction

After detector-level eligibility and scoring, deduplicate overlapping actions.

Default constraints:

```
maximum two opportunities per cluster
maximum one opportunity per target page unless actions are demonstrably non-overlapping
minimum six existing-page opportunities when enough are eligible
maximum two new-page opportunities
merge one page/action supported by multiple keywords into one opportunity
GEO-only opportunities cannot displace materially stronger traffic opportunities
return fewer than ten when fewer than ten pass gates
```

The top ten is therefore a portfolio of actions, not the ten highest keyword scores.

### 6.7 CLI

Add or extend commands equivalent to:

```bash
python -m data_sources.tracking.cli opportunities build \
  --catalogue-version VERSION \
  --period-end YYYY-MM-DD \
  --limit 10 \
  --llm-assist \
  --json

python -m data_sources.tracking.cli opportunities export-review \
  --report-id REPORT_ID \
  --output opportunities.csv \
  --json

python -m data_sources.tracking.cli opportunities import-decisions \
  --report-id REPORT_ID \
  --input opportunities.csv \
  --json
```

Report candidates, eligible, rejected, merged, selected and blocked counts with reasons.

### 6.8 Review artifact

Include:

```
rank
problem
evidence
source type
cluster/family
benchmarks
target page
action type
proposed action
expected click gain
estimated cost
confidence
priority
assumptions
metric
measurement window
reviewer decision
reviewer reason
```

### 6.9 Phase 15 quality gates

| Check | Severity | Pass condition |
| --- | --- | --- |
| Evidence lineage | Critical | Every selected action has valid source references |
| Target validity | Critical | Target page/asset exists or reviewed new-page gap is recorded |
| Actionability | Critical | Problem, action, metric and window are complete |
| Duplicate action | Error | Overlapping page/family actions are merged or reviewed |
| Raw impact preservation | Critical | Raw estimated gain and assumptions are stored |
| Priority reproducibility | Critical | Priority recalculates from stored inputs |
| Portfolio constraints | Error | Top-ten caps and coverage pass or override exists |
| LLM diagnosis validity | Critical | Model output is schema-valid and evidence-bounded |
| Human approval | Critical | No opportunity enters implementation without approval |

### 6.10 Phase 15 deliverables

- multi-signal detector modules;
- opportunity schema extension/versioning;
- deterministic impact and cost-efficiency calculations;
- mandatory non-authoritative LLM diagnosis before final opportunity review;
- action deduplication;
- constrained top-ten portfolio;
- review export/import;
- Lark projection updates without overwriting human-owned fields;
- unit and integration tests.

Suggested commits:

```
feat: add multi-signal opportunity detectors
feat: add evidence-bounded opportunity diagnosis
feat: generate deduplicated top-ten action portfolio
```

## 7. Phase 16 — Costed experiments and post-change learning

### 7.1 Experiment principle

An approved opportunity becomes a new experiment record. Never overwrite the original opportunity evidence.

The experiment exists to answer:

> Did this exact change produce incremental non-branded organic traffic at an acceptable cost?
> 

### 7.2 `content_experiments`

```
experiment_id
opportunity_id
target_page
benchmark_ids_json
action_type
hypothesis
approved_by
approved_at
implementation_started_at
published_at
baseline_start
baseline_end
measurement_schedule_json
estimated_cost
actual_cost
cost_currency
expected_incremental_clicks
control_page_ids_json
evidence_level              before_after|site_trend_adjusted|matched_page_control|controlled_experiment
status                      proposed|approved|implementing|published|measuring|completed|cancelled
created_at
updated_at
```

### 7.3 `experiment_measurements`

```
measurement_id
experiment_id
measurement_date
days_since_publish
window_start
window_end
actual_clicks
expected_clicks_without_change
estimated_incremental_clicks
nonbranded_clicks
organic_sessions
engaged_sessions
product_clicks              nullable
purchases
revenue
cost_per_incremental_click  nullable
confidence
outcome                     win|loss|inconclusive|tracking_failure
calculation_version
source_run_ids_json
created_at
UNIQUE(experiment_id, measurement_date, calculation_version)
```

### 7.4 Measurement schedule

Support configurable 14-, 28- and 56-day checkpoints with GSC completeness lag. Do not compare incomplete windows.

Progressive evidence methods:

1. `before_after` — initial MVP comparison;
2. `site_trend_adjusted` — adjust for overall organic movement;
3. `matched_page_control` — compare with similar unchanged pages;
4. `controlled_experiment` — use when sufficient comparable pages exist.

Do not label correlation as confirmed causality.

### 7.5 Outcome rules

Make thresholds configurable. Example:

```yaml
experiments:
  checkpoints_days: [14, 28, 56]
  minimum_complete_days: 28
  win_min_adjusted_click_lift_pct: 15
  maximum_cost_per_incremental_click: null
  allow_one_inconclusive_extension: true
```

Required outcomes:

```
win
loss
inconclusive
tracking_failure
```

A zero or negative incremental-click denominator must produce a null cost-per-incremental-click and an explicit loss/inconclusive result; never divide by zero.

### 7.6 Learning feedback

Aggregate completed experiments by:

```
action_type
page_type
cluster or lane
cost band
confidence at approval
result
```

Use measured results to produce recommendations such as:

```
scale
continue selectively
revise hypothesis
stop
insufficient evidence
```

Do not automatically change production scoring weights from a small sample. Weight changes require a versioned proposal, minimum sample policy and human approval.

### 7.7 CLI

Add commands equivalent to:

```bash
python -m data_sources.tracking.cli experiments create \
  --opportunity-id OPPORTUNITY_ID \
  --approved-by "Ting" \
  --json

python -m data_sources.tracking.cli experiments publish \
  --experiment-id EXPERIMENT_ID \
  --published-at ISO_TIMESTAMP \
  --actual-cost AMOUNT \
  --json

python -m data_sources.tracking.cli experiments measure \
  --experiment-id EXPERIMENT_ID \
  --as-of-date YYYY-MM-DD \
  --json

python -m data_sources.tracking.cli experiments report \
  --period-end YYYY-MM-DD \
  --json
```

Creation must fail when the opportunity lacks approval. Measurement must fail closed when required data is incomplete.

### 7.8 Phase 16 quality gates

| Check | Severity | Pass condition |
| --- | --- | --- |
| Opportunity approval | Critical | Experiment references an approved opportunity |
| Baseline completeness | Critical | Valid complete baseline exists before publication |
| Publication record | Critical | Exact page, action and timestamp are recorded |
| Cost recording | Error | Estimated and actual cost policy is satisfied |
| Window completeness | Critical | Measurement window respects source lag |
| Calculation reproducibility | Critical | Result recalculates from stored source runs |
| Control integrity | Error | Controls are unchanged and valid when used |
| Outcome validity | Critical | Win/loss/inconclusive/tracking failure follows versioned rules |
| Idempotency | Critical | Reruns create no duplicate logical measurements |
| Learning governance | Critical | Scoring weights do not auto-change without approval |

### 7.9 Phase 16 deliverables

- experiment and measurement migrations;
- experiment lifecycle service;
- scheduled checkpoints;
- site-trend and optional matched-control adjustment;
- cost-per-incremental-click calculation;
- outcome classification;
- learning summary;
- weekly/Lark integration;
- recovery and rollback documentation;
- unit and integration tests.

Suggested commits:

```
feat: add costed seo experiment lifecycle
feat: measure incremental organic traffic outcomes
feat: add governed experiment learning summaries
```

## 8. Testing requirements

### Unit tests

Cover:

- LLM input/output schema validation;
- rejection of invented IDs, pages, metrics and evidence references;
- assessment idempotency and prompt versioning;
- question source-state classification;
- naturalness, distinctness and safety rules;
- question pilot transitions;
- every opportunity detector;
- raw click-gain calculation;
- monetary priority and effort fallback;
- opportunity deduplication and portfolio constraints;
- experiment lifecycle transitions;
- complete-window calculation;
- incremental-click and cost-per-click calculations;
- zero/negative denominator handling;
- outcome classification;
- learning-weight governance.

### Integration tests

Using a temporary database and fake external boundaries:

1. migrate from the latest Phase 13 schema;
2. load an approved v2 keyword catalogue;
3. generate source-backed and synthetic question candidates;
4. record a fake schema-valid LLM rewrite and reject a hallucinated response;
5. run a fake one-repetition question pilot;
6. approve and activate 20 questions;
7. seed GSC, GA4, Serper and AI visibility evidence;
8. produce opportunities from at least three detector types;
9. merge duplicate actions for one page/family;
10. construct a constrained top-ten or return fewer with reasons;
11. import a reviewer approval;
12. create one experiment;
13. record publication and actual cost;
14. run 14-, 28- and 56-day fixture measurements;
15. classify win, loss, inconclusive and tracking-failure fixtures;
16. rerun and prove logical idempotency;
17. verify historical Phase 0–13 data remains queryable.

### Required verification

Use repository-standard commands discovered during inspection. At minimum:

```bash
python -m compileall -q -f .
python scripts/secret_scan.py
python -m pytest -q tests
```

Record exact pass/fail counts and environmental blockers. Fixtures prove behavior only; they do not prove live-source readiness.

## 9. Gate demonstration

Demonstrate in this order:

1. Phase 0–13 completion or blocker status.
2. Historical v1 and v2 keyword builds remain queryable.
3. LLM assessment dry-run cost estimate.
4. One valid assessment with prompt/model/input/output provenance.
5. Rejection of a deliberately hallucinated page, metric or evidence ID.
6. Question candidates with source type clearly distinguishing real evidence from synthetic drafts.
7. A one-repetition question pilot.
8. Twenty approved questions with diversity, target and safety checks.
9. Opportunity candidates from at least three signal types.
10. One action supported by multiple benchmarks merged correctly.
11. A top-ten action portfolio or a smaller valid set with exclusion reasons.
12. One opportunity with raw click gain, cost/effort and reproducible priority.
13. Explicit human approval before experiment creation.
14. One experiment with baseline, publication timestamp and actual cost.
15. One complete post-change measurement and cost per incremental click.
16. One loss or inconclusive case proving the system does not call every increase a success.
17. Same-command reruns with zero duplicate logical records.
18. Updated Lark/weekly view preserving human-owned fields.
19. Tests and secret scan pass.

## 10. Definition of done

- [ ]  Cursor verified rather than assumed completion of Phases 0–13.
- [ ]  No Phase 10–13 catalogue-selection feature was unnecessarily rebuilt.
- [ ]  LLM assessment is mandatory at final catalogue review and opportunity diagnosis, but remains non-authoritative, bounded, schema-validated and auditable.
- [ ]  If a required LLM assessment is unavailable or invalid, the affected item is blocked from approval without stopping deterministic collection, reporting or measurement.
- [ ]  LLM outputs cannot overwrite measured facts or approval state.
- [ ]  Template-only questions remain synthetic drafts until validated.
- [ ]  Production AI questions have truthful source status, target, expected answer, pilot result and reviewer approval.
- [ ]  The opportunity queue is action-centric and uses multiple evidence types.
- [ ]  Top-ten selection deduplicates pages/families and enforces portfolio constraints.
- [ ]  Raw expected click gain is preserved.
- [ ]  Monetary cost is used when available; effort remains a fallback.
- [ ]  No opportunity enters implementation without human approval.
- [ ]  Approved actions become immutable-linked experiments.
- [ ]  Measurement respects GSC completeness and records source-run lineage.
- [ ]  Actual incremental clicks and cost per incremental click are calculated reproducibly.
- [ ]  Results distinguish win, loss, inconclusive and tracking failure.
- [ ]  Learning summaries cannot silently change production weights.
- [ ]  Historical Phase 0–13 behavior and data remain compatible.
- [ ]  All new migrations, tests and security checks pass.

## 11. Cursor phase completion format

After each phase return:

```
Phase:
Status: complete | partial | blocked
Starting commit:
Ending commit:
Predecessor phase status:
Changes:
Files changed:
Migrations:
Tests run:
Test results:
Data/API evidence:
LLM calls and cost:
Question counts and pilot evidence:
Opportunity counts and evidence:
Experiment evidence:
Security/privacy notes:
Known limitations/blockers:
Human decision required:
Next phase:
```

At final completion include:

- complete phase-to-commit map from Phase 14 onward;
- migration summary;
- CLI command list;
- test and secret-scan results;
- live versus fixture evidence status;
- AI-question proposed/approved/rejected counts;
- opportunity candidate/merged/selected counts;
- experiment and measurement status;
- exact remaining blockers;
- rollback instructions.

## 12. Final principle

The system is not successful because it outputs 55 keywords, 20 questions or ten recommendations.

It is successful only when:

```
each benchmark represents defensible demand
→ each opportunity describes a specific evidence-backed action
→ each approved action becomes a costed experiment
→ incremental non-branded organic traffic is measured
→ the team scales winners and stops ineffective work
```