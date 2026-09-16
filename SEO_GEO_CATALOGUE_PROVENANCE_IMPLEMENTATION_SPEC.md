# SEO/GEO Agent — Phase 6 Catalogue Provenance & Operational Hardening Spec

<aside>
🎯

**Purpose:** continue from the Phase 5 implementation and replace the provisional keyword/question catalogues with GSC-derived, GA4-prioritised, Serper-validated and human-approved production benchmarks.

**Owner:** Maliki
**Approver:** Ting
**Implementation status:** Post-Phase 5 continuation
**Semrush status:** Deferred pending permission

</aside>

## 0. Instructions to Cursor

Treat this document as a continuation contract, not a request to rebuild Phases 0–5.

1. Inspect the current repository, Git history, branch, migrations, CLI, tests and Phase 5 implementation before editing.
2. Continue from the latest successful Phase 5 commit. Do not recreate working collectors, storage, reports, alerts or scheduling code.
3. Preserve backward compatibility unless a migration is explicitly required below.
4. Treat `config/tracked_keywords.csv` and `config/ai_questions.csv` as `candidate-v0.1`, not approved production truth.
5. Do not claim that current catalogue rows are derived from GSC, GA4, Serper, Semrush or customer data unless stored lineage proves it.
6. Semrush and customer/support sources are unavailable for this phase. Record them as deferred inputs; do not fabricate substitutes.
7. GSC is the initial demand/visibility source. GA4 enriches page value. Serper validates current SERPs; it must not be described as search-volume evidence.
8. Every selected keyword and AI question must be traceable to stored source records and a recorded approval decision.
9. Never mutate or relabel fixture data as production evidence.
10. Add migrations and tests with every schema or behavior change.
11. Do not overwrite the existing CSV catalogues until an explicit approval/import command is invoked.
12. Pause for human confirmation before activating a newly generated catalogue in production.

### Required first response

Before editing, report:

- current branch, HEAD commit and working-tree status;
- which Phase 0–5 components already exist;
- baseline test commands and results;
- current catalogue schema and provenance gaps;
- proposed migrations and compatibility plan;
- exact files expected to change;
- access blockers for GSC, GA4, Serper and production storage;
- implementation sequence for the phases below.

## 1. Objective

Extend the existing loop:

```
collect → normalize → store → validate → baseline → report → prioritise
```

with a traceable catalogue-construction loop:

```
GSC snapshot
→ normalized query candidates
→ GSC evidence aggregation
→ GA4 landing-page enrichment
→ Serper validation
→ candidate decisions
→ reviewed clusters
→ AI-question derivation
→ human approval
→ versioned production catalogue
```

The system must be able to answer for every active keyword or AI question:

> Which GSC observations produced this item, which page did Google associate with it, what did GA4 show for that page, what did Serper show, how was the item transformed, and who approved it?
> 

## 2. Scope and source policy

### Sources used in this phase

| Source | Role | Allowed claim |
| --- | --- | --- |
| GSC | Initial candidate discovery and observed Google performance | Sunnystep received observed impressions/clicks for this query and page |
| GA4 | Landing-page engagement and commercial prioritisation | This page generated the recorded sessions, engagement, purchases and revenue |
| Serper | Current SERP validation | This is what the configured Google result showed at collection time |
| Business review | Relevance, page ownership and final approval | A reviewer approved the recorded decision |

### Deferred sources

- Semrush: pending permission; no keyword-volume, difficulty or competitor-gap claims in v1.
- Customer/support tickets, reviews, onsite search and sales conversations: unavailable unless access is later granted.

The report and documentation must state this limitation:

> The v1 catalogue is biased toward queries where Sunnystep already received Google impressions. It does not provide complete discovery of zero-visibility market opportunities. Semrush and customer-language sources are planned enrichments for a later catalogue version.
> 

## 3. Existing catalogue treatment

The current 55 keywords and 20 questions must remain available for comparison but be labelled:

```
catalogue_version: candidate-v0.1
status: provisional
origin: existing repository strategy and manual mapping
GSC provenance: unverified
GA4 weighting: not applied
Serper validation: not proven per item
approval: pending
```

Create a deterministic comparison between `candidate-v0.1` and the evidence-derived catalogue. Each prior item must end as:

```
retained
modified
replaced
rejected
deferred
```

Each decision requires a reason and source references.

## 4. Data contracts and migrations

Add new tables or repository-equivalent structures. Prefer additive migrations.

### `catalogue_builds`

```
build_id                 primary key
build_type               keyword|ai_question
status                   draft|review|approved|activated|superseded
source_window_start
source_window_end
gsc_source_run_ids       JSON
 ga4_source_run_ids      JSON
serper_source_run_ids    JSON
source_fingerprint
methodology_version
created_by
created_at
approved_by              nullable
approved_at              nullable
activated_at             nullable
notes                     nullable
```

### `keyword_candidates`

```
candidate_id             primary key
build_id
canonical_keyword
normalized_keyword
brand_status
primary_observed_page
observed_pages_json
source_query_count
gsc_clicks
gsc_impressions
gsc_weighted_ctr
gsc_weighted_position
ga4_organic_sessions     nullable
ga4_engaged_sessions     nullable
ga4_purchases            nullable
ga4_revenue              nullable
ga4_conversion_rate      nullable
serper_position           nullable
serper_ranking_url        nullable
serper_top_10_domains     JSON
serper_intent             nullable
business_relevance_score nullable
gsc_opportunity_score
ga4_value_score           nullable
serper_validation_score  nullable
final_selection_score
decision                  pending|selected|rejected|deferred
decision_reason           nullable
proposed_target_page
reviewed_target_page      nullable
reviewed_by               nullable
reviewed_at               nullable
created_at
updated_at
```

### `keyword_candidate_sources`

```
candidate_source_id       primary key
candidate_id
gsc_natural_key
gsc_run_id
raw_query
raw_page
clicks
impressions
ctr
position
transformation_method     exact|normalized|merged_variants|human_reworded
created_at
```

A selected v1 keyword must have at least one valid `keyword_candidate_sources` row. Strategic additions without GSC evidence are not allowed in production v1; they may be stored as deferred candidates.

### `catalogue_clusters`

```
cluster_id                primary key
build_id
cluster_name
primary_intent
primary_target_page
member_candidate_ids      JSON
rationale
method                    deterministic_rules|reviewed_model_assist|manual
reviewed_by
reviewed_at
created_at
```

### `ai_question_candidates`

```
question_candidate_id     primary key
build_id
question
cluster_id
intent
proposed_target_page
transformation_method     direct_rewrite|merged_queries|context_expansion|comparison_expansion
source_keyword_ids        JSON
source_candidate_ids      JSON
decision                  pending|selected|rejected|deferred
decision_reason
reviewed_by               nullable
reviewed_at               nullable
created_at
updated_at
```

### `ai_question_sources`

```
question_source_id        primary key
question_candidate_id
keyword_id                nullable
candidate_id
gsc_natural_key
gsc_run_id
raw_gsc_query
relationship              direct|supporting|combined
created_at
```

### Existing catalogue changes

Extend the production keyword and AI-question catalogues, or add version tables, so each active row records:

```
catalogue_version
build_id
approval_status
approved_by
approved_at
valid_from
valid_to
```

Historical active versions must remain interpretable. Activation of a new version must expire the previous version transactionally; do not delete it.

## 5. GSC-derived keyword pipeline

### 5.1 Source-window selection

Add a command equivalent to:

```bash
python -m data_sources.tracking.cli catalogue build-keywords \
  --gsc-start-date YYYY-MM-DD \
  --gsc-end-date YYYY-MM-DD \
  --ga4-start-date YYYY-MM-DD \
  --ga4-end-date YYYY-MM-DD \
  --status draft \
  --json
```

Requirements:

- Use stored production `gsc_daily` rows; do not call GSC implicitly unless the command explicitly requests collection.
- Default to a complete 90-day GSC window.
- Record exact source run IDs and a source fingerprint.
- Exclude fixture/demo rows.
- Keep raw queries and normalized candidates separately.
- Apply the versioned brand classifier and retain the classification version.
- Do not treat recent unavailable GSC dates as zero.

### 5.2 Query normalization and candidate formation

Normalize deterministically:

- Unicode normalization;
- lowercase;
- punctuation-to-space where meaning is preserved;
- whitespace collapse;
- approved apostrophe variants;
- no uncontrolled fuzzy merging.

Merge query variants only when the transformation is reviewable. Preserve every contributing raw GSC row in `keyword_candidate_sources`.

### 5.3 GSC aggregation

For each candidate calculate:

```
total_clicks = SUM(clicks)
total_impressions = SUM(impressions)
weighted_ctr = SUM(clicks) / SUM(impressions)
weighted_position = SUM(position × impressions) / SUM(impressions)
```

Never average row-level CTR values. Protect division by zero.

Record:

- all observed pages;
- the page with the strongest evidence;
- whether multiple Sunnystep pages compete for the candidate;
- source-row count;
- source-date coverage.

### 5.4 Eligibility and selection funnel

Make thresholds configuration-driven. Produce funnel counts for:

```
raw GSC rows
unique raw queries
normalized candidates
non-branded candidates
business-relevant candidates
candidates meeting evidence threshold
Serper-validated candidates
selected keywords
```

Do not silently drop rows. Store rejection/defer reasons.

## 6. GA4 landing-page enrichment

Join GSC observed pages to GA4 landing pages using a tested canonical page key:

```
lowercase host
remove query parameters and fragments
normalize trailing slash
normalize absolute URL vs path
preserve meaningful path case according to site behavior
```

For the configured compatible window, attach:

```
organic_sessions
engaged_sessions
engagement_rate
purchases
revenue
conversion_rate
```

Requirements:

- Report matched and unmatched GSC pages.
- Never discard a keyword because its GA4 join failed.
- Mark GA4 metrics unavailable rather than zero when there is no trustworthy match.
- Keep page type in scoring where available: article, collection, product or other.
- Product/collection pages may weight purchases and revenue more heavily.
- Educational pages may weight engagement and downstream navigation more heavily if those events exist.
- Store the GA4 run IDs and window used for every build.

## 7. Candidate scoring

Implement a transparent configurable score. Default:

```
final_selection_score =
  gsc_opportunity_score × 0.40
  + ga4_value_score × 0.30
  + business_relevance_score × 0.20
  + evidence_confidence_score × 0.10
```

Do not call GSC impressions market search volume.

GSC opportunity should recognize at least:

- high impressions and position 4–10 with weak CTR;
- meaningful impressions and position 11–20;
- wrong-page or multi-page competition;
- existing clicks worth protecting;
- insufficient evidence.

Store every component, normalization rule and assumption. A reviewer may override selection or target page only with a reason.

## 8. Serper validation

Add a command equivalent to:

```bash
python -m data_sources.tracking.cli catalogue validate-serp \
  --build-id BUILD_ID \
  --decision pending \
  --limit N \
  --json
```

For shortlisted candidates, record:

- collection timestamp and Serper run ID;
- Sunnystep position or zero when absent in the inspected range;
- ranking Sunnystep URL;
- ordered top-10 domains;
- result/content type observations where deterministic;
- whether the proposed target page ranks;
- AI Overview capability state using existing verified rules.

Serper validates current visibility and intent. It must not add search-volume claims. Paid calls must respect the existing cost cap and require an explicit command.

## 9. Cluster derivation and review

Derive initial clusters only after the candidate universe exists.

Use:

- normalized topic similarity;
- search intent;
- observed GSC page;
- reviewed target page;
- product/category relationship.

Do not cluster only by word overlap. Separate informational and commercial intent where the target page or result type differs.

Output a reviewable cluster artifact containing:

```
cluster name
intent
member candidates
primary target page
supporting pages
GSC totals
GA4 totals where available
rationale
```

No cluster becomes production-active without reviewer approval.

## 10. AI-question derivation

Add a command equivalent to:

```bash
python -m data_sources.tracking.cli catalogue build-ai-questions \
  --keyword-build-id BUILD_ID \
  --count 20 \
  --status draft \
  --json
```

Every production v1 question must reference:

- at least one selected keyword candidate;
- at least one original GSC source row;
- one approved cluster;
- one proposed target page;
- a recorded transformation method.

Generate review candidates across a deliberate intent mix, for example:

| Intent | Target count |
| --- | --- |
| Recommendation | 5 |
| Problem/situation | 5 |
| Product selection | 4 |
| Feature education | 3 |
| Comparison | 3 |

This distribution is configurable and reviewable, not a hard-coded truth.

Questions must be:

- non-branded;
- natural and conversational;
- relevant to Singapore where context matters;
- not twenty superficial rewrites of “best shoes”;
- mapped to an existing or explicitly reviewed target page;
- safe around medical/YMYL language.

If model assistance is used to draft wording, it must receive only approved candidate data, use a versioned prompt, and never decide approval. Deterministic lineage and human review remain required.

## 11. Review and activation workflow

Provide commands equivalent to:

```bash
python -m data_sources.tracking.cli catalogue export-review --build-id BUILD_ID --output review.csv
python -m data_sources.tracking.cli catalogue import-decisions --build-id BUILD_ID --input review.csv
python -m data_sources.tracking.cli catalogue approve --build-id BUILD_ID --approved-by "Ting"
python -m data_sources.tracking.cli catalogue activate --build-id BUILD_ID --confirm
```

Requirements:

- Export must include evidence metrics, source references and decision fields.
- Import validates IDs and rejects unknown or duplicate rows.
- Approval requires exactly the configured keyword count or approved minimum and 20 approved AI questions.
- Activation is a separate explicit action from approval.
- Activation is transactional and idempotent.
- A rerun must not duplicate catalogue rows.
- The old catalogue remains queryable.
- Record the real approval and activation timestamps; do not backdate them.

## 12. Provenance report for stakeholders

Generate a machine-readable JSON result and a human-readable Markdown report containing:

1. Methodology and source limitations.
2. GSC and GA4 windows.
3. Source run IDs and fingerprints.
4. Selection-funnel counts.
5. Cluster distribution.
6. Selected, rejected and deferred counts.
7. GA4 page-match rate and unmatched pages.
8. Serper validation coverage.
9. Old-catalogue comparison: retained, modified, replaced, rejected and deferred.
10. One complete lineage walkthrough:

```
raw GSC rows
→ normalized candidate
→ aggregated GSC metrics
→ GSC observed page
→ GA4 page metrics
→ Serper result
→ reviewer decision
→ cluster
→ AI question
```

1. Deferred source register covering Semrush and customer/support data.
2. Approval identity, timestamp and catalogue version.

Suggested command:

```bash
python -m data_sources.tracking.cli catalogue report --build-id BUILD_ID --output docs/catalogue-provenance-v1.md --json
```

## 13. New quality checks

Add run/build-scoped checks. Do not use historical table-wide success to hide a failed current build.

| Check | Severity | Pass condition |
| --- | --- | --- |
| GSC source lineage | Critical | Every selected keyword has at least one valid GSC source row |
| Source-window integrity | Critical | All source rows belong to recorded runs/windows |
| Fixture exclusion | Critical | No demo/fixture row contributes to a production build |
| GSC aggregation reconciliation | Critical | Candidate totals reconcile to source rows |
| GA4 join accounting | Error | Matched + unmatched pages equals eligible pages |
| Serper validation coverage | Error | All required shortlisted candidates explicitly validated or blocked |
| Keyword decision completeness | Critical | Every candidate has a valid decision before approval |
| Target-page validity | Error | Every selected item has a valid reviewed Sunnystep target URL |
| Cluster membership | Error | Every selected keyword belongs to exactly one approved primary cluster |
| AI-question lineage | Critical | Every approved question links to keyword candidates and GSC source rows |
| AI-question count | Critical | Exactly 20 approved active questions for v1 |
| Non-branded questions | Critical | Zero approved question contains an approved brand term |
| Approval completeness | Critical | Approver and timestamp exist before activation |
| Version transition | Critical | Previous active version expired; new version active exactly once |
| Provenance report parity | Error | Report counts equal stored build counts |

Also correct existing quality checks so daily checks filter by the relevant `run_id` or `as_of_date`, and weekly checks evaluate their reporting window and validated contributing runs.

## 14. Post-Phase 5 operational hardening

Complete these known gaps while preserving the catalogue scope.

### Persistent storage

The current GitHub-hosted workflow must not rely on an ephemeral local SQLite file for production history. Implement or document one approved persistent execution model:

1. persistent VM/server disk with SQLite and systemd; or
2. a supported persistent database backend.

Do not present uploaded workflow artifacts as the primary durable database without explicit approval.

### Google credential material

A hosted runner cannot use a credential path unless the file exists. Add a safe documented process that writes credential JSON from managed secrets to a temporary file with restricted permissions, passes the temporary path to the collector, and removes it after use.

### Production doctor

`doctor` must return non-zero in production when integrations required by the selected production command are missing or credential files do not exist. Development and partial-source commands may remain selective.

### ChatGPT search capability

Verify the actual approved search-enabled API request and citation response. Do not silently fall back to a non-search answer and mark it successful. Record `unsupported` or `unverified` and block published GEO metrics when search capability is required but unavailable.

## 15. CLI acceptance behavior

All new commands must provide:

- `--help`;
- human-readable output and `--json`;
- non-zero exit code for failed required operations;
- inserted, updated, unchanged, rejected, deferred and failed counts where applicable;
- no external paid call unless the command explicitly requests it;
- no production activation without explicit confirmation;
- no secrets or unrestricted raw GSC queries in normal logs.

## 16. Testing requirements

### Unit tests

Cover:

- query normalization and safe variant merging;
- weighted GSC CTR and position;
- source lineage creation;
- GSC page canonicalization;
- GSC-to-GA4 URL matching;
- matched/unmatched accounting;
- scoring components and deterministic tie-breaking;
- selection/rejection/defer reasons;
- cluster membership constraints;
- AI-question lineage and non-brand checks;
- approval and activation transitions;
- catalogue version expiry;
- fixture exclusion;
- stakeholder report counts.

### Integration tests

Using a temporary database:

1. migrate from the current Phase 5 schema;
2. seed sanitized GSC and GA4 source rows;
3. build candidates;
4. confirm every aggregate traces to source rows;
5. enrich matched and unmatched GA4 pages;
6. validate Serper through a fake HTTP boundary;
7. import review decisions;
8. build 20 AI-question candidates;
9. approve and activate a version;
10. rerun and prove no duplicates;
11. query the complete lineage for one keyword and question;
12. confirm candidate-v0.1 remains queryable.

### Required verification

```bash
python -m compileall -q -f .
python scripts/secret_scan.py
python -m pytest -q tests
```

No new failures are acceptable. Record pre-existing failures separately.

## 17. Implementation phases and commits

### Phase 6 — Provenance schema and GSC candidate builder

Deliver:

- additive migrations;
- catalogue build models/repository methods;
- GSC candidate aggregation;
- source-lineage rows;
- selection-funnel output;
- tests.

Suggested commit:

```
feat: add GSC-derived catalogue provenance and candidate builds
```

### Phase 7 — GA4 enrichment and Serper validation

Deliver:

- canonical GSC/GA4 page join;
- GA4 candidate metrics and match report;
- explicit Serper candidate validation;
- scoring components;
- tests.

Suggested commits:

```
feat: enrich keyword candidates with GA4 page value
feat: validate shortlisted keywords with Serper evidence
```

### Phase 8 — Cluster, question, review and activation workflow

Deliver:

- reviewable cluster derivation;
- AI-question candidates with source lineage;
- review export/import;
- approval and activation lifecycle;
- old-vs-new comparison;
- tests.

Suggested commits:

```
feat: add traceable cluster and AI-question derivation
feat: add catalogue review approval and activation workflow
```

### Phase 9 — Quality, stakeholder report and operational hardening

Deliver:

- run/build-scoped quality gates;
- provenance report;
- persistent deployment correction;
- hosted credential-file handling;
- production doctor behavior;
- verified ChatGPT search capability handling;
- runbook and gate-demo updates.

Suggested commits:

```
fix: scope quality checks to contributing runs
feat: add catalogue provenance stakeholder report
ops: harden persistent scheduling and credential setup
fix: verify search-enabled AI visibility behavior
```

After each phase, stop and report results before continuing.

## 18. Gate demo

Acceptance requires demonstrating:

1. A real GSC source window and source run IDs.
2. Candidate build with source fingerprint and funnel counts.
3. One candidate linked to all contributing GSC natural keys.
4. Correct weighted CTR and position reconciliation.
5. GSC page joined to GA4 metrics, including unmatched-page accounting.
6. Serper validation for a shortlisted candidate.
7. Review artifact showing selected, rejected and deferred decisions.
8. Approved clusters with members and target pages.
9. Twenty approved non-branded AI questions, each linked to keyword candidates and GSC rows.
10. Comparison with candidate-v0.1.
11. Approval followed by separate activation.
12. Same build/activation rerun with zero duplicate logical rows.
13. Stakeholder provenance report.
14. Persistent unattended execution evidence.
15. Green tests and secret scan.

If real GSC, GA4 or Serper access is unavailable, mark the relevant gate blocked with exact human action required. Fixtures may prove code behavior but cannot satisfy production-data provenance.

## 19. Definition of done

- [ ]  Current Phase 5 functionality remains operational.
- [ ]  Existing 55/20 catalogues are labelled provisional and preserved.
- [ ]  Every selected v1 keyword traces to one or more real GSC rows.
- [ ]  GSC aggregation is reproducible and reconciled.
- [ ]  GA4 enrichment is attached where joinable and unavailable where not.
- [ ]  Serper is used only for current-SERP validation, not demand claims.
- [ ]  Every candidate has a stored decision and reason.
- [ ]  Clusters have recorded members, intent, target page and reviewer.
- [ ]  Every approved AI question traces to selected keywords and GSC rows.
- [ ]  Semrush and customer/support limitations are disclosed.
- [ ]  Approval and activation are separate, auditable actions.
- [ ]  Catalogue versions are date-effective and historical versions remain queryable.
- [ ]  Build-scoped quality checks pass.
- [ ]  Stakeholder report reconciles to stored records.
- [ ]  Production storage is durable across scheduler runs.
- [ ]  Google credentials are materialized safely in hosted execution.
- [ ]  Search-enabled AI behavior is verified or explicitly blocked.
- [ ]  Tests and secret scan pass.

## 20. Cursor phase completion format

After each phase respond with:

```
Phase:
Status: complete | partial | blocked
Starting commit:
Ending commit:
Changes:
Files changed:
Migrations:
Tests run:
Test results:
Data/API evidence:
Catalogue lineage evidence:
Security notes:
Known limitations/blockers:
Human decision required:
Next phase:
```

At final completion include:

- commit list;
- migration summary;
- CLI command list;
- test results;
- real-source verification status;
- selected/rejected/deferred counts;
- approval and activation status;
- exact remaining blockers;
- gate-demo commands;
- rollback instructions.

## 21. Final principle

The catalogue is not valid because it contains 55 keywords and 20 questions. It is valid only when each active item has reproducible source lineage, a reviewed target page, an explicit decision, a version and an approver.