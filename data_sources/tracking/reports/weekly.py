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

        opportunity_block = self._phase15_opportunities(report_id=report_id, end_date=end)
        opportunities = opportunity_block.get("opportunities") or []
        experiment_block = self._experiment_sections(as_of=end)

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
        if opportunity_block.get("blocked"):
            facts.append(f"Opportunity portfolio blocked: {opportunity_block.get('reason')}.")
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
                "note": "Site-wide observed movement; not attributed experiment impact.",
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
            "opportunity_portfolio": opportunity_block,
            "top_actions": [
                {
                    "opportunity_id": row.get("opportunity_id"),
                    "category": row.get("category") or row.get("source_type"),
                    "target_query_or_question": row.get("target_query_or_question"),
                    "target_page": row.get("target_page"),
                    "proposed_action": row.get("proposed_action"),
                    "action_type": row.get("action_type"),
                    "priority_score": row.get("priority_score"),
                    "expected_incremental_clicks": row.get("expected_incremental_clicks"),
                    "source_row_references_json": row.get("source_row_references_json"),
                    "portfolio_rank": row.get("portfolio_rank"),
                }
                for row in opportunities
            ],
            "experiments": experiment_block,
            "baseline_id": baseline_id,
            "opportunity_count": len(opportunities),
            "opportunity_version": "v2",
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
                self.lark_sink.upsert_experiments(experiment_block.get("register") or [])
                self.lark_sink.upsert_experiment_costs(experiment_block.get("costs") or [])
                self.lark_sink.upsert_experiment_measurements(
                    experiment_block.get("measurements") or []
                )
                self.lark_sink.upsert_experiment_outcomes(
                    experiment_block.get("outcome_summary") or []
                )
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
            "opportunity_blocked": bool(opportunity_block.get("blocked")),
            "experiment_count": len(experiment_block.get("register") or []),
            "lark_record_id": lark_record_id,
            "published": status == ReportStatus.PUBLISHED,
            "summary": summary,
        }

    def _activated_catalogue_version(self) -> Optional[str]:
        rows = self.store.fetchall(
            """
            SELECT catalogue_version
            FROM keyword_catalog
            WHERE active = 1 AND approval_status = 'activated'
            ORDER BY approved_at DESC
            LIMIT 1
            """
        )
        if rows:
            return rows[0]["catalogue_version"]
        return None

    def _phase15_opportunities(self, *, report_id: str, end_date: date) -> Dict[str, Any]:
        from ..opportunities.builder import build_opportunity_portfolio

        catalogue_version = self._activated_catalogue_version()
        if not catalogue_version:
            return {
                "blocked": True,
                "reason": "no_activated_evidence_catalogue",
                "gate_status": "blocked",
                "opportunities": [],
                "blocked_sources": ["opportunity_portfolio_v2"],
                "note": "Do not fall back to legacy v1 opportunities.",
            }
        try:
            built = build_opportunity_portfolio(
                self.store,
                self.config,
                catalogue_version=catalogue_version,
                period_end=end_date,
                limit=10,
                owner="weekly-report",
                report_id=f"{report_id}-opp",
            )
        except TrackingError as exc:
            return {
                "blocked": True,
                "reason": str(exc),
                "gate_status": "blocked",
                "opportunities": [],
                "blocked_sources": list((getattr(exc, "redacted_detail", None) and []) or []),
            }
        rows = [
            dict(r)
            for r in self.store.fetchall(
                "SELECT * FROM opportunities WHERE report_id = ? AND opportunity_version = 'v2'",
                (built["report_id"],),
            )
        ]
        return {
            "blocked": False,
            "catalogue_version": catalogue_version,
            "report_id": built["report_id"],
            "gate_status": (built.get("gates") or {}).get("gate_status"),
            "gates": built.get("gates"),
            "blocked_detectors": built.get("blocked_detectors") or {},
            "concentration_warning": built.get("concentration_warning"),
            "coverage_warning": built.get("coverage_warning"),
            "opportunities": rows,
            "selected_count": len(rows),
        }

    def _experiment_sections(self, *, as_of: date) -> Dict[str, Any]:
        experiments = [dict(r) for r in self.store.fetchall("SELECT * FROM seo_experiments")]
        due = {"14": [], "28": [], "56": []}
        newly_classified = []
        costs = []
        measurements = []
        outcomes = []
        for exp in experiments:
            if not exp.get("published_at"):
                continue
            published = date.fromisoformat(str(exp["published_at"])[:10])
            for days in (14, 28, 56):
                due_date = published + timedelta(days=days)
                existing = self.store.get_experiment_measurement(exp["experiment_id"], days)
                if existing:
                    measurements.append(dict(existing))
                    if existing["outcome"] not in {"provisional"} and days >= 28:
                        newly_classified.append(
                            {
                                "experiment_id": exp["experiment_id"],
                                "checkpoint_days": days,
                                "outcome": existing["outcome"],
                                "adjusted_incremental_clicks": existing.get(
                                    "adjusted_incremental_clicks"
                                ),
                            }
                        )
                elif due_date <= as_of:
                    due[str(days)].append(
                        {
                            "experiment_id": exp["experiment_id"],
                            "action_type": exp["action_type"],
                            "target_page": exp["target_page"],
                            "due_date": due_date.isoformat(),
                        }
                    )
            for cost in self.store.list_experiment_costs(exp["experiment_id"]):
                costs.append(dict(cost))
            latest = self.store.list_experiment_measurements(exp["experiment_id"])
            if latest:
                last = dict(latest[-1])
                incr = last.get("adjusted_incremental_clicks")
                actual = float(exp.get("actual_cost") or 0)
                cost_per = (
                    round(actual / float(incr), 6)
                    if incr is not None and float(incr) > 0
                    else None
                )
                outcomes.append(
                    {
                        "external_key": f"{exp['experiment_id']}:latest_outcome",
                        "experiment_id": exp["experiment_id"],
                        "outcome": last.get("outcome"),
                        "checkpoint_days": last.get("checkpoint_days"),
                        "adjusted_incremental_clicks": incr,
                        "actual_cost": actual,
                        "actual_cost_per_incremental_click": cost_per,
                        "status": exp.get("status"),
                    }
                )
        return {
            "register": experiments,
            "due_for_measurement": due,
            "newly_classified": newly_classified,
            "costs": costs,
            "measurements": measurements,
            "outcome_summary": outcomes,
            "total_intervention_spend": round(sum(float(c.get("amount") or 0) for c in costs), 6),
            "note": (
                "Attributed experiment impact is separate from site-wide observed movement."
            ),
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

    def _freshness_notes(self, end: date) -> Dict[str, Any]:
        return {
            "as_of": end.isoformat(),
            "gsc_lag_days": self.config.freshness.gsc_days,
            "ga4_lag_days": self.config.freshness.ga4_days,
        }


def _index_metrics(metrics: List[Dict[str, Any]]) -> Dict[tuple, Any]:
    return {(m["metric_name"], m.get("segment_json") or "{}"): m.get("metric_value") for m in metrics}


def _num(value: Any) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
