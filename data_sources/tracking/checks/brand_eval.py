from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..config import REPO_ROOT, TrackingConfig
from ..enums import CheckStatus, Severity
from ..transforms.brand_label import BrandClassifier
from .base import Check, CheckResult

DEFAULT_BRAND_EVAL_PATH = "config/brand_eval_set.csv"
ACCURACY_THRESHOLD = 0.95


@dataclass(frozen=True)
class BrandEvalRow:
    query_id: str
    query: str
    label: str  # brand | non_brand


def load_brand_eval_set(path: Optional[Path] = None) -> List[BrandEvalRow]:
    resolved = path or (REPO_ROOT / DEFAULT_BRAND_EVAL_PATH)
    if not resolved.is_absolute():
        resolved = REPO_ROOT / resolved
    rows: List[BrandEvalRow] = []
    with resolved.open(encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            label = (raw.get("label") or "").strip().lower()
            if label not in {"brand", "non_brand"}:
                raise ValueError(f"Invalid brand eval label: {label}")
            rows.append(
                BrandEvalRow(
                    query_id=(raw.get("query_id") or "").strip(),
                    query=(raw.get("query") or "").strip(),
                    label=label,
                )
            )
    return rows


def evaluate_brand_classifier(
    classifier: BrandClassifier,
    rows: Sequence[BrandEvalRow],
) -> Dict[str, object]:
    tp = fp = tn = fn = 0
    mistakes: List[Dict[str, str]] = []
    for row in rows:
        predicted = classifier.is_brand(row.query)
        actual = row.label == "brand"
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
            mistakes.append({"query_id": row.query_id, "query": row.query, "error": "false_positive"})
        elif not predicted and not actual:
            tn += 1
        else:
            fn += 1
            mistakes.append({"query_id": row.query_id, "query": row.query, "error": "false_negative"})
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "n": total,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "mistakes": mistakes[:20],
        "rule_version": classifier.version,
    }


class BrandClassifierQualityCheck(Check):
    name = "brand_classifier_quality"
    severity = Severity.ERROR

    def run(self, *, config: TrackingConfig, **kwargs) -> CheckResult:
        rows = load_brand_eval_set()
        classifier = BrandClassifier.from_config(config)
        metrics = evaluate_brand_classifier(classifier, rows)
        n = int(metrics["n"])
        accuracy = float(metrics["accuracy"])
        ok = n >= 200 and accuracy >= ACCURACY_THRESHOLD
        return CheckResult(
            check_name=self.name,
            scope="brand",
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            severity=self.severity,
            threshold=f"n>=200;accuracy>={ACCURACY_THRESHOLD}",
            observed_value=(
                f"n={n};accuracy={accuracy:.4f};"
                f"precision={float(metrics['precision']):.4f};"
                f"recall={float(metrics['recall']):.4f}"
            ),
            details=metrics,
        )
