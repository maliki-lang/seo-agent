from data_sources.tracking.checks.brand_eval import (
    evaluate_brand_classifier,
    load_brand_eval_set,
)
from data_sources.tracking.config import TrackingConfig
from data_sources.tracking.transforms.brand_label import BrandClassifier


def test_brand_eval_set_has_200_labels():
    rows = load_brand_eval_set()
    assert len(rows) == 200
    assert sum(1 for row in rows if row.label == "brand") == 100
    assert sum(1 for row in rows if row.label == "non_brand") == 100


def test_brand_classifier_meets_accuracy_target():
    rows = load_brand_eval_set()
    metrics = evaluate_brand_classifier(BrandClassifier.from_config(TrackingConfig()), rows)
    assert metrics["n"] == 200
    assert metrics["accuracy"] >= 0.95
    assert "precision" in metrics
    assert "recall" in metrics
