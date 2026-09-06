"""Compare two published cohorts of the same subjects, without blending two kinds of change.

Each cohort is a dated snapshot. Grading the same URL again produces a second record and, until
now, no relation between the two, so the most important question a reader has -- *is this file
getting better?* -- had no answer in this project's own data.

The whole difficulty is that two records can differ for two completely different reasons, and
saying so wrongly is worse than saying nothing:

* the hospital's file changed, or
* this repository's policy changed.

A tool that reported the second as the first would accuse a publisher of a change this project
made to itself. ``docs/how-we-compare.md`` already forbids that at the cohort level; this module
enforces it record by record, by refusing to compare anything whose governing fingerprint moved.

**Three fingerprints, three independently gated layers.** The comparison is not one yes/no. A
cohort's identity carries three separate fingerprints, each governing a different claim, and each
can move on its own:

``retrieval_policy_fingerprint``
    governs how bytes were obtained -- ceilings, redirect rules, revalidation. The ``retrieval``
    layer (``content_sha256``, ``size_bytes``, retrieval coverage) is compared only when it
    matches, because a body truncated by a lower size ceiling and a body a hospital shortened
    hash differently for opposite reasons.

``inspection_fingerprint``
    governs how a document was read. The ``document`` layer (``template_version``,
    ``last_updated_on``) is compared only when it matches, because a changed extractor can move
    a version string that the publisher never touched.

``assessment_policy_fingerprint`` **and** the presentation grade's ``policy_fingerprint``
    govern what the evidence was judged to mean. The ``judgement`` layer (the grade and the
    finding list) is compared only when *both* match.

A layer whose fingerprint moved is reported as ``policy_changed`` with the two fingerprints. It
is never reported as an unchanged layer, and never as a changed file.

**Two further absences that must not be rendered as values.**

A subject present in only one of the two documents is stated as exactly that. It is never a
disappearance, an addition, or a phantom row: these cohorts are drawn samples, and a facility
this project did not draw twice has told us nothing about itself.

``NOT_GRADED`` is not a position on the A-to-F scale. A subject that moved between a letter and
``NOT_GRADED`` has its grade change stated, and its ``regression`` left undetermined, because
``NOT_GRADED`` records a limit of *this* tool -- an unretrievable file, a project ceiling, local
cache trouble -- and scoring it as a worse grade would publish this project's failure as the
hospital's.

**Exit codes**, matching :mod:`mrf_honest.receipt`, and only under ``--fail-on-regression``:

``0``  at least one subject's judgement layer was comparable, and none of those regressed.
``1``  a subject regressed: a worse letter, or a new error-severity finding.
``2``  the question could not be answered -- no subject had a comparable judgement layer. A
       gate that returned ``0`` there would be a gate that cannot fail.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from mrf_honest.cohort import NOT_GRADED
from mrf_honest.receipt import Receipt, receipt_from_row

#: Bumped when this document's shape changes.
DIFF_VERSION = 1

#: Comparison-document versions whose fields this reader has actually been checked against.
#:
#: Deliberately a literal set and not ``range(1, COMPARISON_VERSION + 1)``. A derived bound would
#: quietly accept a future document shape and read the fields it recognises out of a document it
#: does not understand, which is the failure this project keeps finding elsewhere. A test asserts
#: that the current ``COMPARISON_VERSION`` is a member, so bumping the cohort document forces
#: somebody to look at this reader instead of discovering the mismatch in published output.
READABLE_COMPARISON_VERSIONS = frozenset({1, 2, 3})

#: Compared, and nothing moved.
EXIT_NO_REGRESSION = 0
#: A subject regressed.
EXIT_REGRESSED = 1
#: The question could not be answered.
EXIT_CANNOT_COMPARE = 2

#: The presentation grades, worst last. ``NOT_GRADED`` is deliberately absent: it is not a
#: position on this scale, and putting it at either end would make a limit of this tool read as
#: a judgement about a file.
GRADE_ORDER = ("A", "B", "C", "D", "F")

#: A layer both documents governed identically, so its fields were compared.
COMPARED = "compared"
#: A layer whose governing fingerprint moved. Its fields were not compared.
POLICY_CHANGED = "policy_changed"
#: A layer that could not be compared for a reason belonging to the rows, not the policy.
NOT_COMPARABLE = "not_comparable"

#: Printed on every diff document. A diff is a statement about two dated snapshots.
DIFF_NOTICE = (
    "A diff compares two dated snapshots of the same published file. A layer whose governing "
    "policy fingerprint moved is reported as a policy change and is not compared, because a "
    "change this project made to itself is not a change a hospital made to its file. A subject "
    "present in only one cohort is stated as that and nothing more."
)


class DiffError(ValueError):
    """Raised when two documents cannot honestly be compared at all."""


def _mapping(document: Mapping[str, object], key: str, *, where: str) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, Mapping):
        raise DiffError(f"{where} carries no {key!r} block")
    return value


def _rows(document: Mapping[str, object], *, where: str) -> dict[str, Mapping[str, object]]:
    files = document.get("files")
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
        raise DiffError(f"{where} carries no files list")
    rows: dict[str, Mapping[str, object]] = {}
    for row in files:
        if not isinstance(row, Mapping):
            raise DiffError(f"{where} carries a file entry that is not an object")
        slug = str(row.get("slug"))
        if slug in rows:
            raise DiffError(f"{where} carries file slug {slug!r} more than once")
        rows[slug] = row
    return rows


def _identity(document: Mapping[str, object], *, where: str) -> dict[str, object]:
    """The cohort facts a diff is allowed to reason about, checked on the way out."""
    version = document.get("comparison_version")
    if version not in READABLE_COMPARISON_VERSIONS:
        raise DiffError(
            f"{where} declares comparison_version {version!r}; this build reads "
            f"{sorted(READABLE_COMPARISON_VERSIONS)}"
        )
    cohort = _mapping(document, "cohort", where=where)
    scope = _mapping(cohort, "comparison_scope", where=f"{where} cohort")
    grade_policy = _mapping(cohort, "grade_policy", where=f"{where} cohort")
    return {
        "cohort_id": str(cohort.get("cohort_id")),
        "as_of": str(cohort.get("as_of")),
        "comparison_version": version,
        "profile": str(scope.get("profile")),
        "publisher_type": str(scope.get("publisher_type")),
        "url_provenance": str(scope.get("url_provenance")),
        "assessment_policy_fingerprint": str(scope.get("assessment_policy_fingerprint")),
        "retrieval_policy_fingerprint": str(scope.get("retrieval_policy_fingerprint")),
        "inspection_fingerprint": str(cohort.get("inspection_fingerprint")),
        "grade_policy_version": str(grade_policy.get("version")),
        "grade_policy_fingerprint": str(grade_policy.get("fingerprint")),
    }


#: Which identity field gates which layer. One place, so a new layer cannot be added without
#: naming the fingerprint that governs it.
_LAYER_GATES: Mapping[str, tuple[str, ...]] = {
    "retrieval": ("retrieval_policy_fingerprint",),
    "document": ("inspection_fingerprint",),
    "judgement": ("assessment_policy_fingerprint", "grade_policy_fingerprint"),
}


def _layer_states(
    before: Mapping[str, object], after: Mapping[str, object]
) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    for layer, gates in _LAYER_GATES.items():
        moved = [field for field in gates if before[field] != after[field]]
        states[layer] = {
            "state": POLICY_CHANGED if moved else COMPARED,
            "governed_by": list(gates),
            "moved": [
                {"field": field, "before": before[field], "after": after[field]} for field in moved
            ],
        }
    return states


def _field_changes(
    before: Mapping[str, object], after: Mapping[str, object], fields: Sequence[str]
) -> list[dict[str, object]]:
    return [
        {"field": field, "before": before.get(field), "after": after.get(field)}
        for field in fields
        if before.get(field) != after.get(field)
    ]


def _coverage_changes(
    before: Mapping[str, object], after: Mapping[str, object]
) -> list[dict[str, object]]:
    before_coverage = before.get("coverage")
    after_coverage = after.get("coverage")
    if not isinstance(before_coverage, Mapping) or not isinstance(after_coverage, Mapping):
        return []
    keys = sorted(set(before_coverage) | set(after_coverage))
    return [
        {
            "field": f"coverage.{key}",
            "before": before_coverage.get(key),
            "after": after_coverage.get(key),
        }
        for key in keys
        if before_coverage.get(key) != after_coverage.get(key)
    ]


def _finding_key(finding: Mapping[str, object]) -> tuple[str, str]:
    """A finding's identity: its dimension and code.

    The issue asks for identity ``(subject, finding code, location)``. The subject is the row a
    finding was read from and the location is that subject's ``location_id``, so within one
    subject's comparison the remaining identity is the code, kept with its dimension so a code
    that moved dimension is two findings rather than one silently reinterpreted.
    """
    return str(finding.get("dimension")), str(finding.get("code"))


def _finding_changes(before: Receipt, after: Receipt) -> list[dict[str, object]]:
    before_by_key = {_finding_key(item): item for item in before.findings}
    after_by_key = {_finding_key(item): item for item in after.findings}
    changes: list[dict[str, object]] = []
    for key in sorted(set(before_by_key) | set(after_by_key)):
        dimension, code = key
        was = before_by_key.get(key)
        now = after_by_key.get(key)
        if was is None:
            changes.append(
                {
                    "change": "appeared",
                    "code": code,
                    "dimension": dimension,
                    "severity": str(cast(Mapping[str, object], now).get("severity")),
                    "before_occurrences": None,
                    "after_occurrences": cast(Mapping[str, object], now).get("occurrences"),
                }
            )
        elif now is None:
            changes.append(
                {
                    "change": "disappeared",
                    "code": code,
                    "dimension": dimension,
                    "severity": str(was.get("severity")),
                    "before_occurrences": was.get("occurrences"),
                    "after_occurrences": None,
                }
            )
        elif was.get("occurrences") != now.get("occurrences"):
            changes.append(
                {
                    "change": "occurrences",
                    "code": code,
                    "dimension": dimension,
                    "severity": str(now.get("severity")),
                    "before_occurrences": was.get("occurrences"),
                    "after_occurrences": now.get("occurrences"),
                }
            )
    return changes


def _grade_move(before: str, after: str) -> dict[str, object]:
    """How the letter moved, and whether that is a regression this tool may assert.

    ``NOT_GRADED`` on either side leaves ``regression`` undetermined rather than false. It is not
    a better grade and it is not a worse one; it is the absence of a grade, and both of the
    convenient answers would publish something this project cannot support.
    """
    if before == after:
        return {"direction": "unchanged", "regression": False}
    if before == NOT_GRADED or after == NOT_GRADED:
        return {"direction": "grading_state_changed", "regression": None}
    if before not in GRADE_ORDER or after not in GRADE_ORDER:
        return {"direction": "unknown_letter", "regression": None}
    worse = GRADE_ORDER.index(after) > GRADE_ORDER.index(before)
    return {"direction": "worse" if worse else "better", "regression": worse}


def _subject_diff(
    slug: str,
    before_row: Mapping[str, object],
    after_row: Mapping[str, object],
    before_document: Mapping[str, object],
    after_document: Mapping[str, object],
    layers: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    entry: dict[str, object] = {
        "slug": slug,
        "presence": "both",
        "publisher_id": after_row.get("publisher_id"),
        "publisher_name": after_row.get("publisher_name"),
        "location_id": after_row.get("location_id"),
    }

    # A different URL was graded for the same location. Nothing below this line would mean what
    # it says: comparing the bytes of two different files as though one had changed into the
    # other is the exact conflation this module exists to prevent.
    url_changed = before_row.get("requested_url_sha256") != after_row.get("requested_url_sha256")
    entry["subject_url"] = {
        "changed": url_changed,
        "before": before_row.get("requested_url"),
        "after": after_row.get("requested_url"),
    }

    def _layer(name: str, changes: list[dict[str, object]]) -> dict[str, object]:
        if url_changed:
            return {
                "state": NOT_COMPARABLE,
                "reason": "a different URL was graded for this location, so the two records "
                "do not describe the same published file",
                "changes": [],
            }
        if layers[name]["state"] != COMPARED:
            return {
                "state": POLICY_CHANGED,
                "reason": "the policy governing this layer moved between the two cohorts",
                "changes": [],
            }
        return {"state": COMPARED, "reason": None, "changes": changes}

    entry["retrieval"] = _layer(
        "retrieval",
        _field_changes(before_row, after_row, ("content_sha256", "size_bytes"))
        + _coverage_changes(before_row, after_row),
    )
    entry["document"] = _layer(
        "document", _field_changes(before_row, after_row, ("template_version", "last_updated_on"))
    )

    judgement: dict[str, object]
    if url_changed or layers["judgement"]["state"] != COMPARED:
        judgement = _layer("judgement", [])
        judgement["grade"] = {
            "before": cast(Mapping[str, object], before_row["grade"]).get("grade"),
            "after": cast(Mapping[str, object], after_row["grade"]).get("grade"),
            "direction": "not_compared",
            "regression": None,
        }
        judgement["findings"] = []
    else:
        before_receipt = receipt_from_row(before_row, before_document)
        after_receipt = receipt_from_row(after_row, after_document)
        move = _grade_move(before_receipt.grade, after_receipt.grade)
        findings = _finding_changes(before_receipt, after_receipt)
        judgement = {
            "state": COMPARED,
            "reason": None,
            "changes": [],
            "grade": {
                "before": before_receipt.grade,
                "after": after_receipt.grade,
                "direction": move["direction"],
                "regression": move["regression"],
            },
            "findings": findings,
        }
    entry["judgement"] = judgement

    entry["regression"] = _subject_regression(judgement)
    entry["changed"] = bool(
        cast(Sequence[object], cast(Mapping[str, object], entry["retrieval"])["changes"])
        or cast(Sequence[object], cast(Mapping[str, object], entry["document"])["changes"])
        or cast(Sequence[object], judgement["findings"])
        or cast(Mapping[str, object], judgement["grade"])["direction"]
        not in ("unchanged", "not_compared")
        or url_changed
    )
    return entry


def _subject_regression(judgement: Mapping[str, object]) -> bool | None:
    """Whether this subject regressed, or ``None`` when this build may not say.

    A new *error*-severity finding is a regression on its own, because the grade bands are
    coarse: a file can acquire an error and keep its letter.
    """
    if judgement["state"] != COMPARED:
        return None
    grade = cast(Mapping[str, object], judgement["grade"])
    if grade["regression"] is None:
        return None
    new_error = any(
        cast(Mapping[str, object], finding)["change"] == "appeared"
        and cast(Mapping[str, object], finding)["severity"] == "error"
        for finding in cast(Sequence[object], judgement["findings"])
    )
    return bool(grade["regression"]) or new_error


def _absent_subject(
    slug: str, row: Mapping[str, object], presence: str, cohort_id: str
) -> dict[str, object]:
    """One subject present in only one of the two cohorts.

    Deliberately carries no comparison at all. Both cohorts here are drawn samples; a facility
    drawn once has said nothing about whether its file changed, and an "added"/"removed" reading
    would turn this project's sampling into a claim about a hospital.
    """
    return {
        "slug": slug,
        "presence": presence,
        "publisher_id": row.get("publisher_id"),
        "publisher_name": row.get("publisher_name"),
        "location_id": row.get("location_id"),
        "grade": cast(Mapping[str, object], row["grade"]).get("grade"),
        "cohort_id": cohort_id,
        "note": (
            "this subject appears in one of the two cohorts only; nothing is compared for it, "
            "and its absence from the other cohort is not evidence about its file"
        ),
        "changed": False,
        "regression": None,
    }


def _summary(subjects: Sequence[Mapping[str, object]]) -> dict[str, object]:
    both = [item for item in subjects if item["presence"] == "both"]
    judged = [item for item in both if item["regression"] is not None]
    return {
        "subjects_in_both": len(both),
        "only_in_before": sum(1 for item in subjects if item["presence"] == "only_in_before"),
        "only_in_after": sum(1 for item in subjects if item["presence"] == "only_in_after"),
        "changed": sum(1 for item in both if item["changed"]),
        "unchanged": sum(1 for item in both if not item["changed"]),
        "judgement_compared": len(judged),
        "judgement_undetermined": len(both) - len(judged),
        "regressions": sum(1 for item in judged if item["regression"]),
    }


def compare_cohorts(
    before: Mapping[str, object],
    after: Mapping[str, object],
    *,
    slug: str | None = None,
) -> dict[str, object]:
    """Diff two published comparison documents, subject by subject.

    ``before`` and ``after`` are cohort comparison documents as ``mrf-honest compare`` writes
    them. Two cohorts of different profiles are refused outright rather than diffed under a
    "policy changed" heading: a JSON grade and a CSV grade are measurements of different file
    formats, and the finding catalogues do not even share codes.
    """
    before_identity = _identity(before, where="the before document")
    after_identity = _identity(after, where="the after document")
    for field in ("profile", "publisher_type", "url_provenance"):
        if before_identity[field] != after_identity[field]:
            raise DiffError(
                f"these cohorts differ in {field} ({before_identity[field]!r} then "
                f"{after_identity[field]!r}); they are not two snapshots of one measurement"
            )
    layers = _layer_states(before_identity, after_identity)

    before_rows = _rows(before, where="the before document")
    after_rows = _rows(after, where="the after document")
    if slug is not None:
        if slug not in before_rows and slug not in after_rows:
            raise DiffError(f"file slug {slug!r} appears in neither cohort")
        before_rows = {key: row for key, row in before_rows.items() if key == slug}
        after_rows = {key: row for key, row in after_rows.items() if key == slug}

    subjects: list[dict[str, object]] = []
    for key in sorted(set(before_rows) | set(after_rows)):
        if key in before_rows and key in after_rows:
            subjects.append(
                _subject_diff(key, before_rows[key], after_rows[key], before, after, layers)
            )
        elif key in before_rows:
            subjects.append(
                _absent_subject(
                    key,
                    before_rows[key],
                    "only_in_before",
                    str(before_identity["cohort_id"]),
                )
            )
        else:
            subjects.append(
                _absent_subject(
                    key, after_rows[key], "only_in_after", str(after_identity["cohort_id"])
                )
            )

    return {
        "diff_version": DIFF_VERSION,
        "notice": DIFF_NOTICE,
        "before": before_identity,
        "after": after_identity,
        "layers": layers,
        "subjects": subjects,
        "summary": _summary(subjects),
    }


def exit_code(document: Mapping[str, object], *, fail_on_regression: bool) -> int:
    """The process status for one diff document.

    Without ``--fail-on-regression`` a diff is a report and always succeeds. With it, the third
    state is the one that matters: if no subject's judgement layer was comparable, the flag's
    question was not answered, and returning ``0`` would be a gate that cannot fail.
    """
    if not fail_on_regression:
        return EXIT_NO_REGRESSION
    summary = cast(Mapping[str, object], document["summary"])
    if not summary["judgement_compared"]:
        return EXIT_CANNOT_COMPARE
    return EXIT_REGRESSED if summary["regressions"] else EXIT_NO_REGRESSION


def _layer_line(name: str, state: Mapping[str, object]) -> str:
    if state["state"] == COMPARED:
        return f"  {name}: compared"
    moved = ", ".join(
        f"{cast(Mapping[str, object], item)['field']} "
        f"{str(cast(Mapping[str, object], item)['before'])[:12]}"
        f" -> {str(cast(Mapping[str, object], item)['after'])[:12]}"
        for item in cast(Sequence[object], state["moved"])
    )
    return f"  {name}: policy changed, not compared ({moved})"


def _change_lines(prefix: str, layer: Mapping[str, object]) -> list[str]:
    if layer["state"] != COMPARED:
        return [f"    {prefix}: {layer['reason']}"]
    changes = cast(Sequence[object], layer["changes"])
    if not changes:
        return []
    lines = []
    for change in changes:
        item = cast(Mapping[str, object], change)
        lines.append(f"    {prefix} {item['field']}: {item['before']} -> {item['after']}")
    return lines


def human_report(document: Mapping[str, object]) -> str:
    """A deterministic plain-text report of one diff document."""
    before = cast(Mapping[str, object], document["before"])
    after = cast(Mapping[str, object], document["after"])
    summary = cast(Mapping[str, object], document["summary"])
    lines = [
        f"{before['cohort_id']} ({before['as_of']}) -> {after['cohort_id']} ({after['as_of']})",
        f"profile {after['profile']}",
    ]
    layers = cast(Mapping[str, object], document["layers"])
    for name in sorted(layers):
        lines.append(_layer_line(name, cast(Mapping[str, object], layers[name])))
    lines.append("")
    for entry in cast(Sequence[object], document["subjects"]):
        subject = cast(Mapping[str, object], entry)
        if subject["presence"] != "both":
            where = "before" if subject["presence"] == "only_in_before" else "after"
            lines.append(f"{subject['slug']}: in the {where} cohort only; nothing compared")
            continue
        partial = any(
            cast(Mapping[str, object], subject[layer])["state"] != COMPARED
            for layer in _LAYER_GATES
        )
        if subject["changed"]:
            head = "changed"
        elif partial:
            # "no change" over a set of layers that were not all compared would read as a
            # verdict on the whole record, which is the one thing this module must not say.
            head = "no change in the layers that were comparable"
        else:
            head = "no change"
        lines.append(f"{subject['slug']}: {head}")
        url = cast(Mapping[str, object], subject["subject_url"])
        if url["changed"]:
            lines.append(f"    a different URL was graded: {url['before']} -> {url['after']}")
        lines.extend(_change_lines("retrieval", cast(Mapping[str, object], subject["retrieval"])))
        lines.extend(_change_lines("document", cast(Mapping[str, object], subject["document"])))
        judgement = cast(Mapping[str, object], subject["judgement"])
        grade = cast(Mapping[str, object], judgement["grade"])
        if judgement["state"] != COMPARED:
            lines.append(
                f"    judgement: {judgement['reason']} "
                f"(published grade {grade['before']} then {grade['after']})"
            )
        else:
            if grade["direction"] != "unchanged":
                lines.append(
                    f"    grade: {grade['before']} -> {grade['after']} ({grade['direction']})"
                )
            for finding in cast(Sequence[object], judgement["findings"]):
                item = cast(Mapping[str, object], finding)
                lines.append(
                    f"    finding {item['change']}: {item['code']} "
                    f"({item['dimension']}, {item['severity']}) "
                    f"{item['before_occurrences']} -> {item['after_occurrences']}"
                )
    lines.append("")
    lines.append(
        f"{summary['subjects_in_both']} subject(s) in both cohorts: "
        f"{summary['changed']} changed, {summary['unchanged']} unchanged. "
        f"{summary['judgement_compared']} judged, "
        f"{summary['judgement_undetermined']} undetermined, "
        f"{summary['regressions']} regression(s)."
    )
    lines.append(
        f"{summary['only_in_before']} subject(s) only in the before cohort and "
        f"{summary['only_in_after']} only in the after cohort; nothing is compared for those."
    )
    return "\n".join(lines)
