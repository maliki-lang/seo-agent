# Agent Contracts

Generated from `data_sources/modules/eval/contracts.py` (Control Tower Layer 1). Every agent that produces or changes a deliverable declares its contract here.

## seo-writer

- **Kpi:** Non-branded organic clicks + target-cluster rank movement for the published article
- **Source Of Truth:** GSC + GA4 (via ledger baseline); target keyword from the brief
- **Trigger:** /write or /article command
- **Allowed Tools:** Read, Write, Bash, research modules
- **Output Audience:** SunnyStep readers (women 35–55, SG) + the editor/critic
- **Escalation:** Gate < 70 -> regenerate with critic feedback; < 50 or hard-fail -> human owner
- **Safety Boundary:** YMYL compliance gate must pass; no medical claims without citation + disclaimer
- **Kill Criteria:** If drafts repeatedly hard-fail compliance, disable auto-write for that cluster

## critic

- **Kpi:** Catch generic/unsupported/non-compliant drafts the deterministic graders miss
- **Source Of Truth:** Draft content + deterministic scorecard + brand/SEO context files
- **Trigger:** After deterministic draft eval, before publish
- **Allowed Tools:** Read, Bash
- **Output Audience:** The eval runner (machine) + human owner on block
- **Escalation:** hard_fail -> block; otherwise lowers dimension scores
- **Safety Boundary:** Can only lower scores, never raise; independent of the writer
- **Kill Criteria:** If critic scores never correlate with outcomes (see calibration), recalibrate or replace

## seo-optimizer

- **Kpi:** On-page SEO quality (SEOQualityRater overall_score) without keyword stuffing
- **Source Of Truth:** Draft content + context/seo-guidelines.md
- **Trigger:** /optimize, or auto after /write
- **Allowed Tools:** Read, Write, Bash
- **Output Audience:** The draft pipeline
- **Escalation:** Stuffing or no heading structure -> block; < 70 -> regenerate
- **Safety Boundary:** Never inflate keyword density past guideline max
- **Kill Criteria:** n/a

## publisher

- **Kpi:** Content reaches the live Shopify site with correct SEO metadata, zero ranking regressions
- **Source Of Truth:** Shopify Admin API; ledger for baseline capture
- **Trigger:** /publish-draft
- **Allowed Tools:** Bash, shopify_publisher
- **Output Audience:** Live site readers + GSC
- **Escalation:** Pre-publish gate must be ship-ready (>=85) and compliance-clean; else block
- **Safety Boundary:** Publishes as Hidden/draft first; never auto-publishes YMYL without passing gate
- **Kill Criteria:** If post-publish tracking shows ranking losses, halt and audit

## opportunity-scorer

- **Kpi:** Prioritize topics that map to a target cluster + stage 1–3 and move the north-star
- **Source Of Truth:** DataForSEO + GSC + learned_weights
- **Trigger:** /priorities, research commands
- **Allowed Tools:** Bash, research modules
- **Output Audience:** Content planner / human owner
- **Escalation:** Topic with no cluster/stage -> owner review (band at_risk)
- **Safety Boundary:** n/a
- **Kill Criteria:** n/a

