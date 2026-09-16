-- Phase 8 cluster/question workflow metadata (additive).

ALTER TABLE catalogue_builds ADD COLUMN parent_build_id TEXT;
ALTER TABLE keyword_candidates ADD COLUMN cluster_id TEXT;

ALTER TABLE catalogue_clusters ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'draft';
ALTER TABLE catalogue_clusters ADD COLUMN supporting_pages_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE catalogue_clusters ADD COLUMN gsc_clicks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE catalogue_clusters ADD COLUMN gsc_impressions INTEGER NOT NULL DEFAULT 0;
ALTER TABLE catalogue_clusters ADD COLUMN ga4_sessions INTEGER;
ALTER TABLE catalogue_clusters ADD COLUMN ga4_purchases INTEGER;
ALTER TABLE catalogue_clusters ADD COLUMN ga4_revenue TEXT;

CREATE TABLE IF NOT EXISTS catalogue_comparisons (
    comparison_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    provisional_type TEXT NOT NULL,
    provisional_id TEXT NOT NULL,
    provisional_text TEXT NOT NULL,
    provisional_cluster TEXT,
    provisional_target_page TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    matched_candidate_id TEXT,
    matched_question_id TEXT,
    source_references_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE (build_id, provisional_type, provisional_id)
);

CREATE INDEX IF NOT EXISTS idx_keyword_candidates_cluster ON keyword_candidates(cluster_id);
CREATE INDEX IF NOT EXISTS idx_catalogue_comparisons_build ON catalogue_comparisons(build_id);

-- Allow provisional and evidence catalogue versions to coexist for the same text.
CREATE TABLE IF NOT EXISTS keyword_catalog_versioned (
    keyword_id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    cluster TEXT NOT NULL,
    target_page TEXT NOT NULL,
    country TEXT NOT NULL,
    device TEXT NOT NULL,
    language TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    catalogue_version TEXT NOT NULL DEFAULT 'candidate-v0.1',
    build_id TEXT,
    approval_status TEXT NOT NULL DEFAULT 'provisional',
    approved_by TEXT,
    approved_at TEXT,
    UNIQUE (catalogue_version, keyword)
);

INSERT OR IGNORE INTO keyword_catalog_versioned(
    keyword_id, keyword, cluster, target_page, country, device, language,
    active, valid_from, valid_to, created_at, updated_at,
    catalogue_version, build_id, approval_status, approved_by, approved_at
)
SELECT
    keyword_id, keyword, cluster, target_page, country, device, language,
    active, valid_from, valid_to, created_at, updated_at,
    COALESCE(catalogue_version, 'candidate-v0.1'),
    build_id,
    COALESCE(approval_status, 'provisional'),
    approved_by,
    approved_at
FROM keyword_catalog;

DROP TABLE keyword_catalog;
ALTER TABLE keyword_catalog_versioned RENAME TO keyword_catalog;

CREATE TABLE IF NOT EXISTS ai_question_catalog_versioned (
    question_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    cluster TEXT NOT NULL,
    target_page TEXT NOT NULL,
    locale TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    catalogue_version TEXT NOT NULL DEFAULT 'candidate-v0.1',
    build_id TEXT,
    approval_status TEXT NOT NULL DEFAULT 'provisional',
    approved_by TEXT,
    approved_at TEXT,
    UNIQUE (catalogue_version, question)
);

INSERT OR IGNORE INTO ai_question_catalog_versioned(
    question_id, question, cluster, target_page, locale, active,
    valid_from, valid_to, created_at, updated_at,
    catalogue_version, build_id, approval_status, approved_by, approved_at
)
SELECT
    question_id, question, cluster, target_page, locale, active,
    valid_from, valid_to, created_at, updated_at,
    COALESCE(catalogue_version, 'candidate-v0.1'),
    build_id,
    COALESCE(approval_status, 'provisional'),
    approved_by,
    approved_at
FROM ai_question_catalog;

DROP TABLE ai_question_catalog;
ALTER TABLE ai_question_catalog_versioned RENAME TO ai_question_catalog;
