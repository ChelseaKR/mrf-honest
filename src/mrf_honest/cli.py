"""Command-line interface for MRF retrieval, assessment, inspection, and ingestion."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from mrf_honest.cohort import build_comparison
from mrf_honest.container import ArchiveRefused, looks_like_archive, open_member, select_member
from mrf_honest.fetch import PROBE_SAMPLE_BYTES, FetchPolicy, default_open, fetch_url, probe_url
from mrf_honest.inspect import FileInspection, explain_finding, inspect_hospital_file
from mrf_honest.inspect_csv import (
    CsvFileInspection,
    explain_csv_finding,
    inspect_hospital_csv_file,
)
from mrf_honest.lakehouse import LakehouseScopeRefusal, ingest_hospital_file, query_file_profile
from mrf_honest.mcp import serve as serve_mcp
from mrf_honest.politeness import Politeness
from mrf_honest.registry import Registry, discover_domain
from mrf_honest.scorecard import (
    CSV_PROFILE,
    JSON_PROFILE,
    RETRIEVAL_FINDING_CATALOG,
    AssessmentProfile,
    AssessmentRegistry,
    AssessmentSubject,
    FileAssessment,
    PublisherType,
    URLProvenance,
    assess_hospital_url,
)
from mrf_honest.site import DEFAULT_ORIGIN, render_site
from mrf_honest.types import PublisherRef

#: CLI names for the implemented assessment profiles; the JSON profile stays the default so
#: every existing invocation keeps its meaning.
_CLI_PROFILES: dict[str, AssessmentProfile] = {"json": JSON_PROFILE, "csv": CSV_PROFILE}

#: Copy buffer for lifting one member out of a container, so peak memory stays bounded.
_COPY_CHUNK_BYTES = 1 << 20

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


def _emit_inspection_human(inspection: FileInspection | CsvFileInspection) -> None:
    completion = "complete" if inspection.scan_completed else "incomplete"
    print(f"file: {inspection.source_path}")
    print(f"sha256: {inspection.source_sha256}")
    print(f"as_of: {inspection.as_of.isoformat()}")
    print(f"scan: {completion}")
    if isinstance(inspection, CsvFileInspection):
        print(
            "counts: "
            f"layout={inspection.layout}, rows={inspection.row_count}, "
            f"items={inspection.item_count}, codes={inspection.code_count}, "
            f"payer_rates={inspection.payer_rate_count}"
        )
    else:
        print(
            "counts: "
            f"items={inspection.item_count}, codes={inspection.code_count}, "
            f"charge_groups={inspection.charge_group_count}, "
            f"payer_rates={inspection.payer_rate_count}"
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


def _emit_assessment_human(assessment: FileAssessment) -> None:
    public_subject = assessment.subject.to_dict()
    print(f"assessment_id: {assessment.assessment_id}")
    print(f"publisher: {assessment.subject.publisher.identifier}")
    print(f"publisher_type: {assessment.subject.publisher_type.value}")
    print(f"location_id: {assessment.subject.location_id}")
    print(f"url: {public_subject['requested_url']}")
    print(f"observed_at: {assessment.fetch.attempted_at}")
    print(f"retrieval_status: {assessment.fetch.status.value}")
    print(f"as_of: {assessment.as_of.isoformat()}")
    print("dimensions:")
    for dimension in assessment.scorecard.dimensions:
        note = f" — {dimension.note}" if dimension.note else ""
        print(f"  {dimension.name}: {dimension.status}{note}")
    print("findings:")
    if not assessment.findings:
        print("  none")
    for finding in assessment.findings:
        occurrences = f" x{finding.occurrences}" if finding.occurrences > 1 else ""
        print(f"  [{finding.severity}] {finding.code}{occurrences}: {finding.message}")


def _inspect_source(path: Path, output_format: str) -> tuple[Path, dict[str, object] | None, int]:
    """Resolve what to inspect: the file itself, or the one document inside its container.

    Seven publications in the committed draw are ZIP archives. A container is not the document,
    so this returns the member to read and the record of how it was chosen, or a refusal that
    stops the run rather than letting an unopened archive report as an unreadable file.
    """

    if not looks_like_archive(path):
        return path, None, _SUCCESS
    outcome = select_member(path)
    if isinstance(outcome, ArchiveRefused):
        if output_format == "json":
            _emit_json({"container": outcome.as_dict()})
        else:
            print(f"Refused: {outcome.detail}", file=sys.stderr)
        return path, outcome.as_dict(), _FAILURE
    extracted = path.parent / f".{path.name}.{Path(outcome.name).name}"
    with open_member(path, outcome) as source, extracted.open("wb") as sink:
        shutil.copyfileobj(source, sink, length=_COPY_CHUNK_BYTES)
    return extracted, outcome.as_dict(), _SUCCESS


def _run_inspect(args: argparse.Namespace) -> int:
    publisher_id = cast(str | None, args.publisher_id)
    publisher = PublisherRef(publisher_id) if publisher_id is not None else None
    as_of = cast(date | None, args.as_of) or date.today()
    output_format = cast(str, args.output_format)
    if output_format == "human":
        print(f"Inspecting {args.file} ...", file=sys.stderr)
    source, container, code = _inspect_source(cast(Path, args.file), output_format)
    if code != _SUCCESS:
        return code
    if container is not None and output_format == "human":
        print(f"Container: reading {container['name']!r} from the archive", file=sys.stderr)
    inspect = (
        inspect_hospital_csv_file if cast(str, args.profile) == "csv" else inspect_hospital_file
    )
    try:
        inspection = inspect(
            source,
            publisher,
            as_of=as_of,
        )
    finally:
        if container is not None:
            source.unlink(missing_ok=True)
    if output_format == "json":
        payload: dict[str, object] = dict(inspection.to_dict())
        if container is not None:
            payload["container"] = container
        _emit_json(payload)
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
    try:
        result = ingest_hospital_file(
            cast(Path, args.file),
            cast(Path, args.warehouse),
            publisher=publisher,
            memory_limit=cast(str, args.memory_limit),
            threads=cast(int, args.threads),
            as_of=cast(date | None, args.as_of),
        )
    except LakehouseScopeRefusal as refusal:
        # A scope refusal is evidence with a reason, not a traceback. It goes to stdout in the
        # same document shape a successful load produces, so `compare --ingest-result` can
        # publish the reason beside the file it applies to instead of the site showing an
        # unexplained absence. The command still fails: no snapshot was produced.
        payload = refusal.to_dict(publisher_id=publisher.identifier)
        if args.output_format == "json":
            _emit_json(payload)
        else:
            _emit_mapping_human(payload)
            print(f"refused: {refusal.reason}", file=sys.stderr)
        return _FAILURE
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


def _politeness(policy: FetchPolicy) -> Politeness:
    """One Politeness per command invocation, so the per-host interval holds across the run.

    Building it here rather than inside each retrieval is the difference between a paced run
    and a sequence of calls that each politely wait zero seconds.
    """
    return Politeness(
        user_agent=policy.identifying_user_agent,
        opener=default_open,
        timeout_seconds=policy.timeout_seconds,
    )


def _run_fetch(args: argparse.Namespace) -> int:
    policy = FetchPolicy(
        contact=cast(str, args.contact),
        max_bytes=cast(int, args.max_bytes),
    )
    outcome = fetch_url(
        cast(str, args.url),
        cast(Path, args.cache_dir),
        policy=policy,
        politeness=_politeness(policy),
    )
    _emit_json(outcome.to_dict())
    return _SUCCESS if outcome.ok else _FAILURE


def _run_probe(args: argparse.Namespace) -> int:
    policy = FetchPolicy(contact=cast(str, args.contact))
    outcome = probe_url(
        cast(str, args.url),
        policy=policy,
        politeness=_politeness(policy),
        sample_bytes=cast(int, args.sample_bytes),
    )
    _emit_json(outcome.to_dict())
    # A URL that answers with the wrong kind of document is data, not a probe failure.
    return _SUCCESS if outcome.status == "probed" else _FAILURE


def _run_discover(args: argparse.Namespace) -> int:
    registry = Registry(cast(Path, args.registry))
    cache_dir = cast(Path, args.cache_dir)
    policy = FetchPolicy(contact=cast(str, args.contact))
    # One object for the whole loop: the per-host interval has to span the domains, not reset
    # between them.
    politeness = _politeness(policy)
    records: list[dict[str, object]] = []
    for domain in cast(list[str], args.domains):
        print(f"Discovering {domain} ...", file=sys.stderr)
        record = discover_domain(
            domain,
            registry=registry,
            cache_dir=cache_dir,
            policy=policy,
            politeness=politeness,
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


def _run_scorecard(args: argparse.Namespace) -> int:
    url = cast(str, args.url)
    publisher = PublisherRef(
        identifier=cast(str, args.publisher_id),
        name=cast(str | None, args.publisher_name),
        source_url=url,
    )
    subject = AssessmentSubject(
        publisher=publisher,
        publisher_type=PublisherType(cast(str, args.publisher_type)),
        location_id=cast(str, args.location_id),
        requested_url=url,
        url_provenance=URLProvenance(cast(str, args.url_provenance)),
    )
    policy = FetchPolicy(
        contact=cast(str, args.contact),
        max_bytes=cast(int, args.max_bytes),
    )
    if args.output_format == "human":
        print(f"Assessing {subject.to_dict()['requested_url']} ...", file=sys.stderr)
    assessment = assess_hospital_url(
        subject,
        cast(Path, args.cache_dir),
        policy=policy,
        politeness=_politeness(policy),
        registry=AssessmentRegistry(cast(Path, args.registry)),
        profile=_CLI_PROFILES[cast(str, args.profile)],
    )
    if args.output_format == "json":
        _emit_json(assessment.to_dict())
    else:
        _emit_assessment_human(assessment)
    # Publisher/file findings are data. Only local workflow failure is a command failure.
    return _SUCCESS if assessment.operationally_complete else _FAILURE


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{label} must be a JSON object: {path.name}")
    return cast(dict[str, object], loaded)


def _emit_comparison_human(comparison: dict[str, object]) -> None:
    summary = cast(dict[str, object], comparison["summary"])
    print(
        "cohort: "
        f"targeted={summary['targeted']}, graded={summary['graded']}, "
        f"not_graded={summary['not_graded']}"
    )
    for row in cast(list[dict[str, object]], comparison["files"]):
        grade = cast(dict[str, object], row["grade"])
        print(f"{row['slug']}: {grade['grade']} — {grade['reason']}")


def _run_compare(args: argparse.Namespace) -> int:
    registry = AssessmentRegistry(cast(Path, args.assessments))
    manifest = _load_json_object(cast(Path, args.manifest), "manifest")
    ingest_results = [
        _load_json_object(path, "ingest result") for path in cast(list[Path], args.ingest_results)
    ]
    generated_at = cast(str | None, args.generated_at) or (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    comparison = build_comparison(
        registry.records(),
        manifest,
        ingest_results=ingest_results,
        generated_at=generated_at,
    )
    if args.output_format == "json":
        _emit_json(comparison)
    else:
        _emit_comparison_human(comparison)
    return _SUCCESS


def _run_mcp(args: argparse.Namespace) -> int:
    """Serve the published dataset over stdio. Reads committed files; never reaches a network."""

    return serve_mcp(cast(Path, args.site))


def _run_site(args: argparse.Namespace) -> int:
    comparisons = [
        _load_json_object(path, "comparison") for path in cast(list[Path], args.comparisons)
    ]
    written = render_site(
        comparisons,
        cast(Path, args.out),
        origin=cast(str, args.origin).rstrip("/"),
    )
    _emit_json({"files_written": len(written)})
    return _SUCCESS


def _run_explain(args: argparse.Namespace) -> int:
    code = cast(str, args.finding_code)
    try:
        definition = explain_finding(code)
    except KeyError as exc:
        try:
            definition = explain_csv_finding(code)
        except KeyError:
            try:
                definition = RETRIEVAL_FINDING_CATALOG[code]
            except KeyError:
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


def _run_narrate(args: argparse.Namespace) -> int:
    """Narrate one assessment record with verified citations (ADR 0006).

    The provider and the corpus are imported here, not at module import, so
    the rest of the CLI keeps its standard-library-only boundary.
    """
    from mrf_honest.ai.corpus import CorpusIndex
    from mrf_honest.ai.narrate import narrate
    from mrf_honest.ai.provider import provider_from_env

    records_path = cast(Path, args.assessments)
    index = cast(int, args.index)
    lines = [line for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if index < 0 or index >= len(lines):
        raise ValueError(f"--index must be between 0 and {len(lines) - 1} for {records_path}")
    record = json.loads(lines[index])
    corpus = CorpusIndex.load(cast(Path, args.root))
    narration = narrate(
        record, corpus=corpus, provider=provider_from_env(), language=cast(str, args.language)
    )
    if cast(bool, args.json):
        _emit_json(narration.to_dict())
        return _SUCCESS
    print(f"{narration.subject}: grade {narration.grade}")
    print(narration.label)
    print()
    for number, claim in enumerate(narration.claims, start=1):
        print(f"{number}. {claim.text}")
        for citation in claim.citations:
            print(f'   - {citation.source_label} ({citation.passage_id}): "{citation.quote}"')
    if narration.withheld_count:
        print()
        print(
            f"{narration.withheld_count} statement(s) withheld because a citation did not "
            "verify against the committed source text."
        )
    if narration.uncited_sources:
        print(f"Sources not retained in the corpus: {', '.join(narration.uncited_sources)}")
    print(f"Model: {narration.model}. Prompt version: {narration.prompt_version}.")
    return _SUCCESS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mrf-honest",
        description="Retrieve, assess, inspect, and ingest hospital price-transparency files.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser(
        "inspect", help="inspect a hospital MRF without loading it into a warehouse"
    )
    inspect_parser.add_argument("file", type=Path, metavar="FILE")
    inspect_parser.add_argument("--publisher-id")
    inspect_parser.add_argument("--as-of", type=_iso_date)
    inspect_parser.add_argument(
        "--profile",
        choices=tuple(_CLI_PROFILES),
        default="json",
        help="which CMS v3 file format the target claims to be (default: json)",
    )
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

    probe_parser = commands.add_parser(
        "probe",
        help="classify what one URL serves with a single bounded ranged request",
    )
    probe_parser.add_argument("url", metavar="URL")
    probe_parser.add_argument("--contact", required=True)
    probe_parser.add_argument(
        "--bytes",
        dest="sample_bytes",
        type=_positive_int,
        default=PROBE_SAMPLE_BYTES,
        help=f"how many leading bytes to sample (default {PROBE_SAMPLE_BYTES})",
    )
    probe_parser.set_defaults(handler=_run_probe)

    discover_parser = commands.add_parser(
        "discover", help="discover MRF URLs from CMS cms-hpt.txt documents"
    )
    discover_parser.add_argument("domains", nargs="+", metavar="DOMAIN")
    discover_parser.add_argument("--registry", type=Path, required=True)
    discover_parser.add_argument("--cache-dir", type=Path, required=True)
    discover_parser.add_argument("--contact", required=True)
    discover_parser.set_defaults(handler=_run_discover)

    scorecard_parser = commands.add_parser(
        "scorecard",
        aliases=["grade"],
        help="retrieve and durably assess one hospital MRF",
    )
    scorecard_parser.add_argument("url", metavar="URL")
    scorecard_parser.add_argument("--publisher-id", required=True)
    scorecard_parser.add_argument("--publisher-name")
    scorecard_parser.add_argument("--publisher-type", choices=("hospital",), required=True)
    scorecard_parser.add_argument("--location-id", required=True)
    scorecard_parser.add_argument(
        "--url-provenance", choices=tuple(item.value for item in URLProvenance), required=True
    )
    scorecard_parser.add_argument("--registry", type=Path, required=True)
    scorecard_parser.add_argument("--cache-dir", type=Path, required=True)
    scorecard_parser.add_argument("--contact", required=True)
    scorecard_parser.add_argument(
        "--profile",
        choices=tuple(_CLI_PROFILES),
        default="json",
        help="which CMS v3 file format the target claims to be (default: json)",
    )
    scorecard_parser.add_argument("--max-bytes", type=_positive_int, default=_DEFAULT_MAX_BYTES)
    scorecard_parser.add_argument(
        "--format", dest="output_format", choices=("human", "json"), default="human"
    )
    scorecard_parser.set_defaults(handler=_run_scorecard)

    compare_parser = commands.add_parser(
        "compare",
        help="build the published cross-file comparison for one attested collection run",
    )
    compare_parser.add_argument("--assessments", type=Path, required=True)
    compare_parser.add_argument("--manifest", type=Path, required=True)
    compare_parser.add_argument(
        "--ingest-result",
        dest="ingest_results",
        type=Path,
        action="append",
        default=[],
        metavar="INGEST_JSON",
    )
    compare_parser.add_argument("--generated-at", dest="generated_at")
    compare_parser.add_argument(
        "--format", dest="output_format", choices=("human", "json"), default="json"
    )
    compare_parser.set_defaults(handler=_run_compare)

    site_parser = commands.add_parser(
        "site", help="render the static site from one or more published comparison documents"
    )
    site_parser.add_argument(
        "--comparison",
        dest="comparisons",
        type=Path,
        action="append",
        required=True,
        help="a cohort comparison document; repeat for one site over several cohorts",
    )
    site_parser.add_argument("--out", type=Path, required=True)
    site_parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    site_parser.set_defaults(handler=_run_site)

    mcp_parser = commands.add_parser(
        "mcp",
        help="serve the published dataset to an MCP client over stdio, read-only and offline",
    )
    mcp_parser.add_argument(
        "--site",
        type=Path,
        default=Path("site"),
        help="a directory written by `mrf-honest site`; its api/ documents are the whole source",
    )
    mcp_parser.set_defaults(handler=_run_mcp)

    explain_parser = commands.add_parser("explain", help="explain a quality finding code")
    explain_parser.add_argument("finding_code", metavar="FINDING_CODE")
    explain_parser.set_defaults(handler=_run_explain)

    narrate_parser = commands.add_parser(
        "narrate",
        help="explain one graded file in plain language with citations verified against corpus/",
    )
    narrate_parser.add_argument(
        "--assessments", type=Path, required=True, help="JSON Lines assessment records"
    )
    narrate_parser.add_argument("--index", type=int, default=0, help="which record (0-based)")
    narrate_parser.add_argument("--language", choices=("en", "es"), default="en")
    narrate_parser.add_argument(
        "--root", type=Path, default=Path("."), help="repository root holding corpus/"
    )
    narrate_parser.add_argument("--json", action="store_true", help="emit the full record")
    narrate_parser.set_defaults(handler=_run_narrate)

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
