"""Regression alerts: say what moved between two dated collections, and what could not be judged.

This module reads the output of :func:`mrf_honest.diff.compare_cohorts` and the two comparison
documents it was computed from. It adds no comparison rule of its own, and it does not decide what
a grade means. It decides one thing the diff deliberately leaves to its caller: which of the
changes are worth interrupting a person for, and which of them must never be called a regression.

**An unreachable server is not a regression.** The cohort grade policy gives a file that could not
be retrieved over several attempts the letter ``F`` with a dated reason. Read as a grade move that
is a regression from ``A`` to ``F``, and a scheduled job that sent it would alarm every time a
publisher's server had a bad night. The alert therefore requires a verified body on **both** sides
before it will call a move a regression or an improvement. A subject whose newer collection has no
verified body is listed under ``no_evidence``, with the reason the grade carries. It is reported
because silence would hide a publisher that stopped being reachable, and it is not scored.

**What it does not claim.** A regression here is a worse letter or a new error-severity finding
between two collections made under the same grading policy, as ``mrf-honest diff`` defines it.
It is not a compliance finding and not a statement about why the file changed. A layer whose
policy fingerprint moved is carried through as the diff reports it (not compared), and the
subjects it affects are listed as undetermined.

Three renderings of one document: JSON (the document itself), Markdown (for an issue comment or a
job summary) and an Atom 1.0 feed (for a reader or a notifier). All three are deterministic: the
same two inputs give the same bytes, so a rerun does not produce a new alert.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from xml.sax.saxutils import escape, quoteattr

from mrf_honest.diff import (
    COMPARED,
    EXIT_CANNOT_COMPARE,
    EXIT_NO_REGRESSION,
    EXIT_REGRESSED,
    compare_cohorts,
)

#: Bumped when this document's shape changes.
ALERT_VERSION = 1

ALERT_NOTICE = (
    "An alert compares two dated collections made under the same grading policy. A regression is "
    "a worse letter or a new error-severity finding, and it is reported only when a verified file "
    "was read on both dates. A file that could not be retrieved is listed as no evidence and is "
    "never scored as a regression or as a pass. None of this is a compliance finding."
)

CATEGORY_REGRESSION = "regression"
CATEGORY_IMPROVEMENT = "improvement"
CATEGORY_NO_EVIDENCE = "no_evidence"
CATEGORY_UNDETERMINED = "undetermined"


def _verified(row: Mapping[str, object] | None) -> bool:
    if row is None:
        return False
    coverage = row.get("coverage")
    return isinstance(coverage, Mapping) and coverage.get("verified_body_available") is True


def _rows(document: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    return {
        str(cast(Mapping[str, object], row).get("slug")): cast(Mapping[str, object], row)
        for row in cast(Sequence[object], document.get("files") or ())
    }


def _grade_reason(row: Mapping[str, object]) -> str:
    grade = row.get("grade")
    reason = cast(Mapping[str, object], grade).get("reason") if isinstance(grade, Mapping) else None
    return str(reason) if reason else "no reason was recorded"


def _letter(row: Mapping[str, object]) -> str:
    grade = row.get("grade")
    letter = cast(Mapping[str, object], grade).get("grade") if isinstance(grade, Mapping) else None
    return str(letter)


def _new_errors(judgement: Mapping[str, object]) -> list[str]:
    findings = cast(Sequence[Mapping[str, object]], judgement.get("findings") or ())
    return sorted(
        str(item["code"])
        for item in findings
        if item.get("change") == "appeared" and item.get("severity") == "error"
    )


def _entry(
    category: str,
    subject: Mapping[str, object],
    before_row: Mapping[str, object],
    after_row: Mapping[str, object],
    *,
    summary: str,
    new_errors: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "category": category,
        "slug": subject["slug"],
        "publisher_id": subject.get("publisher_id"),
        "publisher_name": subject.get("publisher_name"),
        "location_id": subject.get("location_id"),
        "url": after_row.get("requested_url"),
        "grade_before": _letter(before_row),
        "grade_after": _letter(after_row),
        "new_error_findings": list(new_errors),
        "summary": summary,
    }


def _classify(
    subject: Mapping[str, object],
    before_row: Mapping[str, object],
    after_row: Mapping[str, object],
) -> dict[str, object] | None:
    """One subject present in both collections, or ``None`` when nothing needs saying."""
    name = str(subject.get("publisher_name") or subject.get("slug"))
    if not _verified(after_row):
        return _entry(
            CATEGORY_NO_EVIDENCE,
            subject,
            before_row,
            after_row,
            summary=(
                f"No verified file was read for {name} on {after_row.get('as_of')}: "
                f"{_grade_reason(after_row)}"
            ),
        )
    judgement = cast(Mapping[str, object], subject["judgement"])
    if not _verified(before_row) or judgement["state"] != COMPARED:
        reason = (
            "the earlier collection read no verified file, so there is nothing to compare against"
            if not _verified(before_row)
            else str(judgement.get("reason") or "the two records are not comparable")
        )
        return _entry(
            CATEGORY_UNDETERMINED,
            subject,
            before_row,
            after_row,
            summary=f"{name} could not be judged: {reason}.",
        )
    grade = cast(Mapping[str, object], judgement["grade"])
    if subject.get("regression") is True:
        errors = _new_errors(judgement)
        moved = (
            f"moved from {grade['before']} to {grade['after']}"
            if grade["direction"] == "worse"
            else f"kept grade {grade['after']} and gained error finding(s) {', '.join(errors)}"
        )
        return _entry(
            CATEGORY_REGRESSION,
            subject,
            before_row,
            after_row,
            summary=f"{name} {moved}.",
            new_errors=errors,
        )
    if grade["direction"] == "better":
        return _entry(
            CATEGORY_IMPROVEMENT,
            subject,
            before_row,
            after_row,
            summary=f"{name} moved from {grade['before']} to {grade['after']}.",
        )
    if grade["direction"] not in {"unchanged", "worse"}:
        # A move between a letter and NOT_GRADED, or to a letter this build does not know. The
        # diff leaves its regression undetermined and so does this.
        return _entry(
            CATEGORY_UNDETERMINED,
            subject,
            before_row,
            after_row,
            summary=(
                f"{name} moved between grade {grade['before']} and {grade['after']}, and "
                "a move to or from a missing grade is not scored as better or worse."
            ),
        )
    return None


def build_alert(
    before: Mapping[str, object], after: Mapping[str, object], *, slug: str | None = None
) -> dict[str, object]:
    """Diff two comparison documents and sort the result into what to tell a person.

    Raises :class:`mrf_honest.diff.DiffError` when the two documents cannot be compared at all
    (different profiles, an unreadable version): that is the caller's third state, not an
    empty alert.
    """
    diff = compare_cohorts(before, after, slug=slug)
    before_rows, after_rows = _rows(before), _rows(after)
    alerts: list[dict[str, object]] = []
    judged = 0
    unchanged = 0
    for raw in cast(Sequence[object], diff["subjects"]):
        subject = cast(Mapping[str, object], raw)
        if subject["presence"] != "both":
            continue
        key = str(subject["slug"])
        entry = _classify(subject, before_rows[key], after_rows[key])
        if entry is None:
            judged += 1
            unchanged += 1
            continue
        if entry["category"] in {CATEGORY_REGRESSION, CATEGORY_IMPROVEMENT}:
            judged += 1
        alerts.append(entry)

    def of(category: str) -> list[dict[str, object]]:
        return [item for item in alerts if item["category"] == category]

    summary = cast(Mapping[str, object], diff["summary"])
    return {
        "alert_version": ALERT_VERSION,
        "notice": ALERT_NOTICE,
        "before": diff["before"],
        "after": diff["after"],
        "layers": diff["layers"],
        "regressions": of(CATEGORY_REGRESSION),
        "improvements": of(CATEGORY_IMPROVEMENT),
        "no_evidence": of(CATEGORY_NO_EVIDENCE),
        "undetermined": of(CATEGORY_UNDETERMINED),
        "summary": {
            "subjects_in_both": summary["subjects_in_both"],
            "judged": judged,
            "unchanged": unchanged,
            "regressions": len(of(CATEGORY_REGRESSION)),
            "improvements": len(of(CATEGORY_IMPROVEMENT)),
            "no_evidence": len(of(CATEGORY_NO_EVIDENCE)),
            "undetermined": len(of(CATEGORY_UNDETERMINED)),
            "only_in_before": summary["only_in_before"],
            "only_in_after": summary["only_in_after"],
        },
    }


def exit_code(alert: Mapping[str, object], *, fail_on_regression: bool) -> int:
    """``0`` nothing regressed, ``1`` something did, ``2`` no subject could be judged.

    Only under ``fail_on_regression``, and for the reason ``mrf-honest diff`` gives: a gate that
    returns ``0`` when it judged nothing is a gate that cannot fail.
    """
    if not fail_on_regression:
        return EXIT_NO_REGRESSION
    summary = cast(Mapping[str, object], alert["summary"])
    if not summary["judged"]:
        return EXIT_CANNOT_COMPARE
    return EXIT_REGRESSED if summary["regressions"] else EXIT_NO_REGRESSION


_SECTIONS = (
    ("regressions", "Grade regressions"),
    ("improvements", "Improvements"),
    ("no_evidence", "No evidence this collection"),
    ("undetermined", "Could not be judged"),
)


def render_markdown(alert: Mapping[str, object]) -> str:
    """A short Markdown report, suitable for an issue comment or a job summary."""
    before = cast(Mapping[str, object], alert["before"])
    after = cast(Mapping[str, object], alert["after"])
    summary = cast(Mapping[str, object], alert["summary"])
    lines = [
        f"## Collection changes: {before['as_of']} to {after['as_of']}",
        "",
        f"Profile `{after['profile']}`. {summary['judged']} file(s) judged on both dates, "
        f"{summary['regressions']} regression(s), {summary['improvements']} improvement(s), "
        f"{summary['no_evidence']} with no evidence this time, "
        f"{summary['undetermined']} that could not be judged.",
        "",
        f"_{alert['notice']}_",
    ]
    for key, heading in _SECTIONS:
        items = cast(Sequence[Mapping[str, object]], alert[key])
        if not items:
            continue
        lines += ["", f"### {heading}", ""]
        for item in items:
            lines.append(f"- {item['summary']} (`{item['slug']}`)")
    layers = cast(Mapping[str, Mapping[str, object]], alert["layers"])
    moved = sorted(name for name, state in layers.items() if state["state"] != COMPARED)
    if moved:
        lines += [
            "",
            "Layers not compared because a governing policy changed: " + ", ".join(moved) + ".",
        ]
    lines += [
        "",
        f"{summary['only_in_before']} file(s) appear only in the earlier collection and "
        f"{summary['only_in_after']} only in the later one; nothing is compared for those.",
        "",
    ]
    return "\n".join(lines)


_ATOM_TITLES = {
    CATEGORY_REGRESSION: "Grade regression",
    CATEGORY_IMPROVEMENT: "Grade improvement",
    CATEGORY_NO_EVIDENCE: "No evidence this collection",
    CATEGORY_UNDETERMINED: "Could not be judged",
}


def default_feed_id(alert: Mapping[str, object]) -> str:
    """A stable URN for the feed, per profile, so a reader keeps one subscription across runs."""
    profile = str(cast(Mapping[str, object], alert["after"])["profile"])
    return f"urn:mrf-honest:alerts:{profile}"


def render_atom(
    alert: Mapping[str, object], *, feed_id: str | None = None, self_url: str | None = None
) -> str:
    """An Atom 1.0 feed with one entry per alert item.

    Entry ids are derived from the feed id, the file and the later collection, so the same
    alert keeps one id and a reader does not see it twice. ``updated`` is the later
    collection's date, not the time of rendering, which keeps the bytes reproducible.
    """
    after = cast(Mapping[str, object], alert["after"])
    identifier = feed_id or default_feed_id(alert)
    updated = f"{after['as_of']}T00:00:00Z"
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(f'mrf-honest collection changes ({after["profile"]})')}</title>",
        f"  <id>{escape(identifier)}</id>",
        f"  <updated>{updated}</updated>",
        "  <author><name>mrf-honest</name></author>",
        f"  <subtitle>{escape(str(alert['notice']))}</subtitle>",
    ]
    if self_url:
        lines.append(f'  <link rel="self" href={quoteattr(self_url)}/>')
    for key, _heading in _SECTIONS:
        for item in cast(Sequence[Mapping[str, object]], alert[key]):
            category = str(item["category"])
            slug = str(item["slug"]).replace("/", ":")
            entry_id = f"{identifier}:{slug}:{after['cohort_id']}:{category}"
            title = f"{_ATOM_TITLES[category]}: {item.get('publisher_name') or item['slug']}"
            lines += [
                "  <entry>",
                f"    <title>{escape(title)}</title>",
                f"    <id>{escape(entry_id)}</id>",
                f"    <updated>{updated}</updated>",
                f"    <category term={quoteattr(category)}/>",
                f'    <summary type="text">{escape(str(item["summary"]))}</summary>',
                "  </entry>",
            ]
    lines += ["</feed>", ""]
    return "\n".join(lines)
