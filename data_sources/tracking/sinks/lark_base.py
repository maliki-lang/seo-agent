from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import requests

from ..config import TrackingConfig
from ..exceptions import ConfigurationError, TrackingError

PostFn = Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]
TokenFn = Callable[[], str]


def _default_post(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json() if response.content else {"ok": True}


class LarkBaseSink:
    """Operational Lark Base upserts for weekly reports and opportunities."""

    def __init__(
        self,
        config: TrackingConfig,
        *,
        post_fn: Optional[PostFn] = None,
        token_fn: Optional[TokenFn] = None,
    ):
        self.config = config
        self._post = post_fn or _default_post
        self._token_fn = token_fn

    def _tenant_token(self) -> str:
        if self._token_fn is not None:
            return self._token_fn()
        if not self.config.lark_app_id or not self.config.lark_app_secret:
            raise ConfigurationError(
                "LARK_APP_ID and LARK_APP_SECRET are required to publish to Lark Base"
            )
        response = self._post(
            "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.config.lark_app_id, "app_secret": self.config.lark_app_secret},
            {"Content-Type": "application/json"},
        )
        token = response.get("tenant_access_token") or ""
        if not token:
            raise TrackingError("Lark tenant access token missing from response")
        return token

    def upsert_weekly_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.lark_base_app_token or not self.config.lark_weekly_table_id:
            raise ConfigurationError(
                "LARK_BASE_APP_TOKEN and LARK_WEEKLY_TABLE_ID are required for publish"
            )
        token = self._tenant_token()
        external_key = summary["report_id"]
        fields = {
            "report_id": external_key,
            "period_start": summary["period_start"],
            "period_end": summary["period_end"],
            "quality_status": summary.get("quality_status"),
            "opportunity_count": summary.get("opportunity_count"),
            "executive_summary": (summary.get("executive_summary") or "")[:1800],
            "baseline_id": summary.get("baseline_id") or "",
        }
        url = (
            f"https://open.larksuite.com/open-apis/bitable/v1/apps/"
            f"{self.config.lark_base_app_token}/tables/{self.config.lark_weekly_table_id}/records"
        )
        response = self._post(
            url,
            {"fields": fields, "client_token": external_key},
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        record_id = (
            (response.get("data") or {}).get("record", {}).get("record_id")
            or response.get("record_id")
            or external_key
        )
        return {"lark_record_id": record_id, "external_key": external_key, "response_ok": True}

    def upsert_opportunities(self, opportunities: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.config.lark_base_app_token or not self.config.lark_opportunities_table_id:
            return {"upserted": 0, "skipped": True, "reason": "opportunities table not configured"}
        token = self._tenant_token()
        record_ids = []
        for row in opportunities:
            # Preserve human-owned review fields — only project machine-owned scores/status.
            fields = {
                "opportunity_id": row["opportunity_id"],
                "report_id": row["report_id"],
                "category": row.get("category") or row.get("source_type") or "",
                "target_query_or_question": (row.get("target_query_or_question") or "")[:500],
                "target_page": row.get("target_page") or "",
                "priority_score": row.get("priority_score"),
                "proposed_action": (row.get("proposed_action") or "")[:1000],
                "action_type": row.get("action_type") or "",
                "expected_incremental_clicks": row.get("expected_incremental_clicks"),
                "portfolio_rank": row.get("portfolio_rank"),
            }
            # Do not send reviewed_by / reviewed_at / review_status / reviewer reasons —
            # those remain human-owned in Lark.
            url = (
                f"https://open.larksuite.com/open-apis/bitable/v1/apps/"
                f"{self.config.lark_base_app_token}/tables/{self.config.lark_opportunities_table_id}/records"
            )
            response = self._post(
                url,
                {"fields": fields, "client_token": row["opportunity_id"]},
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            record_ids.append(
                (response.get("data") or {}).get("record", {}).get("record_id")
                or row["opportunity_id"]
            )
        return {"upserted": len(record_ids), "record_ids": record_ids}

    def _upsert_rows(
        self,
        *,
        table_id: str,
        rows: List[Dict[str, Any]],
        external_key_fn,
        fields_fn,
    ) -> Dict[str, Any]:
        if not self.config.lark_base_app_token or not table_id:
            return {"upserted": 0, "skipped": True, "reason": "table not configured"}
        token = self._tenant_token()
        record_ids = []
        for row in rows:
            external_key = external_key_fn(row)
            fields = fields_fn(row)
            url = (
                f"https://open.larksuite.com/open-apis/bitable/v1/apps/"
                f"{self.config.lark_base_app_token}/tables/{table_id}/records"
            )
            response = self._post(
                url,
                {"fields": fields, "client_token": external_key},
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            record_ids.append(
                (response.get("data") or {}).get("record", {}).get("record_id")
                or external_key
            )
        return {"upserted": len(record_ids), "record_ids": record_ids}

    def upsert_experiments(self, experiments: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._upsert_rows(
            table_id=self.config.lark_experiments_table_id,
            rows=experiments,
            external_key_fn=lambda r: r["experiment_id"],
            fields_fn=lambda r: {
                "experiment_id": r["experiment_id"],
                "opportunity_id": r.get("opportunity_id"),
                "status": r.get("status"),
                "action_type": r.get("action_type"),
                "target_page": r.get("target_page") or "",
                "owner": r.get("owner") or "",
                "actual_cost": r.get("actual_cost"),
                "cost_currency": r.get("cost_currency") or "",
                "published_at": r.get("published_at") or "",
            },
        )

    def upsert_experiment_costs(self, costs: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._upsert_rows(
            table_id=self.config.lark_experiment_costs_table_id,
            rows=costs,
            external_key_fn=lambda r: r["cost_id"],
            fields_fn=lambda r: {
                "cost_id": r["cost_id"],
                "experiment_id": r.get("experiment_id"),
                "cost_type": r.get("cost_type"),
                "amount": r.get("amount"),
                "currency": r.get("currency"),
                "incurred_at": r.get("incurred_at") or "",
            },
        )

    def upsert_experiment_measurements(
        self, measurements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self._upsert_rows(
            table_id=self.config.lark_experiment_measurements_table_id,
            rows=measurements,
            external_key_fn=lambda r: f"{r['experiment_id']}:{r['checkpoint_days']}",
            fields_fn=lambda r: {
                "experiment_id": r.get("experiment_id"),
                "checkpoint_days": r.get("checkpoint_days"),
                "outcome": r.get("outcome"),
                "adjusted_incremental_clicks": r.get("adjusted_incremental_clicks"),
                "adjustment_method": r.get("adjustment_method"),
                "confidence_label": r.get("confidence_label"),
                "quality_status": r.get("quality_status"),
            },
        )

    def upsert_experiment_outcomes(self, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._upsert_rows(
            table_id=self.config.lark_experiment_outcomes_table_id,
            rows=outcomes,
            external_key_fn=lambda r: r.get("external_key")
            or f"{r['experiment_id']}:latest_outcome",
            fields_fn=lambda r: {
                "experiment_id": r.get("experiment_id"),
                "outcome": r.get("outcome"),
                "checkpoint_days": r.get("checkpoint_days"),
                "adjusted_incremental_clicks": r.get("adjusted_incremental_clicks"),
                "actual_cost": r.get("actual_cost"),
                "actual_cost_per_incremental_click": r.get(
                    "actual_cost_per_incremental_click"
                ),
                "status": r.get("status"),
            },
        )
