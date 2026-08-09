"""Command-line interface for local MRF retrieval, inspection, and ingestion."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import cast

from mrf_honest.fetch import FetchPolicy, fetch_url
from mrf_honest.inspect import FileInspection, explain_finding, inspect_hospital_file
from mrf_honest.lakehouse import ingest_hospital_file, query_file_profile
from mrf_honest.registry import Registry, discover_domain
from mrf_honest.types import PublisherRef

_SUCCESS = 0
_FAILURE = 1
_DEFAULT_MAX_BYTES = 1 << 30

Command = Callable[[argparse.Namespace], int]


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO date (YYYY-MM-DD)") from exc


def _emit_json(payload: object) -> None:
    """Write one canonical JSON value, keeping stdout safe for downstream tools."""
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def _emit_mapping_human(payload: dict[str, object]) -> None:
    """Render a compact result without pretending nested evidence is a table."""
    for key, value in payload.items():
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif value is None:
            rendered = "-"
        else:
            rendered = str(value)
        print(f"{key}: {rendered}")


def _emit_inspection_human(inspection: FileInspection) -> None:
    completion = "complete" if inspection.scan_completed else "incomplete"
    print(f"file: {inspection.source_path}")
    print(f"sha256: {inspection.source_sha256}")
    print(f"as_of: {inspection.as_of.isoformat()}")
    print(f"scan: {completion}")
    print(
        "counts: "
        f"items={inspection.item_count}, codes={inspection.code_count}, "
        f"charge_groups={inspection.charge_group_count}, payer_rates={inspection.payer_rate_count}"
    )
    print("dimensions:")
    for dimension in inspection.scorecard.dimensions:
        note = f" — {dimension.note}" if dimension.note else ""
        print(f"  {dimension.name}: {dimension.status}{note}")
    print("findings:")
    if not inspection.findings:
        print("  none")
    for finding in inspection.findings:
        occurrences = f" x{finding.occurrences}" if finding.occurrences > 1 else ""
        print(f"  [{finding.severity}] {finding.code}{occurrences}: {finding.message}")


def _run_inspect(args: argparse.Namespace) -> int:
    publisher_id = cast(str | None, args.publisher_id)
    publisher = PublisherRef(publisher_id) if publisher_id is not None else None
    as_of = cast(date | None, args.as_of) or date.today()
    if args.output_format == "human":
        print(f"Inspecting {args.file} ...", file=sys.stderr)
    inspection = inspect_hospital_file(
        cast(Path, args.file),
        publisher,
        as_of=as_of,
    )
    if args.output_format == "json":
        _emit_json(inspection.to_dict())
    else:
        _emit_inspection_human(inspection)
    # Findings are observations about the source, not failures of the inspection tool.
    return _SUCCESS


def _run_ingest(args: argparse.Namespace) -> int:
    publisher = PublisherRef(
        identifier=cast(str, args.publisher_id),
        name=cast(str | None, args.publisher_name),
        source_url=cast(str | None, args.source_url),
    )
    if args.output_format == "human":
        print(f"Ingesting {args.file} ...", file=sys.stderr)
    result = ingest_hospital_file(
        cast(Path, args.file),
        cast(Path, args.warehouse),
        publisher=publisher,
        memory_limit=cast(str, args.memory_limit),
        threads=cast(int, args.threads),
        as_of=cast(date | None, args.as_of),
    )
    payload = result.to_dict()
    if args.output_format == "json":
        _emit_json(payload)
    else:
        _emit_mapping_human(payload)
    return _SUCCESS


def _run_profile(args: argparse.Namespace) -> int:
    rows = query_file_profile(cast(Path, args.warehouse), cast(str, args.run_id))
    _emit_json(rows)
    return _SUCCESS


def _run_fetch(args: argparse.Namespace) -> int:
    policy = FetchPolicy(
        contact=cast(str, args.contact),
        max_bytes=cast(int, args.max_bytes),
    )
    outcome = fetch_url(
        cast(str, args.url),
        cast(Path, args.cache_dir),
        policy=policy,
    )
    _emit_json(outcome.to_dict())
    return _SUCCESS if outcome.ok else _FAILURE


def _run_discover(args: argparse.Namespace) -> int:
    registry = Registry(cast(Path, args.registry))
    cache_dir = cast(Path, args.cache_dir)
    policy = FetchPolicy(contact=cast(str, args.contact))
    records: list[dict[str, object]] = []
    for domain in cast(list[str], args.domains):
        print(f"Discovering {domain} ...", file=sys.stderr)
        record = discover_domain(
            domain,
            registry=registry,
            cache_dir=cache_dir,
            policy=policy,
        )
        records.append(record.to_dict())
    _emit_json(records)
    # Unusable publications are durable evidence, not a failure of the discovery command.
    infrastructure_failed = any(
        isinstance(record.get("fetch"), dict)
        and cast(dict[str, object], record["fetch"]).get("status") == "cache_error"
        for record in records
    )
    return _FAILURE if infrastructure_failed else _SUCCESS


def _run_explain(args: argparse.Namespace) -> int:
    try:
        definition = explain_finding(cast(str, args.finding_code))
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else "unknown finding code"
        raise ValueError(message) from exc
    _emit_json(
        {
            "citations": list(definition.citations),
            "code": definition.code,
            "description": definition.description,
            "dimension": definition.dimension,
            "severity": definition.severity,
        }
    )
    return _SUCCESS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mrf-honest",
        description="Retrieve, inspect, and ingest hospital price-transparency files.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser(
        "inspect", help="inspect a hospital MRF without loading it into a warehouse"
    )
    inspect_parser.add_argument("file", type=Path, metavar="FILE")
    inspect_parser.add_argument("--publisher-id")
    inspect_parser.add_argument("--as-of", type=_iso_date)
    inspect_parser.add_argument(
        "--format", dest="output_format", choices=("human", "json"), default="human"
    )
    inspect_parser.set_defaults(handler=_run_inspect)

    ingest_parser = commands.add_parser("ingest", help="build an idempotent lakehouse snapshot")
    ingest_parser.add_argument("file", type=Path, metavar="FILE")
    ingest_parser.add_argument("--publisher-id", required=True)
    ingest_parser.add_argument("--warehouse", type=Path, required=True)
    ingest_parser.add_argument("--publisher-name")
    ingest_parser.add_argument("--source-url")
    ingest_parser.add_argument("--memory-limit", default="256MB")
    ingest_parser.add_argument("--threads", type=_positive_int, default=2)
    ingest_parser.add_argument("--as-of", type=_iso_date)
    ingest_parser.add_argument(
        "--format", dest="output_format", choices=("human", "json"), default="human"
    )
    ingest_parser.set_defaults(handler=_run_ingest)

    profile_parser = commands.add_parser(
        "profile", help="read methodology and rate-kind denominators for an ingest run"
    )
    profile_parser.add_argument("warehouse", type=Path, metavar="WAREHOUSE")
    profile_parser.add_argument("run_id", metavar="RUN_ID")
    profile_parser.set_defaults(handler=_run_profile)

    fetch_parser = commands.add_parser("fetch", help="retrieve one URL into the verified cache")
    fetch_parser.add_argument("url", metavar="URL")
    fetch_parser.add_argument("--cache-dir", type=Path, required=True)
    fetch_parser.add_argument("--contact", required=True)
    fetch_parser.add_argument("--max-bytes", type=_positive_int, default=_DEFAULT_MAX_BYTES)
    fetch_parser.set_defaults(handler=_run_fetch)

    discover_parser = commands.add_parser(
        "discover", help="discover MRF URLs from CMS cms-hpt.txt documents"
    )
    discover_parser.add_argument("domains", nargs="+", metavar="DOMAIN")
    discover_parser.add_argument("--registry", type=Path, required=True)
    discover_parser.add_argument("--cache-dir", type=Path, required=True)
    discover_parser.add_argument("--contact", required=True)
    discover_parser.set_defaults(handler=_run_discover)

    explain_parser = commands.add_parser("explain", help="explain a quality finding code")
    explain_parser.add_argument("finding_code", metavar="FINDING_CODE")
    explain_parser.set_defaults(handler=_run_explain)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a CLI command and return a process exit status."""
    args = _build_parser().parse_args(argv)
    handler = cast(Command, args.handler)
    try:
        return handler(args)
    except Exception as exc:  # A CLI boundary reports operational failures without a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return _FAILURE


if __name__ == "__main__":  # pragma: no cover - exercised through ``main`` in tests
    raise SystemExit(main())
