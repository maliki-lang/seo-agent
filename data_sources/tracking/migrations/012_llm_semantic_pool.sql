-- Phase 14b: mid-funnel LLM semantic fields on keyword candidates.

ALTER TABLE keyword_candidates ADD COLUMN llm_customer_need TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_intent TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_business_relevance TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_business_relevance_rationale TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_family_key TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_is_representative INTEGER;
ALTER TABLE keyword_candidates ADD COLUMN llm_semantic_duplicates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE keyword_candidates ADD COLUMN llm_recommended_target_page TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_no_suitable_target INTEGER NOT NULL DEFAULT 0;
ALTER TABLE keyword_candidates ADD COLUMN llm_actionability TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_actionability_rationale TEXT;
ALTER TABLE keyword_candidates ADD COLUMN semantic_authority TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_pool_assessment_id TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_semantic_confidence TEXT;

CREATE INDEX IF NOT EXISTS idx_keyword_candidates_semantic_authority
    ON keyword_candidates(build_id, semantic_authority);
CREATE INDEX IF NOT EXISTS idx_keyword_candidates_llm_family
    ON keyword_candidates(build_id, llm_family_key);
