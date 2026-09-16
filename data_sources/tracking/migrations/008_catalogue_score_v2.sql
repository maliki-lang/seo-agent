-- Phase 12: selection score v2 + Serper preselection flags (additive).

ALTER TABLE keyword_candidates ADD COLUMN gsc_protection_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN gsc_opportunity_score_v2 REAL;
ALTER TABLE keyword_candidates ADD COLUMN product_need_relevance_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN serp_visibility_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN serp_target_alignment_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN serp_validation_confidence REAL;
ALTER TABLE keyword_candidates ADD COLUMN serp_feasibility_score REAL;
ALTER TABLE keyword_candidates ADD COLUMN available_evidence_weight REAL;
ALTER TABLE keyword_candidates ADD COLUMN missing_evidence_fields_json TEXT;
ALTER TABLE keyword_candidates ADD COLUMN score_confidence REAL;
ALTER TABLE keyword_candidates ADD COLUMN serp_preselected INTEGER NOT NULL DEFAULT 0;
ALTER TABLE keyword_candidates ADD COLUMN serp_preselect_rank INTEGER;

CREATE INDEX IF NOT EXISTS idx_keyword_candidates_serp_preselected
    ON keyword_candidates(build_id, serp_preselected);
