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
- an incomplete charge-array stream is an ``F``: what could not be read is failed, not passed.
  A fetch that succeeded is not evidence that the document arrived, so the stated reason names
  the media type the server declared when one was recorded: a URL that answered with a web page
  and a hospital that published a malformed file are both ``F``, and must not read alike. The
  declaration is evidence about a failure that already happened and never causes one, so it is
  not a grading input and does not appear in the rule table below;
- a local dimension without evidence counts against the grade exactly like a dimension with
  structural errors; absence of a check is stated, never implied as a pass.

The same rule governs the warehouse evidence a row carries. A file the contracted warehouse
refused is not a file nobody tried to load, and the two must not render identically: the
refusal is recorded with the reason the warehouse gave, because a project limit presented as a
bare absence is the conflation ``docs/how-we-compare.md`` forbids. Warehouse evidence never
touches the grade in either direction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from mrf_honest.scorecard import require_comparable

GRADE_POLICY_VERSION = "cms-hospital-json-v3-file-grade-v1"

#: The CSV profile's presentation-grade policy. The rule table is deliberately identical to the
#: JSON profile's: the letter describes dimensions and severities, not a file format. What must
#: differ is the name, because a published grade names the policy it was minted under, and a CSV
#: page claiming a ``json-v3`` policy would be a small lie in the shop window.
CSV_GRADE_POLICY_VERSION = "cms-hospital-csv-v3-file-grade-v1"

#: Schema version of the published comparison document, bumped whenever the shape of the
#: document changes. Version 2 added the refused branch of ``files[].lakehouse``: warehouse
#: evidence became a discriminated record instead of "an object or ``null``", so a consumer
#: reading version 1 cannot assume a present object means a completed load.
#:
#: This is deliberately *not* ``GRADE_POLICY_VERSION``. Warehouse evidence is not a grading
#: input, the rule table below is byte-identical, and every grade in a re-derived cohort is
#: unchanged; moving the grade fingerprint would announce a regrade that did not happen and
#: would make old and new cohorts falsely incomparable (ADR 0005).
COMPARISON_VERSION = 2

#: ``status`` of an ingest attempt the warehouse declined for scope reasons.
INGEST_REFUSED = "refused"

#: The four dimensions the local inspector can evidence; retrievability is handled separately.
LOCAL_DIMENSIONS = ("conformance", "completeness", "interpretability", "freshness")

NOT_GRADED = "NOT_GRADED"

#: Media types whose whole purpose is to be rendered for a person to look at. When a URL that
#: was asked for a machine-readable file answers with one of these *and* the document did not
#: parse, the response was a web page rather than the file — a closed list of server
#: declarations, not a judgement about what the bytes look like.
#:
#: This list is consulted only after a document has already failed to stream, and never decides
#: a grade. A conforming MRF served as ``text/html`` is a conforming MRF: one of the six files
#: in the 2026-08-14 cohort would be graded on a header rather than on its contents if that
#: were not true, and failing a publisher for a header is a claim this project cannot support.
_WEB_PAGE_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

_UNREAD_CONTENT_RULE = "content that could not be read is treated as failed, not passed"


def _grade_rules(version: str) -> dict[str, object]:
    """The complete deterministic policy, hashed so a rule change creates a new fingerprint
    instead of silently regrading old cohorts under new semantics."""
    return {
        "version": version,
        "retrievability_findings": "F",
        "retrievability_not_assessed": NOT_GRADED,
        "scan_incomplete": "F",
        "error_dimension_counts": {"0": "A or B", "1": "C", "2": "D", "3+": "F"},
        "warnings_split_a_b": True,
        "info_findings_never_lower_a_grade": True,
        "not_assessed_local_dimension_counts_as_error_dimension": True,
    }


def _rules_fingerprint(rules: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(rules, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_GRADE_RULES: dict[str, object] = _grade_rules(GRADE_POLICY_VERSION)

GRADE_POLICY_FINGERPRINT = _rules_fingerprint(_GRADE_RULES)


@dataclass(frozen=True)
class GradePolicy:
    """One profile's presentation-grade policy: its name, rule table, and fingerprint."""

    version: str
    rules: Mapping[str, object]
    fingerprint: str
    unread_noun: str


_JSON_GRADE_POLICY = GradePolicy(
    version=GRADE_POLICY_VERSION,
    rules=_GRADE_RULES,
    fingerprint=GRADE_POLICY_FINGERPRINT,
    unread_noun="the standard_charge_information array",
)
_CSV_GRADE_RULES = _grade_rules(CSV_GRADE_POLICY_VERSION)
_CSV_GRADE_POLICY = GradePolicy(
    version=CSV_GRADE_POLICY_VERSION,
    rules=_CSV_GRADE_RULES,
    fingerprint=_rules_fingerprint(_CSV_GRADE_RULES),
    unread_noun="the standard-charge table",
)

#: Grade policy by the assessment profile recorded in each row's comparison scope. An absent or
#: unknown profile falls back to the JSON policy, which is exactly what every record written
#: before profiles existed was graded under.
_GRADE_POLICIES: dict[str, GradePolicy] = {
    "cms-hospital-json-v3": _JSON_GRADE_POLICY,
    "cms-hospital-csv-v3": _CSV_GRADE_POLICY,
}


def _policy_for(record: Mapping[str, object]) -> GradePolicy:
    scope = record.get("comparison_scope")
    profile = scope.get("profile") if isinstance(scope, Mapping) else None
    return _GRADE_POLICIES.get(str(profile), _JSON_GRADE_POLICY)


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
    policy_version: str = GRADE_POLICY_VERSION
    policy_fingerprint: str = GRADE_POLICY_FINGERPRINT

    def to_dict(self) -> dict[str, object]:
        return {
            "grade": self.grade,
            "reason": self.reason,
            "error_dimensions": list(self.error_dimensions),
            "warning_findings": self.warning_findings,
            "info_findings": self.info_findings,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
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


def _grade_local_dimensions(scorecard: Mapping[str, object], policy: GradePolicy) -> FileGrade:
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
            return _graded("A", reason, failed, warnings, infos, policy)
        reason = f"no structural errors; {warnings} warning finding(s) were recorded"
        return _graded("B", reason, failed, warnings, infos, policy)
    named = ", ".join(failed)
    reason = (
        f"errors or missing evidence in {len(failed)} of {len(LOCAL_DIMENSIONS)} "
        f"local dimensions: {named}"
    )
    if len(failed) == 1:
        return _graded("C", reason, failed, warnings, infos, policy)
    if len(failed) == 2:
        return _graded("D", reason, failed, warnings, infos, policy)
    return _graded("F", reason, failed, warnings, infos, policy)


def _graded(
    grade: str,
    reason: str,
    failed: tuple[str, ...],
    warnings: int,
    infos: int,
    policy: GradePolicy,
) -> FileGrade:
    return FileGrade(grade, reason, failed, warnings, infos, policy.version, policy.fingerprint)


def _media_type(declared: object) -> str | None:
    """The bare media type from a ``Content-Type`` declaration, or ``None``.

    RFC 9110 § 8.3: everything from the first ``;`` is a parameter, and the type is
    case-insensitive. This only tidies the server's own words; it does not inspect the body.
    """
    if not isinstance(declared, str):
        return None
    media_type = declared.split(";", 1)[0].strip().lower()
    return media_type or None


def _unstreamable_reason(record: Mapping[str, object], noun: str) -> str:
    """Explain a document that could not be streamed, using what the server said it was sending.

    Three different events used to share one sentence: a hospital's malformed file, an HTTP 200
    that returned a web page, and (until the truncation fix) a download that stopped early. The
    grade is ``F`` in every case and the grade rules are untouched — what changes is that the
    published sentence now states the evidence rather than only the symptom.

    Where no declaration was recorded, the historical sentence is reproduced exactly. An
    unrecorded header is not the same fact as a server that declared nothing, and neither is
    evidence that the wrong document arrived, so neither may add a claim to a named hospital's
    page.
    """
    retrieval = record.get("retrieval")
    declared = retrieval.get("content_type") if isinstance(retrieval, Mapping) else None
    media_type = _media_type(declared)
    if media_type is None:
        return f"{noun} could not be streamed to completion; {_UNREAD_CONTENT_RULE}"
    if media_type in _WEB_PAGE_MEDIA_TYPES:
        return (
            f"the server declared Content-Type {media_type!r} — a web page, not the requested "
            f"file — and {noun} could not be streamed to "
            f"completion; {_UNREAD_CONTENT_RULE}"
        )
    return (
        f"{noun} could not be streamed to completion; the server "
        f"declared Content-Type {media_type!r}; {_UNREAD_CONTENT_RULE}"
    )


def grade_assessment(record: Mapping[str, object]) -> FileGrade:
    """Map one persisted assessment record to its deterministic presentation grade."""
    policy = _policy_for(record)
    scorecard = _required_mapping(record, "scorecard")
    retrievability = _required_mapping(scorecard, "retrievability")
    status = retrievability.get("status")
    if status == "FINDINGS":
        findings = _findings(retrievability)
        detail = str(findings[0].get("message", "")) if findings else ""
        reason = "the identified download attempt did not produce a verified file"
        if detail:
            reason = f"{reason}: {detail}"
        return _graded("F", reason, (), 0, 0, policy)
    if status == "NOT_ASSESSED":
        note = str(retrievability.get("note") or "retrievability was not assessed")
        return _graded(NOT_GRADED, note, (), 0, 0, policy)
    if status != "OBSERVED":
        raise CohortError(f"unrecognized retrievability status {status!r}")
    inspection = record.get("inspection")
    if not isinstance(inspection, Mapping):
        # OBSERVED without inspection evidence would contradict the composition rules; refuse
        # to guess rather than mint a grade from a record this module cannot explain.
        raise CohortError("retrievability is OBSERVED but no inspection evidence is present")
    if inspection.get("scan_completed") is not True:
        return _graded("F", _unstreamable_reason(record, policy.unread_noun), (), 0, 0, policy)
    # Reached only when the document streamed to completion, which is why the declared media
    # type is not read here: a file that parses has answered the question the header could only
    # have hinted at.
    return _grade_local_dimensions(scorecard, policy)


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
    status = _required_str(raw, "status")
    if status == INGEST_REFUSED:
        # Every field is required. A refusal record without its reason would publish the same
        # unexplained absence as no record at all, which is the whole defect this branch fixes,
        # so an incomplete one is refused rather than accepted and half-rendered.
        return {
            "status": status,
            "source_file_id": _required_str(raw, "source_file_id"),
            "publisher_id": _required_str(raw, "publisher_id"),
            "reason": _required_str(raw, "reason"),
            "implemented_scope": _required_str(raw, "implemented_scope"),
            "observed_scope": _required_str(raw, "observed_scope"),
        }
    counts = raw.get("counts")
    return {
        "run_id": _required_str(raw, "run_id"),
        "source_file_id": _required_str(raw, "source_file_id"),
        "publisher_id": _required_str(raw, "publisher_id"),
        "status": status,
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
    # The union of both profiles' count fields, filtered to what this inspection carries:
    # a JSON inspection has no row_count and a CSV inspection has no charge_group_count, and
    # publishing either as null would read as a measured absence.
    keys = (
        "item_count",
        "code_count",
        "row_count",
        "charge_group_count",
        "payer_plan_combination_count",
        "payer_rate_count",
        "dollar_rate_count",
        "percentage_rate_count",
        "algorithm_rate_count",
        "problem_count",
    )
    return {key: inspection[key] for key in keys if key in inspection}


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
    lakehouse = ingest_evidence.get(content_sha256) if isinstance(content_sha256, str) else None
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
    policy = _policy_for(first)
    return {
        "cohort_id": manifest["cohort_id"],
        "as_of": first.get("as_of"),
        "comparison_scope": scope,
        "inspection_fingerprint": first.get("inspection_fingerprint"),
        "grade_policy": {
            "version": policy.version,
            "fingerprint": policy.fingerprint,
            "rules": dict(policy.rules),
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
            raise CohortError("assessment as_of does not match the manifest collection utc_date")
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
        "comparison_version": COMPARISON_VERSION,
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
