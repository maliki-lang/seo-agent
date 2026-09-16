-- Phase 6 catalogue provenance schema (additive). SQLite-first; types kept portable.

-- Label existing production catalogue rows as provisional candidate-v0.1.
ALTER TABLE keyword_catalog ADD COLUMN catalogue_version TEXT NOT NULL DEFAULT 'candidate-v0.1';
ALTER TABLE keyword_catalog ADD COLUMN build_id TEXT;
ALTER TABLE keyword_catalog ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'provisional';
ALTER TABLE keyword_catalog ADD COLUMN approved_by TEXT;
ALTER TABLE keyword_catalog ADD COLUMN approved_at TEXT;

ALTER TABLE ai_question_catalog ADD COLUMN catalogue_version TEXT NOT NULL DEFAULT 'candidate-v0.1';
ALTER TABLE ai_question_catalog ADD COLUMN build_id TEXT;
ALTER TABLE ai_question_catalog ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'provisional';
ALTER TABLE ai_question_catalog ADD COLUMN approved_by TEXT;
ALTER TABLE ai_question_catalog ADD COLUMN approved_at TEXT;

CREATE TABLE IF NOT EXISTS catalogue_builds (
    build_id TEXT PRIMARY KEY,
    build_type TEXT NOT NULL,
    status TEXT NOT NULL,
    source_window_start TEXT NOT NULL,
    source_window_end TEXT NOT NULL,
    gsc_source_run_ids TEXT NOT NULL DEFAULT '[]',
    ga4_source_run_ids TEXT NOT NULL DEFAULT '[]',
    serper_source_run_ids TEXT NOT NULL DEFAULT '[]',
    source_fingerprint TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    activated_at TEXT,
    notes TEXT,
    funnel_json TEXT NOT NULL DEFAULT '{}',
    ga4_window_start TEXT,
    ga4_window_end TEXT
);

CREATE TABLE IF NOT EXISTS keyword_candidates (
    candidate_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    canonical_keyword TEXT NOT NULL,
    normalized_keyword TEXT NOT NULL,
    brand_status TEXT NOT NULL,
    brand_rule_version TEXT NOT NULL,
    primary_observed_page TEXT NOT NULL,
    observed_pages_json TEXT NOT NULL,
    multi_page_competition INTEGER NOT NULL DEFAULT 0,
    source_query_count INTEGER NOT NULL,
    source_row_count INTEGER NOT NULL,
    source_date_count INTEGER NOT NULL,
    gsc_clicks INTEGER NOT NULL,
    gsc_impressions INTEGER NOT NULL,
    gsc_weighted_ctr REAL,
    gsc_weighted_position REAL,
    ga4_organic_sessions INTEGER,
    ga4_engaged_sessions INTEGER,
    ga4_purchases INTEGER,
    ga4_revenue TEXT,
    ga4_conversion_rate REAL,
    serper_position INTEGER,
    serper_ranking_url TEXT,
    serper_top_10_domains TEXT,
    serper_intent TEXT,
    business_relevance_score REAL,
    gsc_opportunity_score REAL,
    ga4_value_score REAL,
    serper_validation_score REAL,
    evidence_confidence_score REAL,
    final_selection_score REAL,
    decision TEXT NOT NULL,
    decision_reason TEXT,
    proposed_target_page TEXT,
    reviewed_target_page TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (build_id, normalized_keyword)
);

CREATE TABLE IF NOT EXISTS keyword_candidate_sources (
    candidate_source_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    gsc_natural_key TEXT NOT NULL,
    gsc_run_id TEXT NOT NULL,
    raw_query TEXT NOT NULL,
    raw_page TEXT NOT NULL,
    clicks INTEGER NOT NULL,
    impressions INTEGER NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    row_date TEXT NOT NULL,
    transformation_method TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (candidate_id, gsc_natural_key)
);

CREATE TABLE IF NOT EXISTS catalogue_clusters (
    cluster_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    cluster_name TEXT NOT NULL,
    primary_intent TEXT NOT NULL,
    primary_target_page TEXT NOT NULL,
    member_candidate_ids TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL,
    method TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_question_candidates (
    question_candidate_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    question TEXT NOT NULL,
    cluster_id TEXT,
    intent TEXT NOT NULL,
    proposed_target_page TEXT NOT NULL,
    transformation_method TEXT NOT NULL,
    source_keyword_ids TEXT NOT NULL DEFAULT '[]',
    source_candidate_ids TEXT NOT NULL DEFAULT '[]',
    decision TEXT NOT NULL,
    decision_reason TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_question_sources (
    question_source_id TEXT PRIMARY KEY,
    question_candidate_id TEXT NOT NULL,
    keyword_id TEXT,
    candidate_id TEXT NOT NULL,
    gsc_natural_key TEXT NOT NULL,
    gsc_run_id TEXT NOT NULL,
    raw_gsc_query TEXT NOT NULL,
    relationship TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_keyword_candidates_build ON keyword_candidates(build_id);
CREATE INDEX IF NOT EXISTS idx_keyword_candidate_sources_candidate ON keyword_candidate_sources(candidate_id);
CREATE INDEX IF NOT EXISTS idx_catalogue_builds_type_status ON catalogue_builds(build_type, status);
