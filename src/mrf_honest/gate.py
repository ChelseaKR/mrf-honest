"""A publisher-side gate: one local file, the committed policy, and a verdict with a reason.

Every finding this project has ever published was visible in the file before it was posted.
The site says so afterwards; this says so beforehand, to the team that can still fix it. It is
the same deterministic inspector under the same fingerprinted policy, so a verdict here is the
verdict the site would reach from the same bytes.

Three deliberate boundaries.

**A refusal is not a grade.** A file the inspector could not stream to completion, a container
holding more than one document, and a byte sequence that is neither JSON nor CSV all exit
:data:`EXIT_NOT_GRADEABLE`. None of them is an ``F``: an ``F`` is a statement about a file that
was read, and this tool has to be able to say "I could not read this" without that sentence
becoming a claim about the publisher.

**Absence is named, never scored.** A ``NOT_ASSESSED`` dimension carries no finding, so no
``--fail-on`` severity can see it. It does lower the grade (the committed rule table counts an
unassessed local dimension exactly like a failed one), so ``--min-grade`` can. The report lists
those dimensions by name in their own field so a reader is never left inferring them from a
letter, and the summary prints them.

**Clearing the gate is not a certificate.** The summary says so in the README's own words. This
project grades files; it does not certify hospitals, and no exit code from here means anyone has
been found compliant with anything.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mrf_honest.cohort import NOT_GRADED, grade_local_evidence
from mrf_honest.container import ArchiveRefused, looks_like_archive, select_member
from mrf_honest.inspect import (
    FileInspection,
    Finding,
    FindingSeverity,
    inspect_hospital_file,
)
from mrf_honest.inspect_csv import CsvFileInspection, inspect_hospital_csv_file
from mrf_honest.types import PublisherRef

#: The gate passed: the file was read, and nothing the caller asked to be stopped by was found.
EXIT_PASS = 0
#: The file was read and graded, and it did not meet a threshold the caller set.
EXIT_GATE_FAILED = 1
#: No grade could be minted. Never conflated with a bad grade -- see the module docstring.
EXIT_NOT_GRADEABLE = 2

#: Presentation grades worst-first. ``--min-grade B`` fails anything below ``B`` in this order.
_GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D", "F")

#: Severities most-severe-first. ``--fail-on warning`` fails on a WARNING *or* an ERROR.
_SEVERITY_ORDER: tuple[FindingSeverity, ...] = ("ERROR", "WARNING", "INFO")

#: CLI spellings for ``--fail-on``. ``never`` is the default so the verb can be used purely as a
#: reporter; it is a distinct value rather than an absent flag, because "no severity gates" is a
#: decision the report should state rather than a silence a reader has to infer.
FAIL_ON_CHOICES: tuple[str, ...] = ("error", "warning", "info", "never")

#: CLI spellings for ``--min-grade``. ``none`` means the letter does not gate.
MIN_GRADE_CHOICES: tuple[str, ...] = ("A", "B", "C", "D", "F", "none")

#: Profile names this verb accepts. ``auto`` reads the leading bytes; see :func:`detect_profile`.
PROFILE_CHOICES: tuple[str, ...] = ("json", "csv", "auto")

_PROFILE_NAMES: Mapping[str, str] = {
    "json": "cms-hospital-json-v3",
    "csv": "cms-hospital-csv-v3",
}

#: The local dimensions a grade is minted from, in the order the report prints them.
_LOCAL_DIMENSIONS: tuple[str, ...] = (
    "conformance",
    "completeness",
    "interpretability",
    "freshness",
)

#: Bytes read to decide whether a document is JSON or CSV. A CMS v3 JSON file is one object, so
#: the first non-whitespace character settles it; a header row never begins with a brace.
_PROBE_BYTES = 4096

_UTF8_BOM = b"\xef\xbb\xbf"

#: Said on every summary, in the README's words. Clearing this gate is not a finding of
#: compliance by anyone, and the tool must not be quotable as though it were.
NON_CERTIFICATION_NOTICE = (
    "This is a deterministic, spec-cited reading of one file on one date under one stated "
    "policy. It is not the official CMS validator, not a certificate of validity, and not a "
    "finding of compliance by anyone."
)


class GateError(ValueError):
    """Raised when the gate was asked for something it cannot answer honestly."""


def detect_profile(path: Path) -> str | None:
    """Return ``"json"`` or ``"csv"`` from the leading bytes, or ``None`` when neither fits.

    ``None`` is a real answer and the caller must not turn it into a default. Guessing ``json``
    for an unreadable head would grade a CSV file against the JSON dictionary and publish the
    resulting nonsense as the file's own defects.
    """
    with path.open("rb") as handle:
        head = handle.read(_PROBE_BYTES)
    if head.startswith(_UTF8_BOM):
        head = head[len(_UTF8_BOM) :]
    stripped = head.lstrip()
    if not stripped:
        return None
    first = stripped[:1]
    if first in (b"{", b"["):
        return "json"
    # A CMS CSV begins with a general-information row: printable text, no brace. Anything that
    # is not text at all (a compressed body, a PDF, an image) is neither profile.
    try:
        text = stripped[:512].decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = stripped[:512].decode("latin-1")
        except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes every byte
            return None
    if "\x00" in text:
        return None
    if not text.strip():  # pragma: no cover - guarded by the lstrip above
        return None
    return "csv"


@dataclass(frozen=True)
class FileVerdict:
    """One file's outcome: what was read, what it scored, and what stopped it."""

    path: str
    profile: str | None
    source_sha256: str | None
    source_size: int | None
    scan_completed: bool
    grade: str | None
    grade_reason: str | None
    grade_policy_version: str | None
    grade_policy_fingerprint: str | None
    findings: tuple[Finding, ...]
    not_assessed_dimensions: tuple[str, ...]
    gradeable: bool
    refusal: str | None
    failures: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        if not self.gradeable:
            return EXIT_NOT_GRADEABLE
        if self.failures:
            return EXIT_GATE_FAILED
        return EXIT_PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "failures": list(self.failures),
            "findings": [
                {
                    "citations": list(finding.citations),
                    "code": finding.code,
                    "dimension": finding.dimension,
                    "message": finding.message,
                    "occurrences": finding.occurrences,
                    "severity": finding.severity,
                }
                for finding in self.findings
            ],
            "grade": self.grade,
            "grade_policy_fingerprint": self.grade_policy_fingerprint,
            "grade_policy_version": self.grade_policy_version,
            "grade_reason": self.grade_reason,
            "gradeable": self.gradeable,
            "not_assessed_dimensions": list(self.not_assessed_dimensions),
            "path": self.path,
            "profile": self.profile,
            "refusal": self.refusal,
            "scan_completed": self.scan_completed,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
        }


@dataclass(frozen=True)
class GateReport:
    """Every file the gate was pointed at, plus the thresholds it was asked to apply."""

    files: tuple[FileVerdict, ...]
    min_grade: str
    fail_on: str
    as_of: date

    @property
    def exit_code(self) -> int:
        """The worst outcome across the files. Not-gradeable outranks a failed threshold."""
        codes = [verdict.exit_code for verdict in self.files]
        if EXIT_NOT_GRADEABLE in codes:
            return EXIT_NOT_GRADEABLE
        if EXIT_GATE_FAILED in codes:
            return EXIT_GATE_FAILED
        return EXIT_PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "exit_code": self.exit_code,
            "fail_on": self.fail_on,
            "files": [verdict.to_dict() for verdict in self.files],
            "min_grade": self.min_grade,
            "notice": NON_CERTIFICATION_NOTICE,
        }


def _severity_at_least(severity: str, threshold: str) -> bool:
    """True when ``severity`` is at least as severe as ``threshold``."""
    upper = threshold.upper()
    if upper not in _SEVERITY_ORDER:  # pragma: no cover - argparse constrains the choices
        raise GateError(f"unknown severity threshold {threshold!r}")
    return _SEVERITY_ORDER.index(severity) <= _SEVERITY_ORDER.index(upper)


def _grade_below(grade: str, minimum: str) -> bool:
    """True when ``grade`` is worse than ``minimum`` in the published A-to-F order."""
    if grade == NOT_GRADED:
        # Unreachable from a local inspection -- retrievability is never assessed here, and the
        # local grader never mints NOT_GRADED -- but stated rather than silently ordered, because
        # sorting a refusal into a letter scale is how a refusal becomes a score.
        raise GateError("a not-graded file has no place in the A-to-F order")
    return _GRADE_ORDER.index(grade) > _GRADE_ORDER.index(minimum)


def _not_assessed(scorecard: Mapping[str, object]) -> tuple[str, ...]:
    names: list[str] = []
    for name in _LOCAL_DIMENSIONS:
        dimension = scorecard.get(name)
        if isinstance(dimension, Mapping) and dimension.get("status") == "NOT_ASSESSED":
            names.append(name)
    return tuple(names)


def _refused(path: Path, reason: str, profile: str | None = None) -> FileVerdict:
    return FileVerdict(
        path=str(path),
        profile=profile,
        source_sha256=None,
        source_size=None,
        scan_completed=False,
        grade=None,
        grade_reason=None,
        grade_policy_version=None,
        grade_policy_fingerprint=None,
        findings=(),
        not_assessed_dimensions=(),
        gradeable=False,
        refusal=reason,
        failures=(),
    )


def evaluate_file(
    path: Path,
    *,
    profile: str,
    as_of: date,
    min_grade: str,
    fail_on: str,
    publisher: PublisherRef | None = None,
) -> FileVerdict:
    """Inspect one local file and apply the caller's thresholds to the result."""
    if profile not in PROFILE_CHOICES:
        raise GateError(f"unknown profile {profile!r}")
    if not path.is_file():
        return _refused(path, f"{path} is not a readable file")
    if looks_like_archive(path):
        # `inspect` lifts the single member out of a container. Here the container is refused
        # outright: a publisher-side gate should say "this archive holds more than one document"
        # rather than silently pick one and grade it as though it were the publication.
        outcome = select_member(path)
        detail = (
            outcome.detail
            if isinstance(outcome, ArchiveRefused)
            else "the file is a container; post the document itself, not an archive of it"
        )
        return _refused(path, detail)

    resolved = detect_profile(path) if profile == "auto" else profile
    if resolved is None:
        return _refused(
            path,
            "the leading bytes are neither a JSON object nor readable text, "
            "so no assessment profile applies",
        )

    inspection = (
        inspect_hospital_csv_file(path, publisher, as_of=as_of)
        if resolved == "csv"
        else inspect_hospital_file(path, publisher, as_of=as_of)
    )
    findings = inspection.findings
    if not inspection.scan_completed:
        verdict = _refused(
            path,
            "the file could not be streamed to completion, so there is nothing to grade; "
            "the recorded problems are below",
            profile=resolved,
        )
        return FileVerdict(
            path=verdict.path,
            profile=resolved,
            source_sha256=inspection.source_sha256,
            source_size=inspection.source_size,
            scan_completed=False,
            grade=None,
            grade_reason=None,
            grade_policy_version=None,
            grade_policy_fingerprint=None,
            findings=findings,
            not_assessed_dimensions=_not_assessed(_scorecard_mapping(inspection)),
            gradeable=False,
            refusal=verdict.refusal,
            failures=(),
        )

    scorecard = _scorecard_mapping(inspection)
    grade = grade_local_evidence(scorecard, profile=_PROFILE_NAMES[resolved])
    failures: list[str] = []
    if min_grade != "none" and _grade_below(grade.grade, min_grade):
        failures.append(
            f"grade {grade.grade} is below the required minimum {min_grade}: {grade.reason}"
        )
    if fail_on != "never":
        breaching = [
            finding for finding in findings if _severity_at_least(finding.severity, fail_on)
        ]
        if breaching:
            codes = ", ".join(sorted({finding.code for finding in breaching}))
            failures.append(f"{len(breaching)} finding(s) at or above {fail_on.upper()}: {codes}")
    return FileVerdict(
        path=str(path),
        profile=resolved,
        source_sha256=inspection.source_sha256,
        source_size=inspection.source_size,
        scan_completed=True,
        grade=grade.grade,
        grade_reason=grade.reason,
        grade_policy_version=grade.policy_version,
        grade_policy_fingerprint=grade.policy_fingerprint,
        findings=findings,
        not_assessed_dimensions=_not_assessed(scorecard),
        gradeable=True,
        refusal=None,
        failures=tuple(failures),
    )


def _scorecard_mapping(inspection: FileInspection | CsvFileInspection) -> Mapping[str, object]:
    """The serialized scorecard, which is the shape ``cohort`` grades committed records from."""
    scorecard = inspection.to_dict().get("scorecard")
    if not isinstance(scorecard, Mapping):  # pragma: no cover - dataclass shape guarantees it
        raise GateError("inspection did not serialize a scorecard")
    return scorecard


def run_gate(
    paths: Sequence[Path],
    *,
    profile: str,
    as_of: date,
    min_grade: str,
    fail_on: str,
    publisher: PublisherRef | None = None,
) -> GateReport:
    """Evaluate every path and collect the verdicts in the order they were given."""
    if not paths:
        raise GateError("no file to inspect")
    verdicts = tuple(
        evaluate_file(
            path,
            profile=profile,
            as_of=as_of,
            min_grade=min_grade,
            fail_on=fail_on,
            publisher=publisher,
        )
        for path in paths
    )
    return GateReport(files=verdicts, min_grade=min_grade, fail_on=fail_on, as_of=as_of)


# --------------------------------------------------------------------------------------------
# Renderings. Pure string builders so the gate's own output is testable without a subprocess.
# --------------------------------------------------------------------------------------------


def _escape_command_data(value: str) -> str:
    """Escape a GitHub workflow-command message body (percent first, then the newlines)."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_command_property(value: str) -> str:
    """Escape a workflow-command *property* value, which also reserves ``:`` and ``,``."""
    return _escape_command_data(value).replace(":", "%3A").replace(",", "%2C")


_ANNOTATION_LEVEL: Mapping[str, str] = {"ERROR": "error", "WARNING": "warning", "INFO": "notice"}


def _finding_message(finding: Finding) -> str:
    occurrences = f" (x{finding.occurrences})" if finding.occurrences > 1 else ""
    citations = "; ".join(finding.citations)
    cited = f" Cited: {citations}" if citations else ""
    return f"{finding.message}{occurrences}{cited}"


def github_annotations(report: GateReport) -> tuple[str, ...]:
    """One GitHub workflow command per finding, plus one per refusal and per failed threshold."""
    lines: list[str] = []
    for verdict in report.files:
        file_property = _escape_command_property(verdict.path)
        if verdict.refusal is not None:
            title = _escape_command_property("mrf-honest: not gradeable")
            lines.append(
                f"::error file={file_property},title={title}::"
                f"{_escape_command_data(verdict.refusal)}"
            )
        for finding in verdict.findings:
            level = _ANNOTATION_LEVEL.get(finding.severity, "notice")
            title = _escape_command_property(f"{finding.code} ({finding.dimension})")
            lines.append(
                f"::{level} file={file_property},title={title}::"
                f"{_escape_command_data(_finding_message(finding))}"
            )
        for failure in verdict.failures:
            title = _escape_command_property("mrf-honest: gate failed")
            lines.append(
                f"::error file={file_property},title={title}::{_escape_command_data(failure)}"
            )
    return tuple(lines)


def _summary_findings(findings: Iterable[Finding]) -> list[str]:
    rows = [
        "| Severity | Code | Dimension | What it says | Cited to |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        occurrences = f" x{finding.occurrences}" if finding.occurrences > 1 else ""
        citations = "<br>".join(finding.citations) or "none recorded"
        rows.append(
            f"| {finding.severity} | `{finding.code}`{occurrences} | {finding.dimension} "
            f"| {finding.message} | {citations} |"
        )
    return rows


def job_summary(report: GateReport) -> str:
    """A plain-language Markdown summary for a job log, in the inspector's own finding order."""
    lines = ["## mrf-honest: hospital price-transparency file check", ""]
    lines.append(
        f"Checked as of **{report.as_of.isoformat()}**. "
        f"Minimum grade: **{report.min_grade}**. Fails on findings at or above: "
        f"**{report.fail_on.upper()}**."
    )
    lines.append("")
    for verdict in report.files:
        lines.append(f"### `{verdict.path}`")
        lines.append("")
        if verdict.refusal is not None:
            lines.append(f"**Not gradeable.** {verdict.refusal}")
            lines.append("")
            lines.append(
                "This is not an F. It means this tool could not read the document, which is a "
                "statement about the read, not about the file's contents."
            )
            lines.append("")
            if verdict.findings:
                lines.extend(_summary_findings(verdict.findings))
                lines.append("")
            continue
        lines.append(
            f"**Grade {verdict.grade}** under policy `{verdict.grade_policy_version}` "
            f"(fingerprint `{verdict.grade_policy_fingerprint}`), profile "
            f"`{verdict.profile}`, SHA-256 `{verdict.source_sha256}`."
        )
        lines.append("")
        lines.append(f"{verdict.grade_reason}.")
        lines.append("")
        if verdict.not_assessed_dimensions:
            named = ", ".join(verdict.not_assessed_dimensions)
            lines.append(
                f"**Not assessed:** {named}. These dimensions produced no finding, so no "
                "`fail-on` severity can see them; they lower the grade, so `min-grade` can. "
                "They are listed here so their absence is never read as a pass."
            )
            lines.append("")
        if verdict.findings:
            lines.extend(_summary_findings(verdict.findings))
        else:
            lines.append("No findings.")
        lines.append("")
        if verdict.failures:
            lines.append("**This file did not clear the gate:**")
            lines.append("")
            for failure in verdict.failures:
                lines.append(f"- {failure}")
            lines.append("")
    lines.append(f"> {NON_CERTIFICATION_NOTICE}")
    lines.append("")
    return "\n".join(lines)


def human_report(report: GateReport) -> str:
    """A terminal rendering for the pre-commit hook and for a person running it by hand."""
    lines: list[str] = []
    for verdict in report.files:
        lines.append(f"file: {verdict.path}")
        if verdict.profile is not None:
            lines.append(f"profile: {verdict.profile}")
        if verdict.refusal is not None:
            lines.append(f"not gradeable: {verdict.refusal}")
        else:
            lines.append(f"grade: {verdict.grade} — {verdict.grade_reason}")
            lines.append(f"policy: {verdict.grade_policy_version}")
        if verdict.not_assessed_dimensions:
            lines.append(f"not assessed: {', '.join(verdict.not_assessed_dimensions)}")
        lines.append("findings:")
        if not verdict.findings:
            lines.append("  none")
        for finding in verdict.findings:
            occurrences = f" x{finding.occurrences}" if finding.occurrences > 1 else ""
            lines.append(f"  [{finding.severity}] {finding.code}{occurrences}: {finding.message}")
            for citation in finding.citations:
                lines.append(f"      cited: {citation}")
        for failure in verdict.failures:
            lines.append(f"gate failed: {failure}")
        lines.append("")
    lines.append(NON_CERTIFICATION_NOTICE)
    return "\n".join(lines)


def report_json(report: GateReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
