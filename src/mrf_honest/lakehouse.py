"""Local DuckDB + Parquet lakehouse for CMS hospital price-transparency files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

from mrf_honest.contracts import CONTRACT_FINGERPRINT, ContractError, enforce_contracts
from mrf_honest.inspect import INSPECTION_FINGERPRINT, FileInspection, inspect_hospital_file
from mrf_honest.models import INTERMEDIATE_SQL, LAYER_BY_MODEL, MART_SQL, MODEL_DAG, SCHEMA_SQL
from mrf_honest.normalize import (
    NORMALIZATION_POLICY_VERSION,
    NULL_TOKEN,
    SPOOL_COLUMNS,
    NormalizeContext,
    NormalizedCounts,
    spool_hospital_file,
    spool_sizes,
)
from mrf_honest.stream import STREAM_PARSER_VERSION
from mrf_honest.types import PublisherRef

if TYPE_CHECKING:
    import duckdb

PIPELINE_VERSION = "hospital-json-v2"
MANIFEST_SCHEMA_VERSION = 4
_MANIFEST_DIGEST_FIELD = "manifest_body_sha256"
_EXPORT_MODELS = (
    "raw_hospital_items",
    "raw_modifier_information",
    "stg_charge_item",
    "stg_charge_code",
    "stg_charge_group",
    "stg_payer_rate",
    "stg_modifier",
    "stg_modifier_payer",
    "stg_charge_modifier",
    "int_rate_observation",
    "file_finding",
    "mart_file_rate_profile",
    "mart_segmented_dollar_rate",
)
TRANSFORMATION_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "schema_sql": SCHEMA_SQL,
            "intermediate_sql": INTERMEDIATE_SQL,
            "mart_sql": MART_SQL,
            "model_dag": MODEL_DAG,
            "export_models": _EXPORT_MODELS,
            "manifest_schema": MANIFEST_SCHEMA_VERSION,
            "spool_columns": SPOOL_COLUMNS,
            "normalization_policy": NORMALIZATION_POLICY_VERSION,
            "stream_parser": STREAM_PARSER_VERSION,
            "inspection_fingerprint": INSPECTION_FINGERPRINT,
            "contract_fingerprint": CONTRACT_FINGERPRINT,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()
_ENVELOPE_FIELDS = (
    "hospital_name",
    "last_updated_on",
    "version",
    "location_name",
    "hospital_address",
    "license_information",
    "attestation",
    "type_2_npi",
)
_OPTIONAL_ENVELOPE_FIELDS = ("financial_aid_policy",)


class LakehouseError(RuntimeError):
    """The build could not complete without weakening provenance or a contract."""


class LakehouseUnavailable(LakehouseError):
    """The optional DuckDB dependency is not installed."""


@dataclass(frozen=True)
class ModelMetric:
    model_name: str
    layer: str
    rows_produced: int
    rows_scanned: int
    bytes_read: int
    bytes_written: int
    wall_time_ms: float
    system_peak_buffer_memory_bytes: int

    def as_dict(self) -> dict[str, str | int | float]:
        return {
            "model_name": self.model_name,
            "layer": self.layer,
            "rows_produced": self.rows_produced,
            "rows_scanned": self.rows_scanned,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "wall_time_ms": round(self.wall_time_ms, 3),
            "system_peak_buffer_memory_bytes": self.system_peak_buffer_memory_bytes,
        }


@dataclass(frozen=True)
class IngestResult:
    run_id: str
    source_file_id: str
    publisher_id: str
    status: str
    reused: bool
    database_path: Path
    manifest_path: Path
    parquet_files: tuple[Path, ...]
    counts: NormalizedCounts

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "source_file_id": self.source_file_id,
            "publisher_id": self.publisher_id,
            "status": self.status,
            "reused": self.reused,
            "database_path": str(self.database_path),
            "manifest_path": str(self.manifest_path),
            "parquet_files": [str(path) for path in self.parquet_files],
            "counts": self.counts.as_dict(),
        }


def _connect(database: Path) -> duckdb.DuckDBPyConnection:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised in an environment without the extra
        raise LakehouseUnavailable(
            "DuckDB is required for lakehouse builds; install mrf-honest[lakehouse]"
        ) from exc
    return duckdb.connect(str(database))


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _run_id(publisher_id: str, source_file_id: str, inspection_as_of: date) -> str:
    material = (
        f"{PIPELINE_VERSION}\0{publisher_id}\0{source_file_id}\0"
        f"{inspection_as_of.isoformat()}\0{TRANSFORMATION_FINGERPRINT}"
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _partition(value: str) -> str:
    return quote(value, safe="-._~")


def _require_envelope(
    inspection: FileInspection,
) -> tuple[dict[str, Any], date, str, str]:
    observed = inspection.envelope
    envelope = {
        key: observed[key]
        for key in (*_ENVELOPE_FIELDS, *_OPTIONAL_ENVELOPE_FIELDS)
        if key in observed
    }
    missing = [key for key in _ENVELOPE_FIELDS if key not in envelope]
    if missing:
        raise LakehouseError(f"missing required CMS v3 envelope fields: {', '.join(missing)}")
    hospital_name = envelope["hospital_name"]
    updated = envelope["last_updated_on"]
    version = envelope["version"]
    scalar_fields = (hospital_name, updated, version)
    if not all(isinstance(value, str) and value.strip() for value in scalar_fields):
        raise LakehouseError(
            "hospital_name, last_updated_on, and version must be non-empty strings"
        )
    if cast(str, version) != "3.0.0":
        raise LakehouseError(f"unsupported hospital JSON template version: {version!r}")
    try:
        period = date.fromisoformat(cast(str, updated))
    except ValueError as exc:
        raise LakehouseError(f"last_updated_on is not an ISO date: {updated!r}") from exc
    return envelope, period, cast(str, version), cast(str, hospital_name)


def _profile_execute(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: object,
    profile_path: Path,
) -> dict[str, Any]:
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute("PRAGMA enable_profiling='json'")
    connection.execute(f"PRAGMA profiling_output={_sql_string(profile_path)}")
    started = time.perf_counter()
    try:
        connection.execute(sql, parameters)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1_000
        try:
            connection.execute("PRAGMA disable_profiling")
        except Exception as cleanup_exc:
            exc.add_note(f"disabling profiling also failed: {cleanup_exc}")
        raise
    else:
        elapsed_ms = (time.perf_counter() - started) * 1_000
        connection.execute("PRAGMA disable_profiling")
    profile: dict[str, Any] = {}
    if profile_path.exists():
        loaded = json.loads(profile_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            profile = loaded
    profile["measured_wall_time_ms"] = elapsed_ms
    return profile


def _metric_from_profile(
    model: str,
    rows: int,
    profile: dict[str, Any],
) -> ModelMetric:
    return ModelMetric(
        model_name=model,
        layer=LAYER_BY_MODEL[model],
        rows_produced=rows,
        rows_scanned=int(profile.get("cumulative_rows_scanned", 0)),
        bytes_read=int(profile.get("total_bytes_read", 0)),
        bytes_written=int(profile.get("total_bytes_written", 0)),
        wall_time_ms=float(profile.get("measured_wall_time_ms", 0.0)),
        system_peak_buffer_memory_bytes=int(profile.get("system_peak_buffer_memory", 0)),
    )


def _row_count(connection: duckdb.DuckDBPyConnection, model: str, run_id: str) -> int:
    # ``model`` is selected exclusively from the module's declared model constants.
    row = connection.execute(
        f"SELECT count(*) FROM {model} WHERE run_id = ?",  # noqa: S608
        [run_id],
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _load_findings(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    inspection: FileInspection,
    wall_time_ms: float,
) -> ModelMetric:
    rows = [
        [
            run_id,
            ordinal,
            finding.code,
            finding.dimension,
            finding.severity,
            finding.message,
            json.dumps(finding.citations, separators=(",", ":")),
            finding.occurrences,
        ]
        for ordinal, finding in enumerate(inspection.findings)
    ]
    if rows:
        connection.executemany(
            """INSERT INTO file_finding (
                run_id, finding_ordinal, code, dimension, severity, message,
                citations_json, occurrences
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    metric = ModelMetric(
        model_name="file_finding",
        layer=LAYER_BY_MODEL["file_finding"],
        rows_produced=len(rows),
        rows_scanned=inspection.item_count,
        bytes_read=inspection.source_size,
        bytes_written=0,
        wall_time_ms=wall_time_ms,
        system_peak_buffer_memory_bytes=0,
    )
    _record_metric(connection, run_id, metric)
    return metric


def _validate_ingest_request(
    source: Path,
    publisher: PublisherRef,
    threads: int,
) -> None:
    if not source.is_file():
        raise LakehouseError(f"source is not a file: {source}")
    if not publisher.identifier.strip():
        raise LakehouseError("publisher.identifier must be non-empty")
    if threads <= 0:
        raise LakehouseError("threads must be positive")


def _inspect_source(
    source: Path,
    reported_source: Path,
    publisher: PublisherRef,
    source_file_id: str,
    source_size: int,
    as_of: date,
) -> tuple[FileInspection, float]:
    started = time.perf_counter()
    inspection = replace(
        inspect_hospital_file(source, publisher, as_of=as_of),
        source_path=str(reported_source),
    )
    wall_time_ms = (time.perf_counter() - started) * 1_000
    if inspection.source_sha256 != source_file_id or inspection.source_size != source_size:
        raise LakehouseError("source changed while it was being inspected")
    if not inspection.scan_completed:
        raise LakehouseError("inspection could not complete the source array scan")
    return inspection, wall_time_ms


def _snapshot_source(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    """Copy one immutable build input and prove it still matches the admitted identity."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        while chunk := input_handle.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
            output_handle.write(chunk)
    if (digest.hexdigest(), size) != (expected_sha256, expected_size):
        destination.unlink(missing_ok=True)
        raise LakehouseError("source changed while its immutable build snapshot was created")


def _reset_staging(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)


def _record_metric(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    metric: ModelMetric,
) -> None:
    connection.execute(
        """INSERT INTO model_metric VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id, model_name) DO UPDATE SET
            layer = excluded.layer,
            rows_produced = excluded.rows_produced,
            rows_scanned = excluded.rows_scanned,
            bytes_read = excluded.bytes_read,
            bytes_written = excluded.bytes_written,
            wall_time_ms = excluded.wall_time_ms,
            system_peak_buffer_memory_bytes = excluded.system_peak_buffer_memory_bytes,
            measured_at = excluded.measured_at""",
        [
            run_id,
            metric.model_name,
            metric.layer,
            metric.rows_produced,
            metric.rows_scanned,
            metric.bytes_read,
            metric.bytes_written,
            metric.wall_time_ms,
            metric.system_peak_buffer_memory_bytes,
            _now(),
        ],
    )


def _duckdb_runtime(
    connection: duckdb.DuckDBPyConnection,
    *,
    requested_memory_limit: str,
    requested_threads: int,
) -> dict[str, object]:
    row = connection.execute(
        """SELECT version(), current_setting('memory_limit'), current_setting('threads'),
        current_setting('preserve_insertion_order'), current_setting('temp_directory')"""
    ).fetchone()
    if row is None:  # pragma: no cover - scalar settings query always returns one row
        raise LakehouseError("DuckDB did not report its effective runtime settings")
    return {
        "version": str(row[0]),
        "requested_memory_limit": requested_memory_limit,
        "effective_memory_limit": str(row[1]),
        "requested_threads": requested_threads,
        "effective_threads": int(row[2]),
        "preserve_insertion_order": bool(row[3]),
        "temp_directory": str(row[4]),
        "memory_limit_scope": "DuckDB buffer manager; not an end-to-end process RSS cap",
    }


def _load_spools(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    paths: dict[str, Path],
    profile_dir: Path,
) -> list[ModelMetric]:
    metrics: list[ModelMetric] = []
    for model, path in paths.items():
        sql = (
            f"COPY {model} FROM {_sql_string(path)} "
            f"(FORMAT CSV, HEADER TRUE, DELIMITER '\t', NULL '{NULL_TOKEN}')"
        )
        profile = _profile_execute(connection, sql, [], profile_dir / f"load-{model}.json")
        metric = _metric_from_profile(model, _row_count(connection, model, run_id), profile)
        _record_metric(connection, run_id, metric)
        metrics.append(metric)
    return metrics


def _build_declared_models(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    profile_dir: Path,
) -> list[ModelMetric]:
    specs = (
        ("int_rate_observation", INTERMEDIATE_SQL, [run_id, run_id, run_id]),
        ("mart_file_rate_profile", MART_SQL, [run_id]),
    )
    metrics: list[ModelMetric] = []
    for model, sql, parameters in specs:
        profile = _profile_execute(connection, sql, parameters, profile_dir / f"build-{model}.json")
        metric = _metric_from_profile(model, _row_count(connection, model, run_id), profile)
        _record_metric(connection, run_id, metric)
        metrics.append(metric)
    model = "mart_segmented_dollar_rate"
    profile = _profile_execute(
        connection,
        f"SELECT count(*) FROM {model} WHERE run_id = ?",  # noqa: S608
        [run_id],
        profile_dir / f"measure-{model}.json",
    )
    metric = _metric_from_profile(model, _row_count(connection, model, run_id), profile)
    _record_metric(connection, run_id, metric)
    metrics.append(metric)
    return metrics


def _delete_run_rows(connection: duckdb.DuckDBPyConnection, run_id: str) -> None:
    for model in (
        "model_metric",
        "file_finding",
        "mart_file_rate_profile",
        "int_rate_observation",
        "stg_payer_rate",
        "stg_charge_modifier",
        "stg_modifier_payer",
        "stg_modifier",
        "stg_charge_code",
        "stg_charge_group",
        "stg_charge_item",
        "raw_hospital_items",
        "raw_modifier_information",
        "hospital_file",
    ):
        # The table names are a closed internal tuple, never caller-controlled.
        connection.execute(
            f"DELETE FROM {model} WHERE run_id = ?",  # noqa: S608
            [run_id],
        )


def _parquet_relative_path(
    model: str,
    publisher_id: str,
    period: date,
    version: str,
    source_file_id: str,
    run_id: str,
) -> Path:
    return (
        Path("parquet")
        / LAYER_BY_MODEL[model]
        / model
        / f"publisher_id={_partition(publisher_id)}"
        / f"period={period.isoformat()}"
        / f"file_version={_partition(version)}"
        / f"run_id={run_id}"
        / f"{source_file_id}.parquet"
    )


def _source_archive_relative_path(source_file_id: str) -> Path:
    return Path("sources") / "sha256" / source_file_id[:2] / f"{source_file_id}.json"


def _write_parquet(
    connection: duckdb.DuckDBPyConnection,
    staging: Path,
    run_id: str,
    publisher_id: str,
    period: date,
    version: str,
    source_file_id: str,
) -> list[Path]:
    relative_paths: list[Path] = []
    for model in _EXPORT_MODELS:
        relative = _parquet_relative_path(
            model, publisher_id, period, version, source_file_id, run_id
        )
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Model names and the run id are internal SHA-backed identifiers; paths are SQL-escaped.
        connection.execute(
            f"COPY (SELECT * FROM {model} WHERE run_id = {_sql_string(run_id)}) "  # noqa: S608
            f"TO {_sql_string(destination)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        relative_paths.append(relative)
    return relative_paths


def _promote(
    staging: Path,
    warehouse: Path,
    relative_paths: list[Path],
    *,
    reusable_paths: frozenset[Path] = frozenset(),
) -> list[Path]:
    promoted: list[Path] = []
    try:
        for relative in relative_paths:
            source = staging / relative
            destination = warehouse / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and relative in reusable_paths:
                if _hash_file(source) != _hash_file(destination):
                    raise LakehouseError(
                        f"content-addressed artifact has conflicting bytes: {relative}"
                    )
                source.unlink()
                continue
            try:
                os.link(source, destination)
            except FileExistsError as exc:
                raise LakehouseError(
                    f"refusing to overwrite immutable artifact: {relative}"
                ) from exc
            source.unlink()
            promoted.append(destination)
    except BaseException:
        _clean_promoted(promoted)
        raise
    return promoted


def _clean_promoted(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _counts_from_row(row: tuple[Any, ...]) -> NormalizedCounts:
    return NormalizedCounts(
        source_bytes_read=int(row[0]),
        items=int(row[1]),
        codes=int(row[2]),
        charge_groups=int(row[3]),
        payer_rates=int(row[4]),
        modifiers=int(row[5]),
        modifier_payer_mappings=int(row[6]),
        charge_modifiers=int(row[7]),
        had_bom=bool(row[8]),
    )


def _manifest_artifacts(staging: Path, relative_paths: list[Path]) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for relative in relative_paths:
        digest, size = _hash_file(staging / relative)
        artifacts.append({"path": str(relative), "sha256": digest, "bytes": size})
    return artifacts


def _manifest_body_sha256(manifest: dict[str, Any]) -> str:
    """Digest every immutable manifest field while allowing prepared -> success finalization."""
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"status", _MANIFEST_DIGEST_FIELD}
    }
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _verify_manifest_body(manifest: dict[str, Any]) -> None:
    observed = manifest.get(_MANIFEST_DIGEST_FIELD)
    if not isinstance(observed, str) or observed != _manifest_body_sha256(manifest):
        raise LakehouseError("successful run manifest body failed integrity check")


def _artifact_inventory(raw_artifacts: object) -> dict[str, tuple[str, int]]:
    if not isinstance(raw_artifacts, list):
        raise LakehouseError("successful run manifest has no artifact integrity records")
    expected: dict[str, tuple[str, int]] = {}
    for value in raw_artifacts:
        if not isinstance(value, dict):
            raise LakehouseError("successful run manifest has an invalid artifact record")
        path_value = value.get("path")
        digest_value = value.get("sha256")
        size_value = value.get("bytes")
        valid = (
            isinstance(path_value, str)
            and isinstance(digest_value, str)
            and len(digest_value) == 64
            and all(char in "0123456789abcdef" for char in digest_value)
            and isinstance(size_value, int)
            and not isinstance(size_value, bool)
            and size_value >= 0
        )
        if not valid:
            raise LakehouseError("successful run manifest has an invalid artifact record")
        expected[cast(str, path_value)] = (cast(str, digest_value), cast(int, size_value))
    if len(expected) != len(raw_artifacts):
        raise LakehouseError("successful run manifest has duplicate artifact paths")
    return expected


def _resolve_artifact(warehouse: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise LakehouseError("successful run manifest contains an unsafe artifact path")
    try:
        resolved = (warehouse / relative).resolve(strict=True)
    except OSError as exc:
        raise LakehouseError(f"successful run artifact is missing: {relative}") from exc
    if not resolved.is_relative_to(warehouse.resolve()) or not resolved.is_file():
        raise LakehouseError(f"successful run artifact is unsafe: {relative}")
    return resolved


def _verified_manifest_paths(
    warehouse: Path,
    manifest: dict[str, Any],
    expected_paths: tuple[Path, ...],
    expected_source_archive: Path,
) -> tuple[Path, ...]:
    raw_paths = manifest.get("parquet_files")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_paths, list) or not all(isinstance(value, str) for value in raw_paths):
        raise LakehouseError("successful run manifest has invalid parquet_files")
    if cast(list[str], raw_paths) != [str(path) for path in expected_paths]:
        raise LakehouseError("successful run manifest has an unexpected artifact inventory")
    if manifest.get("source_archive") != str(expected_source_archive):
        raise LakehouseError("successful run manifest has an unexpected source archive")
    expected = _artifact_inventory(raw_artifacts)
    expected_artifacts = {str(expected_source_archive), *(str(path) for path in expected_paths)}
    if expected_artifacts != set(expected):
        raise LakehouseError("successful run manifest artifact inventory does not match paths")

    for raw_path in expected_artifacts:
        relative = Path(raw_path)
        resolved = _resolve_artifact(warehouse, relative)
        digest, size = _hash_file(resolved)
        expected_digest, expected_size = expected[raw_path]
        if (digest, size) != (expected_digest, expected_size):
            raise LakehouseError(f"successful run artifact failed integrity check: {relative}")
    return tuple(warehouse / relative for relative in expected_paths)


def _validated_manifest_counts(
    manifest: dict[str, Any],
    *,
    run_id: str,
    source_file_id: str,
    publisher_id: str,
) -> dict[str, Any]:
    _verify_manifest_body(manifest)
    source = manifest.get("source")
    publisher = manifest.get("publisher")
    counts = manifest.get("counts")
    valid_identity = (
        manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and manifest.get("pipeline_version") == PIPELINE_VERSION
        and manifest.get("transformation_fingerprint") == TRANSFORMATION_FINGERPRINT
        and manifest.get("inspection_fingerprint") == INSPECTION_FINGERPRINT
        and manifest.get("run_id") == run_id
        and manifest.get("status") in {"prepared", "success"}
        and isinstance(source, dict)
        and source.get("sha256") == source_file_id
        and isinstance(publisher, dict)
        and publisher.get("identifier") == publisher_id
    )
    if not valid_identity:
        raise LakehouseError("successful run manifest identity does not match the catalog")
    if not isinstance(counts, dict) or not isinstance(counts.get("had_bom"), bool):
        raise LakehouseError("successful run manifest has invalid counts")
    return cast(dict[str, Any], counts)


def _existing_result(
    connection: duckdb.DuckDBPyConnection,
    warehouse: Path,
    database: Path,
    run_id: str,
    requested_as_of: date,
) -> IngestResult | None:
    row = connection.execute(
        """SELECT source_file_id, publisher_id, source_bytes_read, item_count, code_count,
        charge_group_count, payer_rate_count, modifier_count,
        modifier_payer_mapping_count, charge_modifier_count, period, file_version,
        inspection_as_of, transformation_fingerprint
        FROM ingest_run WHERE run_id = ? AND status = 'success'""",
        [run_id],
    ).fetchone()
    if row is None:
        return None
    manifest_path = warehouse / "runs" / f"{run_id}.json"
    if not manifest_path.exists():
        raise LakehouseError(f"successful run {run_id} is missing its immutable manifest")
    try:
        loaded: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LakehouseError(f"successful run {run_id} has an unreadable manifest") from exc
    if not isinstance(loaded, dict):
        raise LakehouseError(f"successful run {run_id} manifest is not an object")
    manifest = cast(dict[str, Any], loaded)
    manifest_counts = _validated_manifest_counts(
        manifest,
        run_id=run_id,
        source_file_id=str(row[0]),
        publisher_id=str(row[1]),
    )
    catalog_counts = {
        "source_bytes_read": int(row[2]),
        "items": int(row[3]),
        "codes": int(row[4]),
        "charge_groups": int(row[5]),
        "payer_rates": int(row[6]),
        "modifiers": int(row[7]),
        "modifier_payer_mappings": int(row[8]),
        "charge_modifiers": int(row[9]),
    }
    if any(manifest_counts.get(key) != value for key, value in catalog_counts.items()):
        raise LakehouseError("successful run manifest counts do not match the catalog")
    catalog_as_of = row[12]
    if (catalog_as_of, row[13]) != (requested_as_of, TRANSFORMATION_FINGERPRINT):
        raise LakehouseError("successful run catalog identity does not match this transformation")
    inspection_value = manifest.get("inspection")
    observed_as_of = inspection_value.get("as_of") if isinstance(inspection_value, dict) else None
    if observed_as_of != requested_as_of.isoformat():
        raise LakehouseError("successful run manifest inspection date does not match its identity")
    period = row[10]
    version = row[11]
    if not isinstance(period, date) or not isinstance(version, str):
        raise LakehouseError("successful run catalog has invalid source dimensions")
    expected_paths = tuple(
        _parquet_relative_path(
            model,
            str(row[1]),
            period,
            version,
            str(row[0]),
            run_id,
        )
        for model in _EXPORT_MODELS
    )
    source_archive = _source_archive_relative_path(str(row[0]))
    parquet_files = _verified_manifest_paths(
        warehouse,
        manifest,
        expected_paths,
        source_archive,
    )
    if manifest.get("status") == "prepared":
        _finalize_manifest(manifest_path)
    counts = NormalizedCounts(
        source_bytes_read=int(row[2]),
        items=int(row[3]),
        codes=int(row[4]),
        charge_groups=int(row[5]),
        payer_rates=int(row[6]),
        modifiers=int(row[7]),
        modifier_payer_mappings=int(row[8]),
        charge_modifiers=int(row[9]),
        had_bom=cast(bool, manifest_counts["had_bom"]),
    )
    return IngestResult(
        run_id=run_id,
        source_file_id=str(row[0]),
        publisher_id=str(row[1]),
        status="success",
        reused=True,
        database_path=database,
        manifest_path=manifest_path,
        parquet_files=parquet_files,
        counts=counts,
    )


def _write_manifest(
    staging: Path,
    run_id: str,
    source: Path,
    source_file_id: str,
    source_size: int,
    publisher: PublisherRef,
    envelope: dict[str, Any],
    counts: NormalizedCounts,
    spool_bytes: dict[str, int],
    metrics: list[ModelMetric],
    inspection: FileInspection,
    source_archive: Path,
    relative_paths: list[Path],
    started_at: datetime,
    finished_at: datetime,
    duckdb_runtime: dict[str, object],
) -> Path:
    relative = Path("runs") / f"{run_id}.json"
    path = staging / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "transformation_fingerprint": TRANSFORMATION_FINGERPRINT,
        "inspection_fingerprint": INSPECTION_FINGERPRINT,
        "run_id": run_id,
        "status": "prepared",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "source": {
            "path": str(source.resolve()),
            "sha256": source_file_id,
            "bytes": source_size,
        },
        "publisher": {
            "identifier": publisher.identifier,
            "name": publisher.name,
            "source_url": publisher.source_url,
        },
        "envelope": envelope,
        "inspection": inspection.to_dict(),
        "counts": counts.as_dict(),
        "spool_bytes": spool_bytes,
        "model_dag": {name: list(parents) for name, parents in MODEL_DAG.items()},
        "model_metrics": [metric.as_dict() for metric in metrics],
        "contracts": {"status": "passed"},
        "duckdb": duckdb_runtime,
        "source_archive": str(source_archive),
        "parquet_files": [str(path) for path in relative_paths],
        "artifacts": _manifest_artifacts(staging, [source_archive, *relative_paths]),
    }
    payload[_MANIFEST_DIGEST_FIELD] = _manifest_body_sha256(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return relative


def _finalize_manifest(path: Path) -> None:
    """Atomically mark a prepared manifest successful only after the catalog commit."""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LakehouseError(f"cannot finalize unreadable run manifest: {path}") from exc
    if not isinstance(loaded, dict) or loaded.get("status") != "prepared":
        raise LakehouseError(f"cannot finalize manifest outside prepared state: {path}")
    _verify_manifest_body(loaded)
    loaded["status"] = "success"
    temporary = path.with_suffix(".json.finalizing")
    temporary.write_text(
        json.dumps(loaded, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clean_incomplete_run_artifacts(
    connection: duckdb.DuckDBPyConnection,
    warehouse: Path,
    run_id: str,
    expected_paths: tuple[Path, ...],
) -> None:
    """Remove only deterministic artifacts belonging to a non-success catalog run."""
    row = connection.execute("SELECT status FROM ingest_run WHERE run_id = ?", [run_id]).fetchone()
    if row is None or row[0] == "success":
        return
    for relative in expected_paths:
        (warehouse / relative).unlink(missing_ok=True)
    (warehouse / "runs" / f"{run_id}.json").unlink(missing_ok=True)


def ingest_hospital_file(
    source: str | Path,
    warehouse: str | Path,
    *,
    publisher: PublisherRef,
    memory_limit: str = "256MB",
    threads: int = 2,
    as_of: date | None = None,
) -> IngestResult:
    """Build one idempotent, contracted hospital-file snapshot.

    The source is hashed before any rows are admitted.  All database changes are transactional;
    Parquet files and the run manifest are staged and atomically promoted before commit.  A
    repeated publisher/content/as-of tuple returns the existing run instead of duplicating a
    snapshot.
    """
    source_path = Path(source)
    warehouse_path = Path(warehouse)
    _validate_ingest_request(source_path, publisher, threads)
    warehouse_path.mkdir(parents=True, exist_ok=True)
    database = warehouse_path / "warehouse.duckdb"
    started_at = _now()
    inspection_as_of = as_of or started_at.date()
    source_file_id, source_size = _hash_file(source_path)
    run_id = _run_id(publisher.identifier, source_file_id, inspection_as_of)
    connection = _connect(database)
    staging = warehouse_path / ".staging" / run_id
    promoted: list[Path] = []
    run_started = False
    transaction_started = False
    committed = False
    stage = "initialization"
    try:
        temp_directory = warehouse_path / ".duckdb-tmp"
        temp_directory.mkdir(parents=True, exist_ok=True)
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET temp_directory = ?", [str(temp_directory)])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        duckdb_runtime = _duckdb_runtime(
            connection,
            requested_memory_limit=memory_limit,
            requested_threads=threads,
        )
        connection.execute(SCHEMA_SQL)
        existing = _existing_result(
            connection,
            warehouse_path,
            database,
            run_id,
            inspection_as_of,
        )
        if existing is not None:
            return existing
        stage = "source snapshot"
        _reset_staging(staging)
        source_archive = _source_archive_relative_path(source_file_id)
        snapshot = staging / source_archive
        _snapshot_source(
            source_path,
            snapshot,
            expected_sha256=source_file_id,
            expected_size=source_size,
        )
        inspection, inspection_wall_time_ms = _inspect_source(
            snapshot,
            source_path,
            publisher,
            source_file_id,
            source_size,
            inspection_as_of,
        )
        stage = "envelope validation"
        envelope, period, version, hospital_name = _require_envelope(inspection)
        expected_paths = tuple(
            _parquet_relative_path(
                model,
                publisher.identifier,
                period,
                version,
                source_file_id,
                run_id,
            )
            for model in _EXPORT_MODELS
        )
        _clean_incomplete_run_artifacts(
            connection,
            warehouse_path,
            run_id,
            expected_paths,
        )
        connection.execute(
            "INSERT OR IGNORE INTO source_file VALUES (?, ?, ?, ?, ?)",
            [source_file_id, source_file_id, source_size, str(source_path.resolve()), started_at],
        )
        connection.execute(
            """INSERT INTO ingest_run (
                run_id, source_file_id, publisher_id, pipeline_version, status, started_at,
                inspection_as_of, period, file_version, transformation_fingerprint
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO UPDATE SET
                status = 'running', started_at = excluded.started_at, finished_at = NULL,
                error_message = NULL""",
            [
                run_id,
                source_file_id,
                publisher.identifier,
                PIPELINE_VERSION,
                started_at,
                inspection_as_of,
                period,
                version,
                TRANSFORMATION_FINGERPRINT,
            ],
        )
        run_started = True
        stage = "bounded normalization"
        spool_dir = staging / "spool"
        context = NormalizeContext(
            run_id=run_id,
            source_file_id=source_file_id,
            publisher_id=publisher.identifier,
            period=period.isoformat(),
            file_version=version,
        )
        counts, spool_paths = spool_hospital_file(snapshot, spool_dir, context)
        measured_spool_bytes = spool_sizes(spool_paths)

        connection.execute("BEGIN TRANSACTION")
        transaction_started = True
        stage = "relational load"
        _delete_run_rows(connection, run_id)
        connection.execute(
            "INSERT INTO hospital_file VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                source_file_id,
                publisher.identifier,
                hospital_name,
                period,
                version,
                json.dumps(envelope, separators=(",", ":"), sort_keys=True),
            ],
        )
        inspection_metric = _load_findings(
            connection,
            run_id,
            inspection,
            inspection_wall_time_ms,
        )
        metrics = [inspection_metric]
        metrics.extend(_load_spools(connection, run_id, spool_paths, staging / "profiles"))
        stage = "declared model build"
        metrics.extend(_build_declared_models(connection, run_id, staging / "profiles"))
        stage = "contract validation"
        enforce_contracts(connection, run_id)

        stage = "Parquet export"
        relative_paths = _write_parquet(
            connection,
            staging,
            run_id,
            publisher.identifier,
            period,
            version,
            source_file_id,
        )
        finished_at = _now()
        manifest_relative = _write_manifest(
            staging,
            run_id,
            source_path,
            source_file_id,
            source_size,
            publisher,
            envelope,
            counts,
            measured_spool_bytes,
            metrics,
            inspection,
            source_archive,
            relative_paths,
            started_at,
            finished_at,
            duckdb_runtime,
        )
        stage = "artifact promotion"
        promoted = _promote(
            staging,
            warehouse_path,
            [source_archive, *relative_paths, manifest_relative],
            reusable_paths=frozenset({source_archive}),
        )
        stage = "catalog commit"
        connection.execute(
            """UPDATE ingest_run SET
                status = 'success', finished_at = ?, source_bytes_read = ?, item_count = ?,
                code_count = ?, charge_group_count = ?, payer_rate_count = ?, modifier_count = ?,
                modifier_payer_mapping_count = ?, charge_modifier_count = ?,
                error_message = NULL
            WHERE run_id = ?""",
            [
                finished_at,
                counts.source_bytes_read,
                counts.items,
                counts.codes,
                counts.charge_groups,
                counts.payer_rates,
                counts.modifiers,
                counts.modifier_payer_mappings,
                counts.charge_modifiers,
                run_id,
            ],
        )
        connection.execute("COMMIT")
        transaction_started = False
        committed = True
        stage = "manifest finalization"
        _finalize_manifest(warehouse_path / manifest_relative)
        parquet_files = tuple(warehouse_path / path for path in relative_paths)
        return IngestResult(
            run_id=run_id,
            source_file_id=source_file_id,
            publisher_id=publisher.identifier,
            status="success",
            reused=False,
            database_path=database,
            manifest_path=warehouse_path / manifest_relative,
            parquet_files=parquet_files,
            counts=counts,
        )
    except Exception as exc:
        if transaction_started:
            try:
                connection.execute("ROLLBACK")
            except Exception as rollback_exc:
                exc.add_note(f"rollback also failed: {rollback_exc}")
        if not committed:
            _clean_promoted(promoted)
            if run_started:
                try:
                    connection.execute(
                        """UPDATE ingest_run SET status = 'failed', finished_at = ?,
                        error_message = ? WHERE run_id = ?""",
                        [_now(), str(exc)[:2_000], run_id],
                    )
                except Exception as status_exc:
                    exc.add_note(f"recording failed status also failed: {status_exc}")
        if isinstance(exc, (ContractError, LakehouseError)):
            raise
        raise LakehouseError(f"lakehouse build failed during {stage}: {exc}") from exc
    finally:
        connection.close()
        _reset_staging(staging)


def query_file_profile(
    warehouse: str | Path,
    run_id: str,
) -> list[dict[str, object]]:
    """Read the explicit denominators in the per-file methodology/rate-kind profile."""
    database = Path(warehouse) / "warehouse.duckdb"
    if not database.is_file():
        raise LakehouseError(f"warehouse database does not exist: {database}")
    connection = _connect(database)
    try:
        identity = connection.execute(
            """SELECT inspection_as_of FROM ingest_run
            WHERE run_id = ? AND status = 'success'""",
            [run_id],
        ).fetchone()
        if identity is None or not isinstance(identity[0], date):
            raise LakehouseError(f"run is not a committed success: {run_id}")
        if (
            _existing_result(
                connection,
                Path(warehouse),
                database,
                run_id,
                identity[0],
            )
            is None
        ):  # pragma: no cover - the status query above found the same run
            raise LakehouseError(f"run is not a committed success: {run_id}")
        rows = connection.execute(
            """SELECT methodology, rate_kind, eligible_for_segmented_comparison,
            observation_count FROM mart_file_rate_profile
            WHERE run_id = ? ORDER BY methodology, rate_kind""",
            [run_id],
        ).fetchall()
        return [
            {
                "methodology": str(row[0]),
                "rate_kind": str(row[1]),
                "eligible_for_segmented_comparison": bool(row[2]),
                "observation_count": int(row[3]),
            }
            for row in rows
        ]
    finally:
        connection.close()
