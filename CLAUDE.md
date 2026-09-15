# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **Sunnystep's SEO agent** — a Claude Code workspace for researching, writing, evaluating, and publishing SEO blog content for Sunnystep, a Singapore comfort-footwear brand (women 35–55). It combines custom commands, specialized agents, Python analytics, and a code-enforced evaluation layer ("Agent Performance Control Tower"). The strategy and concrete goal live in `docs/seo-agent-strategy-and-eval-plan.md`.

## Setup

```bash
pip install -r data_sources/requirements.txt
```

API credentials are configured in `data_sources/config/.env` (GA4, GSC, Serper, Shopify). GA4/GSC service account credentials go in `credentials/ga4-credentials.json`.

## Commands

All commands are defined in `.claude/commands/` and invoked as slash commands:

- `/research [topic]` - Keyword/competitor research, generates brief in `research/`
- `/write [topic]` - Create full article in `drafts/`, auto-triggers optimization agents
- `/rewrite [topic]` - Update existing content, saves to `rewrites/`
- `/optimize [file]` - Final SEO polish pass
- `/analyze-existing [URL or file]` - Content health audit
- `/performance-review` - Analytics-driven content priorities
- `/submit-for-review [file]` - Send a ship-ready draft to the manager's Lark review group; runs the revise→re-eval→resubmit loop against the reviewer agent's verdict until approved (max 4 rounds, then escalates)
- `/publish-draft [file]` - Publish to Shopify (runs the pre-publish eval gate first)
- `/article [topic]` - Simplified article creation
- `/cluster [topic]` - Build complete topic cluster strategy with pillar + supporting articles + linking map
- `/priorities` - Content prioritization matrix
- `/research-serp`, `/research-gaps`, `/research-trending`, `/research-performance`, `/research-topics` - Specialized research commands
- `/research-ai-citations [topic]` - AI citation audit: generates prompts, clusters them, audits which sources AI cites
- `/repurpose [file]` - Adapts article for LinkedIn, Medium, Reddit, Quora distribution
- `/landing-write`, `/landing-audit`, `/landing-research`, `/landing-publish`, `/landing-competitor` - Landing page commands

## Architecture

### Command-Agent Model

**Commands** (`.claude/commands/`) orchestrate workflows. **Agents** (`.claude/agents/`) are specialized roles invoked by commands. After `/write`, these agents auto-run: SEO Optimizer, Meta Creator, Internal Linker, Keyword Mapper.

Key agents: `content-analyzer.md`, `seo-optimizer.md`, `meta-creator.md`, `internal-linker.md`, `keyword-mapper.md`, `editor.md`, `headline-generator.md`, `cro-analyst.md`, `performance.md`, `cluster-strategist.md`.

### Python Analysis Pipeline

Located in `data_sources/modules/`. The Content Analyzer chains:
1. `search_intent_analyzer.py` - Query intent classification
2. `keyword_analyzer.py` - Density, distribution, stuffing detection
3. `content_length_comparator.py` - Benchmarks against top 10 SERP results
4. `readability_scorer.py` - Flesch Reading Ease, grade level
5. `seo_quality_rater.py` - Comprehensive 0-100 SEO score

### Data Integrations

- `google_analytics.py` - GA4 traffic/engagement data
- `google_search_console.py` - Rankings and impressions
- `dataforseo.py` - SERP positions + related questions (Serper-backed; no volumes)
- `data_aggregator.py` - Combines all sources into unified analytics
- `shopify_publisher.py` - Publishes to Shopify (gosunnystep.myshopify.com) with SEO metafields; `shopline_publisher.py` / `wordpress_publisher.py` are legacy

### Opportunity Scoring

`opportunity_scorer.py` uses 8 weighted factors: Volume (25%), Position (20%), Intent (20%), Competition (15%), Cluster (10%), CTR (5%), Freshness (5%), Trend (5%).

## Running Python Scripts

```bash
# Research & analysis scripts (run from repo root)
python3 scripts/research_quick_wins.py
python3 scripts/research_competitor_gaps.py
python3 scripts/research_performance_matrix.py
python3 scripts/research_priorities_comprehensive.py
python3 scripts/research_serp_analysis.py
python3 scripts/research_topic_clusters.py
python3 scripts/research_trending.py
python3 scripts/seo_baseline_analysis.py
python3 scripts/seo_bofu_rankings.py
python3 scripts/seo_competitor_analysis.py

# Test API connectivity
python3 scripts/test_dataforseo.py
```

## Content Pipeline

`topics/` (ideas) → `research/` (briefs) → `drafts/` (articles) → `review-required/` (pending review) → `published/` (final)

Rewrites go to `rewrites/`. Landing pages go to `landing-pages/`. Audits go to `audits/`. Repurposed content goes to `repurposed/`.

## Context Files

`context/` contains brand guidelines that inform all content generation:
- `brand-voice.md` - Tone, messaging pillars
- `style-guide.md` - Grammar, formatting standards
- `seo-guidelines.md` - Keyword and structure rules
- `internal-links-map.md` - Key pages for internal linking
- `features.md` - Product features
- `competitor-analysis.md` - Competitive intelligence
- `cro-best-practices.md` - Conversion optimization guidelines
- `ai-citation-targets.md` - Directories/platforms where your brand should be cited by AI tools
- `reddit-strategy.md` - Reddit engagement strategy for AI SEO and community visibility

## Evaluation — Agent Performance Control Tower

Every pipeline step is gated by an evaluation layer in `data_sources/modules/eval/`,
built to the company standard (Lark "05 Evaluation & Quality"). The concrete goal
and full design are in `docs/seo-agent-strategy-and-eval-plan.md`; agent contracts
are in `docs/agent-contracts.md`. **Do not treat these as checklists — they are
code-enforced gates.**

- `scorecard.py` — the 9-dimension, 0–100 company scorecard (weights + thresholds:
  85 ship / 70 usable / 50 at-risk / <50 block). Persists every run to
  `data_sources/ledger/eval_log.jsonl`.
- `graders.py` — deterministic graders that map the existing scorers
  (ContentScorer, SEOQualityRater, ReadabilityScorer) onto the 9 dimensions.
  Brand word-count standard is ~800 words.
- `compliance.py` — **zero-tolerance YMYL gate** for foot-condition content
  (plantar fasciitis, arch support, flat feet): every health claim needs a
  citation, a disclaimer must be present, forbidden absolutes ("cures",
  "FDA-approved") hard-fail.
- `gates.py` + `eval_runner.py` — turn a scorecard into an enforced decision per
  step. Run: `python3 -m data_sources.modules.eval.eval_runner <draft.md> --step draft`
  (exit `0` ship · `2` regenerate · `3` block).
- **Research-step gates** (catch "writing the wrong thing", before drafting):
  `keyword_grader.py` grades a keyword pick (intent + opportunity + hard filters:
  branded/navigational/no-cluster reject; medical-orthotic shopping queries
  downranked) → Grade A–D; `brief_gate.py` gates brief completeness (sources
  mandatory for YMYL). Both persist to `eval_log.jsonl` and feed the Base.
- `.claude/agents/critic.md` — the **separate** LLM-judge that can only lower the
  deterministic score (never self-grade). Pass its JSON via `--critic`.
- `regression.py` — failures become permanent fixtures; replay with
  `python3 -m data_sources.modules.eval.regression run`.
- `calibration.py` — checks whether publish-time scores actually predict
  post-publish rank/click outcomes (is the score any good?).
- `dashboard.py` — local-first summary + CSV for the Lark Base health dashboard.
- `aio_schema.py` — AIO/answer-engine readiness + FAQ JSON-LD (consultant Phase 3).

North-star: grow **non-branded organic clicks + target-cluster rankings** (GSC),
guardrailed by cluster-level conversions — not total pageviews.

## External review loop (manager's Lark group)

After a draft clears our internal eval gate, `/submit-for-review` posts it (as the
bot) to the manager's Lark review group, where a separate reviewer AI agent scores
it on **its own** system and replies in free text. `data_sources/modules/review_loop.py`
owns the deterministic parts — Lark transport via `lark-cli`, a tolerant
free-text verdict parser (`parse_review_verdict`, never treats ambiguity as
approval), a bounded background poller (`wait_for_reply`), and an append-only
round ledger (`data_sources/ledger/review_rounds.jsonl`). The revision each round
is an LLM step in the command, and the draft must re-pass our internal gate before
each resubmission. Hard cap: 4 rounds / ~30-min waits, then escalate to a human.
Config: `LARK_REVIEW_CHAT_ID` (+ optional `LARK_REVIEWER_OPEN_ID`) in `.env`; the
bot must be a member of the group. Both scores are logged per round so calibration
can check whether our internal score predicts the manager agent's verdict.

## Publishing

The site is on **Shopify** (migrated from Shopline). Publish with
`data_sources/modules/shopify_publisher.py` (via `/publish-draft`), which runs the
pre-publish eval gate before anything reaches the store and records a T-0 baseline
to the ledger. Articles are created **unpublished**. `post_migration_audit.py`
verifies redirects/metadata/schema/indexing carried over. The legacy
`shopline_publisher.py` and the WordPress REST integration
(`wordpress/seo-machine-yoast-rest.php`, Yoast fields) are retained for reference.
