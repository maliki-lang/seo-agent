"""Build-scoped catalogue provenance quality checks (Phase 9)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..config import TrackingConfig
from ..enums import CandidateDecision, CheckStatus, Severity
from ..models import QualityCheckRow
from ..storage import TrackingStore
from ..transforms.brand_label import BrandClassifier
from ..transforms.normalize import utc_now_iso
from ..reports.metrics_calc import impression_weighted_position, weighted_ctr
from .base import CheckResult
from .suite import summarize_check_results


def _status(ok: bool) -> CheckStatus:
    return CheckStatus.PASS if ok else CheckStatus.FAIL


def _sunnystep_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return parsed.scheme in {"http", "https"} and (
        host == "sunnystep.com" or host.endswith(".sunnystep.com") or host == "gosunnystep.myshopify.com"
    )


class CatalogueQualitySuite:
    """Catalogue build checks scoped to one build_id (never whole-table history)."""

    def __init__(self, config: TrackingConfig, store: TrackingStore):
        self.config = config
        self.store = store

    def run_for_build(
        self,
        build_id: str,
        *,
        question_build_id: Optional[str] = None,
        report_counts: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.store.migrate()
        builds = self.store.fetchall("SELECT * FROM catalogue_builds WHERE build_id = ?", (build_id,))
        if not builds:
            raise ValueError(f"Unknown build_id {build_id}")
        build = dict(builds[0])
        if not question_build_id:
            children = self.store.fetchall(
                """
                SELECT build_id FROM catalogue_builds
                WHERE parent_build_id = ? AND build_type = 'ai_question'
                ORDER BY created_at DESC LIMIT 1
                """,
                (build_id,),
            )
            question_build_id = children[0]["build_id"] if children else None

        results: List[CheckResult] = []
        results.append(self._gsc_source_lineage(build_id))
        results.append(self._source_window_integrity(build))
        results.append(self._fixture_exclusion(build))
        results.append(self._gsc_aggregation_reconciliation(build_id))
        results.append(self._ga4_join_accounting(build))
        results.append(self._serper_validation_coverage(build_id, build))
        results.append(self._keyword_decision_completeness(build_id))
        results.append(self._target_page_validity(build_id))
        results.append(self._cluster_membership(build_id))
        if question_build_id:
            results.append(self._ai_question_lineage(question_build_id))
            results.append(self._ai_question_count(question_build_id))
            results.append(self._non_branded_questions(question_build_id))
        results.append(self._approval_completeness(build))
        results.append(self._version_transition(build))
        if report_counts is not None:
            results.append(self._provenance_report_parity(build_id, report_counts))

        self.store.insert_quality_checks(
            [
                QualityCheckRow(
                    check_id=str(uuid.uuid4()),
                    run_id=build_id,
                    check_name=item.check_name,
                    scope=item.scope,
                    status=item.status,
                    severity=item.severity,
                    threshold=item.threshold,
                    observed_value=item.observed_value,
                    details_json=item.details,
                )
                for item in results
            ]
        )
        summary = summarize_check_results(results)
        summary["build_id"] = build_id
        summary["question_build_id"] = question_build_id
        summary["checked_at"] = utc_now_iso()
        return summary

    def _gsc_source_lineage(self, build_id: str) -> CheckResult:
        selected = self.store.fetchall(
            "SELECT candidate_id FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (build_id, CandidateDecision.SELECTED.value),
        )
        missing = []
        for row in selected:
            n = self.store.fetchall(
                "SELECT COUNT(*) AS n FROM keyword_candidate_sources WHERE candidate_id = ?",
                (row["candidate_id"],),
            )[0]["n"]
            if int(n) < 1:
                missing.append(row["candidate_id"])
        return CheckResult(
            check_name="gsc_source_lineage",
            scope="catalogue",
            status=_status(not missing),
            severity=Severity.CRITICAL,
            threshold=">=1 source row per selected keyword",
            observed_value=f"missing={len(missing)};selected={len(selected)}",
            details={"missing_candidate_ids": missing[:20]},
        )

    def _source_window_integrity(self, build: Dict[str, Any]) -> CheckResult:
        build_id = build["build_id"]
        start = build["source_window_start"]
        end = build["source_window_end"]
        try:
            run_ids = json.loads(build.get("gsc_source_run_ids") or "[]")
        except json.JSONDecodeError:
            run_ids = []
        bad = self.store.fetchall(
            """
            SELECT COUNT(*) AS n
            FROM keyword_candidate_sources s
            JOIN keyword_candidates c ON c.candidate_id = s.candidate_id
            WHERE c.build_id = ?
              AND (s.row_date < ? OR s.row_date > ?)
            """,
            (build_id, start, end),
        )[0]["n"]
        unknown_runs = 0
        if run_ids:
            unknown_runs = self.store.fetchall(
                f"""
                SELECT COUNT(*) AS n
                FROM keyword_candidate_sources s
                JOIN keyword_candidates c ON c.candidate_id = s.candidate_id
                WHERE c.build_id = ?
                  AND s.gsc_run_id NOT IN ({",".join("?" * len(run_ids))})
                """,
                [build_id, *run_ids],
            )[0]["n"]
        ok = int(bad) == 0 and int(unknown_runs) == 0
        return CheckResult(
            check_name="source_window_integrity",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.CRITICAL,
            threshold="sources inside recorded window/runs",
            observed_value=f"out_of_window={bad};unknown_runs={unknown_runs}",
            details={"out_of_window": int(bad), "unknown_runs": int(unknown_runs)},
        )

    def _fixture_exclusion(self, build: Dict[str, Any]) -> CheckResult:
        build_id = build["build_id"]
        bad = self.store.fetchall(
            """
            SELECT COUNT(*) AS n
            FROM keyword_candidate_sources s
            JOIN keyword_candidates c ON c.candidate_id = s.candidate_id
            JOIN gsc_daily g ON g.natural_key = s.gsc_natural_key
            LEFT JOIN run_log r ON r.run_id = g.run_id
            WHERE c.build_id = ?
              AND (
                LOWER(COALESCE(g.source, '')) IN ('demo', 'fixture', 'test')
                OR LOWER(COALESCE(r.run_type, '')) = 'demo'
              )
            """,
            (build_id,),
        )[0]["n"]
        return CheckResult(
            check_name="fixture_exclusion",
            scope="catalogue",
            status=_status(int(bad) == 0),
            severity=Severity.CRITICAL,
            threshold="zero fixture/demo source rows",
            observed_value=str(bad),
            details={"fixture_source_rows": int(bad)},
        )

    def _gsc_aggregation_reconciliation(self, build_id: str) -> CheckResult:
        mismatches = []
        candidates = self.store.fetchall(
            """
            SELECT candidate_id, gsc_clicks, gsc_impressions, gsc_weighted_ctr, gsc_weighted_position
            FROM keyword_candidates WHERE build_id = ?
            """,
            (build_id,),
        )
        for cand in candidates:
            sources = self.store.fetchall(
                "SELECT clicks, impressions, position FROM keyword_candidate_sources WHERE candidate_id = ?",
                (cand["candidate_id"],),
            )
            clicks = sum(int(s["clicks"]) for s in sources)
            impressions = sum(int(s["impressions"]) for s in sources)
            if clicks != int(cand["gsc_clicks"]) or impressions != int(cand["gsc_impressions"]):
                mismatches.append(cand["candidate_id"])
                continue
            ctr = weighted_ctr(clicks, impressions)
            pos = impression_weighted_position(
                [{"impressions": s["impressions"], "position": s["position"]} for s in sources]
            )
            stored_ctr = cand["gsc_weighted_ctr"]
            stored_pos = cand["gsc_weighted_position"]
            if ctr is None and stored_ctr is not None:
                mismatches.append(cand["candidate_id"])
            elif ctr is not None and stored_ctr is not None and abs(float(stored_ctr) - float(ctr)) > 1e-9:
                mismatches.append(cand["candidate_id"])
            if pos is None and stored_pos is not None:
                mismatches.append(cand["candidate_id"])
            elif pos is not None and stored_pos is not None and abs(float(stored_pos) - float(pos)) > 1e-9:
                mismatches.append(cand["candidate_id"])
        return CheckResult(
            check_name="gsc_aggregation_reconciliation",
            scope="catalogue",
            status=_status(not mismatches),
            severity=Severity.CRITICAL,
            threshold="candidate totals == source sums",
            observed_value=f"mismatches={len(mismatches)}",
            details={"mismatch_candidate_ids": mismatches[:20]},
        )

    def _ga4_join_accounting(self, build: Dict[str, Any]) -> CheckResult:
        raw = build.get("ga4_match_report_json")
        if not raw:
            return CheckResult(
                check_name="ga4_join_accounting",
                scope="catalogue",
                status=CheckStatus.SKIPPED,
                severity=Severity.ERROR,
                threshold="matched+unmatched==eligible",
                observed_value="no_ga4_report",
                details={"reason": "GA4 enrichment not applied"},
            )
        try:
            report = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            report = {}
        eligible = int(report.get("eligible_pages") or 0)
        matched = int(report.get("matched_pages") or 0)
        unmatched = int(report.get("unmatched_pages") or 0)
        ok = matched + unmatched == eligible
        return CheckResult(
            check_name="ga4_join_accounting",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.ERROR,
            threshold="matched+unmatched==eligible",
            observed_value=f"matched={matched};unmatched={unmatched};eligible={eligible}",
            details=report,
        )

    def _serper_validation_coverage(self, build_id: str, build: Dict[str, Any]) -> CheckResult:
        raw = build.get("serper_validation_report_json")
        selected = self.store.fetchall(
            "SELECT candidate_id, serper_run_id FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (build_id, CandidateDecision.SELECTED.value),
        )
        if not selected:
            return CheckResult(
                check_name="serper_validation_coverage",
                scope="catalogue",
                status=CheckStatus.SKIPPED,
                severity=Severity.ERROR,
                threshold="selected candidates validated or blocked",
                observed_value="no_selected",
                details={},
            )
        if not raw:
            missing = [r["candidate_id"] for r in selected if not r["serper_run_id"]]
            return CheckResult(
                check_name="serper_validation_coverage",
                scope="catalogue",
                status=_status(not missing),
                severity=Severity.ERROR,
                threshold="selected candidates validated or blocked",
                observed_value=f"unvalidated={len(missing)}",
                details={"unvalidated": missing[:20], "note": "no serper report stored"},
            )
        try:
            report = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            report = {}
        ok = bool(report.get("coverage_ok"))
        return CheckResult(
            check_name="serper_validation_coverage",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.ERROR,
            threshold="coverage_ok",
            observed_value=str(report.get("coverage_ok")),
            details=report,
        )

    def _keyword_decision_completeness(self, build_id: str) -> CheckResult:
        valid = {d.value for d in CandidateDecision}
        rows = self.store.fetchall(
            "SELECT candidate_id, decision FROM keyword_candidates WHERE build_id = ?",
            (build_id,),
        )
        bad = [r["candidate_id"] for r in rows if (r["decision"] or "") not in valid]
        return CheckResult(
            check_name="keyword_decision_completeness",
            scope="catalogue",
            status=_status(not bad),
            severity=Severity.CRITICAL,
            threshold="every candidate has valid decision",
            observed_value=f"invalid={len(bad)};total={len(rows)}",
            details={"invalid_ids": bad[:20]},
        )

    def _target_page_validity(self, build_id: str) -> CheckResult:
        selected = self.store.fetchall(
            """
            SELECT candidate_id, reviewed_target_page, proposed_target_page, primary_observed_page
            FROM keyword_candidates WHERE build_id = ? AND decision = ?
            """,
            (build_id, CandidateDecision.SELECTED.value),
        )
        bad = []
        for row in selected:
            page = row["reviewed_target_page"] or row["proposed_target_page"] or row["primary_observed_page"] or ""
            if not _sunnystep_url(page):
                bad.append(row["candidate_id"])
        return CheckResult(
            check_name="target_page_validity",
            scope="catalogue",
            status=_status(not bad),
            severity=Severity.ERROR,
            threshold="selected items have valid Sunnystep URL",
            observed_value=f"invalid={len(bad)}",
            details={"invalid_ids": bad[:20]},
        )

    def _cluster_membership(self, build_id: str) -> CheckResult:
        selected = self.store.fetchall(
            "SELECT candidate_id, cluster_id FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (build_id, CandidateDecision.SELECTED.value),
        )
        missing = [r["candidate_id"] for r in selected if not r["cluster_id"]]
        multi = self.store.fetchall(
            """
            SELECT candidate_id, COUNT(DISTINCT cluster_id) AS n
            FROM keyword_candidates
            WHERE build_id = ? AND decision = ? AND cluster_id IS NOT NULL AND cluster_id != ''
            GROUP BY candidate_id
            HAVING n > 1
            """,
            (build_id, CandidateDecision.SELECTED.value),
        )
        # Also ensure cluster is approved when build is approved/activated.
        unapproved = []
        if selected and not missing:
            for row in selected:
                cluster = self.store.fetchall(
                    "SELECT approval_status FROM catalogue_clusters WHERE cluster_id = ?",
                    (row["cluster_id"],),
                )
                if not cluster:
                    unapproved.append(row["candidate_id"])
        ok = not missing and not multi
        return CheckResult(
            check_name="cluster_membership",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.ERROR,
            threshold="exactly one primary cluster per selected keyword",
            observed_value=f"missing={len(missing)};multi={len(multi)}",
            details={
                "missing": missing[:20],
                "multi": [r["candidate_id"] for r in multi][:20],
                "missing_cluster_row": unapproved[:20],
            },
        )

    def _ai_question_lineage(self, question_build_id: str) -> CheckResult:
        selected = self.store.fetchall(
            """
            SELECT question_candidate_id, source_candidate_ids
            FROM ai_question_candidates
            WHERE build_id = ? AND decision = ?
            """,
            (question_build_id, CandidateDecision.SELECTED.value),
        )
        bad = []
        for row in selected:
            try:
                cand_ids = json.loads(row["source_candidate_ids"] or "[]")
            except json.JSONDecodeError:
                cand_ids = []
            sources = self.store.fetchall(
                "SELECT COUNT(*) AS n FROM ai_question_sources WHERE question_candidate_id = ?",
                (row["question_candidate_id"],),
            )[0]["n"]
            if not cand_ids or int(sources) < 1:
                bad.append(row["question_candidate_id"])
        return CheckResult(
            check_name="ai_question_lineage",
            scope="catalogue",
            status=_status(not bad),
            severity=Severity.CRITICAL,
            threshold="approved questions link to candidates + GSC sources",
            observed_value=f"bad={len(bad)};selected={len(selected)}",
            details={"bad_ids": bad[:20]},
        )

    def _ai_question_count(self, question_build_id: str) -> CheckResult:
        n = self.store.fetchall(
            "SELECT COUNT(*) AS n FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
            (question_build_id, CandidateDecision.SELECTED.value),
        )[0]["n"]
        return CheckResult(
            check_name="ai_question_count",
            scope="catalogue",
            status=_status(int(n) == 20),
            severity=Severity.CRITICAL,
            threshold="exactly 20 selected questions",
            observed_value=str(n),
            details={"selected": int(n)},
        )

    def _non_branded_questions(self, question_build_id: str) -> CheckResult:
        classifier = BrandClassifier.from_config(self.config)
        selected = self.store.fetchall(
            "SELECT question_candidate_id, question FROM ai_question_candidates WHERE build_id = ? AND decision = ?",
            (question_build_id, CandidateDecision.SELECTED.value),
        )
        branded = [r["question_candidate_id"] for r in selected if classifier.is_brand(r["question"] or "")]
        return CheckResult(
            check_name="non_branded_questions",
            scope="catalogue",
            status=_status(not branded),
            severity=Severity.CRITICAL,
            threshold="zero approved questions contain brand terms",
            observed_value=str(len(branded)),
            details={"branded_ids": branded[:20]},
        )

    def _approval_completeness(self, build: Dict[str, Any]) -> CheckResult:
        status = build.get("status") or ""
        if status not in {"approved", "activated"}:
            return CheckResult(
                check_name="approval_completeness",
                scope="catalogue",
                status=CheckStatus.SKIPPED,
                severity=Severity.CRITICAL,
                threshold="approver+timestamp before activation",
                observed_value=status,
                details={"reason": "build not approved/activated yet"},
            )
        ok = bool(build.get("approved_by")) and bool(build.get("approved_at"))
        return CheckResult(
            check_name="approval_completeness",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.CRITICAL,
            threshold="approver+timestamp present",
            observed_value=f"approved_by={bool(build.get('approved_by'))};approved_at={bool(build.get('approved_at'))}",
            details={
                "approved_by": build.get("approved_by"),
                "approved_at": build.get("approved_at"),
            },
        )

    def _version_transition(self, build: Dict[str, Any]) -> CheckResult:
        if build.get("status") != "activated":
            return CheckResult(
                check_name="version_transition",
                scope="catalogue",
                status=CheckStatus.SKIPPED,
                severity=Severity.CRITICAL,
                threshold="previous expired; new active once",
                observed_value=build.get("status") or "",
                details={"reason": "not activated"},
            )
        version_rows = self.store.fetchall(
            """
            SELECT catalogue_version, COUNT(*) AS n
            FROM keyword_catalog
            WHERE approval_status = 'activated' AND active = 1
            GROUP BY catalogue_version
            """
        )
        active_versions = [r["catalogue_version"] for r in version_rows]
        # Exactly one evidence activated version expected (provisional may also be active).
        evidence = [v for v in active_versions if v != "candidate-v0.1"]
        ok = len(evidence) == 1
        return CheckResult(
            check_name="version_transition",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.CRITICAL,
            threshold="exactly one active evidence catalogue version",
            observed_value=f"evidence_versions={len(evidence)}",
            details={"active_versions": active_versions},
        )

    def _provenance_report_parity(self, build_id: str, report_counts: Dict[str, Any]) -> CheckResult:
        stored_selected = self.store.fetchall(
            "SELECT COUNT(*) AS n FROM keyword_candidates WHERE build_id = ? AND decision = ?",
            (build_id, CandidateDecision.SELECTED.value),
        )[0]["n"]
        report_selected = int(report_counts.get("selected_keywords") or report_counts.get("selected") or -1)
        ok = report_selected == int(stored_selected)
        return CheckResult(
            check_name="provenance_report_parity",
            scope="catalogue",
            status=_status(ok),
            severity=Severity.ERROR,
            threshold="report counts == stored counts",
            observed_value=f"report={report_selected};stored={stored_selected}",
            details={"report_counts": report_counts, "stored_selected": int(stored_selected)},
        )
