#!/usr/bin/env python3
"""Re-assess a committed cohort under the current assessment policy, from the verified cache.

A cohort's assessments carry the policy fingerprint they were graded under, and the comparison
refuses rows whose fingerprint is not the current one, so a change to inspection semantics leaves
every committed cohort of that profile un-reproducible until it is re-assessed. This is the
re-assessment, and it is deliberately narrow:

* **It opens no socket.** The retrieval evidence is not re-collected, it is carried over. Each
  row's fetch outcome is rebuilt from the committed record, re-serialized, and required to equal
  the committed evidence byte for byte before anything else happens, so the re-assessed row
  describes the same retrieval on the same date and nothing about how a file was obtained moves.
* **It re-reads the same bytes.** A row that was inspected is inspected again from the verified
  cache, found by its content digest and re-hashed first; a body that is missing, or whose bytes
  no longer hash to the recorded digest, stops the run. Nothing is downloaded to fill a gap.
* **It changes nothing but the policy.** A row that was not inspected (robots.txt, the size
  ceiling, a failed download) is recomposed from the same evidence with no inspection, so it
  stays exactly what it was, under the new fingerprint.

Every row is composed through ``compose_file_assessment`` and written through
``AssessmentRegistry``, the same two paths ``mrf-honest scorecard`` uses, so the output is
validated exactly as a freshly collected row would be.

    python tools/reassess_cohort.py data/cohorts/2026-08-19-csv.assessments.jsonl \\
        --cache-dir data/cache --out /tmp/2026-08-19-csv.assessments.jsonl

Run under the policy a row was already graded with, the output is byte-identical to the input;
that is the check that the rebuild is faithful, and it is how this tool was first exercised.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mrf_honest.fetch import FetchOutcome, FetchStatus  # noqa: E402
from mrf_honest.scorecard import (  # noqa: E402
    ASSESSMENT_PROFILES,
    AssessmentProfile,
    AssessmentRegistry,
    AssessmentSubject,
    FileAssessment,
    InspectionRecord,
    PublisherType,
    RetrievalPolicyEvidence,
    URLProvenance,
    _attempt_date,
    _fetch_evidence,
    compose_file_assessment,
    text_digest,
)
from mrf_honest.types import PublisherRef  # noqa: E402


class ReassessmentError(RuntimeError):
    """A committed row could not be re-assessed without changing more than the policy."""


def _object(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ReassessmentError(f"{where} is not an object")
    return cast(Mapping[str, object], value)


def _text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ReassessmentError(f"{key} is not a string")
    return value


def _optional_text(record: Mapping[str, object], key: str) -> str | None:
    value = record.get(key)
    if value is not None and not isinstance(value, str):
        raise ReassessmentError(f"{key} is neither a string nor null")
    return value


def _optional_int(record: Mapping[str, object], key: str) -> int | None:
    value = record.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ReassessmentError(f"{key} is neither an integer nor null")
    return value


def _cached_urls(cache_dir: Path) -> dict[str, str]:
    """Every exact URL the verified cache recorded, by the digest the assessment rows publish.

    A published row carries a redacted URL (no query, fragment or credentials) beside the digest
    of the exact one, so a URL with a query cannot be rebuilt from the row alone. The cache's own
    metadata recorded the exact request and final URLs when it admitted the body.
    """
    urls: dict[str, str] = {}
    for record in sorted((cache_dir / "metadata").glob("*.json")):
        metadata = json.loads(record.read_text(encoding="utf-8"))
        for key in ("url", "final_url"):
            value = metadata.get(key) if isinstance(metadata, dict) else None
            if isinstance(value, str):
                urls[text_digest(value)] = value
    return urls


def _exact_url(public: str, digest: str | None, what: str, cached: Mapping[str, str]) -> str:
    """The exact URL a row's digest names: the published string when it was not redacted,
    otherwise the one the verified cache recorded. Anything else stops the run."""
    if digest is None or text_digest(public) == digest:
        return public
    exact = cached.get(digest)
    if exact is None:
        raise ReassessmentError(
            f"{what} was redacted when it was published and the cache holds no URL with its digest"
        )
    return exact


def _subject(row: Mapping[str, object], cached: Mapping[str, str]) -> AssessmentSubject:
    subject = _object(row.get("subject"), "subject")
    publisher = _object(subject.get("publisher"), "subject.publisher")
    requested = _exact_url(
        _text(subject, "requested_url"),
        _optional_text(subject, "requested_url_sha256"),
        "requested_url",
        cached,
    )
    source = _optional_text(publisher, "source_url")
    return AssessmentSubject(
        publisher=PublisherRef(
            identifier=_text(publisher, "identifier"),
            name=_optional_text(publisher, "name"),
            source_url=requested if source is not None else None,
        ),
        publisher_type=PublisherType(_text(subject, "publisher_type")),
        location_id=_text(subject, "location_id"),
        requested_url=requested,
        url_provenance=URLProvenance(_text(subject, "url_provenance")),
    )


def _blob(cache_dir: Path, digest: str, size: int | None) -> Path:
    path = cache_dir / "blobs" / digest[:2] / digest
    if not path.is_file():
        raise ReassessmentError(f"the verified body {digest} is not in {cache_dir}")
    hasher = hashlib.sha256()
    length = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            hasher.update(chunk)
            length += len(chunk)
    if (hasher.hexdigest(), length) != (digest, size):
        raise ReassessmentError(f"the cached body {digest} no longer matches its recorded digest")
    return path


def _fetch(
    row: Mapping[str, object], cache_dir: Path, cached: Mapping[str, str], *, inspected: bool
) -> FetchOutcome:
    retrieval = _object(row.get("retrieval"), "retrieval")
    digest = _optional_text(retrieval, "content_sha256")
    size = _optional_int(retrieval, "size_bytes")
    final = _optional_text(retrieval, "final_url")
    attempts = _optional_int(retrieval, "attempts")
    decoded = retrieval.get("decoded_gzip")
    if attempts is None or not isinstance(decoded, bool):
        raise ReassessmentError("retrieval.attempts or retrieval.decoded_gzip is malformed")
    fetch = FetchOutcome(
        url=_exact_url(
            _text(retrieval, "url"),
            _optional_text(retrieval, "url_sha256"),
            "retrieval.url",
            cached,
        ),
        status=FetchStatus(_text(retrieval, "status")),
        attempted_at=_text(retrieval, "attempted_at"),
        attempts=attempts,
        path=_blob(cache_dir, digest, size) if inspected and digest is not None else None,
        content_sha256=digest,
        size_bytes=size,
        wire_size_bytes=_optional_int(retrieval, "wire_size_bytes"),
        etag=_optional_text(retrieval, "etag"),
        last_modified=_optional_text(retrieval, "last_modified"),
        http_status=_optional_int(retrieval, "http_status"),
        final_url=(
            _exact_url(final, _optional_text(retrieval, "final_url_sha256"), "final_url", cached)
            if final is not None
            else None
        ),
        error=_optional_text(retrieval, "error"),
        decoded_gzip=decoded,
        content_type=_optional_text(retrieval, "content_type"),
    )
    if _fetch_evidence(fetch) != dict(retrieval):
        raise ReassessmentError("the rebuilt retrieval evidence differs from the committed record")
    return fetch


def _inspect(
    profile: AssessmentProfile, subject: AssessmentSubject, fetch: FetchOutcome
) -> InspectionRecord:
    if fetch.path is None or fetch.content_sha256 is None or fetch.size_bytes is None:
        raise ReassessmentError("an inspected row has no verified body to re-read")
    record = profile.inspect(fetch.path, subject.publisher, as_of=_attempt_date(fetch.attempted_at))
    if (record.source_sha256, record.source_size) != (fetch.content_sha256, fetch.size_bytes):
        raise ReassessmentError("the re-inspected body is not the body the row recorded")
    return record


def _retrieval_policy(row: Mapping[str, object]) -> RetrievalPolicyEvidence:
    """The committed policy evidence; its constructor re-checks the fingerprint against it."""
    policy = _object(row.get("retrieval_policy"), "retrieval_policy")
    seconds = {key: policy.get(key) for key in ("timeout_seconds", "backoff_seconds")}
    counts = {key: _optional_int(policy, key) for key in ("max_bytes", "retries", "chunk_size")}
    if not all(isinstance(value, int | float) for value in seconds.values()):
        raise ReassessmentError("retrieval_policy carries a malformed duration")
    if None in counts.values():
        raise ReassessmentError("retrieval_policy carries a malformed limit")
    return RetrievalPolicyEvidence(
        user_agent=_text(policy, "user_agent"),
        max_bytes=cast(int, counts["max_bytes"]),
        timeout_seconds=float(cast(float, seconds["timeout_seconds"])),
        retries=cast(int, counts["retries"]),
        backoff_seconds=float(cast(float, seconds["backoff_seconds"])),
        chunk_size=cast(int, counts["chunk_size"]),
        execution_strategy=_text(policy, "execution_strategy"),
        fingerprint=_text(policy, "fingerprint"),
    )


def reassess_row(
    row: Mapping[str, object], cache_dir: Path, cached: Mapping[str, str]
) -> FileAssessment:
    """One committed row, re-composed under the current policy of the profile it names."""
    scope = _object(row.get("comparison_scope"), "comparison_scope")
    profile_name = _text(scope, "profile")
    profile = ASSESSMENT_PROFILES.get(profile_name)
    if profile is None:
        raise ReassessmentError(f"no implemented profile is named {profile_name!r}")
    inspected = row.get("inspection") is not None
    subject = _subject(row, cached)
    fetch = _fetch(row, cache_dir, cached, inspected=inspected)
    problems = row.get("operational_problems")
    if not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
        raise ReassessmentError("operational_problems is not a list of strings")
    assessment = compose_file_assessment(
        subject,
        fetch,
        retrieval_policy=_retrieval_policy(row),
        inspection=_inspect(profile, subject, fetch) if inspected else None,
        operational_problems=tuple(cast(list[str], problems)),
        profile=profile,
    )
    carried = ("retrieval", "retrieval_evidence_sha256", "subject", "retrieval_policy")
    rebuilt = assessment.to_dict()
    for key in carried:
        if rebuilt[key] != row.get(key):
            raise ReassessmentError(f"re-assessment moved {key}, which only a retrieval can move")
    return assessment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("assessments", type=Path, help="a committed <cohort>.assessments.jsonl")
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="a path that does not exist yet")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"{args.out} exists; this writes a new file and never appends to one")
    registry = AssessmentRegistry(args.out)
    cached = _cached_urls(args.cache_dir)
    rows = [
        json.loads(line)
        for line in args.assessments.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for number, row in enumerate(rows, start=1):
        try:
            assessment = reassess_row(_object(row, f"line {number}"), args.cache_dir, cached)
        except (ReassessmentError, ValueError) as exc:
            print(f"{args.assessments}:{number}: {exc}", file=sys.stderr)
            return 1
        registry.append(assessment)
        unchanged = assessment.to_dict() == row
        print(
            f"{number:>3} {assessment.subject.location_id}: "
            f"{'unchanged' if unchanged else 'reassessed'} "
            f"({assessment.profile.policy_version}, {assessment.profile.policy_fingerprint[:12]})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
