-- Phase 14: auditable LLM assessments + evidence-backed AI-question fields.

CREATE TABLE IF NOT EXISTS llm_assessments (
    assessment_id TEXT PRIMARY KEY,
    assessment_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    build_id TEXT,
    prompt_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    input_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    redacted_input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT,
    validation_status TEXT NOT NULL,
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_assessments_idempotent
    ON llm_assessments(assessment_type, subject_type, subject_id, prompt_version, input_fingerprint);

CREATE INDEX IF NOT EXISTS idx_llm_assessments_build
    ON llm_assessments(build_id, assessment_type);

CREATE INDEX IF NOT EXISTS idx_llm_assessments_subject
    ON llm_assessments(subject_type, subject_id);

ALTER TABLE ai_question_candidates ADD COLUMN source_type TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN source_reference TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN source_evidence_refs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE ai_question_candidates ADD COLUMN persona TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN situation TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN decision_to_make TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE ai_question_candidates ADD COLUMN expected_answer_elements_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE ai_question_candidates ADD COLUMN naturalness_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE ai_question_candidates ADD COLUMN distinctness_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE ai_question_candidates ADD COLUMN safety_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE ai_question_candidates ADD COLUMN pilot_status TEXT NOT NULL DEFAULT 'not_run';
ALTER TABLE ai_question_candidates ADD COLUMN pilot_results_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ai_question_candidates ADD COLUMN pilot_override_by TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN pilot_override_at TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN pilot_override_reason TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN llm_assessment_id TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN review_stage TEXT NOT NULL DEFAULT 'evidence_ready';
ALTER TABLE ai_question_candidates ADD COLUMN family_bucket TEXT;
ALTER TABLE ai_question_candidates ADD COLUMN human_validated_hypothesis INTEGER NOT NULL DEFAULT 0;

ALTER TABLE keyword_candidates ADD COLUMN llm_assessment_id TEXT;
ALTER TABLE keyword_candidates ADD COLUMN llm_review_stage TEXT;

CREATE INDEX IF NOT EXISTS idx_ai_question_source_type
    ON ai_question_candidates(build_id, source_type);
CREATE INDEX IF NOT EXISTS idx_ai_question_pilot
    ON ai_question_candidates(build_id, pilot_status);
CREATE INDEX IF NOT EXISTS idx_ai_question_review_stage
    ON ai_question_candidates(build_id, review_stage);
