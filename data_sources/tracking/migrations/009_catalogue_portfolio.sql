-- Phase 13: portfolio slot + review group for selection v2 exports.

ALTER TABLE keyword_candidates ADD COLUMN portfolio_slot INTEGER;
ALTER TABLE keyword_candidates ADD COLUMN review_group TEXT;

CREATE INDEX IF NOT EXISTS idx_keyword_candidates_portfolio
    ON keyword_candidates(build_id, portfolio_slot);
CREATE INDEX IF NOT EXISTS idx_keyword_candidates_review_group
    ON keyword_candidates(build_id, review_group);
