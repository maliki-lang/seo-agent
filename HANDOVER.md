# SEO Agent — Handover (Quynh → Maliki)

_Last updated: 14 Sep 2026_

This is everything needed to run the Sunnystep SEO agent without asking Quynh.
Read in this order: **this file → [README.md](README.md) → [CLAUDE.md](CLAUDE.md) →
[docs/seo-agent-strategy-and-eval-plan.md](docs/seo-agent-strategy-and-eval-plan.md) →
[docs/agent-contracts.md](docs/agent-contracts.md)**.

---

## 1. What this is (30 seconds)

A Claude Code workspace that researches, writes, quality-checks, and publishes SEO
blog content for sunnystep.com (Shopify). Every step is gated by code
(`data_sources/modules/eval/`): a 0–100 scorecard, a separate critic agent, and a
zero-tolerance medical-claims gate for foot-health topics.

- **Goal:** grow non-branded organic clicks and rank the 4 target clusters on Google SG page 1.
- **Project brief (current):** https://zlsbwg9eee.sg.larksuite.com/docx/Mw1JdqcNZoJjfxxYxujlrUEWguh
- **Original brief (Ting / Min Wei version):** https://zlsbwg9eee.sg.larksuite.com/docx/IDOVdYQhxodZYxxe1z3l0V2EgDb

---

## 2. Access checklist

Ask Quynh (or the listed owner) for each. **Secrets are shared privately (Lark DM / password manager), never committed.**

| # | Access | Used for | Status |
|---|---|---|---|
| 1 | GitHub `quynhsunnystep/seo-agent` (private) — collaborator | Code | ☐ |
| 2 | `data_sources/config/.env` values (see `.env.example`) | All APIs | ☐ |
| 3 | `credentials/ga4-credentials.json` (`ga4-reader@ga4-lark-connector…`) **and** `credentials/gsc-service-account.json` (`gsc-puller@sunnystep-gsc…`, Full user on `sc-domain:sunnystep.com`) | GA4 · Search Console | ☐ |
| 4 | GA4 property + Search Console for sunnystep.com (viewer) | Checking data by hand | ☐ |
| 5 | Shopify admin (gosunnystep.myshopify.com) | Reviewing / publishing drafts | ☐ |
| 6 | Serper.dev API key | Google SERP data | ☐ |
| 7 | Lark: SEO Control Tower Base (edit) | Tracking dashboard | ☐ |
| 8 | Lark: manager's review group (bot must be a member) | `/submit-for-review` | ☐ |
| 9 | Lark: Ting's Agents Control Plane Base (edit) | Daily sync | ☐ |
| 10 | Claude Code + `lark-cli` installed and logged in | Running the agent | ☐ |

---

## 3. Setup (≈30 min)

```bash
git clone https://github.com/quynhsunnystep/seo-agent.git seomachine
cd seomachine

python3 -m venv .venv && source .venv/bin/activate
pip install -r data_sources/requirements.txt pytest

cp .env.example data_sources/config/.env      # then paste the real values
mkdir -p credentials                            # put ga4-credentials.json + gsc-service-account.json here

lark-cli auth login --domain base               # Lark Base access (user)
lark-cli auth login --domain docs,wiki          # Lark docs (user)
```

Open the folder in Claude Code — the slash commands in `.claude/commands/` load automatically.

### Smoke tests (all should pass on day 1)

```bash
# 1. Unit tests — expect 14 passed, 3 failed (known, see §7)
python -m pytest -q tests

# 2. Eval regression suite — expect "1/1 passed"
python -m data_sources.modules.eval.regression run

# 3. Quality gate on the flagship draft — expect BLOCK (missing medical disclaimer + 1 uncited claim)
python -m data_sources.modules.eval.eval_runner drafts/comfortable-shoes-singapore.md --step draft --no-persist

# 4. Keyword grader
python -m data_sources.modules.eval.keyword_grader "comfortable shoes singapore" --cluster "comfortable shoes (discovery)" --volume 140 --no-persist

# 5. Serper (live Google SG results)
python scripts/test_dataforseo.py
```

If 1–4 behave as described, the install is correct.

---

## 4. How the pipeline runs

```
/research <keyword>          → research/   (keyword grader + brief gate)
/write <topic>               → drafts/     (draft gate + critic agent)
/optimize drafts/<file>      → final SEO polish
/submit-for-review <file>    → manager's Lark group, revise loop (max 4 rounds)
/publish-draft <file>        → Shopify, created UNPUBLISHED (pre-publish gate)
python track_published.py    → rank/click checkpoints at +14/30/60/90 days
```

Gate exit codes (`eval_runner`): `0` ship · `2` regenerate · `3` block.
Thresholds: 85+ ship · 70–84 usable · 50–69 at risk · <50 block.
**Never auto-publish foot-health (YMYL) content — a human always publishes.**

---

## 5. Where state lives

| What | Where |
|---|---|
| Strategy, clusters, cadence | `docs/seo-agent-strategy-and-eval-plan.md` |
| Brand voice, style, features, competitors | `context/` |
| Research briefs / drafts / published | `research/` · `drafts/` · `published/` (all committed) |
| Eval scores (every run) | `data_sources/ledger/eval_log.jsonl` |
| Publish ledger + checkpoints | `data_sources/ledger/published.jsonl`, `checkpoints.jsonl` |
| Failure fixtures | `data_sources/modules/eval/regression/fixtures/` |
| **SEO Control Tower (Lark Base)** | https://zlsbwg9eee.sg.larksuite.com/base/RQvLbkxBmaJSE9s8CumluZAQgae |

Lark Base tables (token `RQvLbkxBmaJSE9s8CumluZAQgae`):

| Table | ID | Purpose |
|---|---|---|
| Clusters | `tblq04mv4Iw7mLSa` | 4 target clusters + pillar (source of truth) |
| Keywords | `tbl9q7DtHPxv02Ow` | 20 graded keywords (A–D), status Backlog → Published |
| Articles | `tbl0yQwQHta7hz8n` | Pipeline + eval score, compliance, Shopify ID |
| Performance | `tblpHmxGdHsnRLKB` | Rank/click checkpoints |

Company eval standard the agent is built to: Lark wiki "05 Evaluation & Quality" —
https://zlsbwg9eee.sg.larksuite.com/wiki/BeQzwN405ifogskyrBWl2v9tgDh

---

## 6. Scheduled jobs

| Job | What | Status |
|---|---|---|
| `daily_sync.py` | Writes a daily row to Ting's Agents Control Plane Base (drafts/published counts) | Was on Quynh's Mac via launchd — **failing since the folder moved**. Move to the new owner's machine. |
| `track_published.py` | Rank/click checkpoints for published articles | Not scheduled yet (nothing published) |

Install the daily sync on your Mac (edit the path inside first):

```bash
cp scripts/launchd/com.sunnystep.seo-agent.daily-sync.plist ~/Library/LaunchAgents/
# replace REPO_PATH in the file with the absolute repo path, then:
launchctl load ~/Library/LaunchAgents/com.sunnystep.seo-agent.daily-sync.plist
```

Only one machine should run it — Quynh's copy gets unloaded at handover.

---

## 7. Current state & open work (14 Sep 2026)

**Done**
- 4 clusters finalised from real data (GA4 + Search Console + Serper + Keyword Planner + Google Ads) and loaded in the Base with 20 graded keywords.
- Eval system at every step (keyword → brief → draft → optimize → publish → calibration), critic agent, YMYL gate, regression suite.
- Shopify publisher (read access verified; blog `sunnystep blog` = `94480597052`, 85 existing articles).
- Manager review loop (`/submit-for-review`, `review_loop.py`).

**Not done / known issues**
- [ ] **Nothing published yet** → `eval_log`, `published.jsonl`, `checkpoints.jsonl` are empty; calibration has no data.
- [ ] **Shopify `write_content` scope unverified** — first publish may return 403 → add the scope to the custom app.
- [ ] **Flagship draft `comfortable-shoes-singapore.md` is BLOCKED** by the gate (no medical disclaimer, 1 uncited health claim). The April drafts pre-date the gates — re-run them.
- [ ] **Review loop never run live** — needs `LARK_REVIEW_CHAT_ID` and the bot in the group.
- [ ] **Lark Base auto-sync not built** — `dashboard.py --csv` exports the right shape; the push script is the next build.
- [ ] **GEO (AI answer) benchmark not built** — 20 fixed questions + re-runnable test (see project brief).
- [ ] `context/ai-citation-targets.md` and `context/reddit-strategy.md` are still generic templates — not Sunnystep-specific.
- [ ] 3 tests in `tests/test_dataforseo_resilience.py` fail: they test the old DataForSEO client; `dataforseo.py` is now a Serper wrapper. Rewrite or delete.
- [ ] `post_migration_audit.py` not yet run on real Shopify URLs.

**Latest finding (14 Sep) — read before writing the next article**
Google SG results for "comfortable shoes singapore", "most comfortable shoes singapore"
and "comfortable work shoes singapore" are ~6/10 **brand homepages / collection pages**
(Anothersole, Lucca Vudor, prettyFIT, Comfort Co, DMK, ECCO), plus **Reddit
r/askSingapore** threads and **listicles** (Honeycombers, Seth Lui). Almost no blog
posts. Sunnystep is not in the top 10. For these high-intent queries the page to
optimise is likely a **collection/landing page**, not a blog article — and AI
citations for them depend heavily on Reddit/listicle presence (off-site).

---

## 8. Rules that never bend

1. No medical claims — no "cures / treats / prevents / guaranteed relief"; every health statement cited + disclaimer. The gate enforces it; don't bypass it.
2. A human approves and publishes. Shopify articles are created unpublished.
3. One page, one keyword — the grader rejects cannibalisation.
4. Don't hand-edit scores or the ledger; failures become regression fixtures.
5. Secrets never go in git (`.env`, `credentials/` are ignored).

## 9. Contacts

- **Owner:** Maliki
- **Previous builder:** Quynh — questions during handover week
- **Gate approver:** Ting
