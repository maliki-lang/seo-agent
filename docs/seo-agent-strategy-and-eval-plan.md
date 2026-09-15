# SEO Agent — Strategy & Evaluation Build Plan

> **Status:** Proposal · **Date:** 2026-06-22 · **Owner:** Quynh
> **Sources:** Consultant proposal (Naressa Khan, 18 May 2026, *"From Brand Visibility to Category Ownership"*) · Lark *05 Evaluation & Quality / Agent Performance Control Tower* · AI-evaluator verdict on the prior Meta-ads agent · reference repos `AgriciDaniel/claude-seo`, `coreyhaines31/marketingskills`.

---

## Part A — The strategy & the concrete goal

### The agent's concrete goal (its contract)

> **Grow SunnyStep's non-branded organic clicks and rank ≥2 of 4 target clusters onto Google SG page 1, by publishing 8–12 evidence-backed pieces across the customer's stage 1–3 awareness journey — while keeping the cluster-level conversion guardrail healthy.**

This is worded to avoid the **"optimizing the wrong number"** failure flagged on the Meta-ads agent. The SEO trap mirrors the Meta-ROAS trap:

| ❌ Vanity metric (do NOT optimize) | ✅ North-star (causal-ish, from GSC) |
|---|---|
| Total sessions / pageviews | **Non-branded organic clicks** (strips out the 80–90% branded noise) |
| "We published N articles" | **Cluster keyword positions** for the 4 target clusters |

The ledger + `learned_weights.py` already capture GSC position deltas per published URL, so the north-star is already half-instrumented.

**Primary vs guardrail (because we deliberately target top-of-funnel stage 1–3):**

- **Primary metric (per-article, fast):** non-branded organic clicks + cluster rank movement — attributable to each published piece within weeks, so the loop closes quickly.
- **Guardrail metric (cluster-level, slow):** non-branded organic **assisted conversions** per cluster, reviewed monthly/quarterly. Stage-1 "unaware" content can pull clicks from people who never buy; the guardrail catches "we grew traffic that never converts." We do **not** gate individual articles on revenue (too slow/noisy per-post) — we watch it at the aggregate.

### Month-6 success targets (from the proposal)

- Non-branded organic traffic share rising from its <15% baseline.
- ≥2 of the 4 target clusters with a page on Google SG page 1 (top 10).
- A content library of 8–12 fully optimized pieces across the 4 clusters.
- Clean, GSC-verified site (migration to Shopify already complete) with no ranking regressions.

### The target keyword clusters (SG, women 35–55)

Verified against real GSC/GA4 + paid-search data and locked in the Lark Base
("Clusters" table — the source of truth). A **pillar** plus four blog clusters,
listed in priority order:

**Pillar / hub — Comfortable walking shoes.** Every Sunnystep shoe is a walking
shoe; the head term (`walking shoes singapore`, ~110/mo) is owned by
Nike/Adidas/Decathlon, so we compete here with a **collection/brand page, not a
blog**. Every cluster links up to it.

1. **Comfortable shoes (discovery)** — best-X / "shoe brands in Singapore". Anchor `comfortable shoes singapore` (~140/mo). **Fastest win**: already page-2 (~2,700 impressions), beatable SERP; best-X format converted best in paid.
2. **Foot comfort education & arch support** — informational pillars (benefits of arch support, how to tell if you need it, flat-feet guide) + discovery. **Biggest non-branded prize (~840/mo)**; wide-open informational SERP in SG → **YMYL** (educational + cited). We **drop** transactional `arch-support-shoes-singapore` (medical-orthotic intent; paid converted ~0%) — educate, then they try in-store.
3. **Everyday flats & loafers** — flats, ballerinas, loafers, mules. Anchor `ballet flats singapore` (~210/mo). Highest winnable volume; beatable SERP.
4. **Work / office comfort shoes** — comfortable office/work shoes for women. Anchor `comfortable work shoes` (~90/mo). Beatable brand-vs-brand SERP.

> Superseded the consultant's initial four (which included children's footwear —
> dropped as a one-off launch, not a strategic cluster) after refining against our
> own data: arch support is targeted via **education/discovery**, not transactional
> medical intent; walking shoes is the **pillar**, not a blog cluster.

### Strategic reframe

1. **Every piece maps to a cluster + an awareness stage (1–3).** Today the brand only captures stages 4–5 (product / ready-to-buy); growth lives in stages 1–3 (unaware / problem-aware / solution-aware). `opportunity_scorer.py` should reject a topic that doesn't map.
2. **Awareness stage is a required brief field.**
3. **YMYL/medical credibility is mandatory**, not optional (see Part B compliance gate).

> **Resolved — platform:** the site is **already fully migrated to Shopify**. The consultant's pre-go-live "Migration QA" phase is therefore retired; what remains is a one-off **post-migration audit** (confirm redirects, metadata, schema, GSC verification carried over with no ranking regressions), folded into Phase 1.
>
> **Gap to close — publisher:** the repo only ships `shopline_publisher.py` + `wordpress_publisher.py`; there is **no Shopify publisher**. A `shopify_publisher.py` + a retargeted `/publish-draft` is required. Not a blocker for the article-draft quality slice, **but a blocker for real close-the-loop tracking** — rankings can't be tracked for content that never reaches the live Shopify site.

---

## Part B — The evaluation standard

The Lark *Agent Performance Control Tower* and the Meta-ads verdict say the same thing: the verdict is the standard, failed. Every row below is something we build so the AI evaluator cannot flag it.

| Verdict failure (Meta-ads agent) | Lark standard | What we build for the SEO agent |
|---|---|---|
| Eval at only 1 of ~10 steps | Layer 5: every meaningful run scored | A gate on **every** pipeline step (Part C) |
| No LLM-judge / critic anywhere | Layer 4: deterministic **+** calibrated LLM judge | Deterministic graders first (~10 scorers exist), then a critic |
| Same agent grades itself | Critic-agent pattern | A **separate** `critic` agent + code that forces regenerate-below-threshold |
| Only failures persisted | Layer 5: 0–100 saved each step | Scorecard row per step appended to the ledger |
| No regression set; winners typed by hand | Layer 3: golden dataset from real failures | `eval/regression/` fixtures built from real misses |
| Optimized Meta-reported ROAS (wrong #) | Outcome movement | North-star = non-branded clicks (Part A) |
| **No compliance gate on a health brand** | Safety grader (weight 15) | **Zero-tolerance YMYL gate** — foot-condition cluster is the same health-claims risk |
| "Eval theater" (rubrics as prose, not wired) | Operating principle: wired, local-first | Every rubric is a Python grader called in code, not prompt prose |

**Biggest carry-over risk:** the Meta-ads agent shipped "medical-grade / doctor-approved" copy with nothing between generation and the live ad. SunnyStep's plantar-fasciitis / arch-support content is the same YMYL category. If only one gate ships first, it's this one.

### Adopted scorecard (Lark default, /100)

Source grounding 15 · Tool/trajectory correctness 15 · Metric & action quality 15 · Safety & access 15 · Verification evidence 10 · Noise control 10 · Business impact 10 · Reliability/cost/latency 5 · Self-improvement capture 5.

### Thresholds

- **85+** production-grade; keep monitoring
- **70–84** usable; fix within 7 days
- **50–69** at risk; owner fix before scale
- **<50** disable / rollback / rebuild unless explicitly approved

### Operating principle (from Lark)

Do **not** start with a giant eval platform. Start **local-first**: a deterministic eval runner + Lark Base dashboard. Keep source-of-truth in the Agent OS so it can be reused as product infrastructure.

### Scorecard calibration (self-correcting eval)

The publish-time scorecard is a **prediction** ("this should rank"); the post-publish checkpoint is the **outcome** (it ranked or didn't). Periodically check whether high-scoring drafts actually rank/earn clicks better than low-scoring ones. If they don't, the scorecard is miscalibrated and gets adjusted. `learned_weights.py` already wires outcome→score weighting; this adds the missing "**is the score itself any good?**" check, so the eval improves rather than merely runs. This is what "calibrated LLM judge" means in practice.

---

## Part C — The build plan: every step gated

The repo already has the **scorers** (`content_scorer`, `seo_quality_rater`, `readability_scorer`, `keyword_analyzer`, `opportunity_scorer`, `content_length_comparator`, `trust_signal_analyzer`, `cro_checker`, …) and the **loop** (`ledger.py`, `learned_weights.py`, `track_published.py`). Missing: the **wiring** — gates, a critic, per-step persistence, a compliance gate, a golden set.

### The gated pipeline

| # | Step (command/agent) | Gate (grader) | Type | Block if | Persist |
|---|---|---|---|---|---|
| 1 | Select topic (`/priorities`, `opportunity_scorer`) | Maps to a cluster + stage 1–3; north-star aligned | Deterministic | No cluster/stage | scorecard→ledger |
| 2 | Research brief (`/research`) | Completeness: intent, SERP gap, subtopics, word target, internal links, **named sources** | Deterministic + LLM-judge | <70 | ledger |
| 3 | **YMYL compliance preflight** *(new)* | Every health claim cited; disclaimer present; no unverifiable claim | Deterministic + safety LLM-judge | **any uncited health claim (zero-tolerance)** | ledger + regression |
| 4 | **Draft article (`/write`)** ← *first vertical slice* | `seo_quality_rater` + `readability_scorer` + `keyword_analyzer` + `content_length_comparator` | Deterministic | <70 | ledger |
| 5 | Optimize (`/optimize`, `seo-optimizer`) | Keyword density (no stuffing), heading structure, internal links | Deterministic | stuffing / no H-structure | ledger |
| 6 | Meta / internal links / keyword map agents | Per-agent rubric | Deterministic | <70 | ledger |
| 7 | **Critic review** *(new agent)* | Separate agent scores draft on 9-dim scorecard, forces regenerate | LLM-judge (separate agent) | <70 → regenerate; <50 → block | ledger |
| 8 | Post-migration audit *(one-off, Phase 1)* | Redirect map pass/fail, metadata carryover, schema preserved, GSC verified on Shopify | Deterministic | any 301 miss | report |
| 9 | Publish (`/publish-draft`) | Final-deliverable audit + records baseline | Deterministic | checklist fail | ledger (baseline exists) |
| 10 | Track + close loop (`track_published`→`learned_weights`) | T+N rank/clicks checkpoint → outcome → feeds scorer; recurring miss → regression case | Quantitative | — | checkpoints + regression |

### The first vertical slice — the article draft (`/write`)

The agent's core deliverable is the SEO blog post, so the first slice gates the **article draft**, exercising every layer at once (proves the wiring is not theater):

1. **Deterministic graders** (reuse repo scorers): `seo_quality_rater` (0–100), `readability_scorer` (Flesch/grade vs target), `keyword_analyzer` (density, no stuffing), `content_length_comparator` (vs top-10 SERP).
2. **YMYL compliance grader** (foot-condition cluster only): every health claim cited, disclaimer present.
3. **Separate critic agent**: scores the draft on the 9-dim scorecard — especially genericity / E-E-A-T / "real angle" (the consultant's anti-AI-genericity concern); forces regenerate.
4. **Persistence**: 0–100 score + per-dimension breakdown saved to the ledger, with draft version.
5. **Gate**: <70 → regenerate with critic feedback; <50 → block.
6. **Close loop**: failing draft saved as a regression fixture.

Once this works end-to-end, steps 1, 2, 5, 9 are replications of the same proven pattern.

### Proposed eval package layout

```
data_sources/modules/eval/
  scorecard.py     # 9-dimension scorecard, persists to ledger
  graders.py       # deterministic wrappers over existing scorers + LLM-judge calls
  gates.py         # threshold gating per step (85/70/50)
  compliance.py    # YMYL zero-tolerance gate
  eval_runner.py   # runs gates before publish / before agent changes
  regression/      # golden-dataset fixtures from real failures (jsonl)
.claude/agents/
  critic.md        # SEPARATE critic agent (not self-grading)
```
Reuse `ledger.py` as the trace/run-logging layer; reuse `learned_weights.py` as the close-the-loop layer.

### Rollout (mirrors the Lark 4-week sequence, adapted)

- **Phase 0 — Scaffolding + the article-draft vertical slice.** Create `eval/` package, write an agent contract (KPI / source / trigger / tools / safety / kill-criteria) per agent, wire the `/write` article slice end-to-end.
- **Phase 1 — Highest-risk gates + publisher.** (a) YMYL compliance gate (zero-tolerance, blocks publish); (b) `shopify_publisher.py` + retargeted `/publish-draft`; (c) one-off post-migration audit.
- **Phase 2 — Gate the rest** (steps 1, 2, 5), stand up the critic agent broadly, seed `eval/regression/` from real failures (the Meta-ads verdict becomes fixture #1).
- **Phase 3 — Production monitoring + Lark Base dashboard writeback**, plus the consultant's Phase 3 (AIO optimization, FAQ/schema).

---

## Build status (2026-06-22) — all 4 phases implemented

| Phase | What shipped | Where |
|---|---|---|
| 0 | Eval package (scorecard, graders, gates, runner), agent contracts, critic agent, `/write` gate wiring | `data_sources/modules/eval/`, `.claude/agents/critic.md`, `docs/agent-contracts.md` |
| 1 | YMYL zero-tolerance compliance gate, Shopify publisher with pre-publish gate, post-migration audit | `eval/compliance.py`, `shopify_publisher.py`, `post_migration_audit.py` |
| 2 | Regression layer + seed fixture (the YMYL failure class), broadened step gates, contracts for all agents | `eval/regression/`, `eval/gates.py` |
| 3 | Calibration (score-vs-outcome), local-first + Lark-Base dashboard export, AIO/FAQ-schema grader | `eval/calibration.py`, `eval/dashboard.py`, `eval/aio_schema.py` |

Verified: the seed fixture hard-fails (`block`); a clean brand draft ships (91.5);
regression, dashboard, contracts, and calibration runners all execute.

**Known follow-ups:** wire the live Lark Base API sync (CSV contract is ready);
run `post_migration_audit.py` against the real Shopify URL list; populate
`.env` Shopify credentials; let `eval_log` + `checkpoints` accumulate so
calibration produces a verdict.

## Cadence & keyword runway

### Publishing cadence
- **Target: 2 articles per week**, each targeting a **distinct** keyword/intent.
  This is a build-phase sprint, not a forever number.
- The real KPI is **distinct, gate-passing, non-cannibalizing pages shipped** —
  not raw post count. The eval gate is the safety valve: if we push faster than we
  can produce genuinely good, distinct pages, it blocks — that's the signal to
  refill the backlog or slow down, never to ship thin content.

### One page, one job (no cannibalization)
- For a given keyword/intent, Google ranks essentially **one** page from our site.
- Writing multiple articles for the **same** keyword splits ranking signals and
  makes all of them weaker (cannibalization). We never do this — the
  `keyword_grader` cannibalization filter enforces it.
- Volume helps through **breadth**: many articles each on a *different*
  keyword/intent, interlinked into clusters that point to a pillar page. Each
  article also harvests a **constellation of long-tail** variations around its
  primary keyword, so one piece earns far more than its single reported volume.

### Runway (Singapore market reality)
- SG volumes are small (biggest ~480/mo; most 10–90; many unreported). There is
  **no pool of ~100 high-volume keywords** here — and we don't need one.
- There are roughly **30–50 genuinely ownable core topics** in SG women's comfort
  footwear. At 2/week (~8/month) that's a **~4–6 month sprint to build the core**,
  then a lighter maintenance cadence (refresh winners + selective new).
- At this market size the goal is **category ownership** (rank #1 across the
  cluster) + brand authority + AI citations + assisted/in-store conversions — not
  traffic-per-article. A 50/mo keyword is worth owning when the whole category is
  small.

### Extending the net (when the core saturates, in priority order)
1. **Foot-comfort education long-tail** — deep, low-competition informational queries.
2. **Occasion / occupational / lifestyle angles** — e.g. shoes for nurses, travel,
   all-day walking — brand-relevant, expands the universe substantially.
3. **Malaysia** — we have a MY store; MY roughly doubles the addressable keyword set.
4. **Refresh** existing winners — often higher-ROI than net-new.

### Keyword refill
- **Monthly** research pass to add newly-validated keywords (via the research
  gates), catch seasonal/trend demand, and **broaden scope** as the core fills —
  not just more of the same.

### Automation (bounded autonomy — to be set up)
- Planned cron: on cadence, take the top Backlog grade-A/B keyword → research
  gates → write → draft gate → critic → land in `review-required/` + create the
  Base Article row. **A human publishes** (never auto-publish, especially YMYL).
- As calibration proves grade-A keywords actually rank, autonomy can increase to
  auto-publishing the safe (non-YMYL, high-score) pieces. Cron + the additional
  pre-publish steps are deferred until we've proven the pipeline on a manual run.

## Part D — Worth borrowing from the reference repos

- **`AgriciDaniel/claude-seo`** (more useful): E-E-A-T evaluation keyed to Google's Quality Rater Guidelines; a **claim-verification scanner** and **AI-filler detector**; a **falsifiability check** ("how would we know this failed?") on every recommendation — almost exactly our YMYL gate + critic. Its **SQLite drift snapshots** validate the ledger/checkpoint approach.
- **`coreyhaines31/marketingskills`**: `ai-seo` (→ consultant's AIO phase), `schema`, `ab-testing`, `analytics` — most already exist as skills in this environment, so borrow patterns rather than re-import.
