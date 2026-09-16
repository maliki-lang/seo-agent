from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import List, Optional, Sequence

from .config import load_config
from .enums import Source
from .exceptions import ConfigurationError, TrackingError
from .runner import COLLECTOR_ORDER, TrackingRunner
from .transforms.normalize import as_of_date, parse_date


def _print(payload: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, default=str, indent=2, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
        return
    print(payload)


def _parse_sources(raw: Optional[str]) -> List[str]:
    if not raw:
        return list(COLLECTOR_ORDER)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    allowed = {item.value for item in Source}
    unknown = [item for item in values if item not in allowed]
    if unknown:
        raise ConfigurationError(f"Unknown sources: {', '.join(unknown)}")
    return values


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="Machine-readable output")
    shared.add_argument("--config", help="Path to tracking YAML config")
    parser = argparse.ArgumentParser(prog="python -m data_sources.tracking.cli")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--config", help="Path to tracking YAML config")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", parents=[shared], help="Validate configuration without external calls")
    doctor.set_defaults(handler="doctor")

    collect = sub.add_parser("collect", parents=[shared], help="Probe one source")
    collect.add_argument("--source", required=True)
    collect.add_argument("--as-of-date")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--limit", type=int, default=0, help="Limit keywords or questions for a sample pull")
    collect.set_defaults(handler="collect")

    daily = sub.add_parser("daily", parents=[shared], help="Run daily collection")
    daily.add_argument("--as-of-date")
    daily.add_argument("--simulate-failure", default="")
    daily.add_argument("--dry-run", action="store_true")
    daily.set_defaults(handler="daily")

    backfill = sub.add_parser("backfill", parents=[shared], help="Backfill GSC/GA4")
    backfill.add_argument("--sources", default="gsc,ga4")
    backfill.add_argument("--start-date", required=True)
    backfill.add_argument("--end-date", required=True)
    backfill.add_argument("--override-max-range", action="store_true")
    backfill.set_defaults(handler="backfill")

    check = sub.add_parser("check", parents=[shared], help="Run quality checks for a run")
    check.add_argument("--run-id", required=True)
    check.set_defaults(handler="check")

    baseline = sub.add_parser("baseline", parents=[shared], help="Create or lock a 28-day baseline")
    baseline.add_argument("--end-date")
    baseline.add_argument("--lock", action="store_true")
    baseline.add_argument("--run-id", help="Optional run_id whose quality gate must pass before lock")
    baseline.set_defaults(handler="baseline")

    opportunities = sub.add_parser(
        "opportunities", parents=[shared], help="Score evidence-backed SEO/GEO opportunities"
    )
    opportunities.add_argument("--end-date")
    opportunities.add_argument("--report-id")
    opportunities.add_argument("--limit", type=int, default=10)
    opportunities.set_defaults(handler="opportunities")

    weekly = sub.add_parser("weekly", parents=[shared], help="Generate weekly report")
    weekly.add_argument("--period-end")
    weekly.add_argument("--publish", action="store_true")
    weekly.add_argument("--no-publish", action="store_true")
    weekly.set_defaults(handler="weekly")

    catalogue = sub.add_parser("catalogue", parents=[shared], help="Catalogue provenance commands")
    catalogue_sub = catalogue.add_subparsers(dest="catalogue_command", required=True)

    build_kw = catalogue_sub.add_parser(
        "build-keywords",
        parents=[shared],
        help="Build GSC-derived keyword candidates from stored production rows",
    )
    build_kw.add_argument("--gsc-start-date", help="Inclusive GSC window start (default: 90-day complete window)")
    build_kw.add_argument("--gsc-end-date", help="Inclusive GSC window end")
    build_kw.add_argument("--ga4-start-date", help="Optional GA4 window start; triggers enrichment when both GA4 dates set")
    build_kw.add_argument("--ga4-end-date", help="Optional GA4 window end; triggers enrichment when both GA4 dates set")
    build_kw.add_argument("--status", default="draft", help="Build status (default: draft)")
    build_kw.add_argument("--created-by", default="catalogue-builder")
    build_kw.set_defaults(handler="catalogue_build_keywords")

    enrich = catalogue_sub.add_parser(
        "enrich-ga4",
        parents=[shared],
        help="Enrich an existing keyword build with GA4 organic page metrics",
    )
    enrich.add_argument("--build-id", required=True)
    enrich.add_argument("--ga4-start-date")
    enrich.add_argument("--ga4-end-date")
    enrich.set_defaults(handler="catalogue_enrich_ga4")

    validate = catalogue_sub.add_parser(
        "validate-serp",
        parents=[shared],
        help="Explicit paid Serper validation for shortlisted candidates",
    )
    validate.add_argument("--build-id", required=True)
    validate.add_argument("--decision", default="selected", help="Candidate decision filter (default: selected)")
    validate.add_argument("--limit", type=int, default=0, help="Max candidates to validate (0 = all matching)")
    validate.set_defaults(handler="catalogue_validate_serp")

    clusters = catalogue_sub.add_parser(
        "derive-clusters",
        parents=[shared],
        help="Derive reviewable clusters from keyword candidates",
    )
    clusters.add_argument("--build-id", required=True)
    clusters.add_argument("--reviewed-by", default="cluster-builder")
    clusters.set_defaults(handler="catalogue_derive_clusters")

    build_q = catalogue_sub.add_parser(
        "build-ai-questions",
        parents=[shared],
        help="Derive AI-question candidates with GSC/keyword lineage",
    )
    build_q.add_argument("--keyword-build-id", required=True)
    build_q.add_argument("--count", type=int, default=20)
    build_q.add_argument("--status", default="draft")
    build_q.add_argument("--created-by", default="ai-question-builder")
    build_q.set_defaults(handler="catalogue_build_ai_questions")

    export_review = catalogue_sub.add_parser(
        "export-review",
        parents=[shared],
        help="Export keyword/question review CSV with evidence fields",
    )
    export_review.add_argument("--build-id", required=True)
    export_review.add_argument("--question-build-id")
    export_review.add_argument("--output", required=True)
    export_review.set_defaults(handler="catalogue_export_review")

    import_decisions = catalogue_sub.add_parser(
        "import-decisions",
        parents=[shared],
        help="Import reviewer decisions from CSV",
    )
    import_decisions.add_argument("--build-id", required=True)
    import_decisions.add_argument("--question-build-id")
    import_decisions.add_argument("--input", required=True)
    import_decisions.set_defaults(handler="catalogue_import_decisions")

    compare = catalogue_sub.add_parser(
        "compare",
        parents=[shared],
        help="Compare evidence build against provisional candidate-v0.1 catalogue",
    )
    compare.add_argument("--build-id", required=True)
    compare.add_argument("--question-build-id")
    compare.set_defaults(handler="catalogue_compare")

    approve = catalogue_sub.add_parser(
        "approve",
        parents=[shared],
        help="Approve a keyword+question catalogue build after review",
    )
    approve.add_argument("--build-id", required=True)
    approve.add_argument("--question-build-id")
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--keyword-minimum", type=int)
    approve.add_argument("--question-count", type=int, default=20)
    approve.set_defaults(handler="catalogue_approve")

    activate = catalogue_sub.add_parser(
        "activate",
        parents=[shared],
        help="Activate an approved catalogue version (separate from approve)",
    )
    activate.add_argument("--build-id", required=True)
    activate.add_argument("--question-build-id")
    activate.add_argument("--catalogue-version")
    activate.add_argument("--confirm", action="store_true")
    activate.set_defaults(handler="catalogue_activate")

    cat_check = catalogue_sub.add_parser(
        "check",
        parents=[shared],
        help="Run build-scoped catalogue quality gates",
    )
    cat_check.add_argument("--build-id", required=True)
    cat_check.add_argument("--question-build-id")
    cat_check.set_defaults(handler="catalogue_check")

    cat_report = catalogue_sub.add_parser(
        "report",
        parents=[shared],
        help="Write stakeholder provenance Markdown+JSON report",
    )
    cat_report.add_argument("--build-id", required=True)
    cat_report.add_argument("--question-build-id")
    cat_report.add_argument("--output", default="docs/catalogue-provenance-v1.md")
    cat_report.set_defaults(handler="catalogue_report")

    lineage = catalogue_sub.add_parser(
        "lineage",
        parents=[shared],
        help="Show stored lineage for one keyword candidate",
    )
    lineage.add_argument("--candidate-id", required=True)
    lineage.set_defaults(handler="catalogue_lineage")

    classify = catalogue_sub.add_parser(
        "classify",
        parents=[shared],
        help="Apply Phase 10 routing/intent/relevance classification to a keyword build",
    )
    classify.add_argument("--build-id", required=True)
    classify.add_argument(
        "--keep-selected",
        action="store_true",
        help="Do not demote ineligible selected rows to pending",
    )
    classify.set_defaults(handler="catalogue_classify")

    derive_families = catalogue_sub.add_parser(
        "derive-families",
        parents=[shared],
        help="Derive conservative keyword families and mark family primaries",
    )
    derive_families.add_argument("--build-id", required=True)
    derive_families.add_argument(
        "--skip-targets",
        action="store_true",
        help="Do not refresh target-page actionability before family primary selection",
    )
    derive_families.set_defaults(handler="catalogue_derive_families")

    eval_targets = catalogue_sub.add_parser(
        "evaluate-targets",
        parents=[shared],
        help="Evaluate target-page actionability and shared GA4 confidence multipliers",
    )
    eval_targets.add_argument("--build-id", required=True)
    eval_targets.add_argument(
        "--skip-ga4-discount",
        action="store_true",
        help="Skip shared-page GA4 confidence multiplier assignment",
    )
    eval_targets.set_defaults(handler="catalogue_evaluate_targets")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        runner = TrackingRunner(config)
        as_json = args.json
        if args.command == "doctor":
            payload = runner.doctor(command="doctor")
            _print(payload, as_json)
            if config.env == "production" and not payload.get("ok", True):
                return 2
            return 0
        if args.command == "collect":
            as_of = parse_date(args.as_of_date) if args.as_of_date else as_of_date(tz_name=config.timezone)
            if args.dry_run:
                _print(
                    {
                        "source": args.source,
                        "as_of_date": as_of.isoformat(),
                        "dry_run": True,
                        "status": "skipped",
                        "note": "No API call and no source rows written.",
                    },
                    as_json,
                )
                return 0
            payload = runner.collect_source(args.source, as_of, dry_run=False, limit=args.limit)
            _print(payload, as_json)
            return 0 if payload["status"] != "failed" else 2
        if args.command == "daily":
            as_of = parse_date(args.as_of_date) if args.as_of_date else as_of_date(tz_name=config.timezone)
            if args.dry_run:
                _print(
                    {
                        "as_of_date": as_of.isoformat(),
                        "dry_run": True,
                        "sources": COLLECTOR_ORDER,
                        "status": "skipped",
                    },
                    as_json,
                )
                return 0
            payload = runner.run_daily(as_of, simulate_failure=args.simulate_failure)
            _print(payload, as_json)
            return 0 if payload["status"] != "failed" else 2
        if args.command == "backfill":
            sources = _parse_sources(args.sources)
            start = parse_date(args.start_date)
            end = parse_date(args.end_date)
            if start > end:
                raise ConfigurationError("start-date must be on or before end-date")
            span = (end - start).days + 1
            if span > config.backfill_days and not args.override_max_range:
                raise ConfigurationError(
                    f"Backfill range {span} days exceeds configured maximum {config.backfill_days}"
                )
            payload = runner.run_backfill(sources, start, end)
            _print(payload, as_json)
            return 0 if payload["status"] != "failed" else 2
        if args.command == "check":
            row = runner.store.get_run(args.run_id)
            if row is None:
                raise ConfigurationError(f"Unknown run_id {args.run_id}")
            payload = runner.run_quality_checks(args.run_id)
            _print(payload, as_json)
            return 0 if payload.get("gate_status") != "failed" else 2
        if args.command == "baseline":
            end = parse_date(args.end_date) if args.end_date else None
            payload = runner.create_baseline(
                end_date=end,
                lock=bool(args.lock),
                run_id=args.run_id,
            )
            _print(payload, as_json)
            return 0
        if args.command == "opportunities":
            end = parse_date(args.end_date) if args.end_date else None
            payload = runner.build_opportunities(
                report_id=args.report_id,
                end_date=end,
                limit=args.limit,
            )
            _print(payload, as_json)
            return 0
        if args.command == "weekly":
            end = parse_date(args.period_end) if args.period_end else None
            publish = bool(args.publish) and not bool(args.no_publish)
            payload = runner.generate_weekly(period_end=end, publish=publish)
            _print(payload, as_json)
            return 0 if payload.get("status") != "failed" else 2
        if args.command == "catalogue":
            from .catalogue.builder import build_keyword_catalogue, get_candidate_lineage
            from .catalogue.classify import classify_build
            from .catalogue.families import derive_families_for_build
            from .catalogue.target_pages import evaluate_targets_for_build
            from .catalogue.clusters import derive_clusters
            from .catalogue.compare import compare_with_provisional
            from .catalogue.enrich import enrich_build_with_ga4
            from .catalogue.questions import build_ai_questions
            from .catalogue.validate_serp import validate_serp_for_build
            from .catalogue.workflow import (
                activate_catalogue,
                approve_catalogue,
                export_review,
                import_decisions,
            )
            from .catalogue.report import write_provenance_report
            from .checks.catalogue_checks import CatalogueQualitySuite

            if args.catalogue_command == "build-keywords":
                payload = build_keyword_catalogue(
                    runner.store,
                    config,
                    gsc_start=parse_date(args.gsc_start_date) if args.gsc_start_date else None,
                    gsc_end=parse_date(args.gsc_end_date) if args.gsc_end_date else None,
                    ga4_start=parse_date(args.ga4_start_date) if args.ga4_start_date else None,
                    ga4_end=parse_date(args.ga4_end_date) if args.ga4_end_date else None,
                    status=args.status,
                    created_by=args.created_by,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "classify":
                payload = classify_build(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    demote_ineligible_selected=not bool(args.keep_selected),
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "derive-families":
                payload = derive_families_for_build(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    refresh_targets=not bool(args.skip_targets),
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "evaluate-targets":
                payload = evaluate_targets_for_build(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    apply_ga4_discount=not bool(args.skip_ga4_discount),
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "enrich-ga4":
                payload = enrich_build_with_ga4(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    ga4_start=parse_date(args.ga4_start_date) if args.ga4_start_date else None,
                    ga4_end=parse_date(args.ga4_end_date) if args.ga4_end_date else None,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "validate-serp":
                payload = validate_serp_for_build(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    decision=args.decision,
                    limit=args.limit,
                )
                _print(payload, as_json)
                return 0 if payload.get("failed", 0) == 0 and payload.get("blocked", 0) == 0 else 2
            if args.catalogue_command == "derive-clusters":
                payload = derive_clusters(
                    runner.store, build_id=args.build_id, reviewed_by=args.reviewed_by
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "build-ai-questions":
                payload = build_ai_questions(
                    runner.store,
                    config,
                    keyword_build_id=args.keyword_build_id,
                    count=args.count,
                    status=args.status,
                    created_by=args.created_by,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "export-review":
                payload = export_review(
                    runner.store,
                    build_id=args.build_id,
                    output=args.output,
                    question_build_id=args.question_build_id,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "import-decisions":
                payload = import_decisions(
                    runner.store,
                    build_id=args.build_id,
                    input_path=args.input,
                    question_build_id=args.question_build_id,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "compare":
                payload = compare_with_provisional(
                    runner.store,
                    config,
                    keyword_build_id=args.build_id,
                    question_build_id=args.question_build_id,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "approve":
                payload = approve_catalogue(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    approved_by=args.approved_by,
                    question_build_id=args.question_build_id,
                    keyword_minimum=args.keyword_minimum,
                    question_count=args.question_count,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "activate":
                payload = activate_catalogue(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    confirm=bool(args.confirm),
                    question_build_id=args.question_build_id,
                    catalogue_version=args.catalogue_version,
                )
                _print(payload, as_json)
                return 0
            if args.catalogue_command == "check":
                payload = CatalogueQualitySuite(config, runner.store).run_for_build(
                    args.build_id,
                    question_build_id=args.question_build_id,
                )
                _print(payload, as_json)
                return 0 if payload.get("gate_status") != "failed" else 2
            if args.catalogue_command == "report":
                payload = write_provenance_report(
                    runner.store,
                    config,
                    build_id=args.build_id,
                    output=args.output,
                    question_build_id=args.question_build_id,
                )
                # Optional parity check against report counts.
                checks = CatalogueQualitySuite(config, runner.store).run_for_build(
                    args.build_id,
                    question_build_id=args.question_build_id,
                    report_counts=payload.get("counts_for_parity") or {},
                )
                payload["quality"] = checks
                _print(payload, as_json)
                return 0 if checks.get("gate_status") != "failed" else 2
            if args.catalogue_command == "lineage":
                payload = get_candidate_lineage(runner.store, args.candidate_id)
                _print(payload, as_json)
                return 0
            parser.error(f"Unhandled catalogue command {args.catalogue_command}")
            return 2
        parser.error(f"Unhandled command {args.command}")
        return 2
    except TrackingError as exc:
        print(f"{exc.error_code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
