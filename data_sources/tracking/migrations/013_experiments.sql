-- Phase 16: experiment ledger, costs, measurements, and action-type learning priors.

CREATE TABLE IF NOT EXISTS seo_experiments (
    experiment_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    supersedes_experiment_id TEXT,
    status TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_page TEXT NOT NULL,
    target_queries_json TEXT NOT NULL DEFAULT '[]',
    control_pages_json TEXT NOT NULL DEFAULT '[]',
    primary_metric TEXT NOT NULL,
    guardrail_metrics_json TEXT NOT NULL DEFAULT '[]',
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    owner TEXT NOT NULL,
    planned_publish_at TEXT,
    published_at TEXT,
    baseline_start TEXT,
    baseline_end TEXT,
    estimated_incremental_clicks REAL,
    estimated_cost REAL,
    actual_cost REAL NOT NULL DEFAULT 0,
    cost_currency TEXT NOT NULL,
    counterfactual_method TEXT NOT NULL DEFAULT 'sitewide_adjusted',
    content_before_hash TEXT,
    content_after_hash TEXT,
    implementation_reference TEXT,
    catalogue_version TEXT,
    source_report_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(opportunity_id, status)
);

CREATE INDEX IF NOT EXISTS idx_seo_experiments_status
    ON seo_experiments(status);
CREATE INDEX IF NOT EXISTS idx_seo_experiments_opportunity
    ON seo_experiments(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_seo_experiments_published
    ON seo_experiments(published_at);

CREATE TABLE IF NOT EXISTS experiment_changes (
    change_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    target_asset TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    evidence_reference TEXT,
    implemented_by TEXT,
    implemented_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiment_changes_experiment
    ON experiment_changes(experiment_id);

CREATE TABLE IF NOT EXISTS experiment_costs (
    cost_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    cost_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_cost REAL NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    incurred_at TEXT NOT NULL,
    evidence_reference TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiment_costs_experiment
    ON experiment_costs(experiment_id);

CREATE TABLE IF NOT EXISTS experiment_measurements (
    measurement_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    checkpoint_days INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    baseline_clicks REAL,
    observed_clicks REAL,
    expected_clicks_without_change REAL,
    adjusted_incremental_clicks REAL,
    sitewide_trend_factor REAL,
    control_trend_factor REAL,
    nonbranded_impressions REAL,
    ctr REAL,
    average_position REAL,
    organic_sessions REAL,
    engaged_sessions REAL,
    purchases REAL,
    revenue REAL,
    ai_referral_sessions REAL,
    confidence_label TEXT NOT NULL,
    outcome TEXT NOT NULL,
    adjustment_method TEXT NOT NULL,
    source_references_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    quality_status TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    UNIQUE(experiment_id, checkpoint_days)
);

CREATE INDEX IF NOT EXISTS idx_experiment_measurements_checkpoint
    ON experiment_measurements(experiment_id, checkpoint_days);

CREATE TABLE IF NOT EXISTS action_type_priors (
    action_type TEXT PRIMARY KEY,
    completed_experiments INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    inconclusive INTEGER NOT NULL DEFAULT 0,
    median_incremental_clicks REAL,
    median_cost_per_incremental_click REAL,
    empirical_confidence REAL,
    updated_at TEXT NOT NULL
);
