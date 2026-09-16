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
            fields = {
                "opportunity_id": row["opportunity_id"],
                "report_id": row["report_id"],
                "category": row["category"],
                "target_query_or_question": row["target_query_or_question"][:500],
                "target_page": row["target_page"],
                "priority_score": row["priority_score"],
                "proposed_action": row["proposed_action"][:1000],
            }
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
