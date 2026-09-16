-- Phase 10 catalogue selection v2: additive routing / eligibility / policy fields.
-- Historical catalogue_gsc_v1 rows remain readable with NULL v2 columns.

ALTER TABLE keyword_candidates ADD COLUMN methodology_version TEXT;
ALTER TABLE keyword_candidates ADD COLUMN routing_bucket TEXT;
ALTER TABLE keyword_candidates ADD COLUMN brand_match_type TEXT;
ALTER TABLE keyword_candidates ADD COLUMN brand_confidence REAL;
ALTER TABLE keyword_candidates ADD COLUMN competitor_status TEXT;
ALTER TABLE keyword_candidates ADD COLUMN competitor_name TEXT;
ALTER TABLE keyword_candidates ADD COLUMN search_intent TEXT;
ALTER TABLE keyword_candidates ADD COLUMN strategic_lane TEXT;
ALTER TABLE keyword_candidates ADD COLUMN business_relevance_status TEXT;
ALTER TABLE keyword_candidates ADD COLUMN business_relevance_reason TEXT;
ALTER TABLE keyword_candidates ADD COLUMN claims_review_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE keyword_candidates ADD COLUMN family_id TEXT;
ALTER TABLE keyword_candidates ADD COLUMN family_role TEXT;
ALTER TABLE keyword_candidates ADD COLUMN family_method TEXT;
ALTER TABLE keyword_candidates ADD COLUMN family_confidence REAL;
ALTER TABLE keyword_candidates ADD COLUMN target_page_status TEXT;
ALTER TABLE keyword_candidates ADD COLUMN target_page_confidence REAL;
ALTER TABLE keyword_candidates ADD COLUMN intent_fit_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN target_actionability_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN serp_opportunity_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN incremental_coverage_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN duplicate_penalty REAL;
ALTER TABLE keyword_candidates ADD COLUMN selection_score_v2 REAL;
ALTER TABLE keyword_candidates ADD COLUMN selection_rank_within_lane INTEGER;
ALTER TABLE keyword_candidates ADD COLUMN eligibility_status TEXT;
ALTER TABLE keyword_candidates ADD COLUMN eligibility_reasons_json TEXT;
ALTER TABLE keyword_candidates ADD COLUMN selection_reasons_json TEXT;
ALTER TABLE keyword_candidates ADD COLUMN alternate_rank INTEGER;

ALTER TABLE catalogue_builds ADD COLUMN selection_policy_version TEXT;
ALTER TABLE catalogue_builds ADD COLUMN selection_policy_json TEXT;
ALTER TABLE catalogue_builds ADD COLUMN routing_report_json TEXT;
ALTER TABLE catalogue_builds ADD COLUMN family_report_json TEXT;
ALTER TABLE catalogue_builds ADD COLUMN preselection_report_json TEXT;
ALTER TABLE catalogue_builds ADD COLUMN portfolio_report_json TEXT;
ALTER TABLE catalogue_builds ADD COLUMN quality_exceptions_json TEXT;

CREATE INDEX IF NOT EXISTS idx_keyword_candidates_routing
    ON keyword_candidates(build_id, routing_bucket);
CREATE INDEX IF NOT EXISTS idx_keyword_candidates_eligibility
    ON keyword_candidates(build_id, eligibility_status);
CREATE INDEX IF NOT EXISTS idx_keyword_candidates_lane
    ON keyword_candidates(build_id, strategic_lane);
