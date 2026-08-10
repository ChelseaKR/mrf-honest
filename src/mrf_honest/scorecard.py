"""Durable remote-plus-local scorecards for hospital machine-readable files.

``inspect_hospital_file`` deliberately assesses a local body only.  This module is the separate
boundary that records one dated retrieval attempt, verifies any admitted body against that
attempt, and composes retrievability with the four local dimensions.  Remote failures are rows,
not exceptions or missing publishers, and no dimension is collapsed into a rank.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from mrf_honest.fetch import (
    Backoff,
    Clock,
    FetchOutcome,
    FetchPolicy,
    FetchStatus,
    Opener,
    Sleeper,
    fetch_url,
)
from mrf_honest.inspect import (
    CFR_180_50,
    INSPECTION_FINGERPRINT,
    DimensionResult,
    FileInspection,
    FileScorecard,
    Finding,
    FindingDefinition,
    inspect_hospital_file,
)
from mrf_honest.types import PublisherRef

CMS_HPT_POLICY_FAQ = "https://www.cms.gov/files/document/hpt-policy-faqs-june-2026.pdf"

_ASSESSMENT_VERSION = 1
ASSESSMENT_POLICY_VERSION = "cms-hospital-json-v3-scorecard-v1"
_PROFILE = "cms-hospital-json-v3"
_PROBLEM_TEXT_LIMIT = 500
_URL_TEXT_LIMIT = 2_048
_AUTOMATION_BARRIER_HTTP_STATUSES = frozenset({401, 403})
_URL_IN_TEXT_RE = re.compile(r"https?:[^\s\"'<>]+", re.IGNORECASE)
_LOCAL_PATH_RE = re.compile(r"(?<![:/\w])/(?:[^/\s]+/)*[^/\s,;]+")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s,;]+")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class PublisherType(StrEnum):
    """Publisher kinds that must never share one comparison cohort."""

    HOSPITAL = "hospital"
    PAYER = "payer"


class URLProvenance(StrEnum):
    """How the requested URL entered the assessment dataset."""

    CMS_HPT = "cms_hpt"
    OPERATOR = "operator"


@dataclass(frozen=True)
class AssessmentSubject:
    """Explicit identity for one publisher location and one expected file URL."""

    publisher: PublisherRef
    publisher_type: PublisherType
    location_id: str
    requested_url: str
    url_provenance: URLProvenance

    def __post_init__(self) -> None:
        _validate_identifier(self.publisher.identifier, "publisher identifier")
        _validate_identifier(self.location_id, "location_id")
        if not self.requested_url.strip():
            raise ValueError("requested_url cannot be empty")
        if _authority_has_userinfo(self.requested_url):
            raise ValueError("requested_url cannot contain credentials")
        try:
            parsed = urlsplit(self.requested_url)
        except ValueError:
            parsed = None
        if parsed is not None and (parsed.username is not None or parsed.password is not None):
            raise ValueError("requested_url cannot contain credentials")
        if (
            self.publisher.source_url is not None
            and self.publisher.source_url != self.requested_url
        ):
            raise ValueError("publisher source_url must match the assessed requested_url")

    def to_dict(self) -> dict[str, object]:
        publisher_source = self.publisher.source_url
        return {
            "location_id": self.location_id,
            "publisher": {
                "identifier": self.publisher.identifier,
                "name": (
                    _bounded_problem(self.publisher.name)
                    if self.publisher.name is not None
                    else None
                ),
                "source_url": _public_url(publisher_source) if publisher_source else None,
                "source_url_sha256": (_text_digest(publisher_source) if publisher_source else None),
            },
            "publisher_type": self.publisher_type.value,
            "requested_url": _public_url(self.requested_url),
            "requested_url_sha256": _text_digest(self.requested_url),
            "url_provenance": self.url_provenance.value,
        }

    def identity_dict(self) -> dict[str, object]:
        """Stable subject identity excluding correctable display metadata."""
        return {
            "location_id": self.location_id,
            "publisher_identifier": self.publisher.identifier,
            "publisher_type": self.publisher_type.value,
            "requested_url": _public_url(self.requested_url),
            "requested_url_sha256": _text_digest(self.requested_url),
            "url_provenance": self.url_provenance.value,
        }


@dataclass(frozen=True)
class RetrievalPolicyEvidence:
    """Publishable retrieval limits plus a fingerprint of the full identified policy."""

    user_agent: str
    max_bytes: int
    timeout_seconds: float
    retries: int
    backoff_seconds: float
    chunk_size: int
    execution_strategy: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise ValueError("retrieval user_agent cannot be empty")
        if (
            self.max_bytes <= 0
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.chunk_size <= 0
        ):
            raise ValueError("retrieval limits must be positive")
        if self.retries < 0 or not math.isfinite(self.backoff_seconds) or self.backoff_seconds < 0:
            raise ValueError("retrieval retry values cannot be negative")
        if self.execution_strategy not in {"default", "custom"}:
            raise ValueError("unknown retrieval execution strategy")
        if self.fingerprint != _digest(self._fingerprint_body()):
            raise ValueError("retrieval policy fingerprint does not match its fields")

    @classmethod
    def from_policy(
        cls, policy: FetchPolicy, *, execution_strategy: str = "default"
    ) -> RetrievalPolicyEvidence:
        body = {
            "backoff_seconds": policy.backoff_seconds,
            "chunk_size": policy.chunk_size,
            "execution_strategy": execution_strategy,
            "max_bytes": policy.max_bytes,
            "retries": policy.retries,
            "timeout_seconds": policy.timeout_seconds,
            "user_agent": policy.user_agent,
        }
        return cls(
            user_agent=policy.user_agent,
            max_bytes=policy.max_bytes,
            timeout_seconds=policy.timeout_seconds,
            retries=policy.retries,
            backoff_seconds=policy.backoff_seconds,
            chunk_size=policy.chunk_size,
            execution_strategy=execution_strategy,
            fingerprint=_digest(body),
        )

    def _fingerprint_body(self) -> dict[str, object]:
        return {
            "backoff_seconds": self.backoff_seconds,
            "chunk_size": self.chunk_size,
            "execution_strategy": self.execution_strategy,
            "max_bytes": self.max_bytes,
            "retries": self.retries,
            "timeout_seconds": self.timeout_seconds,
            "user_agent": self.user_agent,
        }

    def to_dict(self) -> dict[str, object]:
        # The operator contact is intentionally neither published nor part of grading semantics.
        return {
            "backoff_seconds": self.backoff_seconds,
            "chunk_size": self.chunk_size,
            "execution_strategy": self.execution_strategy,
            "fingerprint": self.fingerprint,
            "max_bytes": self.max_bytes,
            "retries": self.retries,
            "timeout_seconds": self.timeout_seconds,
            "user_agent": self.user_agent,
        }


RETRIEVAL_FINDING_CATALOG: Mapping[str, FindingDefinition] = MappingProxyType(
    {
        "MRF_AUTOMATION_BARRIER_OBSERVED": FindingDefinition(
            code="MRF_AUTOMATION_BARRIER_OBSERVED",
            dimension="retrievability",
            severity="ERROR",
            description="The direct-download request received an HTTP access barrier.",
            citations=(CFR_180_50, CMS_HPT_POLICY_FAQ),
        ),
        "MRF_DIRECT_DOWNLOAD_FAILED": FindingDefinition(
            code="MRF_DIRECT_DOWNLOAD_FAILED",
            dimension="retrievability",
            severity="ERROR",
            description="The direct-download request did not produce a verified local body.",
            citations=(CFR_180_50,),
        ),
    }
)

_PUBLISHER_FAILURES = frozenset(
    {FetchStatus.HTTP_ERROR, FetchStatus.NETWORK_ERROR, FetchStatus.CONTENT_ERROR}
)
_LOCAL_OR_POLICY_AMBIGUITY = frozenset(
    {FetchStatus.TOO_LARGE, FetchStatus.CACHE_MISS, FetchStatus.CACHE_ERROR}
)
_SUCCESS_STATUSES = frozenset({FetchStatus.FETCHED, FetchStatus.NOT_MODIFIED})
_MAPPED_STATUSES = (
    _PUBLISHER_FAILURES | _LOCAL_OR_POLICY_AMBIGUITY | _SUCCESS_STATUSES | {FetchStatus.INVALID_URL}
)
if _MAPPED_STATUSES != frozenset(FetchStatus):  # fail loudly when the fetch taxonomy changes
    raise RuntimeError("scorecard retrieval mapping does not cover every FetchStatus")


ASSESSMENT_POLICY_FINGERPRINT = _digest(
    {
        "assessment_policy_version": ASSESSMENT_POLICY_VERSION,
        "automation_barrier_http_statuses": sorted(_AUTOMATION_BARRIER_HTTP_STATUSES),
        "coverage_fields": (
            "targeted",
            "network_attempted",
            "verified_body_available",
            "inspection_performed",
            "inspection_scan_completed",
        ),
        "comparison_scope_fields": (
            "publisher_type",
            "profile",
            "url_provenance",
            "assessment_policy_fingerprint",
            "retrieval_policy_fingerprint",
            "as_of",
        ),
        "invalid_url_rule": "finding-only-for-observed-unsafe-redirect-v2",
        "inspection_fingerprint": INSPECTION_FINGERPRINT,
        "prior_evidence_join": "forbidden-v1",
        "profile": _PROFILE,
        "publisher_failures": sorted(status.value for status in _PUBLISHER_FAILURES),
        "local_or_policy_ambiguity": sorted(status.value for status in _LOCAL_OR_POLICY_AMBIGUITY),
        "success_statuses": sorted(status.value for status in _SUCCESS_STATUSES),
        "success_rule": "matching-digest-size-post-inspection-rehash-v1",
        "url_publication": "userinfo-query-fragment-redacted-with-exact-hash-v2",
        "retrieval_findings": {
            code: {
                "citations": definition.citations,
                "description": definition.description,
                "dimension": definition.dimension,
                "severity": definition.severity,
            }
            for code, definition in sorted(RETRIEVAL_FINDING_CATALOG.items())
        },
    }
)


@dataclass(frozen=True)
class FileAssessment:
    """One integrity-hashed scorecard joining dated remote and optional local evidence."""

    subject: AssessmentSubject
    fetch: FetchOutcome
    retrieval_policy: RetrievalPolicyEvidence
    as_of: date
    inspection: FileInspection | None
    scorecard: FileScorecard
    operational_problems: tuple[str, ...] = ()

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.scorecard.findings

    @property
    def coverage(self) -> dict[str, bool]:
        """Denominator flags that retain failed targets instead of dropping them."""
        return {
            "inspection_performed": self.inspection is not None,
            "inspection_scan_completed": (
                self.inspection.scan_completed if self.inspection is not None else False
            ),
            "network_attempted": self.fetch.attempts > 0,
            "targeted": True,
            "verified_body_available": self.inspection is not None,
        }

    @property
    def comparison_scope(self) -> dict[str, object]:
        """The complete scope that must match before assessments are compared."""
        return {
            "as_of": self.as_of.isoformat(),
            "assessment_policy_fingerprint": ASSESSMENT_POLICY_FINGERPRINT,
            "profile": _PROFILE,
            "publisher_type": self.subject.publisher_type.value,
            "retrieval_policy_fingerprint": self.retrieval_policy.fingerprint,
            "url_provenance": self.subject.url_provenance.value,
        }

    @property
    def operationally_complete(self) -> bool:
        """Whether local infrastructure completed the assessment workflow."""
        return (
            not self.operational_problems
            and self.fetch.status not in {FetchStatus.CACHE_ERROR, FetchStatus.CACHE_MISS}
            and not (self.fetch.ok and self.inspection is None)
        )

    @property
    def assessment_id(self) -> str:
        return _digest(self._identity_dict())

    def _identity_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "assessment_policy_fingerprint": ASSESSMENT_POLICY_FINGERPRINT,
            "inspection_source_sha256": (
                self.inspection.source_sha256 if self.inspection is not None else None
            ),
            "observed_at": self.fetch.attempted_at,
            "retrieval_evidence_sha256": _digest(_fetch_evidence(self.fetch)),
            "retrieval_policy_fingerprint": self.retrieval_policy.fingerprint,
            "subject": self.subject.identity_dict(),
            "version": _ASSESSMENT_VERSION,
        }

    def _body_dict(self) -> dict[str, object]:
        retrieval = _fetch_evidence(self.fetch)
        return {
            "as_of": self.as_of.isoformat(),
            "assessment_policy_fingerprint": ASSESSMENT_POLICY_FINGERPRINT,
            "assessment_policy_version": ASSESSMENT_POLICY_VERSION,
            "comparison_scope": self.comparison_scope,
            "coverage": self.coverage,
            "inspection": _inspection_evidence(self.inspection),
            "inspection_fingerprint": INSPECTION_FINGERPRINT,
            "observed_at": self.fetch.attempted_at,
            "operational_problems": list(self.operational_problems),
            "retrieval": retrieval,
            "retrieval_evidence_sha256": _digest(retrieval),
            "retrieval_policy": self.retrieval_policy.to_dict(),
            "scorecard": _scorecard_dict(self.scorecard),
            "subject": self.subject.to_dict(),
            "version": _ASSESSMENT_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        body = self._body_dict()
        body_digest = _digest(body)
        return {
            "assessment_body_sha256": body_digest,
            "assessment_id": self.assessment_id,
            **body,
        }


class AssessmentRegistryError(Exception):
    """Raised when assessment persistence or integrity verification fails."""


class AssessmentRegistry:
    """Single-writer JSONL scorecards with atomic whole-file replacement per append.

    This local operator workflow does not support concurrent writers. Replacing the complete file
    avoids leaving a partial trailing line after process or power loss; every pre-existing record
    is integrity-checked before the next append.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, assessment: FileAssessment) -> None:
        payload = assessment.to_dict()
        try:
            _verify_persisted_record(payload)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existing = b""
            if self.path.exists():
                # Validate semantics as well as JSON before carrying old evidence forward.
                self.records()
                existing = self.path.read_bytes()
                if existing and not existing.endswith(b"\n"):
                    raise AssessmentRegistryError(
                        "existing assessment registry has an incomplete trailing record"
                    )
            line = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=self.path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(existing)
                    handle.write(line)
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                _fsync_directory(self.path.parent)
            finally:
                temporary.unlink(missing_ok=True)
        except AssessmentRegistryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise AssessmentRegistryError(f"could not append assessment record: {exc}") from exc

    def records(self) -> tuple[dict[str, object], ...]:
        return tuple(self)

    def __iter__(self) -> Iterator[dict[str, object]]:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        raw: object = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("record must be a JSON object")
                        record = cast(dict[str, object], raw)
                        _verify_persisted_record(record)
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise AssessmentRegistryError(
                            f"invalid assessment record at line {line_number}: {exc}"
                        ) from exc
                    yield record
        except (OSError, UnicodeError) as exc:
            raise AssessmentRegistryError(f"could not read assessment registry: {exc}") from exc


def compose_file_assessment(
    subject: AssessmentSubject,
    fetch: FetchOutcome,
    *,
    retrieval_policy: RetrievalPolicyEvidence,
    inspection: FileInspection | None = None,
    operational_problems: tuple[str, ...] = (),
) -> FileAssessment:
    """Purely compose one fetch outcome and an optional verified local inspection."""
    if subject.publisher_type is not PublisherType.HOSPITAL:
        raise ValueError("only the hospital assessment profile is implemented")
    if fetch.url != subject.requested_url:
        raise ValueError("fetch URL does not match the assessment subject")
    as_of = _attempt_date(fetch.attempted_at)
    if inspection is not None:
        if not fetch.ok:
            raise ValueError("an inspection can only be attached to its successful fetch")
        if fetch.content_sha256 is None or fetch.size_bytes is None:
            raise ValueError("successful fetch evidence is missing body identity")
        if inspection.source_sha256 != fetch.content_sha256:
            raise ValueError("inspection and fetch content digests do not match")
        if inspection.source_size != fetch.size_bytes:
            raise ValueError("inspection and fetch body sizes do not match")
        if inspection.as_of != as_of:
            raise ValueError("inspection and retrieval as_of dates do not match")
        if inspection.publisher != subject.publisher:
            raise ValueError("inspection and assessment publisher identities do not match")

    retrievability = _retrievability(
        fetch,
        has_inspection=inspection is not None,
        has_operational_problems=bool(operational_problems),
    )
    scorecard = _integrated_scorecard(retrievability, inspection)
    return FileAssessment(
        subject=subject,
        fetch=fetch,
        retrieval_policy=retrieval_policy,
        as_of=as_of,
        inspection=inspection,
        scorecard=scorecard,
        operational_problems=tuple(_bounded_problem(problem) for problem in operational_problems),
    )


def assess_hospital_url(
    subject: AssessmentSubject,
    cache_dir: str | Path,
    *,
    policy: FetchPolicy,
    registry: AssessmentRegistry,
    opener: Opener | None = None,
    sleep: Sleeper = time.sleep,
    backoff: Backoff | None = None,
    clock: Clock | None = None,
) -> FileAssessment:
    """Fetch, inspect when possible, compose, and durably append exactly one scorecard."""
    if subject.publisher_type is not PublisherType.HOSPITAL:
        raise ValueError("assess_hospital_url requires publisher_type=hospital")
    fetch = fetch_url(
        subject.requested_url,
        cache_dir,
        policy=policy,
        opener=opener,
        sleep=sleep,
        backoff=backoff,
        clock=clock,
    )
    inspection: FileInspection | None = None
    problems: list[str] = []
    if fetch.ok:
        if fetch.path is None or fetch.content_sha256 is None or fetch.size_bytes is None:
            problems.append("successful fetch evidence is missing path, digest, or size")
        else:
            try:
                candidate = inspect_hospital_file(
                    fetch.path,
                    subject.publisher,
                    as_of=_attempt_date(fetch.attempted_at),
                )
                final_sha256, final_size = _hash_path(fetch.path)
            except Exception as exc:
                problems.append(f"could not inspect the admitted cached body: {exc}")
            else:
                if candidate.source_sha256 != fetch.content_sha256:
                    problems.append("cached body digest no longer matches the fetch evidence")
                elif candidate.source_size != fetch.size_bytes:
                    problems.append("cached body size no longer matches the fetch evidence")
                elif (final_sha256, final_size) != (
                    candidate.source_sha256,
                    candidate.source_size,
                ):
                    problems.append("cached body changed while it was being inspected")
                else:
                    inspection = candidate

    assessment = compose_file_assessment(
        subject,
        fetch,
        retrieval_policy=RetrievalPolicyEvidence.from_policy(
            policy,
            execution_strategy=(
                "custom"
                if opener is not None
                or sleep is not time.sleep
                or backoff is not None
                or clock is not None
                else "default"
            ),
        ),
        inspection=inspection,
        operational_problems=tuple(problems),
    )
    registry.append(assessment)
    return assessment


def require_comparable(*assessments: FileAssessment | Mapping[str, object]) -> None:
    """Reject in-memory or persisted cohorts with different methodological scope."""
    if len(assessments) < 2:
        return
    scopes = tuple(_comparison_scope(item) for item in assessments)
    if any(_execution_strategy(item) != "default" for item in assessments):
        raise ValueError(
            "assessments using custom retrieval execution strategies are not directly comparable"
        )
    expected = _canonical(scopes[0])
    if any(_canonical(scope) != expected for scope in scopes[1:]):
        raise ValueError(
            "assessments are not directly comparable; publisher type, profile, policies, and "
            "as_of must match"
        )


def _execution_strategy(assessment: FileAssessment | Mapping[str, object]) -> object:
    if isinstance(assessment, FileAssessment):
        return assessment.retrieval_policy.execution_strategy
    record = dict(assessment)
    return _required_mapping(record, "retrieval_policy").get("execution_strategy")


def _comparison_scope(
    assessment: FileAssessment | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(assessment, FileAssessment):
        return assessment.comparison_scope
    if not isinstance(assessment, dict):
        assessment = dict(assessment)
    record = cast(dict[str, object], assessment)
    _verify_persisted_record(record)
    return _required_mapping(record, "comparison_scope")


def _retrievability(
    fetch: FetchOutcome,
    *,
    has_inspection: bool,
    has_operational_problems: bool,
) -> DimensionResult:
    status = fetch.status
    if status in _SUCCESS_STATUSES:
        if has_inspection and not has_operational_problems:
            source = "cached validation" if status is FetchStatus.NOT_MODIFIED else "download"
            return DimensionResult(
                name="retrievability",
                status="OBSERVED",
                note=(
                    f"Verified {source} at {fetch.attempted_at}; final_url="
                    f"{_public_url(fetch.final_url or fetch.url)!r}, "
                    f"sha256={fetch.content_sha256}."
                ),
            )
        return DimensionResult(
            name="retrievability",
            status="NOT_ASSESSED",
            note=(
                "The fetch reported success, but the exact cached body could not be verified and "
                "inspected; local evidence integrity is not attributed to the publisher."
            ),
        )

    if status in _PUBLISHER_FAILURES or _observed_unsafe_redirect(fetch):
        barrier = (
            status is FetchStatus.HTTP_ERROR
            and fetch.http_status in _AUTOMATION_BARRIER_HTTP_STATUSES
        )
        code = "MRF_AUTOMATION_BARRIER_OBSERVED" if barrier else "MRF_DIRECT_DOWNLOAD_FAILED"
        definition = RETRIEVAL_FINDING_CATALOG[code]
        reason = _fetch_reason(fetch)
        finding = Finding(
            code=definition.code,
            dimension=definition.dimension,
            severity=definition.severity,
            message=(
                f"The request observed {status.value!r} at {fetch.attempted_at} after "
                f"{fetch.attempts} attempt(s): {reason}"
            ),
            citations=definition.citations,
        )
        return DimensionResult(
            name="retrievability",
            status="FINDINGS",
            findings=(finding,),
            note="This is a dated technical observation, not a legal compliance determination.",
        )

    if status is FetchStatus.INVALID_URL:
        note = (
            "The URL was invalid before any network attempt; caller-supplied provenance alone is "
            "not enough to attribute that input to the publisher."
        )
    elif status is FetchStatus.TOO_LARGE:
        note = (
            "The configured decoded-byte ceiling stopped retrieval; this project policy does not "
            "establish publisher unavailability."
        )
    else:
        note = (
            f"Local cache or infrastructure status {status.value!r} prevented assessment; it is "
            "not attributed to the publisher."
        )
    return DimensionResult(name="retrievability", status="NOT_ASSESSED", note=note)


def _integrated_scorecard(
    retrievability: DimensionResult, inspection: FileInspection | None
) -> FileScorecard:
    if inspection is None:
        unavailable_note = "No verified local body was available for this retrieval attempt."
        return FileScorecard(
            retrievability=retrievability,
            conformance=DimensionResult("conformance", "NOT_ASSESSED", note=unavailable_note),
            completeness=DimensionResult("completeness", "NOT_ASSESSED", note=unavailable_note),
            interpretability=DimensionResult(
                "interpretability", "NOT_ASSESSED", note=unavailable_note
            ),
            freshness=DimensionResult("freshness", "NOT_ASSESSED", note=unavailable_note),
        )
    local = inspection.scorecard
    return FileScorecard(
        retrievability=retrievability,
        conformance=local.conformance,
        completeness=local.completeness,
        interpretability=local.interpretability,
        freshness=local.freshness,
    )


def _attempt_date(raw: str) -> date:
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        observed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("fetch attempted_at must be an ISO timestamp") from exc
    if observed.tzinfo is None:
        raise ValueError("fetch attempted_at must include a timezone")
    return observed.astimezone(UTC).date()


def _fetch_reason(fetch: FetchOutcome) -> str:
    parts: list[str] = []
    if fetch.http_status is not None:
        parts.append(f"HTTP {fetch.http_status}")
    if fetch.final_url is not None and _public_url(fetch.final_url) != _public_url(fetch.url):
        parts.append(f"final URL {_public_url(fetch.final_url)!r}")
    if fetch.error:
        parts.append(_bounded_problem(fetch.error))
    return "; ".join(parts) if parts else "no verified body was produced"


def _fetch_evidence(fetch: FetchOutcome) -> dict[str, object]:
    evidence = fetch.to_dict()
    # A local cache path is neither portable nor needed to reproduce the scorecard evidence.
    evidence.pop("path", None)
    for key in ("url", "final_url"):
        raw_url = evidence.get(key)
        if isinstance(raw_url, str):
            evidence[key] = _public_url(raw_url)
            evidence[f"{key}_sha256"] = _text_digest(raw_url)
        else:
            evidence[f"{key}_sha256"] = None
    raw_error = evidence.get("error")
    evidence["error"] = _bounded_problem(raw_error) if isinstance(raw_error, str) else None
    for key in ("etag", "last_modified"):
        value = evidence.get(key)
        if isinstance(value, str):
            evidence[key] = _bounded_problem(value)
    return evidence


def _inspection_evidence(inspection: FileInspection | None) -> dict[str, object] | None:
    if inspection is None:
        return None
    evidence: dict[str, object] = dict(inspection.to_dict())
    # Cache roots are machine-local implementation detail, not assessment identity or evidence.
    evidence.pop("source_path", None)
    # The sanitized top-level subject is authoritative and avoids repeating a raw source URL.
    evidence.pop("publisher", None)
    # The top-level scorecard is authoritative after remote/local composition.
    local_scorecard = evidence.pop("scorecard", None)
    if isinstance(local_scorecard, dict):
        evidence["local_dimensions"] = {
            key: value for key, value in local_scorecard.items() if key != "retrievability"
        }
    return evidence


def _finding_dict(finding: Finding) -> dict[str, object]:
    return {
        "citations": list(finding.citations),
        "code": finding.code,
        "dimension": finding.dimension,
        "message": finding.message,
        "occurrences": finding.occurrences,
        "severity": finding.severity,
    }


def _dimension_dict(dimension: DimensionResult) -> dict[str, object]:
    return {
        "findings": [_finding_dict(finding) for finding in dimension.findings],
        "name": dimension.name,
        "note": dimension.note,
        "status": dimension.status,
    }


def _scorecard_dict(scorecard: FileScorecard) -> dict[str, object]:
    return {dimension.name: _dimension_dict(dimension) for dimension in scorecard.dimensions}


def _bounded_problem(problem: str) -> str:
    redacted = _URL_IN_TEXT_RE.sub(lambda match: _public_url(match.group(0)), problem)
    redacted = _LOCAL_PATH_RE.sub("<local-path>", redacted)
    redacted = _WINDOWS_PATH_RE.sub("<local-path>", redacted)
    single_line = " ".join(redacted.splitlines()).strip()
    return single_line[:_PROBLEM_TEXT_LIMIT]


def _public_url(raw: str) -> str:
    redacted = _strip_url_secrets(raw)
    try:
        parsed = urlsplit(redacted)
        public_netloc = parsed.netloc.rsplit("@", 1)[-1]
        public = urlunsplit((parsed.scheme, public_netloc, parsed.path, "", ""))
    except ValueError:
        public = redacted
    return public[:_URL_TEXT_LIMIT]


def _authority_has_userinfo(raw: str) -> bool:
    scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw)
    remainder = raw[scheme.end() :] if scheme is not None else raw
    remainder = remainder.lstrip("/")
    authority = re.split(r"[/\s?#]", remainder, maxsplit=1)[0]
    return "@" in authority


def _strip_url_secrets(raw: str) -> str:
    base = re.split(r"[?#]", raw, maxsplit=1)[0]
    scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", base)
    if scheme is None:
        return base
    prefix = base[: scheme.end()]
    remainder = base[scheme.end() :]
    slash_count = len(remainder) - len(remainder.lstrip("/"))
    prefix += "/" * slash_count
    remainder = remainder[slash_count:]
    authority, separator, suffix = remainder.partition("/")
    public_authority = authority.rsplit("@", 1)[-1]
    return prefix + public_authority + (separator + suffix if separator else "")


def _observed_unsafe_redirect(fetch: FetchOutcome) -> bool:
    return (
        fetch.status is FetchStatus.INVALID_URL
        and fetch.attempts > 0
        and fetch.error is not None
        and fetch.error.startswith("unsafe redirect target:")
    )


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _validate_identifier(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} cannot be empty")
    if len(value) > 300 or "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be a single line of at most 300 characters")


def _hash_path(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_persisted_record(record: dict[str, object]) -> None:
    if record.get("version") != _ASSESSMENT_VERSION:
        raise ValueError("unsupported assessment record version")
    identifier = record.get("assessment_id")
    body_digest = record.get("assessment_body_sha256")
    if not isinstance(identifier, str) or not isinstance(body_digest, str):
        raise ValueError("assessment identity fields must be strings")
    body = {
        key: value
        for key, value in record.items()
        if key not in {"assessment_id", "assessment_body_sha256"}
    }
    calculated = _digest(body)
    if body_digest != calculated:
        raise ValueError("assessment body digest does not match")
    retrieval = record.get("retrieval")
    evidence_digest = record.get("retrieval_evidence_sha256")
    if not isinstance(retrieval, dict) or not isinstance(evidence_digest, str):
        raise ValueError("retrieval evidence fields are malformed")
    if _digest(retrieval) != evidence_digest:
        raise ValueError("retrieval evidence digest does not match")
    _verify_persisted_semantics(record, identifier)


def _verify_persisted_semantics(record: dict[str, object], identifier: str) -> None:
    subject = _required_mapping(record, "subject")
    retrieval = _required_mapping(record, "retrieval")
    policy = _required_mapping(record, "retrieval_policy")
    scope = _required_mapping(record, "comparison_scope")
    _verify_policy_context(record)
    _verify_subject_context(record, subject, retrieval)
    policy_fingerprint = _verify_retrieval_policy(policy)
    _verify_comparison_scope(record, subject, scope, policy_fingerprint)
    _verify_assessment_identity(record, subject, identifier, policy_fingerprint)
    _verify_scorecard_v1(record, subject, retrieval)


def _verify_policy_context(record: Mapping[str, object]) -> None:
    policy_version = record.get("assessment_policy_version")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("assessment policy version must be a non-empty string")
    for key in ("assessment_policy_fingerprint", "inspection_fingerprint"):
        if not _is_digest(record.get(key)):
            raise ValueError(f"{key} must be a SHA-256 digest")
    if record.get("assessment_policy_fingerprint") == ASSESSMENT_POLICY_FINGERPRINT and (
        policy_version != ASSESSMENT_POLICY_VERSION
        or record.get("inspection_fingerprint") != INSPECTION_FINGERPRINT
    ):
        raise ValueError("current assessment policy context does not match its known components")


def _verify_subject_context(
    record: Mapping[str, object],
    subject: Mapping[str, object],
    retrieval: Mapping[str, object],
) -> None:
    if subject.get("requested_url") != retrieval.get("url"):
        raise ValueError("subject and retrieval URLs do not match")
    if subject.get("requested_url_sha256") != retrieval.get("url_sha256"):
        raise ValueError("subject and retrieval URL digests do not match")
    publisher = _required_mapping(subject, "publisher")
    source_url = publisher.get("source_url")
    source_digest = publisher.get("source_url_sha256")
    if source_url is not None and source_url != subject.get("requested_url"):
        raise ValueError("publisher source and requested URLs do not match")
    if source_digest is not None and source_digest != subject.get("requested_url_sha256"):
        raise ValueError("publisher source and requested URL digests do not match")
    if record.get("observed_at") != retrieval.get("attempted_at"):
        raise ValueError("assessment and retrieval timestamps do not match")
    observed_at = retrieval.get("attempted_at")
    if not isinstance(observed_at, str):
        raise ValueError("retrieval attempted_at must be a string")
    if record.get("as_of") != _attempt_date(observed_at).isoformat():
        raise ValueError("assessment as_of does not match the retrieval date")


def _verify_retrieval_policy(policy: Mapping[str, object]) -> str:
    policy_body = {key: value for key, value in policy.items() if key != "fingerprint"}
    policy_fingerprint = policy.get("fingerprint")
    if not isinstance(policy_fingerprint, str) or _digest(policy_body) != policy_fingerprint:
        raise ValueError("retrieval policy fingerprint does not match")
    return policy_fingerprint


def _verify_comparison_scope(
    record: Mapping[str, object],
    subject: Mapping[str, object],
    scope: Mapping[str, object],
    policy_fingerprint: str,
) -> None:
    expected_scope = {
        "as_of": record.get("as_of"),
        "assessment_policy_fingerprint": record.get("assessment_policy_fingerprint"),
        "profile": _PROFILE,
        "publisher_type": subject.get("publisher_type"),
        "retrieval_policy_fingerprint": policy_fingerprint,
        "url_provenance": subject.get("url_provenance"),
    }
    if scope != expected_scope:
        raise ValueError("comparison scope does not match assessment context")


def _verify_assessment_identity(
    record: Mapping[str, object],
    subject: Mapping[str, object],
    identifier: str,
    policy_fingerprint: str,
) -> None:
    inspection = record.get("inspection")
    inspection_sha: object = None
    if inspection is not None:
        if not isinstance(inspection, dict):
            raise ValueError("inspection evidence must be an object or null")
        inspection_sha = inspection.get("source_sha256")
    identity_body = {
        "as_of": record.get("as_of"),
        "assessment_policy_fingerprint": record.get("assessment_policy_fingerprint"),
        "inspection_source_sha256": inspection_sha,
        "observed_at": record.get("observed_at"),
        "retrieval_evidence_sha256": record.get("retrieval_evidence_sha256"),
        "retrieval_policy_fingerprint": policy_fingerprint,
        "subject": _persisted_subject_identity(subject),
        "version": _ASSESSMENT_VERSION,
    }
    if _digest(identity_body) != identifier:
        raise ValueError("assessment identity does not match semantic evidence")


def _persisted_subject_identity(subject: Mapping[str, object]) -> dict[str, object]:
    publisher = _required_mapping(subject, "publisher")
    return {
        "location_id": subject.get("location_id"),
        "publisher_identifier": publisher.get("identifier"),
        "publisher_type": subject.get("publisher_type"),
        "requested_url": subject.get("requested_url"),
        "requested_url_sha256": subject.get("requested_url_sha256"),
        "url_provenance": subject.get("url_provenance"),
    }


def _verify_scorecard_v1(
    record: Mapping[str, object],
    subject: Mapping[str, object],
    retrieval: Mapping[str, object],
) -> None:
    scorecard = _required_mapping(record, "scorecard")
    coverage = _required_mapping(record, "coverage")
    operational = record.get("operational_problems")
    if not isinstance(operational, list) or not all(isinstance(item, str) for item in operational):
        raise ValueError("operational_problems must be an array of strings")
    if any(_bounded_problem(item) != item for item in cast(list[str], operational)):
        raise ValueError("operational problems are not bounded public evidence")

    inspection = record.get("inspection")
    inspection_mapping: dict[str, object] | None = None
    if inspection is not None:
        if not isinstance(inspection, dict):
            raise ValueError("inspection evidence must be an object or null")
        inspection_mapping = cast(dict[str, object], inspection)
        if inspection_mapping.get("as_of") != record.get("as_of"):
            raise ValueError("inspection as_of does not match assessment as_of")
    _verify_retrievability_v1(
        scorecard,
        subject=subject,
        retrieval=retrieval,
        has_inspection=inspection_mapping is not None,
        has_operational_problems=bool(operational),
    )
    _verify_local_dimensions(scorecard, inspection_mapping, retrieval)
    _verify_coverage_v1(coverage, retrieval, inspection_mapping)


def _verify_retrievability_v1(
    scorecard: Mapping[str, object],
    *,
    subject: Mapping[str, object],
    retrieval: Mapping[str, object],
    has_inspection: bool,
    has_operational_problems: bool,
) -> None:
    dimension = _required_mapping(scorecard, "retrievability")
    try:
        fetch = FetchOutcome.from_dict(retrieval)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"retrieval evidence is malformed: {exc}") from exc
    if fetch.attempts < 0:
        raise ValueError("retrieval attempts cannot be negative")
    expected = _dimension_dict(
        _retrievability(
            fetch,
            has_inspection=has_inspection,
            has_operational_problems=has_operational_problems,
        )
    )
    if dimension != expected:
        raise ValueError("retrievability dimension does not match retrieval evidence")
    if subject.get("publisher_type") != PublisherType.HOSPITAL.value:
        raise ValueError("assessment record uses an unsupported publisher profile")


def _verify_local_dimensions(
    scorecard: Mapping[str, object],
    inspection: Mapping[str, object] | None,
    retrieval: Mapping[str, object],
) -> None:
    names = ("conformance", "completeness", "interpretability", "freshness")
    if inspection is None:
        expected = _scorecard_dict(
            _integrated_scorecard(DimensionResult("retrievability", "NOT_ASSESSED"), None)
        )
        if any(_required_mapping(scorecard, name) != expected[name] for name in names):
            raise ValueError("local dimensions require verified inspection evidence")
        return
    if retrieval.get("status") not in {
        FetchStatus.FETCHED.value,
        FetchStatus.NOT_MODIFIED.value,
    }:
        raise ValueError("inspection evidence requires a successful retrieval status")
    local_dimensions = _required_mapping(inspection, "local_dimensions")
    if any(_required_mapping(scorecard, name) != local_dimensions.get(name) for name in names):
        raise ValueError("integrated local dimensions do not match inspection evidence")
    if inspection.get("source_sha256") != retrieval.get("content_sha256") or inspection.get(
        "source_size"
    ) != retrieval.get("size_bytes"):
        raise ValueError("inspection body identity does not match retrieval evidence")


def _verify_coverage_v1(
    coverage: Mapping[str, object],
    retrieval: Mapping[str, object],
    inspection: Mapping[str, object] | None,
) -> None:
    attempts = retrieval.get("attempts")
    if not isinstance(attempts, int):
        raise ValueError("retrieval attempts must be an integer")
    expected = {
        "inspection_performed": inspection is not None,
        "inspection_scan_completed": (
            inspection.get("scan_completed") is True if inspection is not None else False
        ),
        "network_attempted": attempts > 0,
        "targeted": True,
        "verified_body_available": inspection is not None,
    }
    if coverage != expected:
        raise ValueError("coverage flags do not match assessment evidence")


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _required_mapping(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key!r} must be an object")
    return cast(dict[str, object], value)
