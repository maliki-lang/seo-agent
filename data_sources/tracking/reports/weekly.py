from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from ..checks.suite import QualityCheckSuite
from ..config import TrackingConfig
from ..enums import ChannelClass, ReportStatus, RunStatus, RunType, Severity
from ..exceptions import TrackingError
from ..models import RunLog
from ..sinks.alerts import AlertService
from ..sinks.lark_base import LarkBaseSink
from ..storage import TrackingStore
from ..transforms.normalize import utc_now_iso
from .metrics_calc import compute_period_metrics
from .opportunities import OpportunityBuilder


class WeeklyReportService:
    def __init__(
        self,
        config: TrackingConfig,
        store: TrackingStore,
        *,
        lark_sink: Optional[LarkBaseSink] = None,
        alert_service: Optional[AlertService] = None,
    ):
        self.config = config
        self.store = store
        self.lark_sink = lark_sink or LarkBaseSink(config)
        self.alert_service = alert_service or AlertService(config, store)

    def generate(
        self,
        *,
        period_end: Optional[date] = None,
        publish: bool = False,
    ) -> Dict[str, Any]:
        self.store.migrate()
        end = period_end or date.today()
        start = end - timedelta(days=6)
        report_id = f"weekly-{start.isoformat()}-to-{end.isoformat()}"

        run = RunLog(
            run_id=str(uuid.uuid4()),
            run_type=RunType.WEEKLY,
            as_of_date=end,
            started_at=utc_now_iso(),
            status=RunStatus.RUNNING,
            requested_collectors=[],
            completed_collectors=[],
        )
        self.store.insert_run(run)

        quality = QualityCheckSuite(self.config, self.store).run_for_run(run.run_id)
        metrics, _fingerprint = compute_period_metrics(self.store, start, end)
        metric_map = _index_metrics(metrics)
        prior_start = start - timedelta(days=7)
        prior_end = start - timedelta(days=1)
        prior_metrics, _ = compute_period_metrics(self.store, prior_start, prior_end)
        prior_map = _index_metrics(prior_metrics)

        baseline = self.store.fetchall(
            """
            SELECT baseline_id, metric_name, segment_json, metric_value, status
            FROM baseline
            WHERE status = 'locked'
            ORDER BY locked_at DESC
            """
        )
        baseline_id = baseline[0]["baseline_id"] if baseline else None
        baseline_map = {
            (row["metric_name"], row["segment_json"]): row["metric_value"] for row in baseline
            if not baseline_id or row["baseline_id"] == baseline_id
        }

        opportunities = OpportunityBuilder(self.config, self.store).build(
            report_id=report_id,
            end_date=end,
            limit=10,
        )

        seo_clicks = _num(metric_map.get(("gsc_clicks", "{}")))
        prior_clicks = _num(prior_map.get(("gsc_clicks", "{}")))
        branded = _num(metric_map.get(("gsc_branded_clicks", json.dumps({"brand": True}, sort_keys=True))))
        nonbranded = _num(metric_map.get(("gsc_nonbranded_clicks", json.dumps({"brand": False}, sort_keys=True))))
        organic_sessions = _num(
            metric_map.get(("ga4_organic_sessions", json.dumps({"channel": "organic_search"}, sort_keys=True)))
        )
        ai_sessions = self._ai_referral_sessions(start, end)
        geo_mention = metric_map.get(("geo_combined_mention_rate", json.dumps({"engine": "combined"}, sort_keys=True)))
        baseline_clicks = _num(baseline_map.get(("gsc_clicks", "{}"))) if baseline_map else None

        facts = []
        if seo_clicks is not None and prior_clicks is not None:
            delta = seo_clicks - prior_clicks
            facts.append(f"GSC clicks {seo_clicks:.0f} vs prior week {prior_clicks:.0f} ({delta:+.0f}).")
        if baseline_clicks is not None and seo_clicks is not None:
            facts.append(f"GSC clicks vs locked baseline {baseline_clicks:.0f}: {seo_clicks - baseline_clicks:+.0f}.")
        if geo_mention not in (None, "None"):
            facts.append(f"Combined GEO mention rate {geo_mention}.")
        if not facts:
            facts.append("Insufficient complete data for period comparisons; treat conclusions as provisional.")

        summary = {
            "report_id": report_id,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "data_freshness": self._freshness_notes(end),
            "run_id": run.run_id,
            "quality_status": quality.get("gate_status"),
            "quality_counts": quality.get("counts"),
            "executive_summary": " ".join(facts),
            "seo_vs_baseline": {
                "gsc_clicks": seo_clicks,
                "baseline_gsc_clicks": baseline_clicks,
                "prior_week_gsc_clicks": prior_clicks,
                "weighted_ctr": metric_map.get(("gsc_weighted_ctr", "{}")),
                "weighted_avg_position": metric_map.get(("gsc_weighted_avg_position", "{}")),
            },
            "geo_vs_baseline": {
                "combined_mention_rate": geo_mention,
                "combined_citation_rate": metric_map.get(
                    ("geo_combined_citation_rate", json.dumps({"engine": "combined"}, sort_keys=True))
                ),
                "chatgpt_mention_rate": metric_map.get(
                    ("geo_mention_rate", json.dumps({"engine": "chatgpt"}, sort_keys=True))
                ),
                "perplexity_mention_rate": metric_map.get(
                    ("geo_mention_rate", json.dumps({"engine": "perplexity"}, sort_keys=True))
                ),
            },
            "branded_vs_nonbranded": {
                "branded_clicks": branded,
                "nonbranded_clicks": nonbranded,
            },
            "keyword_distribution": {
                "top3_rate": metric_map.get(("serp_visibility_top3_rate", "{}")),
                "top10_rate": metric_map.get(("serp_visibility_top10_rate", "{}")),
                "absent_rate": metric_map.get(("serp_visibility_absent_rate", "{}")),
            },
            "traffic_conversion": {
                "organic_sessions": organic_sessions,
                "ai_referral_sessions": ai_sessions,
                "organic_conversion_rate": metric_map.get(
                    (
                        "ga4_organic_conversion_rate",
                        json.dumps({"channel": "organic_search"}, sort_keys=True),
                    )
                ),
            },
            "data_issues": {
                "critical_failures": quality.get("critical_failures") or [],
                "error_failures": quality.get("error_failures") or [],
                "warnings": quality.get("warnings") or [],
            },
            "top_actions": [
                {
                    "opportunity_id": row["opportunity_id"],
                    "category": row["category"],
                    "target_query_or_question": row["target_query_or_question"],
                    "target_page": row["target_page"],
                    "proposed_action": row["proposed_action"],
                    "priority_score": row["priority_score"],
                    "source_row_references_json": row["source_row_references_json"],
                }
                for row in opportunities
            ],
            "baseline_id": baseline_id,
            "opportunity_count": len(opportunities),
        }

        status = ReportStatus.DRAFT
        lark_record_id = None
        published_at = None
        if quality.get("gate_status") == "failed":
            status = ReportStatus.FAILED
            self.alert_service.emit(
                run_id=run.run_id,
                alert_type="weekly_report_quality_failed",
                severity=Severity.CRITICAL,
                summary="Weekly report blocked by critical quality failures",
                details={"critical_failures": quality.get("critical_failures")},
                recommended_action="Inspect quality_check_log and fix collectors before publishing conclusions",
            )
        elif publish:
            try:
                result = self.lark_sink.upsert_weekly_summary(summary)
                self.lark_sink.upsert_opportunities(opportunities)
                lark_record_id = result["lark_record_id"]
                status = ReportStatus.PUBLISHED
                published_at = utc_now_iso()
            except TrackingError as exc:
                status = ReportStatus.FAILED
                self.alert_service.emit(
                    run_id=run.run_id,
                    alert_type="weekly_report_publish_failed",
                    severity=Severity.ERROR,
                    summary=str(exc),
                    details={"error_class": exc.error_code},
                    recommended_action="Verify Lark credentials/table IDs and retry with --publish",
                )

        self.store.upsert_weekly_report(
            {
                "report_id": report_id,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "baseline_id": baseline_id,
                "status": status.value,
                "summary_json": json.dumps(summary, sort_keys=True, default=str),
                "quality_status": summary["quality_status"],
                "opportunity_count": len(opportunities),
                "lark_record_id": lark_record_id,
                "created_at": utc_now_iso(),
                "published_at": published_at,
            }
        )
        self.store.update_run(
            run.run_id,
            status=RunStatus.SUCCEEDED if status != ReportStatus.FAILED else RunStatus.FAILED,
            finished_at=utc_now_iso(),
        )
        return {
            "report_id": report_id,
            "run_id": run.run_id,
            "status": status.value,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "quality_status": summary["quality_status"],
            "opportunity_count": len(opportunities),
            "lark_record_id": lark_record_id,
            "published": status == ReportStatus.PUBLISHED,
            "summary": summary,
        }

    def _ai_referral_sessions(self, start: date, end: date) -> Optional[float]:
        rows = self.store.fetchall(
            """
            SELECT COALESCE(SUM(sessions), 0) AS sessions
            FROM ga4_daily
            WHERE date >= ? AND date <= ? AND channel_class = ?
            """,
            (start.isoformat(), end.isoformat(), ChannelClass.AI_REFERRAL.value),
        )
        if not rows:
            return None
        return float(rows[0]["sessions"])

    def _freshness_notes(self, as_of: date) -> Dict[str, Any]:
        notes = {"as_of_date": as_of.isoformat()}
        for table, column in (("gsc_daily", "date"), ("ga4_daily", "date"), ("serp_daily", "date")):
            if self.store.count(table) == 0:
                notes[table] = "unavailable"
                continue
            row = self.store.fetchall(f"SELECT MAX({column}) AS d FROM {table}")[0]
            notes[table] = row["d"]
        return notes


def _index_metrics(metrics: List[Dict[str, Any]]) -> Dict[tuple, Any]:
    return {(item["metric_name"], item["segment_json"]): item.get("metric_value") for item in metrics}


def _num(value: Any) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
