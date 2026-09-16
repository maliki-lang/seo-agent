# SEO/GEO Agent — Phase 10 Catalogue Selection Quality & Portfolio Control Specification

**Project:** Sunnystep SEO/GEO Agent  
**Owner:** Maliki  
**Approver:** Ting  
**Document role:** Continuation implementation contract for Cursor  
**Predecessor specifications:**

1. `SEO_GEO_DATA_TRACKING_IMPLEMENTATION_SPEC.md`
2. `SEO_GEO_CATALOGUE_PROVENANCE_IMPLEMENTATION_SPEC.md`

**Status:** Ready for repository inspection and phased implementation  
**Primary outcome:** Replace the global top-55 proposal rule with an intent-aware, family-deduplicated, target-actionable and coverage-constrained selection process.

---

## 0. Instructions to Cursor

Treat this document as a continuation of the two predecessor specifications. Do not rebuild the tracking layer or the catalogue provenance workflow.

### Operating rules

1. Inspect the current branch, Git history, migrations, database schema, catalogue CLI, tests and generated review workflow before editing.
2. Preserve the existing GSC source lineage, candidate IDs, source receipts, draft review, import, approval and activation behavior.
3. Do not delete or rewrite working collectors, GA4 enrichment, Serper evidence storage, quality checks, reports, alerts or scheduling code.
4. Preserve the existing `selected`, `pending`, `deferred` and `rejected` decision values for backward compatibility. Add routing, eligibility and selection fields rather than redefining existing historical records.
5. Treat the current global top-55 output as a draft generated under `catalogue_gsc_v1`, not approved production truth.
6. Introduce a new versioned methodology, tentatively `catalogue_selection_v2`.
7. Keep exact deterministic query normalization as the stable candidate identity. Do not make the existing normalized key fuzzy. Add a separate keyword-family layer.
8. Do not classify a query as business-relevant only because it contains a market/location term such as `singapore`.
9. Branded, competitor, local and non-brand discovery queries must not compete under identical assumptions.
10. Serper remains current-SERP evidence, not search-volume evidence.
11. GA4 remains page-level evidence, not keyword-level revenue attribution.
12. Any automatic selection must be deterministic, reproducible and explainable from stored fields and a recorded policy version.
13. Never activate a newly generated catalogue without explicit human approval.
14. Add migrations and tests with every schema or behavior change.
15. Never expose or commit credential files. Verify credential paths and generated exports are ignored before implementation begins.

### Required first response

Before editing, report:

- current branch, HEAD commit and working-tree status;
- baseline test commands and results;
- current catalogue build, scoring, clustering, Serper, review and activation flow;
- existing migrations and relevant schema columns;
- differences between this specification and current behavior;
- exact files expected to change or be added;
- proposed migration identifiers;
- API-cost implications of wider Serper validation;
- compatibility plan for existing builds and CSV imports;
- blockers or decisions requiring Maliki or Ting.

Do not start coding until this inspection report is complete.

---

## 1. Problem statement

The current implementation provides strong evidence lineage and operational review, but its draft shortlist is produced by globally sorting all eligible candidates by `final_selection_score` and taking the first configured number, currently 55.

Current behavior is approximately:

```text
production GSC rows
→ deterministic normalization
→ exact brand-term exclusion
→ flat relevance-term check
→ GSC evidence gate
→ GSC/GA4/confidence score
→ global top 55
→ selected-only Serper validation
→ cluster derivation
→ human review
```

This creates a technically traceable but strategically weak shortlist because the score does not adequately control for:

- search intent;
- keyword-family duplication;
- brand-classifier leakage;
- competitor intent;
- target-page suitability;
- portfolio coverage across product and customer-need clusters;
- overrepresentation of local/store word-order variants;
- shared page-level GA4 signals;
- the difference between protecting existing visibility and finding new opportunity.

The new implementation must preserve provenance while changing the selection order to:

```text
production GSC rows
→ stable candidate normalization
→ routing and intent classification
→ business-relevance eligibility
→ conservative keyword-family derivation
→ target-page actionability
→ preliminary lane ranking
→ wider Serper validation
→ v2 scoring
→ quota-constrained portfolio selection
→ 55 proposals + alternates + exceptions
→ human review
→ approval and activation
```

---

## 2. Objective

Implement a catalogue-composition layer that answers, for every proposed primary keyword:

1. What search intent does the keyword represent?
2. Is it branded, non-branded, competitor-led or ambiguous?
3. Which keyword family does it represent?
4. Which variants are covered by that family?
5. Why is the query relevant to Sunnystep?
6. Which existing or planned page should own it?
7. Is the target page actionable?
8. What GSC, GA4 and Serper evidence supports it?
9. Which strategic portfolio slot does it fill?
10. Which candidate would replace it if rejected?
11. Which methodology and configuration produced the decision?
12. Who approved the final catalogue?

The final 55 must be a balanced set of distinct search-demand representatives, not merely the 55 highest numeric scores.

---

## 3. Non-goals

This phase does not:

- replace exact candidate normalization with embeddings or an opaque model;
- claim complete market discovery from GSC alone;
- infer keyword-level purchases from GA4;
- treat Serper as keyword-volume evidence;
- automatically write or publish content;
- remove human approval;
- activate branded queries inside the non-brand catalogue by default;
- add Semrush or customer/support integrations without access;
- guarantee exactly 55 high-quality keywords when fewer than 55 pass the configured eligibility policy.

If fewer than the required minimum pass, the build must report a coverage blocker rather than filling the catalogue with low-quality rows.

---

## 4. Current behavior that must remain intact

Preserve:

- filtering to production GSC evidence;
- GSC query × page × day receipts;
- deterministic candidate IDs and source lineage;
- weighted CTR and impression-weighted position;
- fixture/demo exclusion;
- immutable build records and methodology versioning;
- GA4 homepage suppression;
- unavailable GA4 values represented as null, not zero;
- raw Serper response retention and redaction;
- Serper cost-cap enforcement;
- review CSV import by candidate/build ID;
- reviewer target-page overrides;
- explicit approval and activation;
- quality-check logging;
- safe reruns and idempotent persistence.

Historical v1 builds must remain queryable and exportable.

---

## 5. Target architecture

Add focused catalogue-selection modules instead of expanding `builder.py` into a monolith:

```text
data_sources/tracking/catalogue/
├── builder.py                 # existing aggregation and candidate creation
├── scoring.py                 # retain v1 functions; add/version v2 carefully
├── classify.py                # new routing, relevance and intent classification
├── families.py                # new conservative family derivation
├── target_pages.py            # new target-page actionability evaluation
├── preselection.py            # new lane-balanced Serper candidate pool
├── selection.py               # new final portfolio selection
├── policy.py                  # new validated policy/config model
├── clusters.py                # adapt to consume semantic fields
├── validate_serp.py           # extend to preselection pool
├── workflow.py                # extend export/import/approval fields
└── reports.py                 # or existing report module integration
```

Do not require these exact filenames if the repository has a better established convention, but preserve these component boundaries.

---

## 6. Terminology and state model

### Candidate

One exact normalized GSC query candidate with complete source lineage.

### Keyword family

A conservative grouping of query variants that represent substantially the same intent and can normally be tracked by one primary keyword.

### Content cluster

A broader strategic topic or page-ownership grouping. A content cluster can contain multiple keyword families.

Do not use `keyword_family` and `content_cluster` interchangeably.

### Routing bucket

One of:

```text
nonbrand_discovery
branded_benchmark
competitor_benchmark
local_store
ambiguous_brand
irrelevant
manual_review
```

### Strategic lane

One of:

```text
need_state
product_category
use_case_audience
local_store
commercial_discovery
competitor_discovery
strategic_gap
informational_editorial
```

### Eligibility status

One of:

```text
eligible
eligible_with_review
ineligible_brand
ineligible_duplicate_variant
ineligible_irrelevant
ineligible_unsupported_claim
ineligible_no_actionable_target
pending_classification
```

### Existing decision values

Continue to persist:

```text
selected
pending
rejected
deferred
```

Interpretation under v2:

- `selected`: proposed portfolio primary, still draft until approval;
- `pending`: eligible alternate or unresolved review candidate;
- `deferred`: valid but outside current priority or evidence policy;
- `rejected`: excluded from this catalogue version.

Routing fields must explain whether a rejected candidate was routed to a separate branded or competitor benchmark.

---

## 7. Data contracts and migrations

Create additive migrations. Do not destructively modify historical v1 rows.

### 7.1 Extend `keyword_candidates`

Add nullable fields as needed:

```text
methodology_version
routing_bucket
brand_match_type
brand_confidence
competitor_status
competitor_name
search_intent
strategic_lane
business_relevance_status
business_relevance_reason
claims_review_required
family_id
family_role
family_method
family_confidence
target_page_status
target_page_confidence
intent_fit_score
target_actionability_score
serp_opportunity_score
incremental_coverage_score
duplicate_penalty
selection_score_v2
selection_rank_within_lane
eligibility_status
eligibility_reasons_json
selection_reasons_json
alternate_rank
```

Use nullable fields so old builds remain readable.

### 7.2 Add `keyword_families`

Minimum fields:

```text
family_id                     primary key
build_id                      foreign key
family_key
family_label
primary_candidate_id          nullable until selected
routing_bucket
search_intent
strategic_lane
member_candidate_ids_json
member_count
family_gsc_clicks
family_gsc_impressions
family_weighted_ctr
family_weighted_position
primary_target_page
family_method
family_confidence
approval_status
reviewed_by
reviewed_at
created_at
updated_at
```

Natural uniqueness:

```text
build_id + family_key
```

### 7.3 Extend `catalogue_builds`

Add or use JSON fields for:

```text
selection_policy_version
selection_policy_json
routing_report_json
family_report_json
preselection_report_json
portfolio_report_json
quality_exceptions_json
```

The stored policy must be sufficient to reproduce selection.

### 7.4 Optional `catalogue_selection_runs`

If build-level JSON is insufficient for auditability, add:

```text
selection_run_id
build_id
methodology_version
policy_fingerprint
started_at
finished_at
status
eligible_family_count
serper_required_count
serper_validated_count
selected_count
alternate_count
lane_counts_json
exception_counts_json
created_by
```

Prefer the simplest schema that preserves reproducibility.

---

## 8. Configuration contract

Extend the existing `catalogue` configuration without breaking current keys.

Illustrative configuration:

```yaml
catalogue:
  methodology_version: catalogue_selection_v2
  min_impressions: 10
  min_clicks_protect: 1
  selected_limit: 55
  alternate_limit: 15
  serper_preselection_limit: 100

  relevance:
    product_terms:
      - shoe
      - shoes
      - sneaker
      - sneakers
      - sandal
      - sandals
      - loafer
      - loafers
      - mule
      - mules
      - slipper
      - slippers
      - flat
      - flats
    need_terms:
      - comfort
      - comfortable
      - walking
      - standing
      - arch support
      - wide feet
      - cushioning
    commercial_terms:
      - buy
      - shop
      - store
      - best
      - recommended
      - near me
    location_terms:
      - singapore
      - sg
    prohibited_location_only: true

  family_rules:
    singular_plural: true
    shop_store_equivalence: true
    safe_word_order_equivalence: true
    fuzzy_brand_routing: true
    minimum_confidence_for_auto_family: 0.90

  lane_quotas:
    need_state: 15
    product_category: 12
    use_case_audience: 8
    local_store: 8
    commercial_discovery: 5
    competitor_discovery: 4
    strategic_gap: 3

  lane_caps:
    competitor_discovery: 4
    local_store: 8
    broad_head_term: 4

  score_weights:
    intent_fit: 0.25
    product_need_relevance: 0.20
    target_actionability: 0.15
    gsc_opportunity: 0.15
    serp_feasibility: 0.10
    incremental_coverage: 0.10
    evidence_confidence: 0.05

  penalties:
    duplicate_non_primary: 1.00
    ambiguous_brand: 1.00
    competitor_without_strategy: 0.25
    unresolved_target_page: 0.15
```

Validate that score weights total 1.0 and lane quotas total the configured selected limit. Invalid policy must fail configuration validation.

Competitor names and aliases must come from the existing competitor configuration where possible, not a second hardcoded source.

---

## 9. Classification pipeline

### 9.1 Brand classification

Replace the binary operational outcome with stored three-way classification:

```text
branded
non_branded
ambiguous_brand
```

Required match types:

```text
exact_configured_term
approved_alias
approved_typo
fuzzy_suspect
no_match
```

Rules:

1. Exact configured brand terms remain branded.
2. Approved aliases and known misspellings are versioned configuration, not undocumented code constants.
3. Conservative fuzzy detection may route candidates to `ambiguous_brand`, but must never automatically mark uncertain candidates as branded truth.
4. `ambiguous_brand` is ineligible for automatic non-brand selection until reviewed.
5. Store classifier version, match type and confidence.

Regression cases must include:

```text
sunnystep                  → branded
sunny step                 → branded
sunnysteps                 → branded
sunny shoes                → ambiguous_brand unless approved as alias
sunny feet shoes           → ambiguous_brand unless independently resolved
sunnysteo                  → ambiguous_brand or approved typo
comfortable walking shoes  → non_branded
```

### 9.2 Competitor classification

Use configured competitor aliases.

Persist:

```text
competitor_status: none | explicit | ambiguous
competitor_name
```

Examples:

```text
birkenstock singapore
scholl shoes singapore
on cloud shoes singapore
another sole singapore
```

Competitor terms must route to `competitor_benchmark` or `competitor_discovery`. They must be subject to a configured cap and require an approved strategic rationale or suitable comparison/category page.

### 9.3 Search-intent classification

Minimum intent values:

```text
navigational_brand
navigational_competitor
local_store
transactional_category
commercial_investigation
problem_solution
informational
campaign_event
ambiguous
```

Prefer deterministic, testable rules. If an LLM-assisted classifier is introduced later, store model/version, prompt version, raw result and deterministic fallback. LLM use must not be required for repeatability in this phase.

### 9.4 Business relevance

A query must not pass solely because it contains a location token.

Required baseline rule:

```text
relevant if:
  product/category evidence
  OR customer-need evidence
  OR approved competitor evidence
  OR commercial footwear evidence
  OR explicit reviewer override
```

Examples:

```text
walkathon singapore  → location alone is insufficient
slippers             → product category
mules singapore      → product category + location
comfortable shoes    → need + product
mao ting             → not automatically relevant
```

Store the rule that passed or failed.

### 9.5 Claims review

Queries involving health, pain or medical conditions must be flagged:

```text
plantar fasciitis
orthotic
pain relief
medical recommendation
injury recovery
```

They may remain candidates, but cannot be automatically selected unless product/claims review is approved or the selected target is clearly educational and compliant.

---

## 10. Keyword-family derivation

### 10.1 Principle

Keep `normalized_keyword` as the exact stable candidate key. Derive family membership separately.

### 10.2 Safe deterministic family rules

Permitted automatic transformations include configured, tested rules such as:

- singular/plural equivalence;
- `shop`/`store` equivalence when the remaining intent is identical;
- possessive removal already handled by normalization;
- conservative word-order normalization for known local patterns;
- approved geographical alias normalization;
- approved product synonym maps.

Do not automatically merge based only on edit distance.

### 10.3 Example families

```text
Family: local_shoe_store_near_me
Primary candidates:
- shoe shop near me
Variants:
- shoe shops near me
- shoe store near me
- shoe stores near me
- shoes shop near me
```

```text
Family: nex_shoe_store
Candidate variants:
- nex shoes
- nex shoes shop
- shoes nex
- shoe shop at nex
```

```text
Family: walking_shoes_women
Candidate variants:
- walking shoes for women
- walking shoes women
- best walking shoes for women
```

Do not merge `walking shoes` with `best walking shoes for women`; they can share a content cluster while representing distinct families if SERP intent differs.

### 10.4 Primary-family selection

Choose the family primary using deterministic criteria:

1. clearest natural-language query;
2. strongest intent match;
3. highest target-page actionability;
4. strongest compatible evidence;
5. highest impressions only as a later tie-breaker.

Do not select an awkward word-order variant merely because it has slightly more GSC impressions.

### 10.5 Family-level evidence

Store both:

- candidate-level evidence, unchanged;
- family-level aggregates for review.

Do not sum metrics across candidates that are not proven family variants.

---

## 11. Target-page actionability

### 11.1 Separate observed and intended pages

Continue storing `primary_observed_page` as GSC evidence. Do not treat it as automatically suitable.

Evaluate a separate status:

```text
observed_page_suitable
observed_page_needs_optimization
multiple_pages_competing
approved_new_page
homepage_unresolved
no_sensible_target
manual_review
```

### 11.2 Homepage behavior

A homepage observation is not automatically an actionable target.

Rules:

- homepage + generic brand/store intent may be valid;
- homepage + specific product/need intent should normally be `homepage_unresolved`;
- homepage must not inherit GA4 commercial value for keyword scoring;
- an unresolved homepage mapping prevents automatic final selection unless explicitly reviewed.

### 11.3 New-page opportunities

A missing current page may still be eligible when:

- the intent is relevant;
- a realistic page type is identified;
- the content/product opportunity is approved;
- the target is stored as planned rather than observed.

Persist whether the proposed action is:

```text
optimize_existing
consolidate_competing_pages
create_new_page
protect_existing
monitor_only
no_action
```

### 11.4 Shared GA4 page discount

Retain `ga4_shared_page`, but introduce a deterministic confidence multiplier. Suggested default:

```text
one family mapped to page        1.00
2–5 families mapped to page      0.60
more than 5 families             0.25
homepage                         0.00
unmatched                        unavailable
```

Store the multiplier and do not represent discounted page value as keyword-attributed revenue.

---

## 12. GSC and Serper scoring changes

### 12.1 Separate protection from opportunity

Current visibility and improvement opportunity must be stored separately.

Suggested components:

```text
gsc_protection_score
- stable clicks
- high current position
- material impressions

gsc_opportunity_score_v2
- position-relative CTR gap
- positions with plausible movement
- meaningful demand
- target-page actionability
- cannibalization requiring correction
```

Do not give a fixed large reward merely because `clicks >= 1`.

### 12.2 Position-relative CTR

Where an approved CTR curve exists, compare observed CTR with expected CTR for the observed position. If no approved curve exists, keep the existing weak-CTR rule but document it as provisional.

Never claim a causal click gain from the score alone.

### 12.3 Multi-page evidence

Do not always treat multiple pages as positive opportunity.

Classify it as:

```text
cannibalization_candidate
intent_split
normal_page_variation
unresolved
```

Only `cannibalization_candidate` should contribute to an optimization score, and it should also require a consolidation or ownership action.

### 12.4 Serper components

Split the current Serper score into:

```text
serp_visibility_score
serp_opportunity_score
serp_target_alignment_score
serp_validation_confidence
```

Examples:

- top-three result: high visibility/protection, not automatically high opportunity;
- positions 4–20: potentially higher improvement opportunity;
- absent: a visibility gap, but only valuable when intent and target actionability are strong;
- proposed target ranks: positive alignment evidence;
- unexpected Sunnystep page ranks: target-review signal.

### 12.5 Wider validation pool

Do not validate only the already selected 55.

Build a preliminary pool of distinct family primaries, default maximum 100, balanced across strategic lanes. Run Serper on this pool under the existing explicit command and cost cap. Final selection happens only after validation is complete or each missing result is explicitly recorded as blocked/failed.

If cost approval only allows 55 validations, report the limitation and do not claim Serper informed comparisons against unvalidated alternates.

---

## 13. Catalogue selection score v2

### 13.1 Components

Default formula:

```text
selection_score_v2 =
    intent_fit_score                 × 0.25
  + product_need_relevance_score     × 0.20
  + target_actionability_score       × 0.15
  + gsc_opportunity_score_v2         × 0.15
  + serp_feasibility_score           × 0.10
  + incremental_coverage_score       × 0.10
  + evidence_confidence_score        × 0.05
  − configured_penalties
```

All component scores must be stored separately.

### 13.2 Missing evidence

Do not silently renormalize in a way that makes candidates with missing evidence appear equivalent to fully validated candidates.

Store:

```text
available_evidence_weight
missing_evidence_fields
score_confidence
```

If renormalization is retained for compatibility, expose the original denominator and apply a confidence penalty.

### 13.3 Hard exclusions

The following are ineligible for automatic selection:

- family non-primary variants;
- exact branded queries in the non-brand catalogue;
- ambiguous brand candidates pending review;
- irrelevant or malformed queries;
- unsupported claims requiring unresolved review;
- candidates with no actionable existing or planned target;
- competitor queries without an approved strategy;
- candidates missing required Serper validation when policy requires it.

A numeric score cannot override a hard exclusion.

---

## 14. Portfolio selection

### 14.1 Do not globally take top N

Rank eligible family primaries within their strategic lane. Apply configured minimums, quotas and caps.

Default 55-slot policy:

| Strategic lane | Slots |
| --- | ---: |
| Need state / customer problem | 15 |
| Product category / style | 12 |
| Use case / audience | 8 |
| Local / store discovery | 8 |
| General commercial discovery | 5 |
| Competitor discovery | 4 |
| Strategic gap / emerging | 3 |
| **Total** | **55** |

These defaults must be configurable and explicitly reviewed by Maliki/Ting before activation.

### 14.2 Quota behavior

1. Select the highest-quality eligible family primaries inside each lane.
2. Never select more than one primary from the same family.
3. Enforce configured caps for competitor, local and broad head terms.
4. If a lane cannot fill its quota, record the shortage.
5. Redistribute unused slots only according to a configured deterministic fallback order.
6. Do not redistribute into a lane whose cap has been reached.
7. If fewer than the minimum required high-quality candidates remain, fail the catalogue quality gate rather than filling with weak candidates.

### 14.3 Incremental coverage

`incremental_coverage_score` should reward a candidate for adding a missing product, need, audience or intent—not simply for having a high standalone score.

It must be evaluated during deterministic portfolio construction. Store the selection sequence and the coverage contribution at the time each candidate is added.

### 14.4 Alternates and exceptions

Produce:

```text
55 proposed primaries
15 ranked alternates
manual-review exception set
separate branded benchmark recommendations
```

Each selected primary must have at least one alternate where available.

### 14.5 Branded benchmark

Do not consume non-brand slots with branded queries. Generate a separate recommendation report for approximately 10–15 representative branded families, subject to human approval.

Branded queries remain important for protection and navigational health even though they are outside the non-brand portfolio.

---

## 15. Clustering changes

The existing cluster process relies heavily on page-path family and page type. Extend it to consume:

```text
keyword family
strategic lane
search intent
product/need entities
target page status
```

Required separation:

```text
keyword_family_id  # query-variant equivalence
cluster_id         # broader content/product grouping
```

A page path may support clustering but must not replace semantic topic information.

Cluster derivation must occur before final portfolio selection so coverage can be measured. Final human review may override both family and cluster assignments with audit fields.

---

## 16. Review export and import

Extend the review CSV while preserving existing required columns.

Add:

```text
routing_bucket
brand_match_type
brand_confidence
competitor_name
search_intent
strategic_lane
eligibility_status
family_id
family_role
family_members
family_primary
primary_observed_page
target_page_status
proposed_action
claims_review_required
selection_score_v2
selection_rank_within_lane
portfolio_slot
alternate_rank
selection_reasons
eligibility_reasons
serper_validation_status
serp_visibility_score
serp_opportunity_score
serp_target_alignment_score
score_confidence
```

The export should offer practical filtered sections or separate files:

```text
proposed-55.csv
alternates-15.csv
manual-review-exceptions.csv
branded-benchmark-proposals.csv
full-candidate-audit.csv
```

If the existing workflow requires one file, include a `review_group` column and document the filter values.

Import must continue to require stable candidate/build IDs. Reject imports that silently change candidate identity or contain duplicate IDs.

---

## 17. CLI contract

Adapt names to the current CLI style. Required behavior should include equivalents of:

```bash
# Build exact GSC candidates and initial classifications
python -m data_sources.tracking.cli catalogue build-keywords ...

# Derive routing, intent, families and target actionability
python -m data_sources.tracking.cli catalogue classify --build-id <id>
python -m data_sources.tracking.cli catalogue derive-families --build-id <id>
python -m data_sources.tracking.cli catalogue evaluate-targets --build-id <id>

# Produce lane-balanced Serper preselection
python -m data_sources.tracking.cli catalogue preselect-serp --build-id <id> --limit 100

# Explicit paid validation
python -m data_sources.tracking.cli catalogue validate-serp --build-id <id> --pool preselected

# Compose portfolio and alternates
python -m data_sources.tracking.cli catalogue select-portfolio --build-id <id>

# Export, import and approval remain explicit
python -m data_sources.tracking.cli catalogue export-review --build-id <id>
python -m data_sources.tracking.cli catalogue import-review --build-id <id> --file <path>
python -m data_sources.tracking.cli catalogue approve --build-id <id>
python -m data_sources.tracking.cli catalogue activate --build-id <id>
```

Commands may be combined when safe, but every stage must remain observable and testable.

CLI output must include:

- build and methodology version;
- policy fingerprint;
- candidate/family counts;
- routing counts;
- eligibility counts;
- per-lane proposed and alternate counts;
- Serper coverage and cost;
- quality-gate status;
- unresolved blockers;
- paths to generated review artifacts.

---

## 18. Quality checks

Add checks with stored results and thresholds.

### Required error-level checks

1. Exactly the configured number of selected primaries when sufficient eligible candidates exist.
2. At least the production minimum active keyword count before activation.
3. Every selected candidate is a family primary.
4. No family contributes more than one selected primary.
5. No exact branded candidate is in the non-brand selection.
6. No ambiguous-brand candidate is automatically selected.
7. Competitor selection does not exceed its cap.
8. Local/store selection does not exceed its cap.
9. Every selected candidate has a search intent and strategic lane.
10. Every selected candidate has an actionable target status.
11. Every selected candidate has required Serper validation or an explicitly approved exception.
12. Every selected candidate has complete source lineage.
13. Score weights and quotas validate against the stored policy.
14. Selection is deterministic for identical data and policy fingerprints.
15. No unsupported claim candidate is selected without approval.

### Required warning-level checks

1. Broad head-term cap approaching limit.
2. Excessive candidates mapped to one deep page.
3. Excessive homepage observations.
4. Lane quota shortage.
5. Low score confidence due to missing evidence.
6. Family derivation confidence below threshold.
7. Selected candidate lacks a ranked alternate.
8. Branded benchmark has no approved representatives.

---

## 19. Testing requirements

### 19.1 Unit tests

Test:

- location-only relevance failure;
- product and need-term relevance success;
- three-way brand classification;
- configured competitor aliases;
- search-intent classification;
- safe family transformations;
- unsafe family merges remain separate;
- primary-family selection;
- target-page statuses;
- homepage unresolved behavior;
- shared GA4 confidence discount;
- protection versus opportunity scoring;
- Serper score separation;
- v2 score calculation and missing-evidence confidence;
- quota and cap validation;
- deterministic fallback allocation;
- alternate ranking.

### 19.2 Required regression fixtures

At minimum:

```text
sunny shoes                    → ambiguous brand, not auto-selected
sunny feet shoes               → ambiguous brand, not auto-selected
walkathon singapore            → location-only relevance must fail
slippers                       → recognized product category
mules singapore                → recognized product category
nex shoes / shoes nex          → same conservative family
shoe shop / shoe store         → equivalent only under approved rule
birkenstock singapore          → competitor route
comfortable walking shoes      → non-brand need/product route
plantar fasciitis shoes        → claims review required
```

### 19.3 Integration tests

Prove:

1. Existing v1 build and export still work.
2. A v2 build creates routing, family, target and policy evidence.
3. Serper preselection contains distinct family primaries and obeys lane balancing.
4. Final selection occurs after Serper validation.
5. Final 55 obey quotas/caps and contain no family duplicates.
6. Review export/import preserves new fields and existing identity rules.
7. Approval fails when quality gates fail.
8. Activation requires explicit confirmation and produces a versioned catalogue.
9. Repeating selection with identical build and policy produces identical results.
10. Changing the policy produces a new fingerprint and auditable selection result.

### 19.4 Dataset-level acceptance test

Run the v2 pipeline against the current production-backed candidate build or a safe copy of its database.

The acceptance report must compare v1 and v2:

```text
selected overlap
brand-like terms removed
competitor count
local/store count
unique family count
lane coverage
target-page actionability coverage
Serper validation coverage
score-confidence distribution
manual exceptions
```

Do not claim improvement solely because the numeric score changed. Show named examples of corrected selections and explain the policy reason.

### 19.5 Required verification commands

Run repository-standard formatting, static checks and test commands discovered during inspection. At minimum, run all catalogue unit and integration tests plus the existing full safe test suite.

Record exact commands, pass/fail counts and unresolved environmental blockers.

---

## 20. Backward compatibility and migration behavior

1. Existing v1 builds remain unchanged.
2. Existing review CSVs remain importable under their original build methodology.
3. V2-only fields are optional for v1 records.
4. Do not recalculate historical scores in place.
5. A v1 build may be copied/rebuilt under v2 only through an explicit command that creates a new build ID.
6. Existing catalogue activation remains valid until a human approves and activates a v2 catalogue.
7. Reports must label whether they display `catalogue_gsc_v1` or `catalogue_selection_v2`.
8. If enums or database constraints prevent additive states, preserve the old decision enum and store new routing/eligibility values in separate validated fields.

---

## 21. Implementation phases and commits

### Phase 10 — Schema, policy and classification

Deliver:

- additive migration;
- policy/config validation;
- routing fields;
- three-way brand result;
- competitor classifier;
- search-intent and business-relevance classification;
- claims-review flag;
- regression tests.

Suggested commits:

```text
feat: add catalogue selection v2 schema and policy
feat: add keyword routing and intent classification
fix: prevent location-only relevance matches
feat: add ambiguous brand and competitor routing
```

### Phase 11 — Families and target actionability

Deliver:

- `keyword_families` persistence;
- conservative family rules;
- family-primary selection;
- family-level evidence;
- target-page status evaluation;
- shared GA4 confidence discount;
- tests and audit report.

Suggested commits:

```text
feat: derive conservative keyword families
feat: add family primary and variant audit trail
feat: evaluate target page actionability
fix: discount shared page-level ga4 evidence
```

### Phase 12 — Serper preselection and score v2

Deliver:

- lane-balanced preselection pool;
- explicit paid validation for that pool;
- separated Serper components;
- protection/opportunity separation;
- v2 scoring with confidence and penalties;
- tests.

Suggested commits:

```text
feat: add lane-balanced serp preselection
feat: separate serp visibility and opportunity scores
feat: add catalogue selection score v2
```

### Phase 13 — Portfolio, review and activation hardening

Deliver:

- quota/cap portfolio selection;
- 55 proposals and 15 alternates;
- branded benchmark proposals;
- extended review export/import;
- quality gates;
- v1 versus v2 comparison report;
- documentation updates;
- safe approval and activation demonstration.

Suggested commits:

```text
feat: add coverage-constrained keyword portfolio selection
feat: add alternates and branded benchmark proposals
feat: extend catalogue review workflow for selection v2
test: add catalogue portfolio quality gates
 docs: document v2 catalogue review and activation
```

Use repository conventions for final commit wording.

---

## 22. Gate demo

Demonstrate in this order:

1. Show current branch, clean/known working tree and baseline tests.
2. Show a historical v1 build remains readable.
3. Build a new v2 candidate set from production-backed GSC evidence.
4. Show routing counts for non-brand, branded, competitor, local and ambiguous candidates.
5. Show regression cases such as `sunny shoes`, `walkathon singapore`, `slippers` and `mules singapore` classified correctly.
6. Show family grouping for near-me and mall/store variants.
7. Show each family retains original candidate/source receipts.
8. Show target-page actionability and homepage-unresolved handling.
9. Show lane-balanced Serper preselection and approved cost.
10. Run or replay Serper validation and show separated visibility/opportunity/alignment evidence.
11. Produce the v2 55-keyword proposal and 15 alternates.
12. Show that no family contributes multiple primaries.
13. Show quota and cap compliance.
14. Show branded queries excluded from the non-brand 55 but available in a branded benchmark proposal.
15. Export the review artifacts.
16. Apply a safe reviewer override and re-import it.
17. Show approval blocked when a required quality gate fails.
18. Approve a safe review fixture or approved real build.
19. Prove activation is explicit and versioned.
20. Rerun selection with identical data/policy and prove the same result is produced.

---

## 23. Definition of done

### Code

- Selection no longer uses one global score sort followed directly by `[:55]`.
- Classification, family derivation, target evaluation, preselection and portfolio selection have separate testable boundaries.
- Methodology and policy are versioned.
- Historical v1 behavior remains available.

### Data

- Every proposed primary has complete source lineage.
- Every proposed primary has routing, intent, lane, family and target-page fields.
- Every family retains its variants and family-level evidence.
- Serper evidence is complete or explicitly blocked according to policy.

### Quality

- No brand-like regression fixture is automatically selected.
- No location-only query passes business relevance.
- No family contributes multiple selected primaries.
- Competitor/local/broad-term caps are enforced.
- Required lane coverage is reported and validated.
- Unsupported claims cannot pass without review.
- Selection is deterministic.

### Review

- Reviewer receives 55 proposed primaries, 15 alternates, exceptions and branded benchmark proposals.
- Every proposal has a concise selection reason and a target-page status.
- Existing import, approval and activation protections remain intact.

### Security

- Credential JSON files are absent from commits, generated review bundles and implementation artifacts.
- `.gitignore` and secret-scan tests cover known credential paths and private keys.
- No secret value is printed during testing or completion reporting.

---

## 24. Blocker protocol

Pause and report when:

- a schema change would destructively alter historical builds;
- the current database cannot be safely migrated;
- Serper preselection cost exceeds the approved cap;
- competitor aliases or lane quotas require business approval;
- target-page ownership cannot be determined from available evidence;
- fewer than the production minimum number of candidates pass quality gates;
- a medical/product claim requires approval;
- tests reveal current production catalogue corruption;
- credential files appear tracked or shared and rotation requires authorization.

A blocker report must include:

```text
blocker
affected phase
verified evidence
work safely completed
work that must not proceed
specific human decision or access required
recommended next action
```

---

## 25. Cursor phase completion format

After each phase, return:

```text
Phase:
Status:

Implemented:
- ...

Files changed:
- ...

Migrations:
- ...

Tests run:
- command
- result

Data/behavior verification:
- ...

Compatibility:
- ...

Security checks:
- ...

Known limitations:
- ...

Blockers or approvals required:
- ...

Next phase:
- ...
```

Do not report a phase complete when required tests did not run. Distinguish code completion from authenticated external validation.

---

## 26. Final engineering principle

The catalogue must not answer only:

> Which 55 rows have the highest score?

It must answer:

> Which distinct search-demand families give Sunnystep the most useful, balanced and actionable measurement portfolio—and what evidence, policy and human decision placed each one there?

The intended production loop is:

```text
discover
→ classify
→ deduplicate into families
→ validate relevance and target ownership
→ gather comparable evidence
→ compose a balanced portfolio
→ review
→ approve
→ activate
→ measure
→ revise through a new version
```
