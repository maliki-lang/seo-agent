-- Phase 7 catalogue enrichment metadata (additive).

ALTER TABLE keyword_candidates ADD COLUMN page_type TEXT;
ALTER TABLE keyword_candidates ADD COLUMN ga4_match_status TEXT;
ALTER TABLE keyword_candidates ADD COLUMN ga4_engagement_rate REAL;
ALTER TABLE keyword_candidates ADD COLUMN serper_run_id TEXT;
ALTER TABLE keyword_candidates ADD COLUMN serper_collected_at TEXT;
ALTER TABLE keyword_candidates ADD COLUMN proposed_target_ranks INTEGER;
ALTER TABLE keyword_candidates ADD COLUMN serper_ai_overview_status TEXT;
ALTER TABLE keyword_candidates ADD COLUMN serper_result_types_json TEXT;

ALTER TABLE catalogue_builds ADD COLUMN ga4_match_report_json TEXT;
ALTER TABLE catalogue_builds ADD COLUMN serper_validation_report_json TEXT;
