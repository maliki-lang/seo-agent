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

    lineage = catalogue_sub.add_parser(
        "lineage",
        parents=[shared],
        help="Show stored lineage for one keyword candidate",
    )
    lineage.add_argument("--candidate-id", required=True)
    lineage.set_defaults(handler="catalogue_lineage")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        runner = TrackingRunner(config)
        as_json = args.json
        if args.command == "doctor":
            _print(runner.doctor(), as_json)
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
            from .catalogue.enrich import enrich_build_with_ga4
            from .catalogue.validate_serp import validate_serp_for_build

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
