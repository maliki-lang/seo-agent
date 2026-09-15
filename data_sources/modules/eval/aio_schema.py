"""
AIO (AI Overview) readiness + schema/FAQ grader  — consultant Phase 3.

Content increasingly has to be retrieved by AI answer engines, not just ranked
by Google. This grader scores how "answer-engine ready" a draft is and can emit
FAQ JSON-LD for the page. It is a deterministic grader, usable as an extra gate
dimension or standalone via /optimize.

Checks:
  - Question-style H2/H3 headings (how AI engines locate answerable units)
  - A concise direct answer near the top (TL;DR / definition in first ~120 words)
  - An explicit FAQ section (Q/A pairs)
  - Structured-data readiness (FAQ pairs we can serialize to JSON-LD)
  - Lists/tables (extractable, citable units)

Usage:
    python3 -m data_sources.modules.eval.aio_schema <draft.md> [--faq-jsonld]
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _headings(content: str) -> List[str]:
    return re.findall(r"^#{2,3}\s+(.+)$", content, re.MULTILINE)


def extract_faq(content: str) -> List[Tuple[str, str]]:
    """Pull Q/A pairs: a question-style heading followed by its answer text."""
    pairs: List[Tuple[str, str]] = []
    blocks = re.split(r"^(#{2,3}\s+.+)$", content, flags=re.MULTILINE)
    # blocks alternate: [pre, heading, body, heading, body, ...]
    for i in range(1, len(blocks) - 1, 2):
        heading = re.sub(r"^#{2,3}\s+", "", blocks[i]).strip()
        body = blocks[i + 1].strip()
        if heading.endswith("?") or re.match(
            r"(?i)^(how|what|why|when|where|which|can|do|does|is|are|should)\b", heading
        ):
            answer = re.split(r"\n\s*\n", body)[0].strip() if body else ""
            answer = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", answer)  # strip links
            if answer:
                pairs.append((heading, answer[:500]))
    return pairs


def faq_jsonld(pairs: List[Tuple[str, str]]) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in pairs
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def score_aio(content: str) -> Dict[str, Any]:
    headings = _headings(content)
    q_headings = [h for h in headings if h.endswith("?") or re.match(
        r"(?i)^(how|what|why|when|where|which|can|do|does|is|are|should)\b", h)]
    faq = extract_faq(content)

    first_120 = " ".join(content.split()[:120]).lower()
    has_direct_answer = bool(
        re.search(r"\b(tl;dr|in short|the short answer|simply put)\b", first_120)
        or re.search(r"\bis a\b|\bare\b|\bmeans\b", first_120)
    )
    has_lists = bool(re.search(r"^\s*[-*+]\s|\n\d+\.\s", content, re.MULTILINE))
    has_tables = "|" in content and re.search(r"\|.*\|", content) is not None

    checks = {
        "question_headings": len(q_headings) >= 2,
        "direct_answer_up_top": has_direct_answer,
        "faq_section": len(faq) >= 2,
        "lists_or_tables": has_lists or has_tables,
    }
    score = round(100.0 * sum(checks.values()) / len(checks), 1)
    recs = []
    if not checks["question_headings"]:
        recs.append("Add question-style H2/H3 headings (How…, What…, Why…).")
    if not checks["direct_answer_up_top"]:
        recs.append("Add a 1–2 sentence direct answer / TL;DR near the top.")
    if not checks["faq_section"]:
        recs.append("Add an FAQ section with ≥2 real Q/A pairs for FAQ schema.")
    if not checks["lists_or_tables"]:
        recs.append("Add a list or comparison table (extractable by AI engines).")

    return {
        "aio_score": score,
        "checks": checks,
        "question_headings": q_headings,
        "faq_pairs": faq,
        "recommendations": recs,
    }


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: aio_schema <draft.md> [--faq-jsonld]")
        return 1
    content = Path(argv[0]).read_text(encoding="utf-8")
    result = score_aio(content)
    print(f"AIO readiness: {result['aio_score']}/100")
    for k, v in result["checks"].items():
        print(f"  [{'OK' if v else '  '}] {k}")
    for r in result["recommendations"]:
        print(f"  → {r}")
    if "--faq-jsonld" in argv and result["faq_pairs"]:
        print("\n--- FAQ JSON-LD (paste into the page <head>) ---")
        print(faq_jsonld(result["faq_pairs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
