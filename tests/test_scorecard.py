from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.request import Request

import pytest
import robots_fixtures

from mrf_honest.cli import _emit_assessment_human
from mrf_honest.fetch import FetchOutcome, FetchPolicy, FetchStatus, ResponseLike
from mrf_honest.inspect import DimensionResult, FileInspection, inspect_hospital_file
from mrf_honest.scorecard import (
    RETRIEVAL_FINDING_CATALOG,
    AssessmentRegistry,
    AssessmentRegistryError,
    AssessmentSubject,
    FileAssessment,
    PublisherType,
    RetrievalPolicyEvidence,
    URLProvenance,
    assess_hospital_url,
    compose_file_assessment,
    require_comparable,
)
from mrf_honest.types import PublisherRef

URL = "https://hospital.test/123456789_example_standardcharges.json"
OBSERVED_AT = "2026-08-09T12:30:00Z"
NOW = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)


class Response:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = URL,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.url = url
        self.position = 0
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self.body) - self.position
        chunk = self.body[self.position : self.position + amount]
        self.position += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        self.closed = True


class OneResponse:
    def __init__(self, response: ResponseLike) -> None:
        self.response = response
        self.request: Request | None = None

    def __call__(self, request: Request, *, timeout: float) -> ResponseLike:
        self.request = request
        return self.response


def _clock() -> datetime:
    return NOW


def _policy(
    *,
    contact: str = "maintainer@example.test",
    max_bytes: int = 1 << 20,
) -> FetchPolicy:
    return FetchPolicy(
        contact=contact,
        retries=0,
        max_bytes=max_bytes,
        chunk_size=64,
    )


def _subject(
    *,
    url: str = URL,
    provenance: URLProvenance = URLProvenance.CMS_HPT,
    publisher_type: PublisherType = PublisherType.HOSPITAL,
) -> AssessmentSubject:
    publisher = PublisherRef(
        identifier="example-health",
        name="Example Hospital",
        source_url=url,
    )
    return AssessmentSubject(
        publisher=publisher,
        publisher_type=publisher_type,
        location_id="example-main",
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


def _body() -> bytes:
    return json.dumps(_document(), separators=(",", ":")).encode()


def _inspection(path: Path, subject: AssessmentSubject, *, as_of: date) -> FileInspection:
    return inspect_hospital_file(path, subject.publisher, as_of=as_of)


def _success_evidence(
    path: Path,
    raw: bytes,
    *,
    status: FetchStatus = FetchStatus.FETCHED,
    attempted_at: str = OBSERVED_AT,
) -> FetchOutcome:
    return FetchOutcome(
        url=URL,
        status=status,
        attempted_at=attempted_at,
        attempts=1,
        path=path,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        wire_size_bytes=len(raw),
        http_status=304 if status is FetchStatus.NOT_MODIFIED else 200,
        final_url=URL,
    )


def _failure_evidence(
    status: FetchStatus,
    *,
    url: str = URL,
    attempted_at: str = OBSERVED_AT,
    attempts: int = 1,
    http_status: int | None = None,
    final_url: str | None = None,
    error: str | None = None,
) -> FetchOutcome:
    return FetchOutcome(
        url=url,
        status=status,
        attempted_at=attempted_at,
        attempts=attempts,
        http_status=http_status,
        final_url=final_url or url,
        error=error or f"fixture {status.value}",
    )


def _compose_success(
    tmp_path: Path,
    *,
    attempted_at: str = OBSERVED_AT,
    policy: FetchPolicy | None = None,
) -> tuple[FileAssessment, FileInspection]:
    subject = _subject()
    raw = _body()
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "hospital.json"
    path.write_bytes(raw)
    as_of = date.fromisoformat(attempted_at[:10])
    inspection = _inspection(path, subject, as_of=as_of)
    assessment = compose_file_assessment(
        subject,
        _success_evidence(path, raw, attempted_at=attempted_at),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(policy or _policy()),
        inspection=inspection,
    )
    return assessment, inspection


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def test_successful_200_with_matching_body_composes_remote_and_local_evidence(
    tmp_path: Path,
) -> None:
    subject = _subject()
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    assessment = assess_hospital_url(
        subject,
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
        opener=OneResponse(Response(_body())),
        clock=_clock,
    )

    assert assessment.fetch.status is FetchStatus.FETCHED
    assert assessment.inspection is not None
    assert assessment.scorecard.retrievability.status == "OBSERVED"
    assert assessment.scorecard.conformance.status == "OBSERVED"
    assert assessment.coverage == {
        "inspection_performed": True,
        "inspection_scan_completed": True,
        "network_attempted": True,
        "targeted": True,
        "verified_body_available": True,
    }
    assert assessment.operationally_complete
    assert len(registry.records()) == 1


def test_retrieval_and_malformed_json_conformance_are_independent(tmp_path: Path) -> None:
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    assessment = assess_hospital_url(
        _subject(),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
        opener=OneResponse(Response(b"not-json")),
        clock=_clock,
    )

    assert assessment.fetch.status is FetchStatus.FETCHED
    assert assessment.inspection is not None
    assert assessment.scorecard.retrievability.status == "OBSERVED"
    assert assessment.scorecard.conformance.status == "FINDINGS"
    assert "JSON_STREAM_INCOMPLETE" in {
        finding.code for finding in assessment.scorecard.conformance.findings
    }


_STATUS_CASES = (
    (FetchStatus.FETCHED, "OBSERVED", None),
    (FetchStatus.NOT_MODIFIED, "OBSERVED", None),
    (FetchStatus.INVALID_URL, "NOT_ASSESSED", None),
    (FetchStatus.HTTP_ERROR, "FINDINGS", "MRF_DIRECT_DOWNLOAD_FAILED"),
    (FetchStatus.NETWORK_ERROR, "FINDINGS", "MRF_DIRECT_DOWNLOAD_FAILED"),
    (FetchStatus.TOO_LARGE, "NOT_ASSESSED", None),
    (FetchStatus.CONTENT_ERROR, "FINDINGS", "MRF_DIRECT_DOWNLOAD_FAILED"),
    (FetchStatus.CACHE_MISS, "NOT_ASSESSED", None),
    (FetchStatus.CACHE_ERROR, "NOT_ASSESSED", None),
    # A robots.txt disallow is a fact about this crawler's permission, never about whether
    # the hospital published. NOT_ASSESSED, with the reason stated, and never an F.
    (FetchStatus.ROBOTS_DISALLOWED, "NOT_ASSESSED", None),
)


def test_status_matrix_covers_every_fetch_terminal_outcome() -> None:
    assert {status for status, _, _ in _STATUS_CASES} == set(FetchStatus)


@pytest.mark.parametrize(("status", "expected", "finding_code"), _STATUS_CASES)
def test_every_fetch_status_has_explicit_retrievability_semantics(
    tmp_path: Path,
    status: FetchStatus,
    expected: str,
    finding_code: str | None,
) -> None:
    subject = _subject()
    inspection: FileInspection | None = None
    if status in {FetchStatus.FETCHED, FetchStatus.NOT_MODIFIED}:
        raw = _body()
        path = tmp_path / "hospital.json"
        path.write_bytes(raw)
        inspection = _inspection(path, subject, as_of=date(2026, 8, 9))
        fetch = _success_evidence(path, raw, status=status)
    else:
        fetch = _failure_evidence(
            status,
            attempts=0 if status is FetchStatus.INVALID_URL else 1,
            http_status=404 if status is FetchStatus.HTTP_ERROR else None,
        )

    assessment = compose_file_assessment(
        subject,
        fetch,
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
        inspection=inspection,
    )

    assert assessment.scorecard.retrievability.status == expected
    assert {finding.code for finding in assessment.scorecard.retrievability.findings} == (
        {finding_code} if finding_code is not None else set()
    )


@pytest.mark.parametrize("http_status", [401, 403])
def test_http_access_barriers_have_a_specific_finding(http_status: int) -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.HTTP_ERROR, http_status=http_status),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    finding = assessment.scorecard.retrievability.findings[0]
    assert assessment.scorecard.retrievability.status == "FINDINGS"
    assert finding.code == "MRF_AUTOMATION_BARRIER_OBSERVED"
    assert f"HTTP {http_status}" in finding.message


def test_operator_input_error_and_unsafe_redirect_have_distinct_attribution() -> None:
    invalid_url = "http://hospital.test/prices.json"
    subject = _subject(url=invalid_url, provenance=URLProvenance.OPERATOR)
    policy = RetrievalPolicyEvidence.from_policy(_policy())

    invalid_input = compose_file_assessment(
        subject,
        _failure_evidence(FetchStatus.INVALID_URL, url=invalid_url, attempts=0),
        retrieval_policy=policy,
    )
    unsafe_redirect = compose_file_assessment(
        subject,
        _failure_evidence(
            FetchStatus.INVALID_URL,
            url=invalid_url,
            attempts=1,
            final_url="http://redirected.test/prices.json",
            error="unsafe redirect target: refused non-HTTPS URL",
        ),
        retrieval_policy=policy,
    )

    assert invalid_input.scorecard.retrievability.status == "NOT_ASSESSED"
    assert unsafe_redirect.scorecard.retrievability.status == "FINDINGS"
    assert unsafe_redirect.scorecard.retrievability.findings[0].code == (
        "MRF_DIRECT_DOWNLOAD_FAILED"
    )


def test_unverified_cms_hpt_provenance_does_not_attribute_invalid_input() -> None:
    url = "http://hospital.test/prices.json"
    assessment = compose_file_assessment(
        _subject(url=url, provenance=URLProvenance.CMS_HPT),
        _failure_evidence(FetchStatus.INVALID_URL, url=url, attempts=0),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    assert assessment.scorecard.retrievability.status == "NOT_ASSESSED"
    assert not assessment.scorecard.retrievability.findings


@pytest.mark.parametrize(
    ("field", "message"),
    [("content_sha256", "digests"), ("size_bytes", "sizes")],
)
def test_compose_rejects_body_identity_mismatch(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    subject = _subject()
    raw = _body()
    path = tmp_path / "hospital.json"
    path.write_bytes(raw)
    inspection = _inspection(path, subject, as_of=date(2026, 8, 9))
    fetch = _success_evidence(path, raw)
    fetch = replace(fetch, **{field: "0" * 64 if field == "content_sha256" else len(raw) + 1})

    with pytest.raises(ValueError, match=message):
        compose_file_assessment(
            subject,
            fetch,
            retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
            inspection=inspection,
        )


def test_no_verified_body_leaves_all_four_local_dimensions_not_assessed() -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    local_dimensions = assessment.scorecard.dimensions[1:]
    assert [dimension.name for dimension in local_dimensions] == [
        "conformance",
        "completeness",
        "interpretability",
        "freshness",
    ]
    assert {dimension.status for dimension in local_dimensions} == {"NOT_ASSESSED"}
    assert not assessment.coverage["inspection_performed"]
    assert not assessment.coverage["verified_body_available"]


def test_success_without_verified_inspection_is_not_operationally_complete() -> None:
    assessment = compose_file_assessment(
        _subject(),
        FetchOutcome(
            url=URL,
            status=FetchStatus.FETCHED,
            attempted_at=OBSERVED_AT,
            attempts=1,
            content_sha256="0" * 64,
            size_bytes=1,
            final_url=URL,
        ),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    assert assessment.scorecard.retrievability.status == "NOT_ASSESSED"
    assert not assessment.operationally_complete


def test_registry_round_trip_verifies_body_and_nested_retrieval_digests(tmp_path: Path) -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")
    registry.append(assessment)

    record = registry.records()[0]

    assert record == assessment.to_dict()
    assert record["assessment_id"] == assessment.assessment_id
    assert registry.path.read_text(encoding="utf-8").count("\n") == 1

    record["as_of"] = "2026-08-10"
    registry.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(AssessmentRegistryError, match="body digest"):
        registry.records()

    record = assessment.to_dict()
    retrieval = record["retrieval"]
    assert isinstance(retrieval, dict)
    retrieval["error"] = "tampered"
    body = {
        key: value
        for key, value in record.items()
        if key not in {"assessment_id", "assessment_body_sha256"}
    }
    outer_digest = _canonical_digest(body)
    record["assessment_id"] = outer_digest
    record["assessment_body_sha256"] = outer_digest
    registry.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(AssessmentRegistryError, match="retrieval evidence digest"):
        registry.records()


def test_retrieval_policy_keeps_contact_private_and_out_of_comparison_fingerprint() -> None:
    first = RetrievalPolicyEvidence.from_policy(_policy(contact="alice@example.test"))
    second = RetrievalPolicyEvidence.from_policy(_policy(contact="bob@example.test"))
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=first,
    )

    published = json.dumps(
        [first.to_dict(), second.to_dict(), assessment.to_dict()], sort_keys=True
    )

    assert "alice@example.test" not in published
    assert "bob@example.test" not in published
    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("backoff_seconds", float("nan")),
        ("backoff_seconds", float("inf")),
    ],
)
def test_retrieval_policy_evidence_rejects_nonfinite_numbers(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        replace(RetrievalPolicyEvidence.from_policy(_policy()), **{field: value})


def test_assessment_serialization_omits_local_paths_and_nested_local_scorecard(
    tmp_path: Path,
) -> None:
    assessment, _ = _compose_success(tmp_path)

    payload = assessment.to_dict()
    inspection = payload["inspection"]

    assert isinstance(inspection, dict)
    assert "source_path" not in inspection
    assert "scorecard" not in inspection


def test_assessment_id_is_stable_across_equivalent_cache_roots(tmp_path: Path) -> None:
    subject = _subject()
    raw = _body()
    policy = RetrievalPolicyEvidence.from_policy(_policy())
    assessments = []
    for directory in (tmp_path / "first", tmp_path / "second"):
        directory.mkdir()
        path = directory / "hospital.json"
        path.write_bytes(raw)
        inspection = _inspection(path, subject, as_of=date(2026, 8, 9))
        assessments.append(
            compose_file_assessment(
                subject,
                _success_evidence(path, raw),
                retrieval_policy=policy,
                inspection=inspection,
            )
        )

    first, second = assessments
    assert first.assessment_id == first.to_dict()["assessment_id"]
    assert first.assessment_id == second.assessment_id
    assert first.to_dict() == second.to_dict()


def test_comparison_scope_rejects_publisher_type_policy_and_as_of_changes(
    tmp_path: Path,
) -> None:
    base, inspection = _compose_success(tmp_path / "base")
    require_comparable(base, base)

    payer_subject = replace(base.subject, publisher_type=PublisherType.PAYER)
    payer = replace(base, subject=payer_subject)
    with pytest.raises(ValueError, match="not directly comparable"):
        require_comparable(base, payer)

    different_policy = compose_file_assessment(
        base.subject,
        base.fetch,
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy(max_bytes=(1 << 20) + 1)),
        inspection=inspection,
    )
    with pytest.raises(ValueError, match="not directly comparable"):
        require_comparable(base, different_policy)

    later_dir = tmp_path / "later"
    later_dir.mkdir()
    later_path = later_dir / "hospital.json"
    raw = _body()
    later_path.write_bytes(raw)
    later_inspection = _inspection(later_path, base.subject, as_of=date(2026, 8, 10))
    later = compose_file_assessment(
        base.subject,
        _success_evidence(later_path, raw, attempted_at="2026-08-10T00:00:00Z"),
        retrieval_policy=base.retrieval_policy,
        inspection=later_inspection,
    )
    with pytest.raises(ValueError, match="not directly comparable"):
        require_comparable(base, later)


def test_custom_retrieval_execution_is_never_treated_as_a_comparable_policy() -> None:
    custom_policy = RetrievalPolicyEvidence.from_policy(_policy(), execution_strategy="custom")
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=custom_policy,
    )

    with pytest.raises(ValueError, match="custom retrieval execution"):
        require_comparable(assessment, assessment)
    with pytest.raises(ValueError, match="custom retrieval execution"):
        require_comparable(assessment.to_dict(), assessment.to_dict())


def test_payer_profile_fails_closed_until_an_adapter_exists() -> None:
    with pytest.raises(ValueError, match="only the hospital assessment profile"):
        compose_file_assessment(
            _subject(publisher_type=PublisherType.PAYER),
            _failure_evidence(FetchStatus.NETWORK_ERROR),
            retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
        )


def test_failed_retrieval_is_exactly_one_durable_assessment_row(tmp_path: Path) -> None:
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")
    response = Response(b"not read", status=404)

    assessment = assess_hospital_url(
        _subject(),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
        opener=OneResponse(response),
        clock=_clock,
    )

    rows = registry.records()
    assert assessment.fetch.status is FetchStatus.HTTP_ERROR
    assert assessment.retrieval_policy.execution_strategy == "custom"
    assert assessment.scorecard.retrievability.status == "FINDINGS"
    assert assessment.inspection is None
    assert len(rows) == 1
    assert rows[0]["assessment_id"] == assessment.assessment_id
    assert registry.path.read_text(encoding="utf-8").count("\n") == 1


def test_public_evidence_redacts_query_tokens_paths_and_unbounded_errors() -> None:
    url = "https://hospital.test/prices.json?token=do-not-publish#fragment"
    subject = _subject(url=url, provenance=URLProvenance.OPERATOR)
    fetch = _failure_evidence(FetchStatus.NETWORK_ERROR, url=url)
    fetch = replace(
        fetch,
        error=(
            "failed at /Users/alice/private/cache/body after "
            f"https://user:secret@redirect.test/file?key=embedded-secret {'x' * 1000}"
        ),
    )

    assessment = compose_file_assessment(
        subject,
        fetch,
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    payload = assessment.to_dict()
    rendered = json.dumps(payload, sort_keys=True)
    retrieval = payload["retrieval"]

    assert isinstance(retrieval, dict)
    assert retrieval["url"] == "https://hospital.test/prices.json"
    assert len(str(retrieval["error"])) <= 500
    assert "do-not-publish" not in rendered
    assert "embedded-secret" not in rendered
    assert "user:secret" not in rendered
    assert "/Users/alice" not in rendered
    assert "<local-path>" in rendered
    assert payload["subject"]["requested_url_sha256"]  # type: ignore[index]


def test_malformed_credential_final_url_is_redacted_from_public_evidence() -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(
            FetchStatus.NETWORK_ERROR,
            final_url="https://user:secret@[broken/path?token=private#fragment",
        ),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    rendered = json.dumps(assessment.to_dict(), sort_keys=True)
    assert "user:secret" not in rendered
    assert "token=private" not in rendered
    assert "https://[broken/path" in rendered


def test_success_scorecard_note_does_not_republish_url_query_token(tmp_path: Path) -> None:
    url = "https://hospital.test/prices.json?signature=private-token"
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    assessment = assess_hospital_url(
        _subject(url=url, provenance=URLProvenance.OPERATOR),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
        opener=OneResponse(Response(_body(), url=url)),
        clock=_clock,
    )
    rendered = json.dumps(assessment.to_dict(), sort_keys=True)

    assert assessment.scorecard.retrievability.status == "OBSERVED"
    assert "private-token" not in rendered
    assert "https://hospital.test/prices.json" in rendered


def test_human_scorecard_output_does_not_republish_url_query_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = "https://hospital.test/prices.json?signature=private-token#fragment"
    assessment = compose_file_assessment(
        _subject(url=url, provenance=URLProvenance.OPERATOR),
        _failure_evidence(FetchStatus.NETWORK_ERROR, url=url),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    _emit_assessment_human(assessment)

    rendered = capsys.readouterr().out
    assert "private-token" not in rendered
    assert "fragment" not in rendered
    assert "url: https://hospital.test/prices.json" in rendered


def test_registry_rejects_inconsistent_public_dataclass_before_writing(tmp_path: Path) -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    with pytest.raises(AssessmentRegistryError, match="as_of"):
        registry.append(replace(assessment, as_of=date(2026, 8, 10)))
    assert not registry.path.exists()

    invalid_scorecard = replace(
        assessment.scorecard,
        retrievability=DimensionResult("retrievability", "OBSERVED"),
    )
    with pytest.raises(AssessmentRegistryError, match="retrievability dimension"):
        registry.append(replace(assessment, scorecard=invalid_scorecard))
    assert not registry.path.exists()


def test_registry_rejects_tampered_retrieval_finding_semantics(tmp_path: Path) -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.HTTP_ERROR, http_status=403),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    record = assessment.to_dict()
    scorecard = record["scorecard"]
    assert isinstance(scorecard, dict)
    retrievability = scorecard["retrievability"]
    assert isinstance(retrievability, dict)
    findings = retrievability["findings"]
    assert isinstance(findings, list)
    finding = findings[0]
    assert isinstance(finding, dict)
    finding["severity"] = "INFO"
    body = {
        key: value
        for key, value in record.items()
        if key not in {"assessment_id", "assessment_body_sha256"}
    }
    record["assessment_body_sha256"] = _canonical_digest(body)
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")
    registry.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(AssessmentRegistryError, match="retrievability dimension"):
        registry.records()


def test_registry_rejects_inspection_date_that_differs_from_assessment(
    tmp_path: Path,
) -> None:
    assessment, _ = _compose_success(tmp_path / "source")
    record = assessment.to_dict()
    inspection = record["inspection"]
    assert isinstance(inspection, dict)
    inspection["as_of"] = "2026-08-10"
    body = {
        key: value
        for key, value in record.items()
        if key not in {"assessment_id", "assessment_body_sha256"}
    }
    record["assessment_body_sha256"] = _canonical_digest(body)
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")
    registry.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(AssessmentRegistryError, match="inspection as_of"):
        registry.records()


def test_registry_binds_current_policy_fingerprint_to_known_components(
    tmp_path: Path,
) -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    record = assessment.to_dict()
    record["inspection_fingerprint"] = "0" * 64
    body = {
        key: value
        for key, value in record.items()
        if key not in {"assessment_id", "assessment_body_sha256"}
    }
    record["assessment_body_sha256"] = _canonical_digest(body)
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")
    registry.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(AssessmentRegistryError, match="known components"):
        registry.records()


def test_comparison_scope_includes_url_provenance() -> None:
    fetch = _failure_evidence(FetchStatus.NETWORK_ERROR)
    policy = RetrievalPolicyEvidence.from_policy(_policy())
    discovered = compose_file_assessment(_subject(), fetch, retrieval_policy=policy)
    operator = compose_file_assessment(
        _subject(provenance=URLProvenance.OPERATOR), fetch, retrieval_policy=policy
    )

    with pytest.raises(ValueError, match="not directly comparable"):
        require_comparable(discovered, operator)
    require_comparable(discovered.to_dict(), discovered.to_dict())
    with pytest.raises(ValueError, match="not directly comparable"):
        require_comparable(discovered.to_dict(), operator.to_dict())


def test_display_name_correction_does_not_change_semantic_assessment_id() -> None:
    fetch = _failure_evidence(FetchStatus.NETWORK_ERROR)
    policy = RetrievalPolicyEvidence.from_policy(_policy())
    first_subject = _subject()
    second_subject = replace(
        first_subject,
        publisher=replace(first_subject.publisher, name="Corrected Hospital Name"),
    )

    first = compose_file_assessment(first_subject, fetch, retrieval_policy=policy)
    second = compose_file_assessment(second_subject, fetch, retrieval_policy=policy)

    assert first.assessment_id == second.assessment_id
    assert first.to_dict() != second.to_dict()


def test_zero_attempt_invalid_url_has_honest_coverage_flags() -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.INVALID_URL, attempts=0),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )

    assert assessment.coverage == {
        "inspection_performed": False,
        "inspection_scan_completed": False,
        "network_attempted": False,
        "targeted": True,
        "verified_body_available": False,
    }


def test_retrieval_finding_catalog_is_immutable() -> None:
    with pytest.raises(TypeError):
        RETRIEVAL_FINDING_CATALOG["MUTATED"] = RETRIEVAL_FINDING_CATALOG[
            "MRF_DIRECT_DOWNLOAD_FAILED"
        ]  # type: ignore[index]


def test_unexpected_inspection_error_still_writes_one_operational_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import mrf_honest.scorecard as scorecard

    def fail_inspection(*args: object, **kwargs: object) -> FileInspection:
        raise RuntimeError("fixture inspection failure")

    monkeypatch.setattr(scorecard, "inspect_hospital_file", fail_inspection)
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    assessment = assess_hospital_url(
        _subject(),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
        opener=OneResponse(Response(_body())),
        clock=_clock,
    )

    assert not assessment.operationally_complete
    assert assessment.inspection is None
    assert len(registry.records()) == 1
    assert "fixture inspection failure" in assessment.operational_problems[0]


def test_post_inspection_rehash_detects_body_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import mrf_honest.scorecard as scorecard

    monkeypatch.setattr(scorecard, "_hash_path", lambda path: ("0" * 64, 1))
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    assessment = assess_hospital_url(
        _subject(),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
        opener=OneResponse(Response(_body())),
        clock=_clock,
    )

    assert assessment.inspection is None
    assert not assessment.operationally_complete
    assert "changed while" in assessment.operational_problems[0]
    assert len(registry.records()) == 1


def test_historical_policy_fingerprint_remains_readable_and_scope_disjoint(
    tmp_path: Path,
) -> None:
    assessment = compose_file_assessment(
        _subject(),
        _failure_evidence(FetchStatus.NETWORK_ERROR),
        retrieval_policy=RetrievalPolicyEvidence.from_policy(_policy()),
    )
    record = assessment.to_dict()
    historical_fingerprint = "a" * 64
    record["assessment_policy_fingerprint"] = historical_fingerprint
    scope = record["comparison_scope"]
    subject = record["subject"]
    inspection = record["inspection"]
    assert isinstance(scope, dict)
    assert isinstance(subject, dict)
    assert inspection is None
    scope["assessment_policy_fingerprint"] = historical_fingerprint
    publisher = subject["publisher"]
    assert isinstance(publisher, dict)
    identity = {
        "as_of": record["as_of"],
        "assessment_policy_fingerprint": historical_fingerprint,
        "inspection_source_sha256": None,
        "observed_at": record["observed_at"],
        "retrieval_evidence_sha256": record["retrieval_evidence_sha256"],
        "retrieval_policy_fingerprint": record["retrieval_policy"]["fingerprint"],  # type: ignore[index]
        "subject": {
            "location_id": subject["location_id"],
            "publisher_identifier": publisher["identifier"],
            "publisher_type": subject["publisher_type"],
            "requested_url": subject["requested_url"],
            "requested_url_sha256": subject["requested_url_sha256"],
            "url_provenance": subject["url_provenance"],
        },
        "version": record["version"],
    }
    record["assessment_id"] = _canonical_digest(identity)
    body = {
        key: value
        for key, value in record.items()
        if key not in {"assessment_id", "assessment_body_sha256"}
    }
    record["assessment_body_sha256"] = _canonical_digest(body)
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")
    registry.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert registry.records()[0]["assessment_id"] == record["assessment_id"]
    assert scope != assessment.comparison_scope


def test_malformed_noncredential_url_is_a_durable_zero_attempt_row(tmp_path: Path) -> None:
    url = "https://[broken"
    registry = AssessmentRegistry(tmp_path / "assessments.jsonl")

    assessment = assess_hospital_url(
        _subject(url=url, provenance=URLProvenance.OPERATOR),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=registry,
    )

    assert assessment.fetch.status is FetchStatus.INVALID_URL
    assert assessment.retrieval_policy.execution_strategy == "default"
    assert assessment.fetch.attempts == 0
    assert assessment.scorecard.retrievability.status == "NOT_ASSESSED"
    assert len(registry.records()) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@[broken/prices.json",
        "https:user:secret@host.test/prices.json",
        "https:/user:secret@host.test/prices.json?token=private",
        "https:///user:secret@host.test/prices.json",
    ],
)
def test_credential_url_is_rejected_before_public_persistence(url: str) -> None:
    with pytest.raises(ValueError, match="credentials"):
        _subject(url=url, provenance=URLProvenance.OPERATOR)


def test_custom_opener_value_error_is_not_attributed_as_a_network_observation(
    tmp_path: Path,
) -> None:
    def invalid_opener(request: Request, *, timeout: float) -> ResponseLike:
        raise ValueError("local adapter rejected input")

    assessment = assess_hospital_url(
        _subject(),
        tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=_policy(),
        registry=AssessmentRegistry(tmp_path / "assessments.jsonl"),
        opener=invalid_opener,
    )

    assert assessment.fetch.status is FetchStatus.INVALID_URL
    assert assessment.fetch.attempts == 0
    assert not assessment.coverage["network_attempted"]
    assert assessment.scorecard.retrievability.status == "NOT_ASSESSED"
    assert not assessment.findings
