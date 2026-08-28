"""The published dataset, its Table Schema, and the static JSON API.

Phase 5 of `docs/IMPLEMENTATION-PLAN.md` asks for `dataset.csv` plus a Table Schema description
and a static JSON API. All three are derived here from exactly the comparison documents the
site renders, in the same run, so there is no second pipeline to drift: a row that is not on the
site is not in the dataset, and a number in the dataset is the number on the page.

Two properties are load-bearing and are asserted by tests rather than hoped for:

* **Every column is scoped.** Each row carries its cohort, profile, and both policy
  fingerprints, because rows assessed under different profiles must never be pooled
  (`docs/how-we-compare.md`). A consumer that groups by grade without grouping by profile
  first is doing something the document format cannot prevent, but it cannot say it was not
  told.
* **A refusal travels with the data.** The statistics block's refusal reaches the API as a
  stated field, not as a missing key, for the same reason the site renders it as a paragraph.

Standard library only, per ADR 0002.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

#: Bumped when a column is added, renamed, retyped or removed. Adding a column is a minor
#: change for a consumer that selects by name and a breaking one for a consumer that selects by
#: position, so the number is published rather than left to inference.
DATASET_VERSION = 1

DATASET_NAME = "mrf-honest-file-grades"

#: (column, type, description). One tuple drives the CSV header, the Table Schema, and the
#: tests, so a column cannot appear in the data and be missing from its own description.
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("cohort_id", "string", "The cohort this row was published in."),
    ("as_of", "date", "The UTC date of the collection run."),
    ("profile", "string", "The assessment profile. Rows of different profiles are not comparable."),
    ("publisher_type", "string", "hospital or payer. Rows of different types are not comparable."),
    ("url_provenance", "string", "How the graded URL was obtained."),
    (
        "assessment_policy_fingerprint",
        "string",
        "Fingerprint of the assessment policy this row was produced under.",
    ),
    (
        "retrieval_policy_fingerprint",
        "string",
        "Fingerprint of the retrieval policy this row was produced under.",
    ),
    ("publisher_id", "string", "Stable publisher identifier supplied by an operator."),
    ("publisher_name", "string", "Publisher name as recorded, never inferred from a filename."),
    ("location_id", "string", "The location entry this file was published for."),
    ("slug", "string", "Site path for this row's own page; unique across the dataset."),
    ("requested_url", "string", "The URL retrieved, with any userinfo stripped."),
    ("requested_url_sha256", "string", "SHA-256 of the requested URL."),
    ("content_sha256", "string", "SHA-256 of the verified body, empty when none was admitted."),
    ("size_bytes", "integer", "Byte size of the verified body, empty when none was admitted."),
    ("observed_at", "datetime", "When the retrieval attempt was observed."),
    ("last_updated_on", "date", "The date the file states for itself, empty when it states none."),
    ("template_version", "string", "The template version the file declares, empty when none."),
    ("grade", "string", "The presentation grade, or NOT_GRADED with the reason stated."),
    ("grade_reason", "string", "One sentence saying why the grade is what it is."),
    ("grade_policy_version", "string", "The grade policy this letter was minted under."),
    ("grade_policy_fingerprint", "string", "Fingerprint of that policy's rule table."),
    ("retrievability_status", "string", "OBSERVED, FINDINGS, or NOT_ASSESSED."),
    ("conformance_status", "string", "OBSERVED, FINDINGS, or NOT_ASSESSED."),
    ("completeness_status", "string", "OBSERVED, FINDINGS, or NOT_ASSESSED."),
    ("interpretability_status", "string", "OBSERVED, FINDINGS, or NOT_ASSESSED."),
    ("freshness_status", "string", "OBSERVED, FINDINGS, or NOT_ASSESSED."),
    ("error_findings", "integer", "Count of ERROR findings across all dimensions."),
    ("warning_findings", "integer", "Count of WARNING findings across all dimensions."),
    ("info_findings", "integer", "Count of INFO findings, which never lower a grade."),
    ("network_attempted", "boolean", "Whether a network retrieval was attempted."),
    ("verified_body_available", "boolean", "Whether a verified body was admitted."),
    ("inspection_scan_completed", "boolean", "Whether the charge array was streamed to the end."),
    (
        "lakehouse_status",
        "string",
        "Outcome of the contracted warehouse load, or empty where none was attempted. A refusal "
        "is a stated status, never a blank.",
    ),
)

_DIMENSIONS = (
    "retrievability",
    "conformance",
    "completeness",
    "interpretability",
    "freshness",
)

_CAVEAT = (
    "Each row describes one published file under one stated policy on one date. Rows are not "
    "comparable across profile, publisher type, URL provenance, policy fingerprint, or date, "
    "and a grade does not rank an organization, price care, or determine compliance with "
    "45 CFR part 180."
)


def table_schema() -> dict[str, object]:
    """A Frictionless Table Schema for `dataset.csv`, generated from `COLUMNS`."""

    return {
        "name": DATASET_NAME,
        "version": DATASET_VERSION,
        "primaryKey": ["cohort_id", "slug"],
        "missingValues": [""],
        "caveat": _CAVEAT,
        "fields": [
            {"name": name, "type": kind, "description": description}
            for name, kind, description in COLUMNS
        ],
    }


def _severity_counts(row: Mapping[str, object]) -> dict[str, int]:
    """Count findings by severity across every dimension of one row."""

    counts = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    dimensions = row.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return counts
    for dimension in dimensions.values():
        if not isinstance(dimension, Mapping):
            continue
        findings = dimension.get("findings")
        if not isinstance(findings, Sequence):
            continue
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            severity = str(finding.get("severity"))
            if severity in counts:
                counts[severity] += 1
    return counts


def _dimension_status(row: Mapping[str, object], name: str) -> str:
    dimensions = row.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return ""
    dimension = dimensions.get(name)
    if not isinstance(dimension, Mapping):
        return ""
    return str(dimension.get("status", ""))


def _cell(value: object) -> str:
    """Render one cell. `None` becomes empty, which the schema declares as the missing value."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def dataset_rows(comparisons: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    """One row per graded file, across every comparison, each carrying its own scope."""

    rows: list[dict[str, str]] = []
    for comparison in comparisons:
        cohort = cast(Mapping[str, object], comparison["cohort"])
        scope = cast(Mapping[str, object], cohort["comparison_scope"])
        files = comparison.get("files")
        for entry in files if isinstance(files, Sequence) else ():
            rows.append(_dataset_row(cast(Mapping[str, object], entry), cohort, scope))
    rows.sort(key=lambda row: (row["cohort_id"], row["slug"]))
    return rows


def _dataset_row(
    entry: Mapping[str, object],
    cohort: Mapping[str, object],
    scope: Mapping[str, object],
) -> dict[str, str]:
    grade = cast(Mapping[str, object], entry.get("grade") or {})
    coverage = cast(Mapping[str, object], entry.get("coverage") or {})
    lakehouse = entry.get("lakehouse")
    severities = _severity_counts(entry)
    values: dict[str, object] = {
        "cohort_id": cohort.get("cohort_id"),
        "as_of": entry.get("as_of"),
        "profile": scope.get("profile"),
        "publisher_type": scope.get("publisher_type"),
        "url_provenance": scope.get("url_provenance"),
        "assessment_policy_fingerprint": scope.get("assessment_policy_fingerprint"),
        "retrieval_policy_fingerprint": scope.get("retrieval_policy_fingerprint"),
        "publisher_id": entry.get("publisher_id"),
        "publisher_name": entry.get("publisher_name"),
        "location_id": entry.get("location_id"),
        "slug": entry.get("slug"),
        "requested_url": entry.get("requested_url"),
        "requested_url_sha256": entry.get("requested_url_sha256"),
        "content_sha256": entry.get("content_sha256"),
        "size_bytes": entry.get("size_bytes"),
        "observed_at": entry.get("observed_at"),
        "last_updated_on": entry.get("last_updated_on"),
        "template_version": entry.get("template_version"),
        "grade": grade.get("grade"),
        "grade_reason": grade.get("reason"),
        "grade_policy_version": grade.get("policy_version"),
        "grade_policy_fingerprint": grade.get("policy_fingerprint"),
        "error_findings": severities["ERROR"],
        "warning_findings": severities["WARNING"],
        "info_findings": severities["INFO"],
        "network_attempted": coverage.get("network_attempted"),
        "verified_body_available": coverage.get("verified_body_available"),
        "inspection_scan_completed": coverage.get("inspection_scan_completed"),
        "lakehouse_status": (lakehouse.get("status") if isinstance(lakehouse, Mapping) else None),
    }
    for dimension in _DIMENSIONS:
        values[f"{dimension}_status"] = _dimension_status(entry, dimension)
    return {name: _cell(values.get(name)) for name, _, _ in COLUMNS}


def dataset_csv(comparisons: Sequence[Mapping[str, object]]) -> str:
    """`dataset.csv`, with CRLF line endings as RFC 4180 specifies."""

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=[name for name, _, _ in COLUMNS])
    writer.writeheader()
    writer.writerows(dataset_rows(comparisons))
    return buffer.getvalue()


def api_documents(
    comparisons: Sequence[Mapping[str, object]], origin: str
) -> dict[str, dict[str, object]]:
    """The static JSON API, as a mapping of site-relative path to document.

    The index is deliberately not a bare list of grades. A consumer reaching for one number
    should meet the scope, the caveat, and each cohort's statistics outcome on the way, because
    an API that is easier to misuse than the page it was derived from would undo the page.
    """

    documents: dict[str, dict[str, object]] = {}
    cohorts: list[dict[str, object]] = []
    for comparison in comparisons:
        cohort = cast(Mapping[str, object], comparison["cohort"])
        cohort_id = str(cohort.get("cohort_id"))
        scope = cast(Mapping[str, object], cohort["comparison_scope"])
        statistics = comparison.get("statistics")
        summary = comparison.get("summary")
        entry: dict[str, object] = {
            "cohort_id": cohort_id,
            "as_of": cohort.get("as_of"),
            "comparison_scope": dict(scope),
            "grade_policy": dict(cast(Mapping[str, object], cohort.get("grade_policy") or {})),
            "summary": dict(cast(Mapping[str, object], summary or {})),
            "statistics": (
                dict(cast(Mapping[str, object], statistics))
                if isinstance(statistics, Mapping)
                else None
            ),
            "comparison": f"{origin}/data/{cohort_id}.comparison.json",
            "cohort_document": f"{origin}/api/cohorts/{cohort_id}.json",
        }
        cohorts.append(entry)
        documents[f"api/cohorts/{cohort_id}.json"] = dict(entry) | {
            "files": list(cast(Sequence[object], comparison.get("files") or ())),
            "finding_matrix": list(cast(Sequence[object], comparison.get("finding_matrix") or ())),
            "exclusions": list(cast(Sequence[object], comparison.get("exclusions") or ())),
        }
    documents["api/index.json"] = {
        "name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "caveat": _CAVEAT,
        "not_comparable_across": [
            "profile",
            "publisher_type",
            "url_provenance",
            "assessment_policy_fingerprint",
            "retrieval_policy_fingerprint",
            "as_of",
        ],
        "dataset": f"{origin}/dataset.csv",
        "table_schema": f"{origin}/dataset.schema.json",
        "cohorts": cohorts,
    }
    return documents


def encode(document: Mapping[str, object]) -> str:
    """One encoder for every published JSON document, so bytes are reproducible."""

    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dataset_problems(root: Path, comparisons: Sequence[Mapping[str, object]]) -> list[str]:
    """Check `dataset.csv` and its schema against the documents the render was given."""

    problems: list[str] = []
    dataset_path = root / "dataset.csv"
    schema_path = root / "dataset.schema.json"
    if not dataset_path.is_file():
        return ["dataset.csv was not written"]
    written = list(csv.DictReader(io.StringIO(dataset_path.read_text(encoding="utf-8"))))
    expected = dataset_rows(comparisons)
    if len(written) != len(expected):
        problems.append(f"dataset.csv holds {len(written)} rows against {len(expected)} expected")
    declared_columns = [name for name, _, _ in COLUMNS]
    if written and list(written[0]) != declared_columns:
        problems.append("dataset.csv's header is not the declared column order")
    if not schema_path.is_file():
        problems.append("dataset.schema.json was not written")
        return problems
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    fields = schema.get("fields", [])
    if [str(field["name"]) for field in fields] != declared_columns:
        problems.append("dataset.schema.json does not describe dataset.csv's columns")
    return problems


def _api_problems(root: Path, comparisons: Sequence[Mapping[str, object]]) -> list[str]:
    """Check the static API against the same documents."""

    index_path = root / "api" / "index.json"
    if not index_path.is_file():
        return ["api/index.json was not written"]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    listed = {str(entry.get("cohort_id")) for entry in index.get("cohorts", [])}
    problems: list[str] = []
    for comparison in comparisons:
        cohort_id = str(cast(Mapping[str, object], comparison["cohort"]).get("cohort_id"))
        if cohort_id not in listed:
            problems.append(f"api/index.json does not list {cohort_id}")
        if not (root / "api" / "cohorts" / f"{cohort_id}.json").is_file():
            problems.append(f"api/cohorts/{cohort_id}.json was not written")
    return problems


def missing_exports(comparisons: Sequence[Mapping[str, object]], out_dir: Path) -> list[str]:
    """Report every way the written exports disagree with the documents they came from.

    The deploy path calls this. A dataset that is generated and then silently truncated, or a
    schema that stops describing its own columns, would pass every check that only asks whether
    the file exists. An empty list means the exports say what the comparisons say.
    """

    root = Path(out_dir)
    return _dataset_problems(root, comparisons) + _api_problems(root, comparisons)
