"""Experiment cost ledger (Phase 16)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from ..config import TrackingConfig
from ..enums import ExperimentCostType
from ..exceptions import DataQualityError
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso

ALLOWED_COST_TYPES = {item.value for item in ExperimentCostType}


def add_experiment_cost(
    store: TrackingStore,
    config: TrackingConfig,
    *,
    experiment_id: str,
    cost_type: str,
    quantity: float,
    unit_cost: float,
    currency: Optional[str] = None,
    incurred_at: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    notes: Optional[str] = None,
    conversion_rate: Optional[float] = None,
    conversion_rate_date: Optional[str] = None,
) -> Dict[str, Any]:
    store.migrate()
    experiment = store.get_experiment(experiment_id)
    if not experiment:
        raise DataQualityError(f"Unknown experiment_id: {experiment_id}")
    if cost_type not in ALLOWED_COST_TYPES:
        raise DataQualityError(f"Invalid cost_type: {cost_type}")
    if float(quantity) < 0 or float(unit_cost) < 0:
        raise DataQualityError("quantity and unit_cost must be non-negative")

    amount = round(float(quantity) * float(unit_cost), 6)
    currency = (currency or config.economics.default_currency).strip().upper()
    existing_currency = (experiment.get("cost_currency") or "").strip().upper()
    if existing_currency and currency != existing_currency:
        if conversion_rate is None or not conversion_rate_date:
            raise DataQualityError(
                "Mixed currencies require conversion_rate and conversion_rate_date"
            )
        amount = round(amount * float(conversion_rate), 6)
        currency = existing_currency
        notes = (notes or "") + f" [converted@{conversion_rate} on {conversion_rate_date}]"

    now = utc_now_iso()
    cost_id = str(uuid.uuid4())
    row = {
        "cost_id": cost_id,
        "experiment_id": experiment_id,
        "cost_type": cost_type,
        "quantity": float(quantity),
        "unit_cost": float(unit_cost),
        "amount": amount,
        "currency": currency,
        "incurred_at": incurred_at or now,
        "evidence_reference": evidence_reference,
        "notes": notes,
        "created_at": now,
    }
    store.insert_experiment_cost(row)
    actual = store.recalculate_experiment_actual_cost(experiment_id)
    return {
        "cost_id": cost_id,
        "experiment_id": experiment_id,
        "amount": amount,
        "currency": currency,
        "actual_cost": actual,
        "computed_from": "quantity * unit_cost",
    }


def cost_per_incremental_click(
    *,
    cost: Optional[float],
    incremental_clicks: Optional[float],
) -> Optional[float]:
    if cost is None or incremental_clicks is None:
        return None
    if float(incremental_clicks) <= 0:
        return None
    return round(float(cost) / float(incremental_clicks), 6)
