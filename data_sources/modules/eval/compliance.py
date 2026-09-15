"""
YMYL compliance gate — zero tolerance.

This is the gate the prior Meta-ads agent was flagged for *not* having on a
health-claims brand. SunnyStep's foot-condition cluster (plantar fasciitis,
arch support, flat feet) is YMYL ("Your Money or Your Life") content that Google
holds to a higher credibility bar, so a hard gate sits between generation and
publish.

Rules (all enforced in code, not prose):
  1. Any sentence making a medical/health claim must be backed by a citation
     (an external link or a named, credible source) within the same section.
  2. A medical disclaimer must be present somewhere in the document.
  3. Forbidden absolute claims (e.g. "cures", "guaranteed to heal", "clinically
     proven to cure") are banned outright — they cannot be made compliant by a
     citation.

Returns a structured result; `hard_fail=True` means: do not publish.
"""

import re
from dataclasses import dataclass, field
from typing import List

# Two-tier YMYL detection — avoids false-failing ordinary comfort content that
# merely mentions a feature like "arch support".
#
# STRONG: medical/condition topics. Their mere presence triggers the gate.
STRONG_YMYL = [
    "plantar fasciitis", "plantar", "fasciitis", "flat feet", "flat foot",
    "fallen arch", "heel pain", "heel spur", "bunion", "foot pain",
    "foot condition", "diabetic", "neuropathy", "overpronation", "supination",
    "metatarsal", "achilles", "swollen feet", "edema", "arthritis",
]
# SOFT: health-adjacent feature/effect terms. These only trigger the gate when a
# health CLAIM is also being made nearby (feature mention alone is fine).
SOFT_YMYL = [
    "arch support", "orthotic", "orthopedic", "posture", "alignment", "gait",
    "knee pain", "joint pain", "medical-grade", "medical grade", "therapeutic",
]
# Back-compat alias.
YMYL_TRIGGERS = STRONG_YMYL + SOFT_YMYL

# Phrases that signal a health/medical claim is being made.
CLAIM_SIGNALS = [
    r"\bcure[sd]?\b", r"\bheal[s]?\b", r"\btreat(?:s|ment|ing)?\b",
    r"\brelie(?:f|ve[sd]?)\b", r"\bprevent[s]?\b",
    r"\b(?:reduce|relieve|ease|soothe|alleviate)[sd]?\b.{0,15}\b(?:pain|inflammation|swelling)\b",
    r"\bclinically\b", r"\bmedically\b", r"\bdoctor[- ]?(?:recommended|approved)\b",
    r"\bpodiatrist[- ]?(?:recommended|approved)\b", r"\bproven\s+to\b",
    r"\bcorrect[s]?\s+(?:your|the)?\s*(?:posture|alignment|gait)\b",
    r"\brealign[s]?\b", r"\bdiagnos(?:e|is|ed)\b", r"\bsymptom[s]?\b",
    r"\bFDA\b", r"\bmedical[- ]?grade\b", r"\btherapeutic\b",
]
_CLAIM_RE = re.compile("|".join(CLAIM_SIGNALS), re.IGNORECASE)

# Absolute claims that are banned outright (cannot be cited into compliance).
FORBIDDEN_ABSOLUTES = [
    r"\bcure[s]?\s+(?:plantar fasciitis|your|the|all)\b",
    r"\bguaranteed?\s+to\s+(?:cure|heal|fix|eliminate)\b",
    r"\bclinically proven to cure\b",
    r"\b100%\s+(?:effective|cure|relief)\b",
    r"\beliminate[s]?\s+(?:all\s+)?(?:pain|your pain)\s+(?:permanently|forever)\b",
    r"\bFDA[- ]?approved\b",  # footwear is not FDA-approved; banned claim
    r"\breverse[s]?\s+(?:diabetes|neuropathy|arthritis)\b",
]

# Disclaimer detection.
DISCLAIMER_SIGNALS = [
    r"not\s+(?:a\s+substitute|medical advice)",
    r"consult\s+(?:a|your)\s+(?:doctor|physician|podiatrist|healthcare)",
    r"informational purposes only",
    r"speak\s+(?:to|with)\s+(?:a|your)\s+(?:doctor|podiatrist|healthcare)",
    r"seek\s+(?:professional|medical)\s+advice",
]

# A citation = a markdown external link, or a "according to <Source>" attribution.
_LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^\)]+\)")
_ATTRIB_RE = re.compile(
    r"\b(?:according to|per|cited by|study (?:by|from)|research (?:by|from)|"
    r"(?:[A-Z][a-z]+ ){0,3}(?:University|Hospital|Clinic|Journal|Association|"
    r"Society|podiatrist|physiotherapist|orthopedic))\b"
)


@dataclass
class ComplianceResult:
    is_ymyl: bool
    hard_fail: bool = False
    reasons: List[str] = field(default_factory=list)
    uncited_claims: List[str] = field(default_factory=list)
    forbidden_hits: List[str] = field(default_factory=list)
    has_disclaimer: bool = False
    score: float = 100.0  # 0..100 for the safety_compliance dimension


def is_ymyl(*texts: str) -> bool:
    """
    True if the content should pass through the YMYL gate.

    Gated when a STRONG condition topic is present, OR a SOFT health-adjacent
    term co-occurs with an actual health claim. A bare feature mention
    ("arch support") with no claim is NOT gated.
    """
    blob = " ".join(t for t in texts if t).lower()
    if any(t in blob for t in STRONG_YMYL):
        return True
    if any(t in blob for t in SOFT_YMYL) and _CLAIM_RE.search(blob):
        return True
    return False


def _split_sentences(text: str) -> List[str]:
    # Strip headings/markdown noise lightly; keep links inline for the check.
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if p.strip()]


def _has_citation(window: str) -> bool:
    return bool(_LINK_RE.search(window) or _ATTRIB_RE.search(window))


def check_compliance(
    content: str,
    cluster: str = "",
    target_keyword: str = "",
) -> ComplianceResult:
    """
    Run the YMYL compliance gate over article content.

    Non-YMYL content passes automatically (score 100). YMYL content must have
    every health claim cited, a disclaimer present, and no forbidden absolutes.
    """
    ymyl = is_ymyl(content, cluster, target_keyword)
    res = ComplianceResult(is_ymyl=ymyl)
    if not ymyl:
        return res

    lowered = content.lower()

    # Rule 3: forbidden absolutes — instant hard fail.
    for pat in FORBIDDEN_ABSOLUTES:
        for m in re.finditer(pat, lowered):
            snippet = content[max(0, m.start() - 20):m.end() + 20].strip()
            res.forbidden_hits.append(snippet)
    if res.forbidden_hits:
        res.hard_fail = True
        res.reasons.append(
            f"{len(res.forbidden_hits)} forbidden absolute health claim(s) — "
            "banned regardless of citation."
        )

    # Rule 2: disclaimer present.
    res.has_disclaimer = any(
        re.search(p, lowered) for p in DISCLAIMER_SIGNALS
    )
    if not res.has_disclaimer:
        res.hard_fail = True
        res.reasons.append("No medical disclaimer found in a YMYL article.")

    # Rule 1: every health claim must have a nearby citation.
    sentences = _split_sentences(content)
    claim_re = re.compile("|".join(CLAIM_SIGNALS), re.IGNORECASE)
    for i, sent in enumerate(sentences):
        if not claim_re.search(sent):
            continue
        # Citation may be in the claim sentence or the immediate next one.
        window = " ".join(sentences[i:i + 2])
        if not _has_citation(window):
            res.uncited_claims.append(sent[:160])
    if res.uncited_claims:
        res.hard_fail = True
        res.reasons.append(
            f"{len(res.uncited_claims)} health claim(s) without a citation."
        )

    # Score for the safety dimension (informational; hard_fail is the real gate).
    penalty = (
        len(res.forbidden_hits) * 50
        + len(res.uncited_claims) * 15
        + (0 if res.has_disclaimer else 25)
    )
    res.score = max(0.0, 100.0 - penalty)
    return res
