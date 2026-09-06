"""Receipts: the procedure behind "every published grade can be re-derived from its source".

The README has said that since the first cohort. It was true and it had no command. A reader
who wanted to check it had to reimplement the grader. This turns the sentence into something a
hospital, a reporter or a regulator can run without trusting this project: the bytes, the
policy, the tool version, and the result, checkable offline.

**A receipt is issued for every published row, and most of them are not re-derivable.** That is
the part worth reading twice. Of the 48 committed rows, seven carry no ``content_sha256`` at
all: four were never retrieved (``NOT_GRADED``) and three stopped mid-stream (``F``). There are
no bytes to hand anybody, so there is nothing for anybody to re-derive. Those receipts are
still written -- a row with no receipt would look like an oversight -- and they say
``re_derivable: false`` with the reason, carry the published grade as a *statement of what was
published* rather than as a claim anyone can check, and :func:`verify` refuses them by name
rather than pretending.

The opposite arrangement is the failure this module exists to avoid: issuing a receipt that
looks like every other receipt, letting ``verify`` run against a file that cannot possibly hash
to the recorded value, and reporting the resulting mismatch as though the hospital's file had
changed.

**Three exit states, and the difference between the last two matters.**

``0``  the grade and every finding reproduced from the bytes.
``1``  the file was read and something differs. The differences are listed.
``2``  the check could not be performed: the hash does not match, the receipt names a policy
       version this build does not have, or the receipt was never re-derivable. None of these
       is evidence about the file's quality, and none of them may be read as one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, cast

from mrf_honest import __version__
from mrf_honest.cohort import grade_local_evidence
from mrf_honest.inspect import FileInspection, Finding, inspect_hospital_file
from mrf_honest.inspect_csv import CsvFileInspection, inspect_hospital_csv_file
from mrf_honest.types import PublisherRef

#: Bumped when the receipt document's shape changes. A verifier that does not know a version
#: refuses rather than reading the fields it recognises out of a document it does not.
RECEIPT_VERSION = 1

#: Reproduced fully.
EXIT_REPRODUCED = 0
#: Read, and something differs.
EXIT_DIFFERS = 1
#: The check could not be performed. Never evidence about the file.
EXIT_CANNOT_CHECK = 2


class _Inspector(Protocol):
    """The shape both profile inspectors share, so the dispatch below stays typed."""

    def __call__(
        self, path: Path, publisher: PublisherRef | None = ..., *, as_of: date
    ) -> FileInspection | CsvFileInspection: ...


#: The two profiles this build can re-inspect under, and the inspector for each. A profile
#: that is not a key is refused, never defaulted: re-inspecting a CSV file under the JSON
#: dictionary would produce a confident disagreement with a correct receipt.
_INSPECTORS: Mapping[str, _Inspector] = {
    "cms-hospital-json-v3": inspect_hospital_file,
    "cms-hospital-csv-v3": inspect_hospital_csv_file,
}

_READ_CHUNK = 1 << 20

#: Printed on every receipt and in the badge's own title. A reproduced grade is a statement
#: about one file on one date under one policy, and nothing else.
RECEIPT_NOTICE = (
    "A grade describes one published file under one stated policy on one date. Reproducing it "
    "confirms that this policy applied to these bytes gives this result. It is not a "
    "certificate of validity and not a finding of compliance by anyone."
)


class ReceiptError(ValueError):
    """Raised when a receipt cannot be built or read honestly."""


def _finding_view(finding: Mapping[str, object]) -> dict[str, object]:
    """One finding, reduced to the fields a re-derivation can actually compare."""
    return {
        "code": str(finding.get("code")),
        "dimension": str(finding.get("dimension")),
        "occurrences": int(cast(int, finding.get("occurrences", 1))),
        "severity": str(finding.get("severity")),
    }


def _findings_of_row(row: Mapping[str, object]) -> list[dict[str, object]]:
    dimensions = row.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise ReceiptError("a published row carries no dimensions block")
    collected: list[dict[str, object]] = []
    for name in sorted(dimensions):
        dimension = dimensions[name]
        if not isinstance(dimension, Mapping):
            continue
        raw = dimension.get("findings")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            continue
        for finding in raw:
            if isinstance(finding, Mapping):
                collected.append(_finding_view(finding))
    return sorted(collected, key=lambda item: (str(item["dimension"]), str(item["code"])))


def _findings_of_inspection(findings: Sequence[Finding]) -> list[dict[str, object]]:
    return sorted(
        (
            {
                "code": finding.code,
                "dimension": finding.dimension,
                "occurrences": finding.occurrences,
                "severity": finding.severity,
            }
            for finding in findings
        ),
        key=lambda item: (str(item["dimension"]), str(item["code"])),
    )


def _not_re_derivable_reason(row: Mapping[str, object]) -> str | None:
    """Why this row's grade cannot be re-derived from bytes, or ``None`` when it can.

    Presence of ``content_sha256`` is the necessary condition: with no recorded hash there is
    nothing to check a file against, and handing somebody a receipt they cannot use would be
    worse than handing them none. ``inspection_scan_completed`` is the sufficient one: a grade
    minted from a stream that stopped early came from the *retrieval*, not from a document, and
    re-inspecting a complete local file would legitimately disagree with it.
    """
    coverage = row.get("coverage")
    coverage_map = coverage if isinstance(coverage, Mapping) else {}
    if not row.get("content_sha256"):
        if coverage_map.get("network_attempted") is not True:
            return "the file was never retrieved, so no bytes were ever graded"
        return "the retrieval produced no verified body, so no bytes were ever graded"
    if coverage_map.get("inspection_scan_completed") is not True:
        return (
            "the document could not be streamed to completion, so this grade describes the "
            "retrieval rather than a document a reader could re-inspect"
        )
    return None


@dataclass(frozen=True)
class Receipt:
    """One published row, reduced to what somebody else needs to check it."""

    receipt_version: int
    assessment_id: str
    slug: str
    publisher_id: str
    publisher_name: str
    location_id: str
    requested_url: str
    content_sha256: str | None
    size_bytes: int | None
    as_of: str
    profile: str
    grade: str
    grade_reason: str
    grade_policy_version: str
    grade_policy_fingerprint: str
    tool_version: str
    findings: tuple[Mapping[str, object], ...]
    re_derivable: bool
    not_re_derivable_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of,
            "assessment_id": self.assessment_id,
            "content_sha256": self.content_sha256,
            "findings": [dict(finding) for finding in self.findings],
            "grade": self.grade,
            "grade_policy_fingerprint": self.grade_policy_fingerprint,
            "grade_policy_version": self.grade_policy_version,
            "grade_reason": self.grade_reason,
            "location_id": self.location_id,
            "not_re_derivable_reason": self.not_re_derivable_reason,
            "notice": RECEIPT_NOTICE,
            "profile": self.profile,
            "publisher_id": self.publisher_id,
            "publisher_name": self.publisher_name,
            "re_derivable": self.re_derivable,
            "receipt_version": self.receipt_version,
            "requested_url": self.requested_url,
            "size_bytes": self.size_bytes,
            "slug": self.slug,
            "tool_version": self.tool_version,
        }


def receipt_from_row(
    row: Mapping[str, object], comparison: Mapping[str, object], *, tool_version: str = __version__
) -> Receipt:
    """Build the receipt for one published file row of one comparison document."""
    cohort = comparison.get("cohort")
    if not isinstance(cohort, Mapping):
        raise ReceiptError("comparison document carries no cohort block")
    scope = cohort.get("comparison_scope")
    if not isinstance(scope, Mapping):
        raise ReceiptError("cohort carries no comparison scope")
    grade = row.get("grade")
    if not isinstance(grade, Mapping):
        raise ReceiptError("a published row carries no grade block")
    reason = _not_re_derivable_reason(row)
    size = row.get("size_bytes")
    return Receipt(
        receipt_version=RECEIPT_VERSION,
        assessment_id=str(row.get("assessment_id")),
        slug=str(row.get("slug")),
        publisher_id=str(row.get("publisher_id")),
        publisher_name=str(row.get("publisher_name")),
        location_id=str(row.get("location_id")),
        requested_url=str(row.get("requested_url")),
        content_sha256=(
            str(row["content_sha256"]) if row.get("content_sha256") is not None else None
        ),
        size_bytes=int(size) if isinstance(size, int) and not isinstance(size, bool) else None,
        as_of=str(row.get("as_of")),
        profile=str(scope.get("profile")),
        grade=str(grade.get("grade")),
        grade_reason=str(grade.get("reason")),
        grade_policy_version=str(grade.get("policy_version")),
        grade_policy_fingerprint=str(grade.get("policy_fingerprint")),
        tool_version=tool_version,
        findings=tuple(_findings_of_row(row)),
        re_derivable=reason is None,
        not_re_derivable_reason=reason,
    )


def receipts_for(
    comparisons: Sequence[Mapping[str, object]], *, tool_version: str = __version__
) -> dict[str, Receipt]:
    """Every published row's receipt, keyed by slug."""
    receipts: dict[str, Receipt] = {}
    for comparison in comparisons:
        rows = comparison.get("files")
        if not isinstance(rows, Sequence):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            receipt = receipt_from_row(row, comparison, tool_version=tool_version)
            if receipt.slug in receipts:
                # `render_site` refuses the same collision for file pages. A silent overwrite
                # here would publish one cohort's receipt at the other cohort's URL, so a
                # reader following the link from a 2026-08-14 page would re-derive against a
                # 2026-08-19 hash and be told the hospital's file had changed.
                raise ReceiptError(
                    f"file slug {receipt.slug!r} appears in more than one comparison; every "
                    "receipt needs exactly one source row"
                )
            receipts[receipt.slug] = receipt
    return receipts


def hash_file(path: Path) -> tuple[str, int]:
    """SHA-256 and byte count of a local file, streamed so memory stays bounded."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class VerifyOutcome:
    """The result of re-deriving one receipt against one local file."""

    exit_code: int
    summary: str
    differences: tuple[str, ...] = ()
    observed_sha256: str | None = None
    observed_grade: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "differences": list(self.differences),
            "exit_code": self.exit_code,
            "notice": RECEIPT_NOTICE,
            "observed_grade": self.observed_grade,
            "observed_sha256": self.observed_sha256,
            "summary": self.summary,
        }


def _preflight(receipt: Mapping[str, object], path: Path) -> VerifyOutcome | None:
    """Every reason this check cannot be performed at all, or ``None`` to go ahead.

    Kept apart from the comparison below because these are a different kind of answer. None of
    them says anything about the file's quality, and folding any of them into "the grade
    differs" would publish a claim about a hospital's file out of a failure to look at it.
    """
    version = receipt.get("receipt_version")
    if version != RECEIPT_VERSION:
        return VerifyOutcome(
            EXIT_CANNOT_CHECK,
            f"this build reads receipt_version {RECEIPT_VERSION}; the receipt declares {version!r}",
        )
    if receipt.get("re_derivable") is not True:
        stated = receipt.get("not_re_derivable_reason") or "no reason was recorded"
        return VerifyOutcome(
            EXIT_CANNOT_CHECK,
            f"this receipt was issued as not re-derivable: {stated}. The grade it records is "
            "what was published, not a claim anybody can reproduce from bytes.",
        )
    if str(receipt.get("profile")) not in _INSPECTORS:
        return VerifyOutcome(
            EXIT_CANNOT_CHECK,
            f"this build has no inspector for profile {str(receipt.get('profile'))!r}",
        )
    if not path.is_file():
        return VerifyOutcome(EXIT_CANNOT_CHECK, f"{path} is not a readable file")
    return None


def _finding_differences(
    receipt: Mapping[str, object], derived: Sequence[Mapping[str, object]]
) -> list[str]:
    raw = receipt.get("findings")
    recorded = sorted(
        (
            _finding_view(finding)
            for finding in (raw if isinstance(raw, Sequence) else ())
            if isinstance(finding, Mapping)
        ),
        key=lambda item: (str(item["dimension"]), str(item["code"])),
    )
    differences = [
        f"finding in the receipt but not re-derived: {item['code']}"
        for item in recorded
        if item not in derived
    ]
    differences.extend(
        f"finding re-derived but not in the receipt: {item['code']}"
        for item in derived
        if item not in recorded
    )
    return differences


def verify_receipt(receipt: Mapping[str, object], path: Path) -> VerifyOutcome:
    """Re-derive one receipt's grade and findings from a local file. Opens no socket."""
    refusal = _preflight(receipt, path)
    if refusal is not None:
        return refusal
    profile = str(receipt.get("profile"))
    observed_sha, observed_size = hash_file(path)
    recorded_sha = str(receipt.get("content_sha256"))
    if observed_sha != recorded_sha:
        return VerifyOutcome(
            EXIT_CANNOT_CHECK,
            "these are not the bytes the receipt describes, so nothing was re-derived",
            differences=(
                f"receipt content_sha256: {recorded_sha}",
                f"observed content_sha256: {observed_sha}",
            ),
            observed_sha256=observed_sha,
        )

    inspection = _INSPECTORS[profile](
        path, None, as_of=date.fromisoformat(str(receipt.get("as_of")))
    )
    scorecard = inspection.to_dict().get("scorecard")
    if not isinstance(scorecard, Mapping):  # pragma: no cover - dataclass shape guarantees it
        raise ReceiptError("inspection did not serialize a scorecard")
    grade = grade_local_evidence(scorecard, profile=profile)
    recorded_version = str(receipt.get("grade_policy_version"))
    if grade.policy_version != recorded_version:
        # The *policy* moved, not the file. Deliberately code 2: this build cannot speak to a
        # grade minted under a policy it no longer has, and reporting that as a difference
        # would blame a hospital's file for a change in this repository.
        return VerifyOutcome(
            EXIT_CANNOT_CHECK,
            f"the receipt names grade policy {recorded_version!r}; this build has "
            f"{grade.policy_version!r}, so the two grades are not comparable",
            observed_sha256=observed_sha,
        )

    derived = _findings_of_inspection(inspection.findings)
    differences: list[str] = []
    recorded_grade = str(receipt.get("grade"))
    if grade.grade != recorded_grade:
        differences.append(f"grade: receipt {recorded_grade}, re-derived {grade.grade}")
    recorded_size = receipt.get("size_bytes")
    if isinstance(recorded_size, int) and recorded_size != observed_size:
        differences.append(f"size_bytes: receipt {recorded_size}, observed {observed_size}")
    differences.extend(_finding_differences(receipt, derived))

    if differences:
        return VerifyOutcome(
            EXIT_DIFFERS,
            "the bytes match, and the re-derivation does not agree with the receipt",
            differences=tuple(differences),
            observed_sha256=observed_sha,
            observed_grade=grade.grade,
        )
    return VerifyOutcome(
        EXIT_REPRODUCED,
        f"grade {grade.grade} and all {len(derived)} finding(s) reproduced under "
        f"{grade.policy_version}",
        observed_sha256=observed_sha,
        observed_grade=grade.grade,
    )


def encode(document: Mapping[str, object]) -> str:
    """One encoder for a published receipt, so its bytes are reproducible."""
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
