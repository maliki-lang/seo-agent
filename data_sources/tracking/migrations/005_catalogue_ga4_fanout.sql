-- Catalogue GA4 join provenance: which page metrics came from, and whether shared.

ALTER TABLE keyword_candidates ADD COLUMN ga4_match_page TEXT;
ALTER TABLE keyword_candidates ADD COLUMN ga4_shared_page INTEGER;
