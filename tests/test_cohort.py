"""Cohort comparison and presentation-grade policy tests.

Every grade band, every fail-closed path, and every refusal in ``mrf_honest.cohort`` is pinned
here with synthetic assessments composed through the real phase-3 composition path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from mrf_honest.cohort import (
    GRADE_POLICY_FINGERPRINT,
    NOT_GRADED,
    CohortError,
    FileGrade,
    build_comparison,
    grade_assessment,
)
from mrf_honest.fetch import FetchOutcome, FetchPolicy, FetchStatus
from mrf_honest.inspect import inspect_hospital_file
from mrf_honest.scorecard import (
    AssessmentSubject,
    PublisherType,
    RetrievalPolicyEvidence,
    URLProvenance,
    compose_file_assessment,
)
from mrf_honest.types import PublisherRef

OBSERVED_AT = "2026-05-01T12:00:00Z"
AS_OF = date(2026, 5, 1)
GENERATED_AT = "2026-05-01T13:00:00Z"


def _policy() -> FetchPolicy:
    return FetchPolicy(contact="maintainer@example.test", retries=0)


def _subject(
    publisher_id: str = "example-health",
    location_id: str = "main",
    *,
    provenance: URLProvenance = URLProvenance.CMS_HPT,
) -> AssessmentSubject:
    url = f"https://files.example.test/{publisher_id}/{location_id}/standardcharges.json"
    return AssessmentSubject(
        publisher=PublisherRef(identifier=publisher_id, name="Example Hospital", source_url=url),
        publisher_type=PublisherType.HOSPITAL,
        location_id=location_id,
        requested_url=url,
        url_provenance=provenance,
    )


def _document() -> dict[str, object]:
    return {
        "hospital_name": "Example Hospital",
        "last_updated_on": "2026-04-01",
        "version": "3.0.0",
        "location_name": ["Example Main"],
        "hospital_address": ["1 Main St, Testville, CA 90000"],
        "type_2_npi": ["1234567890"],
        "license_information": {"state": "CA", "license_number": "A1"},
        "attestation": {
            "attestation": "CMS-required statement represented in the fixture",
            "confirm_attestation": True,
            "attester_name": "A. Executive",
        },
        "standard_charge_information": [
            {
                "description": "Example service",
                "code_information": [{"code": "12345", "type": "CPT"}],
                "standard_charges": [
                    {
                        "gross_charge": 120,
                        "discounted_cash": 90,
                        "minimum": 70,
                        "maximum": 100,
                        "setting": "outpatient",
                        "payers_information": [
                            {
                                "payer_name": "Alpha",
                                "plan_name": "PPO",
                                "methodology": "fee schedule",
                                "standard_charge_dollar": 80,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _success_record(
    tmp_path: Path,
    document: dict[str, object] | None = None,
    *,
    raw: bytes | None = None,
    subject: AssessmentSubject | None = None,
) -> dict[str, object]:
    subject = subject or _subject()
    body = raw if raw is not None else json.dumps(document or _document()).encode()
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{subject.publisher.identifier}-{subject.location_id}.json"
    path.write_bytes(body)
    inspection = inspect_hospital_file(path, subject.publisher, as_of=AS_OF)
    fetch = FetchOutcome(
        url=subject.requested_url,
        status=FetchStatus.FETCHED,
        attempted_at=OBSERVED_AT,
        attempts=1,
        path=path,
        content_sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        wire_size_bytes=len(body),
        http_status=200,
        final_url=subject.requested_url,
    )
    assessment = compose_file_assessment(
        subject,
        fetch,
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
        inspection=inspection,
    )
    return assessment.to_dict()


def _failure_record(
    status: FetchStatus,
    *,
    http_status: int | None = None,
    subject: AssessmentSubject | None = None,
) -> dict[str, object]:
    subject = subject or _subject()
    fetch = FetchOutcome(
        url=subject.requested_url,
        status=status,
        attempted_at=OBSERVED_AT,
        attempts=0 if status is FetchStatus.INVALID_URL else 1,
        http_status=http_status,
        final_url=subject.requested_url,
        error=f"fixture {status.value}",
    )
    assessment = compose_file_assessment(
        subject,
        fetch,
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    return assessment.to_dict()


def _manifest(utc_date: str = "2026-05-01") -> dict[str, object]:
    return {
        "policy": "test cohorts attest their collection run",
        "cohort_id": "test-cohort",
        "collection": {"operator_controlled_single_run": True, "utc_date": utc_date},
        "exclusions": [{"id": "held-out", "reason": "out of profile"}],
    }


# --- grade bands ---------------------------------------------------------------------------


def test_clean_file_grades_a(tmp_path: Path) -> None:
    grade = grade_assessment(_success_record(tmp_path))
    assert grade.grade == "A"
    assert grade.error_dimensions == ()
    assert grade.warning_findings == 0


def test_info_findings_never_lower_a_grade(tmp_path: Path) -> None:
    body = b"\xef\xbb\xbf" + json.dumps(_document()).encode()
    grade = grade_assessment(_success_record(tmp_path, raw=body))
    assert grade.grade == "A"
    assert grade.info_findings >= 1


def test_warning_only_file_grades_b(tmp_path: Path) -> None:
    document = _document()
    document["last_updated_on"] = "2024-01-01"  # more than a year before AS_OF
    grade = grade_assessment(_success_record(tmp_path, document))
    assert grade.grade == "B"
    assert grade.warning_findings >= 1
    assert "warning" in grade.reason


def test_errors_in_one_dimension_grade_c(tmp_path: Path) -> None:
    document = _document()
    charges = cast(list[dict[str, object]], document["standard_charge_information"])
    del charges[0]["description"]  # completeness ERROR only
    grade = grade_assessment(_success_record(tmp_path, document))
    assert grade.grade == "C"
    assert grade.error_dimensions == ("completeness",)


def test_errors_in_two_dimensions_grade_d(tmp_path: Path) -> None:
    document = _document()
    charges = cast(list[dict[str, object]], document["standard_charge_information"])
    del charges[0]["description"]
    groups = cast(list[dict[str, object]], charges[0]["standard_charges"])
    groups[0]["setting"] = "clinic"  # conformance ERROR: outside the accepted set
    grade = grade_assessment(_success_record(tmp_path, document))
    assert grade.grade == "D"
    assert set(grade.error_dimensions) == {"conformance", "completeness"}


def test_errors_in_three_dimensions_grade_f(tmp_path: Path) -> None:
    document = _document()
    del document["last_updated_on"]  # conformance ERROR + freshness ERROR
    charges = cast(list[dict[str, object]], document["standard_charge_information"])
    del charges[0]["description"]  # completeness ERROR
    grade = grade_assessment(_success_record(tmp_path, document))
    assert grade.grade == "F"
    assert len(grade.error_dimensions) >= 3


def test_incomplete_stream_is_f_not_a_pass(tmp_path: Path) -> None:
    truncated = b'{"standard_charge_information": [ {"description": "x"'
    grade = grade_assessment(_success_record(tmp_path, raw=truncated))
    assert grade.grade == "F"
    assert "could not be streamed to completion" in grade.reason


def test_failed_download_is_a_stated_f() -> None:
    grade = grade_assessment(_failure_record(FetchStatus.HTTP_ERROR, http_status=500))
    assert grade.grade == "F"
    assert "did not produce a verified file" in grade.reason


def test_project_size_ceiling_is_not_graded_not_f() -> None:
    grade = grade_assessment(_failure_record(FetchStatus.TOO_LARGE))
    assert grade.grade == NOT_GRADED
    assert "ceiling" in grade.reason


def test_invalid_url_is_not_graded() -> None:
    record = _failure_record(FetchStatus.INVALID_URL)
    grade = grade_assessment(record)
    assert grade.grade == NOT_GRADED


def test_unrecognized_retrievability_status_is_refused(tmp_path: Path) -> None:
    record = _success_record(tmp_path)
    scorecard = cast(dict[str, object], record["scorecard"])
    retrievability = dict(cast(dict[str, object], scorecard["retrievability"]))
    retrievability["status"] = "MYSTERY"
    scorecard["retrievability"] = retrievability
    with pytest.raises(CohortError, match="unrecognized retrievability status"):
        grade_assessment(record)


def test_observed_without_inspection_evidence_is_refused(tmp_path: Path) -> None:
    record = _success_record(tmp_path)
    record["inspection"] = None
    with pytest.raises(CohortError, match="no inspection evidence"):
        grade_assessment(record)


def test_missing_scorecard_field_is_refused() -> None:
    with pytest.raises(CohortError, match="scorecard"):
        grade_assessment({})


def test_grade_serialization_carries_the_policy_identity() -> None:
    payload = FileGrade("A", "reason").to_dict()
    assert payload["policy_fingerprint"] == GRADE_POLICY_FINGERPRINT
    assert len(GRADE_POLICY_FINGERPRINT) == 64


# --- cohort building -----------------------------------------------------------------------


def _two_records(tmp_path: Path) -> list[dict[str, object]]:
    stale = _document()
    stale["last_updated_on"] = "2024-01-01"
    return [
        _success_record(tmp_path, subject=_subject("alpha-health", "main")),
        _success_record(tmp_path, stale, subject=_subject("beta-health", "north")),
    ]


def test_build_comparison_grades_sorts_and_counts(tmp_path: Path) -> None:
    records = _two_records(tmp_path)
    content = cast(dict[str, object], records[0]["retrieval"])["content_sha256"]
    ingest = {
        "run_id": "r1",
        "source_file_id": content,
        "publisher_id": "alpha-health",
        "status": "success",
        "reused": False,
        "counts": {"items": 1},
        "database_path": "/local/warehouse/catalog.duckdb",
    }
    comparison = build_comparison(
        records,
        _manifest(),
        ingest_results=[ingest],
        generated_at=GENERATED_AT,
    )
    files = cast(list[dict[str, object]], comparison["files"])
    assert [row["slug"] for row in files] == ["alpha-health/main", "beta-health/north"]
    summary = cast(dict[str, object], comparison["summary"])
    assert summary["targeted"] == 2
    assert summary["graded"] == 2
    distribution = cast(dict[str, int], summary["grade_distribution"])
    assert distribution["A"] == 1
    assert distribution["B"] == 1
    lakehouse = cast(dict[str, object], files[0]["lakehouse"])
    assert lakehouse["run_id"] == "r1"
    assert "database_path" not in lakehouse
    assert files[1]["lakehouse"] is None
    matrix = cast(list[dict[str, object]], comparison["finding_matrix"])
    overdue = [entry for entry in matrix if entry["code"] == "FRESHNESS_ANNUAL_UPDATE_OVERDUE"]
    assert overdue and overdue[0]["files"] == ["beta-health/north"]
    header = cast(dict[str, object], comparison["cohort"])
    grade_policy = cast(dict[str, object], header["grade_policy"])
    assert grade_policy["fingerprint"] == GRADE_POLICY_FINGERPRINT
    assert comparison["generated_at"] == GENERATED_AT


def test_failed_target_stays_a_row_in_the_comparison(tmp_path: Path) -> None:
    records = [
        _success_record(tmp_path, subject=_subject("alpha-health", "main")),
        _failure_record(
            FetchStatus.HTTP_ERROR,
            http_status=403,
            subject=_subject("gamma-health", "main"),
        ),
    ]
    comparison = build_comparison(records, _manifest(), generated_at=GENERATED_AT)
    files = cast(list[dict[str, object]], comparison["files"])
    graded = {
        cast(str, row["slug"]): cast(dict[str, object], row["grade"])["grade"] for row in files
    }
    assert graded["gamma-health/main"] == "F"
    summary = cast(dict[str, object], comparison["summary"])
    assert summary["verified_body_available"] == 1
    assert summary["targeted"] == 2


def test_comparison_requires_the_single_run_attestation(tmp_path: Path) -> None:
    manifest = _manifest()
    collection = cast(dict[str, object], manifest["collection"])
    collection["operator_controlled_single_run"] = False
    with pytest.raises(CohortError, match="operator-controlled collection run"):
        build_comparison(_two_records(tmp_path), manifest, generated_at=GENERATED_AT)


def test_comparison_requires_at_least_two_rows(tmp_path: Path) -> None:
    records = [_success_record(tmp_path)]
    with pytest.raises(CohortError, match="at least two"):
        build_comparison(records, _manifest(), generated_at=GENERATED_AT)


def test_comparison_refuses_duplicate_subjects(tmp_path: Path) -> None:
    records = [_success_record(tmp_path), _success_record(tmp_path)]
    with pytest.raises(CohortError, match="duplicate assessment subject"):
        build_comparison(records, _manifest(), generated_at=GENERATED_AT)


def test_comparison_refuses_as_of_outside_the_attested_run(tmp_path: Path) -> None:
    with pytest.raises(CohortError, match="utc_date"):
        build_comparison(_two_records(tmp_path), _manifest("2026-05-02"), generated_at=GENERATED_AT)


def test_comparison_refuses_foreign_or_duplicate_ingest_evidence(tmp_path: Path) -> None:
    records = _two_records(tmp_path)
    foreign = {
        "run_id": "r9",
        "source_file_id": "0" * 64,
        "publisher_id": "alpha-health",
        "status": "success",
    }
    with pytest.raises(CohortError, match="does not match any cohort assessment"):
        build_comparison(records, _manifest(), ingest_results=[foreign], generated_at=GENERATED_AT)
    content = cast(dict[str, object], records[0]["retrieval"])["content_sha256"]
    duplicate = {
        "run_id": "r1",
        "source_file_id": content,
        "publisher_id": "alpha-health",
        "status": "success",
    }
    with pytest.raises(CohortError, match="duplicate ingest evidence"):
        build_comparison(
            records,
            _manifest(),
            ingest_results=[duplicate, duplicate],
            generated_at=GENERATED_AT,
        )


def test_comparison_propagates_the_scope_boundary(tmp_path: Path) -> None:
    records = [
        _success_record(tmp_path, subject=_subject("alpha-health", "main")),
        _success_record(
            tmp_path,
            subject=_subject("beta-health", "north", provenance=URLProvenance.OPERATOR),
        ),
    ]
    with pytest.raises(ValueError, match="not directly comparable"):
        build_comparison(records, _manifest(), generated_at=GENERATED_AT)


def test_manifest_requires_cohort_id(tmp_path: Path) -> None:
    manifest = _manifest()
    del manifest["cohort_id"]
    with pytest.raises(CohortError, match="cohort_id"):
        build_comparison(_two_records(tmp_path), manifest, generated_at=GENERATED_AT)


# --- CLI -----------------------------------------------------------------------------------


def _write_cohort_inputs(tmp_path: Path) -> tuple[Path, Path]:
    records = _two_records(tmp_path / "bodies")
    assessments = tmp_path / "assessments.jsonl"
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    assessments.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    return assessments, manifest


def test_cli_compare_emits_canonical_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mrf_honest.cli import main

    assessments, manifest = _write_cohort_inputs(tmp_path)
    status = main(
        [
            "compare",
            "--assessments",
            str(assessments),
            "--manifest",
            str(manifest),
            "--generated-at",
            GENERATED_AT,
        ]
    )
    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["generated_at"] == GENERATED_AT
    assert payload["summary"]["targeted"] == 2


def test_cli_compare_human_format_names_each_grade(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mrf_honest.cli import main

    assessments, manifest = _write_cohort_inputs(tmp_path)
    status = main(
        [
            "compare",
            "--assessments",
            str(assessments),
            "--manifest",
            str(manifest),
            "--format",
            "human",
        ]
    )
    assert status == 0
    out = capsys.readouterr().out
    assert "alpha-health/main: A" in out
    assert "beta-health/north: B" in out


def test_cli_compare_reports_refusals_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mrf_honest.cli import main

    assessments, manifest = _write_cohort_inputs(tmp_path)
    unattested = json.loads(manifest.read_text(encoding="utf-8"))
    unattested["collection"]["operator_controlled_single_run"] = False
    manifest.write_text(json.dumps(unattested), encoding="utf-8")
    status = main(["compare", "--assessments", str(assessments), "--manifest", str(manifest)])
    assert status == 1
    assert "operator-controlled collection run" in capsys.readouterr().err
