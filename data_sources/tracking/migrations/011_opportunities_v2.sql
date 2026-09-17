-- Phase 15: action-centric opportunity intelligence (additive on opportunities).

ALTER TABLE opportunities ADD COLUMN opportunity_version TEXT NOT NULL DEFAULT 'v1';
ALTER TABLE opportunities ADD COLUMN source_type TEXT;
ALTER TABLE opportunities ADD COLUMN cluster_id TEXT;
ALTER TABLE opportunities ADD COLUMN family_id TEXT;
ALTER TABLE opportunities ADD COLUMN benchmark_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE opportunities ADD COLUMN action_type TEXT;
ALTER TABLE opportunities ADD COLUMN target_asset TEXT;
ALTER TABLE opportunities ADD COLUMN target_page_status TEXT;
ALTER TABLE opportunities ADD COLUMN expected_incremental_clicks REAL;
ALTER TABLE opportunities ADD COLUMN expected_geo_gain REAL;
ALTER TABLE opportunities ADD COLUMN estimated_cost REAL;
ALTER TABLE opportunities ADD COLUMN cost_currency TEXT;
ALTER TABLE opportunities ADD COLUMN measurement_window_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE opportunities ADD COLUMN assumptions_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE opportunities ADD COLUMN llm_assessment_id TEXT;
ALTER TABLE opportunities ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE opportunities ADD COLUMN reviewed_by TEXT;
ALTER TABLE opportunities ADD COLUMN reviewed_at TEXT;
ALTER TABLE opportunities ADD COLUMN updated_at TEXT;
ALTER TABLE opportunities ADD COLUMN portfolio_rank INTEGER;
ALTER TABLE opportunities ADD COLUMN priority_inputs_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE opportunities ADD COLUMN merge_group_id TEXT;
ALTER TABLE opportunities ADD COLUMN blocked_reason TEXT;
ALTER TABLE opportunities ADD COLUMN catalogue_version TEXT;
ALTER TABLE opportunities ADD COLUMN selection_report_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_opportunities_report_version
    ON opportunities(report_id, opportunity_version);
CREATE INDEX IF NOT EXISTS idx_opportunities_review
    ON opportunities(report_id, review_status);
CREATE INDEX IF NOT EXISTS idx_opportunities_action
    ON opportunities(report_id, action_type);
