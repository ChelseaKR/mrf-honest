"""Cross-file comparison over persisted hospital MRF assessments.

The persisted ``FileAssessment`` deliberately carries no composite score, rank, or letter
(``docs/how-we-grade.md``, ADR 0004). A published cohort still needs one honest, explainable
summary per file, so this module derives a separate, versioned presentation grade from each
persisted record and never writes it back into the assessment artifact. The grade describes one
file under one stated policy; it is not an organization rating and not a compliance label
(ADR 0005).

Phase 3 recorded a prerequisite before any published retrievability comparison: matching
default-policy rows are comparable only when the caller knows they came from one controlled
collection run (``docs/PHASE-3-FINDINGS.md``). ``build_comparison`` therefore requires a
manifest that attests exactly that, and refuses to proceed without it.

Fail-closed rules, in one place:

- a failed download attempt is a stated ``F`` with the dated reason, never a missing row;
- retrievability that was ``NOT_ASSESSED`` (invalid input, a project size ceiling, local cache
  trouble) is ``NOT_GRADED`` with the reason, because attributing local limits to the publisher
  would be wrong, and silently conflating it with ``F`` would be worse;
- an incomplete charge-array stream is an ``F``: what could not be read is failed, not passed;
- a local dimension without evidence counts against the grade exactly like a dimension with
  structural errors; absence of a check is stated, never implied as a pass.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from mrf_honest.scorecard import require_comparable

GRADE_POLICY_VERSION = "cms-hospital-json-v3-file-grade-v1"

#: The four dimensions the local inspector can evidence; retrievability is handled separately.
LOCAL_DIMENSIONS = ("conformance", "completeness", "interpretability", "freshness")

NOT_GRADED = "NOT_GRADED"

#: The complete deterministic policy, hashed so a rule change creates a new fingerprint instead
#: of silently regrading old cohorts under new semantics.
_GRADE_RULES: dict[str, object] = {
    "version": GRADE_POLICY_VERSION,
    "retrievability_findings": "F",
    "retrievability_not_assessed": NOT_GRADED,
    "scan_incomplete": "F",
    "error_dimension_counts": {"0": "A or B", "1": "C", "2": "D", "3+": "F"},
    "warnings_split_a_b": True,
    "info_findings_never_lower_a_grade": True,
    "not_assessed_local_dimension_counts_as_error_dimension": True,
}

GRADE_POLICY_FINGERPRINT = hashlib.sha256(
    json.dumps(_GRADE_RULES, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


class CohortError(Exception):
    """Raised when a cohort cannot be built without violating a stated comparison rule."""


@dataclass(frozen=True)
class FileGrade:
    """One deterministic presentation grade with the sentence that justifies it."""

    grade: str
    reason: str
    error_dimensions: tuple[str, ...] = ()
    warning_findings: int = 0
    info_findings: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "grade": self.grade,
            "reason": self.reason,
            "error_dimensions": list(self.error_dimensions),
            "warning_findings": self.warning_findings,
            "info_findings": self.info_findings,
            "policy_version": GRADE_POLICY_VERSION,
            "policy_fingerprint": GRADE_POLICY_FINGERPRINT,
        }


def _required_mapping(record: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = record.get(key)
    if not isinstance(value, Mapping):
        raise CohortError(f"assessment record is missing required object field {key!r}")
    return cast(Mapping[str, object], value)


def _required_str(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise CohortError(f"assessment record is missing required string field {key!r}")
    return value


def _findings(dimension: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = dimension.get("findings")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    collected: list[Mapping[str, object]] = []
    for finding in raw:
        if isinstance(finding, Mapping):
            collected.append(cast(Mapping[str, object], finding))
    return tuple(collected)


def _severity_count(findings: Iterable[Mapping[str, object]], severity: str) -> int:
    return sum(1 for finding in findings if finding.get("severity") == severity)


def _grade_local_dimensions(scorecard: Mapping[str, object]) -> FileGrade:
    error_dimensions: list[str] = []
    warnings = 0
    infos = 0
    for name in LOCAL_DIMENSIONS:
        dimension = _required_mapping(scorecard, name)
        findings = _findings(dimension)
        has_error = _severity_count(findings, "ERROR") > 0
        if has_error or dimension.get("status") == "NOT_ASSESSED":
            error_dimensions.append(name)
        warnings += _severity_count(findings, "WARNING")
        infos += _severity_count(findings, "INFO")
    failed = tuple(error_dimensions)
    if not failed:
        if warnings == 0:
            reason = (
                "every assessed dimension completed with no error or warning findings; "
                "tolerated INFO observations do not lower a grade"
            )
            return FileGrade("A", reason, failed, warnings, infos)
        reason = f"no structural errors; {warnings} warning finding(s) were recorded"
        return FileGrade("B", reason, failed, warnings, infos)
    named = ", ".join(failed)
    reason = (
        f"errors or missing evidence in {len(failed)} of {len(LOCAL_DIMENSIONS)} "
        f"local dimensions: {named}"
    )
    if len(failed) == 1:
        return FileGrade("C", reason, failed, warnings, infos)
    if len(failed) == 2:
        return FileGrade("D", reason, failed, warnings, infos)
    return FileGrade("F", reason, failed, warnings, infos)


def grade_assessment(record: Mapping[str, object]) -> FileGrade:
    """Map one persisted assessment record to its deterministic presentation grade."""
    scorecard = _required_mapping(record, "scorecard")
    retrievability = _required_mapping(scorecard, "retrievability")
    status = retrievability.get("status")
    if status == "FINDINGS":
        findings = _findings(retrievability)
        detail = str(findings[0].get("message", "")) if findings else ""
        reason = "the identified download attempt did not produce a verified file"
        if detail:
            reason = f"{reason}: {detail}"
        return FileGrade("F", reason)
    if status == "NOT_ASSESSED":
        note = str(retrievability.get("note") or "retrievability was not assessed")
        return FileGrade(NOT_GRADED, note)
    if status != "OBSERVED":
        raise CohortError(f"unrecognized retrievability status {status!r}")
    inspection = record.get("inspection")
    if not isinstance(inspection, Mapping):
        # OBSERVED without inspection evidence would contradict the composition rules; refuse
        # to guess rather than mint a grade from a record this module cannot explain.
        raise CohortError("retrievability is OBSERVED but no inspection evidence is present")
    if inspection.get("scan_completed") is not True:
        return FileGrade(
            "F",
            "the standard_charge_information array could not be streamed to completion; "
            "content that could not be read is treated as failed, not passed",
        )
    return _grade_local_dimensions(scorecard)


def _validated_manifest(manifest: Mapping[str, object]) -> Mapping[str, object]:
    collection = _required_mapping(manifest, "collection")
    if collection.get("operator_controlled_single_run") is not True:
        raise CohortError(
            "comparison refused: the manifest must attest that every row came from one "
            "operator-controlled collection run (docs/PHASE-3-FINDINGS.md); matching policy "
            "fingerprints alone cannot establish that"
        )
    _required_str(manifest, "cohort_id")
    _required_str(collection, "utc_date")
    return manifest


def _subject_fields(record: Mapping[str, object]) -> tuple[str, str, str, str, str | None]:
    subject = _required_mapping(record, "subject")
    publisher = _required_mapping(subject, "publisher")
    publisher_id = _required_str(publisher, "identifier")
    name = publisher.get("name")
    location_id = _required_str(subject, "location_id")
    url = _required_str(subject, "requested_url")
    url_sha256 = _required_str(subject, "requested_url_sha256")
    return publisher_id, location_id, url, url_sha256, name if isinstance(name, str) else None


def _sanitized_ingest(raw: Mapping[str, object]) -> dict[str, object]:
    counts = raw.get("counts")
    return {
        "run_id": _required_str(raw, "run_id"),
        "source_file_id": _required_str(raw, "source_file_id"),
        "publisher_id": _required_str(raw, "publisher_id"),
        "status": _required_str(raw, "status"),
        "reused": bool(raw.get("reused")),
        "counts": dict(cast(Mapping[str, object], counts)) if isinstance(counts, Mapping) else None,
    }


def _ingest_by_content(
    ingest_results: Sequence[Mapping[str, object]],
    content_digests: frozenset[str],
) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for raw in ingest_results:
        sanitized = _sanitized_ingest(raw)
        digest = cast(str, sanitized["source_file_id"])
        if digest not in content_digests:
            raise CohortError(
                f"ingest evidence for content {digest!r} does not match any cohort assessment"
            )
        if digest in evidence:
            raise CohortError(f"duplicate ingest evidence for content {digest!r}")
        evidence[digest] = sanitized
    return evidence


def _inspection_counts(inspection: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "item_count",
        "code_count",
        "charge_group_count",
        "payer_rate_count",
        "dollar_rate_count",
        "percentage_rate_count",
        "algorithm_rate_count",
        "problem_count",
    )
    return {key: inspection.get(key) for key in keys}


def _dimension_view(scorecard: Mapping[str, object]) -> dict[str, object]:
    view: dict[str, object] = {}
    for name in ("retrievability", *LOCAL_DIMENSIONS):
        dimension = _required_mapping(scorecard, name)
        view[name] = {
            "status": dimension.get("status"),
            "note": dimension.get("note"),
            "findings": [dict(finding) for finding in _findings(dimension)],
        }
    return view


def _file_row(
    record: Mapping[str, object],
    ingest_evidence: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    publisher_id, location_id, url, url_sha256, name = _subject_fields(record)
    retrieval = _required_mapping(record, "retrieval")
    scorecard = _required_mapping(record, "scorecard")
    grade = grade_assessment(record)
    inspection = record.get("inspection")
    inspection_map = (
        cast(Mapping[str, object], inspection) if isinstance(inspection, Mapping) else None
    )
    content_sha256 = retrieval.get("content_sha256")
    lakehouse = (
        ingest_evidence.get(content_sha256) if isinstance(content_sha256, str) else None
    )
    return {
        "slug": f"{publisher_id}/{location_id}",
        "publisher_id": publisher_id,
        "publisher_name": name,
        "location_id": location_id,
        "requested_url": url,
        "requested_url_sha256": url_sha256,
        "observed_at": record.get("observed_at"),
        "as_of": record.get("as_of"),
        "content_sha256": content_sha256,
        "size_bytes": retrieval.get("size_bytes"),
        "grade": grade.to_dict(),
        "dimensions": _dimension_view(scorecard),
        "coverage": dict(_required_mapping(record, "coverage")),
        "counts": _inspection_counts(inspection_map) if inspection_map is not None else None,
        "last_updated_on": inspection_map.get("period") if inspection_map is not None else None,
        "template_version": inspection_map.get("version") if inspection_map is not None else None,
        "lakehouse": lakehouse,
        "assessment_id": record.get("assessment_id"),
        "assessment_body_sha256": record.get("assessment_body_sha256"),
    }


def _finding_matrix(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    by_code: dict[str, dict[str, object]] = {}
    for row in rows:
        dimensions = cast(Mapping[str, object], row["dimensions"])
        for dimension in dimensions.values():
            for finding in cast(
                Sequence[Mapping[str, object]],
                cast(Mapping[str, object], dimension)["findings"],
            ):
                code = str(finding.get("code"))
                entry = by_code.setdefault(
                    code,
                    {
                        "code": code,
                        "dimension": finding.get("dimension"),
                        "severity": finding.get("severity"),
                        "citations": finding.get("citations"),
                        "occurrence_total": 0,
                        "files": [],
                    },
                )
                occurrences = finding.get("occurrences")
                entry["occurrence_total"] = cast(int, entry["occurrence_total"]) + (
                    occurrences if isinstance(occurrences, int) else 1
                )
                cast(list[str], entry["files"]).append(cast(str, row["slug"]))
    return [by_code[code] for code in sorted(by_code)]


def _coverage_flag(row: Mapping[str, object], flag: str) -> bool:
    return bool(cast(Mapping[str, object], row["coverage"]).get(flag))


def _summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    grades = [cast(Mapping[str, object], row["grade"])["grade"] for row in rows]
    return {
        "targeted": len(rows),
        "network_attempted": sum(_coverage_flag(row, "network_attempted") for row in rows),
        "verified_body_available": sum(
            _coverage_flag(row, "verified_body_available") for row in rows
        ),
        "inspection_scan_completed": sum(
            _coverage_flag(row, "inspection_scan_completed") for row in rows
        ),
        "graded": sum(grade != NOT_GRADED for grade in grades),
        "not_graded": sum(grade == NOT_GRADED for grade in grades),
        "grade_distribution": {letter: grades.count(letter) for letter in "ABCDF"},
    }


def _cohort_header(
    records: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> dict[str, object]:
    first = records[0]
    scope = dict(_required_mapping(first, "comparison_scope"))
    return {
        "cohort_id": manifest["cohort_id"],
        "as_of": first.get("as_of"),
        "comparison_scope": scope,
        "inspection_fingerprint": first.get("inspection_fingerprint"),
        "grade_policy": {
            "version": GRADE_POLICY_VERSION,
            "fingerprint": GRADE_POLICY_FINGERPRINT,
            "rules": _GRADE_RULES,
        },
    }


def build_comparison(
    records: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
    *,
    ingest_results: Sequence[Mapping[str, object]] = (),
    generated_at: str,
) -> dict[str, object]:
    """Build the deterministic published comparison for one attested collection run.

    ``records`` must already be integrity-verified persisted assessment rows (the
    ``AssessmentRegistry`` reader enforces that); this function enforces the comparison
    boundary, the single-run attestation, one row per subject, and per-cohort ingest evidence.
    """
    rows = tuple(records)
    if len(rows) < 2:
        raise CohortError("a published comparison needs at least two assessment rows")
    manifest = _validated_manifest(manifest)
    require_comparable(*rows)
    collection = _required_mapping(manifest, "collection")
    expected_date = collection["utc_date"]
    seen: set[tuple[str, str, str]] = set()
    digests: set[str] = set()
    for record in rows:
        if record.get("as_of") != expected_date:
            raise CohortError(
                "assessment as_of does not match the manifest collection utc_date"
            )
        publisher_id, location_id, _, url_sha256, _ = _subject_fields(record)
        key = (publisher_id, location_id, url_sha256)
        if key in seen:
            raise CohortError(
                f"duplicate assessment subject {publisher_id}/{location_id}; the cohort "
                "snapshot must carry exactly one row per subject"
            )
        seen.add(key)
        content = _required_mapping(record, "retrieval").get("content_sha256")
        if isinstance(content, str):
            digests.add(content)
    evidence = _ingest_by_content(ingest_results, frozenset(digests))
    file_rows = sorted(
        (_file_row(record, evidence) for record in rows),
        key=lambda row: cast(str, row["slug"]),
    )
    return {
        "comparison_version": 1,
        "generated_at": generated_at,
        "policy": manifest.get("policy"),
        "cohort": _cohort_header(rows, manifest),
        "collection": dict(collection),
        "discovery": manifest.get("discovery"),
        "exclusions": manifest.get("exclusions", []),
        "summary": _summary(file_rows),
        "files": file_rows,
        "finding_matrix": _finding_matrix(file_rows),
    }
