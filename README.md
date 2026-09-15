# Sunnystep SEO Agent

A Claude Code workspace that researches, writes, evaluates, and publishes SEO blog
content for **Sunnystep** — a Singapore comfort-footwear brand (women 35–55).

It pairs a content pipeline (research → write → optimize → publish → track) with a
**code-enforced evaluation layer** so every step is gated for quality, safety, and
strategic fit — not just vibes.

> **Start here:** [`docs/seo-agent-strategy-and-eval-plan.md`](docs/seo-agent-strategy-and-eval-plan.md)
> — the concrete goal, strategy, clusters, cadence, and the full eval design.
> Agent contracts: [`docs/agent-contracts.md`](docs/agent-contracts.md).
> Working guide for Claude: [`CLAUDE.md`](CLAUDE.md).

## The goal

Grow Sunnystep's **non-branded organic clicks** and rank target clusters onto
Google SG page 1 — moving from "a brand people search by name" to **owning the
category**. We optimize non-branded clicks + cluster rankings (guardrailed by
cluster-level conversions), not total pageviews.

## Setup

```bash
pip install -r data_sources/requirements.txt
```

Credentials live in `data_sources/config/.env` (GA4, GSC, Serper, Shopify); the
GA4/GSC service-account key in `credentials/ga4-credentials.json`. See
[`docs/data-sources-setup.md`](docs/data-sources-setup.md).

## How it works

```
research → write → optimize → publish → track
  │          │        │          │        │
  keyword   draft    on-page   pre-pub   rank/click
  + brief   + critic  SEO       gate      checkpoints
  gates     gate                          → calibration
```

- **Commands** (`.claude/commands/`) orchestrate workflows; **agents**
  (`.claude/agents/`) are specialized roles (writer, critic, SEO optimizer, …).
- **Eval layer** (`data_sources/modules/eval/`) gates every step: a 9-dimension
  scorecard, a zero-tolerance YMYL compliance gate (foot-health content), an
  independent critic, research-step keyword/brief gates, regression fixtures, and
  score-vs-outcome calibration. **These are enforced code, not checklists.**
- **Tracking** (`data_sources/ledger/`) logs every publish + rank checkpoint and
  feeds the prioritization loop.

## Key commands

| Command | Does |
|---|---|
| `/research [topic]` | Keyword + competitor research → brief (+ keyword & brief eval gates) |
| `/write [topic]` | Draft a full article → draft eval gate → critic |
| `/optimize [file]` | On-page SEO polish |
| `/publish-draft [file]` | Publish to Shopify (runs the pre-publish gate first) |
| `/priorities`, `/cluster`, `/performance-review` | Planning & analysis |

## Layout

```
.claude/            commands, agents, skills
context/            Sunnystep brand guidelines (voice, SEO, competitors, links)
data_sources/       modules (analytics, scorers, eval/, publishers), ledger, config
scripts/            manual research & SEO analysis scripts (run from repo root)
docs/               strategy, eval plan, agent contracts, setup
drafts/ research/ published/ rewrites/ review-required/ topics/   content pipeline
tests/              unit tests
```
Operational scripts (`daily_sync.py`, `track_published.py`, `backfill_ledger.py`,
`post_migration_audit.py`) live at the repo root as they are cron/launchd-wired.

## Tracking dashboard

The human-facing cockpit is a Lark Base (Clusters · Keywords · Articles ·
Performance) synced from the repo. See `docs/seo-agent-strategy-and-eval-plan.md`.
