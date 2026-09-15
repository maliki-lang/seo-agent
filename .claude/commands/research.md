# Research Command

Use this command to conduct comprehensive SEO keyword research and competitive analysis before writing new content.

## Usage
`/research [topic]`

## What This Command Does
1. Performs keyword research for the footwear industry-related topics
2. Analyzes top-ranking competitor content
3. Identifies content gaps and opportunities
4. Develops unique angle for Sunnystep perspective
5. Creates detailed research brief for writing

## Process

### Keyword Research
- **Primary Keyword**: Identify main target keyword for the topic
- **Search Volume & Difficulty**: Research estimated monthly searches and competition level
- **Keyword Variations**: Find semantic variations and long-tail opportunities
- **Related Questions**: Discover what people are actually asking (People Also Ask, forums, Reddit)
- **Search Intent**: Determine if intent is informational, navigational, commercial, or transactional
- **Topic Cluster**: Identify how this topic fits into Sunnystep content clusters

### Competitive Analysis
- **Top 10 SERP Review**: Analyze the top 10 ranking articles for target keyword
- **Content Length**: Note word count of top-performing articles (benchmark target)
- **Common Themes**: What topics/sections do all top articles cover?
- **Content Gaps**: What's missing from competitor coverage?
- **Unique Angles**: What perspectives or insights are underexplored?
- **Featured Snippets**: Identify if there's a featured snippet opportunity
- **Domain Authority**: Note which competitors rank (indie blogs vs. major publications)

### Context Integration
- **Sunnystep Advantage**: How can Sunnystep product features naturally enhance this content?
- **Brand Alignment**: Check @context/brand-voice.md for messaging fit
- **Existing Content**: Review @context/internal-links-map.md for related Sunnystep articles
- **Target Keywords**: Cross-reference with @context/target-keywords.md priority list
- **SEO Guidelines**: Ensure research aligns with @context/seo-guidelines.md requirements

### Footwear & Customer Focus
- **Customer Angle**: How does this topic impact the Sunnystep customer (women 35–55 in Singapore who are on their feet)?
- **Intent segmentation**: Informational / discovery / transactional — target informational + discovery; skip transactional-medical (see strategy doc).
- **Occasion / need**: Work, everyday, walking, standing all day, foot comfort — which use case does this serve?
- **Real scenarios**: Concrete situations (a nurse on a 12-hour shift, a teacher, daily MRT commute) where this matters
- **Pain Points**: Specific challenges the customer faces (sore feet after work, shoes that look professional but hurt)

### Content Planning
- **Recommended Structure**: Outline H2 and H3 headings based on research
- **Content Depth**: Determine target word count (typically 2000-3000+ for SEO)
- **Supporting Evidence**: Identify statistics, studies, or data to include
- **Expert Sources**: Find industry experts or quotes to reference
- **Visual Opportunities**: Suggest images, screenshots, or graphics needed
- **Internal Links**: Map 3-5 key Sunnystep pages to link to (from @context/internal-links-map.md)
- **External Authority**: Identify 2-3 authoritative external sources to link

### Hook Development
- **Introduction Angle**: Compelling way to open the article
- **Value Proposition**: Clear benefit reader will get from article
- **Contrarian Elements**: Any unexpected perspectives to explore
- **Story Opportunities**: Real examples or case studies to feature

## Output
Provides a comprehensive research brief with:

### 1. SEO Foundation
- **Primary Keyword**: [keyword] (volume, difficulty)
- **Secondary Keywords**: 3-5 related keywords and variations
- **Target Word Count**: Minimum words needed to compete
- **Featured Snippet Opportunity**: Yes/No, format (paragraph, list, table)

### 2. Competitive Landscape
- **Top 3 Competitor Articles**: URLs and key takeaways from each
- **Common Sections**: Must-cover topics based on SERP analysis
- **Content Gaps**: Opportunities to provide unique value
- **Differentiation Strategy**: How Sunnystep can stand out

### 3. Recommended Outline
```
H1: [Optimized headline with primary keyword]

Introduction
- Hook
- Problem statement
- Value proposition

H2: [Main section 1]
H3: [Subsection]
H3: [Subsection]

H2: [Main section 2]
...

Conclusion
- Key takeaways
- Call to action
```

### 4. Supporting Elements
- **Statistics to Include**: 5-7 relevant data points with sources
- **Expert Quotes**: Potential sources or existing quotes
- **Examples/Case Studies**: Real customer scenarios to feature (on-feet professionals, daily walkers)
- **Visual Suggestions**: Screenshots, charts, or graphics needed

### 5. Internal Linking Strategy
- **Pillar Page**: Main Sunnystep pillar content to link to
- **Related Articles**: 2-4 relevant blog posts to link
- **Product Pages**: Sunnystep features to naturally mention
- **Resource Pages**: Tools or guides to reference

### 6. Meta Elements Preview
- **Meta Title**: Draft optimized title (50-60 characters)
- **Meta Description**: Draft compelling description (150-160 characters)
- **URL Slug**: Recommended URL structure

## File Management
After completing the research, automatically save the brief to:
- **File Location**: `research/brief-[topic-slug]-[YYYY-MM-DD].md`
- **File Format**: Markdown with clear sections and structured data
- **Naming Convention**: Use lowercase, hyphenated topic slug and current date

Example: `research/brief-comfortable-shoes-singapore-2026-06-28.md`

## Research Eval Gates (REQUIRED — "are we writing the right thing?")

Two code-enforced gates run at the research step, *before* `/write`. They catch
the failure the draft eval cannot: a perfect article on the wrong keyword, or a
thin brief that guarantees a weak article. See
`docs/seo-agent-strategy-and-eval-plan.md`.

### Gate 1 — Keyword pick (decision quality)
Grade the target keyword before committing to write it:
```bash
python3 -m data_sources.modules.eval.keyword_grader "<keyword>" \
    --cluster "<cluster>" --volume <N> [--position <N>] [--impressions <N>]
```
- Combines `search_intent_analyzer` (intent) + `opportunity_scorer` signal +
  hard filters (branded / navigational / no-cluster / cannibalization).
- **Hard-rejects branded & navigational** terms → grade D / Parked.
- **Downranks medical-orthotic shopping queries** (clinical token or
  podiatry-owned SERP) — target those topics only via the educational/discovery
  angle, never the transactional one (see strategy: target by intent, not topic).
- Output Grade A–D + score feed the Base **Keywords** table (Grade / Opportunity
  score / Status). Only **A/B** keywords proceed to a brief; C is optional; D is
  parked. A human may override (e.g. park to D on paid-conversion evidence).

### Gate 2 — Brief completeness (plan quality)
After writing the brief, gate it before `/write`:
```bash
python3 -m data_sources.modules.eval.brief_gate research/brief-<slug>.md --cluster "<cluster>"
```
Requires: target keyword · search intent · SERP gap · ≥3 subtopics · ≥2 internal
links · **credible sources (mandatory for YMYL/foot-comfort — missing = hard
fail)** · word/format target · cluster + awareness stage. Exit `0` pass · `2`
incomplete (fix & re-run) · `3` block/hard-fail. Score persists to
`eval_log.jsonl` (step `research_brief`).

These predictions are validated downstream: `calibration.py` checks whether
grade-A keywords actually rank, and recalibrates if they don't.

## Next Steps
The research brief serves as the foundation for:
1. Passing both research eval gates above
2. Running `/write [topic]` to create the optimized article
3. Reference material for maintaining SEO focus throughout writing
4. Checklist to ensure all competitive gaps are addressed

This ensures every article is built on solid, *evaluated* SEO research.
