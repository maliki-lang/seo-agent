-- Initial tracking schema. SQLite-first; types kept portable.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    requested_collectors TEXT NOT NULL DEFAULT '[]',
    completed_collectors TEXT NOT NULL DEFAULT '[]',
    failed_collectors TEXT NOT NULL DEFAULT '{}',
    attempt_number INTEGER NOT NULL DEFAULT 1,
    parent_run_id TEXT,
    code_version TEXT,
    config_fingerprint TEXT,
    row_counts TEXT NOT NULL DEFAULT '{}',
    cost_usd TEXT NOT NULL DEFAULT '0',
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_locks (
    lock_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_state (
    run_id TEXT NOT NULL,
    collector TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    row_count INTEGER,
    cost_usd TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, collector)
);

CREATE TABLE IF NOT EXISTS keyword_catalog (
    keyword_id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL UNIQUE,
    cluster TEXT NOT NULL,
    target_page TEXT NOT NULL,
    country TEXT NOT NULL,
    device TEXT NOT NULL,
    language TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_question_catalog (
    question_id TEXT PRIMARY KEY,
    question TEXT NOT NULL UNIQUE,
    cluster TEXT NOT NULL,
    target_page TEXT NOT NULL,
    locale TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gsc_daily (
    natural_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    date TEXT NOT NULL,
    query TEXT NOT NULL,
    page TEXT NOT NULL,
    country TEXT NOT NULL,
    clicks INTEGER NOT NULL,
    impressions INTEGER NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    is_brand INTEGER NOT NULL,
    brand_rule_version TEXT NOT NULL,
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    raw_record_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (date, query, page, country)
);

CREATE TABLE IF NOT EXISTS ga4_daily (
    natural_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    date TEXT NOT NULL,
    session_source TEXT NOT NULL,
    session_medium TEXT NOT NULL,
    landing_page TEXT NOT NULL,
    channel_class TEXT NOT NULL,
    sessions INTEGER NOT NULL,
    engaged_sessions INTEGER NOT NULL,
    purchases INTEGER NOT NULL,
    total_revenue TEXT NOT NULL,
    revenue_currency TEXT NOT NULL DEFAULT 'SGD',
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    raw_record_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (date, session_source, session_medium, landing_page)
);

CREATE TABLE IF NOT EXISTS serp_daily (
    natural_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    date TEXT NOT NULL,
    keyword_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    cluster TEXT NOT NULL,
    target_page TEXT NOT NULL,
    country TEXT NOT NULL,
    device TEXT NOT NULL,
    sunnystep_position INTEGER NOT NULL,
    result_count_inspected INTEGER NOT NULL,
    top_10_domains TEXT NOT NULL,
    ai_overview_status TEXT NOT NULL,
    ai_overview_citations TEXT NOT NULL,
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    raw_record_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (date, keyword_id, country, device)
);

CREATE TABLE IF NOT EXISTS ai_answer_runs (
    natural_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    engine TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question TEXT NOT NULL,
    repetition_number INTEGER NOT NULL,
    raw_answer TEXT NOT NULL,
    mentioned_sunnystep INTEGER NOT NULL,
    cited_urls TEXT NOT NULL,
    named_competitors TEXT NOT NULL,
    target_cluster TEXT NOT NULL,
    target_page TEXT NOT NULL,
    api_cost_usd TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    model TEXT NOT NULL,
    search_enabled INTEGER NOT NULL,
    parser_version TEXT NOT NULL,
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    raw_record_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (as_of_date, engine, question_id, repetition_number)
);

CREATE TABLE IF NOT EXISTS raw_records (
    raw_record_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    endpoint_or_operation TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_type TEXT NOT NULL,
    received_at TEXT NOT NULL,
    retention_class TEXT NOT NULL,
    checksum TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_check_log (
    check_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    threshold TEXT,
    observed_value TEXT,
    details_json TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baseline (
    baseline_id TEXT NOT NULL,
    baseline_name TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT,
    status TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    segment_json TEXT NOT NULL,
    metric_value TEXT,
    numerator TEXT,
    denominator TEXT,
    source_query_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (baseline_id, metric_name, segment_json)
);

CREATE TRIGGER IF NOT EXISTS baseline_no_update_locked
BEFORE UPDATE ON baseline
FOR EACH ROW
WHEN OLD.status = 'locked'
BEGIN
    SELECT RAISE(ABORT, 'locked baseline rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS baseline_no_delete_locked
BEFORE DELETE ON baseline
FOR EACH ROW
WHEN OLD.status = 'locked'
BEGIN
    SELECT RAISE(ABORT, 'locked baseline rows are immutable');
END;

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    category TEXT NOT NULL,
    problem TEXT NOT NULL,
    supporting_evidence_json TEXT NOT NULL,
    source_row_references_json TEXT NOT NULL,
    target_query_or_question TEXT NOT NULL,
    target_page TEXT NOT NULL,
    proposed_action TEXT NOT NULL,
    owner TEXT NOT NULL,
    impact_score REAL NOT NULL,
    impact_estimate TEXT NOT NULL,
    confidence_label TEXT NOT NULL,
    confidence_value REAL NOT NULL,
    effort_label TEXT NOT NULL,
    effort_value REAL NOT NULL,
    priority_score REAL NOT NULL,
    metric_to_watch TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_reports (
    report_id TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    baseline_id TEXT,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    opportunity_count INTEGER NOT NULL,
    lark_record_id TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE (period_start, period_end)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_redacted TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    external_reference TEXT,
    UNIQUE (run_id, alert_type)
);
