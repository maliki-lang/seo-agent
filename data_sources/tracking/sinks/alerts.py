from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional

import requests

from ..config import TrackingConfig
from ..enums import AlertStatus, Severity
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso

PostFn = Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]


def _default_post(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    if not response.content:
        return {"ok": True, "status_code": response.status_code}
    try:
        return response.json()
    except ValueError:
        return {"ok": True, "status_code": response.status_code, "text": response.text[:200]}


def _redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    blocked = {"authorization", "api_key", "token", "secret", "password", "cookie"}
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if any(part in key.lower() for part in blocked):
            out[key] = "[redacted]"
        elif isinstance(value, dict):
            out[key] = _redact(value)
        else:
            out[key] = value
    return out


class AlertService:
    """Deduplicated operational alerts (webhook + local ledger)."""

    def __init__(
        self,
        config: TrackingConfig,
        store: TrackingStore,
        *,
        post_fn: Optional[PostFn] = None,
    ):
        self.config = config
        self.store = store
        self._post = post_fn or _default_post

    def emit(
        self,
        *,
        run_id: str,
        alert_type: str,
        severity: Severity,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
        recommended_action: str = "",
    ) -> Dict[str, Any]:
        existing = self.store.fetchall(
            "SELECT alert_id, status, attempts FROM alerts WHERE run_id = ? AND alert_type = ?",
            (run_id, alert_type),
        )
        if existing:
            row = existing[0]
            return {
                "alert_id": row["alert_id"],
                "status": row["status"],
                "deduplicated": True,
                "attempts": int(row["attempts"] or 0),
            }

        alert_id = str(uuid.uuid4())
        details_payload = {
            "environment": self.config.env,
            "run_id": run_id,
            "alert_type": alert_type,
            "severity": severity.value,
            "summary": summary,
            "recommended_action": recommended_action,
            "details": _redact(details or {}),
        }
        self.store.insert_alert(
            {
                "alert_id": alert_id,
                "run_id": run_id,
                "severity": severity.value,
                "alert_type": alert_type,
                "summary": summary,
                "details_redacted": json.dumps(details_payload, sort_keys=True),
                "status": AlertStatus.PENDING.value,
                "attempts": 0,
                "created_at": utc_now_iso(),
                "sent_at": None,
                "external_reference": None,
            }
        )

        if not self.config.lark_alert_webhook_url:
            self.store.update_alert(
                alert_id,
                status=AlertStatus.FAILED.value,
                attempts=1,
                external_reference="webhook-not-configured",
            )
            return {
                "alert_id": alert_id,
                "status": AlertStatus.FAILED.value,
                "deduplicated": False,
                "reason": "LARK_ALERT_WEBHOOK_URL not configured",
            }

        try:
            body = {
                "msg_type": "text",
                "content": {
                    "text": (
                        f"[{self.config.env}] {severity.value.upper()} {alert_type}\n"
                        f"run_id={run_id}\n{summary}\n"
                        f"action={recommended_action or 'See docs/tracking-runbook.md'}"
                    )
                },
            }
            response = self._post(
                self.config.lark_alert_webhook_url,
                body,
                {"Content-Type": "application/json"},
            )
            self.store.update_alert(
                alert_id,
                status=AlertStatus.SENT.value,
                attempts=1,
                sent_at=utc_now_iso(),
                external_reference=str(
                    response.get("message_id") or response.get("data") or "webhook-ok"
                )[:200],
            )
            return {"alert_id": alert_id, "status": AlertStatus.SENT.value, "deduplicated": False}
        except Exception as exc:  # noqa: BLE001
            self.store.update_alert(
                alert_id,
                status=AlertStatus.FAILED.value,
                attempts=1,
                external_reference=type(exc).__name__,
            )
            return {
                "alert_id": alert_id,
                "status": AlertStatus.FAILED.value,
                "deduplicated": False,
                "error_class": type(exc).__name__,
            }
