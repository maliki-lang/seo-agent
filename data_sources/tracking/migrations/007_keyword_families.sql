-- Phase 11: keyword families persistence + target/GA4 actionability fields (additive).

CREATE TABLE IF NOT EXISTS keyword_families (
    family_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    family_key TEXT NOT NULL,
    family_label TEXT NOT NULL,
    primary_candidate_id TEXT,
    routing_bucket TEXT,
    search_intent TEXT,
    strategic_lane TEXT,
    member_candidate_ids_json TEXT NOT NULL DEFAULT '[]',
    member_count INTEGER NOT NULL DEFAULT 0,
    family_gsc_clicks INTEGER NOT NULL DEFAULT 0,
    family_gsc_impressions INTEGER NOT NULL DEFAULT 0,
    family_weighted_ctr REAL,
    family_weighted_position REAL,
    primary_target_page TEXT,
    family_method TEXT NOT NULL,
    family_confidence REAL,
    approval_status TEXT NOT NULL DEFAULT 'draft',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (build_id, family_key)
);

CREATE INDEX IF NOT EXISTS idx_keyword_families_build ON keyword_families(build_id);
CREATE INDEX IF NOT EXISTS idx_keyword_families_primary ON keyword_families(primary_candidate_id);

ALTER TABLE keyword_candidates ADD COLUMN proposed_action TEXT;
ALTER TABLE keyword_candidates ADD COLUMN ga4_confidence_multiplier REAL;
ALTER TABLE keyword_candidates ADD COLUMN multi_page_class TEXT;
