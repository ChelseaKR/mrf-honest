"""Longitudinal diff tests.

The fixtures are deep copies of a *committed* comparison document rather than hand-written
objects, so a shape change in ``mrf_honest.cohort`` cannot leave this module testing a document
the project no longer publishes.

The behaviour worth reading twice is the negative space: what this module refuses to compare.
Every assertion about a "policy changed" layer is an assertion that a change this repository
made to itself was not published as a change a hospital made to its file.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest

import mrf_honest.cli as cli
from mrf_honest.cohort import COMPARISON_VERSION, NOT_GRADED
from mrf_honest.diff import (
    COMPARED,
    EXIT_CANNOT_COMPARE,
    EXIT_NO_REGRESSION,
    EXIT_REGRESSED,
    GRADE_ORDER,
    NOT_COMPARABLE,
    POLICY_CHANGED,
    READABLE_COMPARISON_VERSIONS,
    DiffError,
    compare_cohorts,
    exit_code,
    human_report,
)

ROOT = Path(__file__).resolve().parent.parent
COHORTS = ROOT / "data" / "cohorts"
JSON_2026_08_14 = COHORTS / "2026-08-14.comparison.json"
JSON_2026_08_19 = COHORTS / "2026-08-19.comparison.json"
CSV_2026_08_19 = COHORTS / "2026-08-19-csv.comparison.json"


def _document(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _one_subject(path: Path = JSON_2026_08_19) -> dict[str, object]:
    """A committed cohort reduced to its first row, so a fixture has exactly one subject."""
    document = _document(path)
    files = cast(list[dict[str, object]], document["files"])
    document["files"] = [copy.deepcopy(files[0])]
    return document


def _row(document: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], document["files"])[0]


def _subject(result: dict[str, object], index: int = 0) -> dict[str, object]:
    return cast(list[dict[str, object]], result["subjects"])[index]


def _later(document: dict[str, object], *, as_of: str = "2026-09-01") -> dict[str, object]:
    """The same cohort re-dated, as a second run of an unchanged policy would produce."""
    after = copy.deepcopy(document)
    cohort = cast(dict[str, object], after["cohort"])
    cohort["cohort_id"] = f"{cohort['cohort_id']}-again"
    cohort["as_of"] = as_of
    cast(dict[str, object], cohort["comparison_scope"])["as_of"] = as_of
    _row(after)["as_of"] = as_of
    return after


# --- the version gate -----------------------------------------------------------------------


def test_the_reader_reads_exactly_up_to_the_version_this_build_writes() -> None:
    """The readable set is a literal, so bumping the cohort document has to come past here.

    The upper-bound assertion is the load-bearing half. Membership alone is satisfied by a
    derived bound such as ``frozenset(range(1, 99))``, which would accept a future document
    shape and read the fields it recognises out of a document it does not understand -- measured
    as a silent no-op when this test asserted membership only.
    """
    assert COMPARISON_VERSION in READABLE_COMPARISON_VERSIONS
    assert max(READABLE_COMPARISON_VERSIONS) == COMPARISON_VERSION


def test_an_unknown_document_version_is_refused_rather_than_partially_read() -> None:
    before = _one_subject()
    after = _later(before)
    # A literal, not ``max(READABLE_COMPARISON_VERSIONS) + 1``: a bound derived from the thing
    # under test moves with the sabotage and cannot catch it.
    after["comparison_version"] = 99
    with pytest.raises(DiffError, match="comparison_version"):
        compare_cohorts(before, after)


# --- what is refused outright ---------------------------------------------------------------


def test_two_profiles_are_refused_rather_than_diffed_as_a_policy_change() -> None:
    with pytest.raises(DiffError, match="differ in profile"):
        compare_cohorts(_document(JSON_2026_08_19), _document(CSV_2026_08_19))


def test_a_slug_in_neither_cohort_is_refused() -> None:
    before = _one_subject()
    with pytest.raises(DiffError, match="appears in neither cohort"):
        compare_cohorts(before, _later(before), slug="nowhere/at-all")


def test_a_duplicated_slug_is_refused_rather_than_silently_overwritten() -> None:
    before = _one_subject()
    cast(list[object], before["files"]).append(copy.deepcopy(_row(before)))
    with pytest.raises(DiffError, match="more than once"):
        compare_cohorts(before, _later(_one_subject()))


# --- the issue's own acceptance criteria ----------------------------------------------------


def test_two_records_differing_only_in_last_updated_on_diff_to_exactly_that_line() -> None:
    """The first "done when" of #68, and the one a blended diff would fail."""
    before = _one_subject()
    after = _later(before)
    _row(after)["last_updated_on"] = "2026-08-01"

    result = compare_cohorts(before, after)
    subject = _subject(result)

    assert subject["changed"] is True
    assert cast(dict[str, object], subject["document"])["changes"] == [
        {
            "field": "last_updated_on",
            "before": _row(before)["last_updated_on"],
            "after": "2026-08-01",
        }
    ]
    assert cast(dict[str, object], subject["retrieval"])["changes"] == []
    judgement = cast(dict[str, object], subject["judgement"])
    assert judgement["findings"] == []
    assert cast(dict[str, object], judgement["grade"])["direction"] == "unchanged"
    assert subject["regression"] is False


def test_a_moved_assessment_policy_is_a_policy_change_and_no_finding_comparison() -> None:
    """The second "done when": the finding list is not compared at all, not compared and equal."""
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], cast(dict[str, object], after["cohort"])["comparison_scope"])[
        "assessment_policy_fingerprint"
    ] = "0" * 64
    # A finding that really did appear, so an unguarded comparison would have something to say.
    dimensions = cast(dict[str, object], _row(after)["dimensions"])
    conformance = cast(dict[str, object], dimensions["conformance"])
    cast(list[object], conformance["findings"]).append(
        {
            "code": "invented_for_this_test",
            "dimension": "conformance",
            "severity": "error",
            "occurrences": 3,
        }
    )

    result = compare_cohorts(before, after)
    layers = cast(dict[str, object], result["layers"])
    assert cast(dict[str, object], layers["judgement"])["state"] == POLICY_CHANGED
    assert cast(dict[str, object], layers["retrieval"])["state"] == COMPARED

    judgement = cast(dict[str, object], _subject(result)["judgement"])
    assert judgement["state"] == POLICY_CHANGED
    assert judgement["findings"] == []
    assert cast(dict[str, object], judgement["grade"])["direction"] == "not_compared"
    assert _subject(result)["regression"] is None
    assert "policy" in human_report(result)


def test_a_moved_grade_policy_alone_also_stops_the_finding_comparison() -> None:
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], cast(dict[str, object], after["cohort"])["grade_policy"])[
        "fingerprint"
    ] = "1" * 64
    result = compare_cohorts(before, after)
    assert cast(dict[str, object], _subject(result)["judgement"])["state"] == POLICY_CHANGED
    assert cast(dict[str, object], _subject(result)["document"])["state"] == COMPARED


def test_a_moved_inspection_fingerprint_stops_only_the_document_layer() -> None:
    """A changed extractor can move a version string the publisher never touched."""
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], after["cohort"])["inspection_fingerprint"] = "2" * 64
    _row(after)["template_version"] = "9.9.9"

    result = compare_cohorts(before, after)
    subject = _subject(result)
    assert cast(dict[str, object], subject["document"])["state"] == POLICY_CHANGED
    assert cast(dict[str, object], subject["document"])["changes"] == []
    assert cast(dict[str, object], subject["retrieval"])["state"] == COMPARED


def test_a_moved_retrieval_policy_stops_only_the_byte_layer() -> None:
    """A body truncated by a lower ceiling and a body a hospital shortened hash differently."""
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], cast(dict[str, object], after["cohort"])["comparison_scope"])[
        "retrieval_policy_fingerprint"
    ] = "3" * 64
    _row(after)["size_bytes"] = 17

    result = compare_cohorts(before, after)
    subject = _subject(result)
    assert cast(dict[str, object], subject["retrieval"])["state"] == POLICY_CHANGED
    assert cast(dict[str, object], subject["retrieval"])["changes"] == []
    assert cast(dict[str, object], subject["judgement"])["state"] == COMPARED


def test_a_changed_file_reports_its_bytes_and_its_findings() -> None:
    before = _one_subject()
    after = _later(before)
    _row(after)["content_sha256"] = "4" * 64
    _row(after)["size_bytes"] = cast(int, _row(before)["size_bytes"]) + 1
    dimensions = cast(dict[str, object], _row(after)["dimensions"])
    completeness = cast(dict[str, object], dimensions["completeness"])
    cast(list[object], completeness["findings"]).append(
        {
            "code": "a_new_warning",
            "dimension": "completeness",
            "severity": "warning",
            "occurrences": 2,
        }
    )

    subject = _subject(compare_cohorts(before, after))
    fields = {
        str(cast(dict[str, object], change)["field"])
        for change in cast(list[object], cast(dict[str, object], subject["retrieval"])["changes"])
    }
    assert fields == {"content_sha256", "size_bytes"}
    judgement = cast(dict[str, object], subject["judgement"])
    findings = cast(list[dict[str, object]], judgement["findings"])
    assert [(item["change"], item["code"]) for item in findings] == [("appeared", "a_new_warning")]
    assert subject["regression"] is False


# --- absence is never rendered as a value ---------------------------------------------------


def test_a_subject_in_one_cohort_only_is_stated_and_never_compared() -> None:
    before = _one_subject()
    after = _later(_document(JSON_2026_08_19))

    result = compare_cohorts(before, after)
    only_after = [
        item
        for item in cast(list[dict[str, object]], result["subjects"])
        if item["presence"] == "only_in_after"
    ]
    assert only_after, "the fixture must contain a subject the before cohort does not have"
    for item in only_after:
        assert item["changed"] is False
        assert item["regression"] is None
        assert "retrieval" not in item
        assert "judgement" not in item
    summary = cast(dict[str, object], result["summary"])
    assert summary["subjects_in_both"] == 1
    assert summary["only_in_after"] == len(only_after)
    assert "nothing compared" in human_report(result)


def test_a_move_between_a_letter_and_not_graded_leaves_the_regression_undetermined() -> None:
    """``NOT_GRADED`` is a limit of this tool. Scoring it as a worse letter would publish that
    limit as the hospital's failure."""
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], _row(after)["grade"])["grade"] = NOT_GRADED

    subject = _subject(compare_cohorts(before, after))
    grade = cast(dict[str, object], cast(dict[str, object], subject["judgement"])["grade"])
    assert grade["direction"] == "grading_state_changed"
    assert grade["regression"] is None
    assert subject["regression"] is None
    assert subject["changed"] is True


def test_not_graded_is_not_a_position_on_the_letter_scale() -> None:
    assert NOT_GRADED not in GRADE_ORDER


def test_a_different_url_for_one_location_stops_every_layer() -> None:
    """Two different files are not one file that changed."""
    before = _one_subject()
    after = _later(before)
    _row(after)["requested_url"] = "https://elsewhere.example.test/other.json"
    _row(after)["requested_url_sha256"] = "5" * 64
    _row(after)["content_sha256"] = "6" * 64

    subject = _subject(compare_cohorts(before, after))
    assert cast(dict[str, object], subject["subject_url"])["changed"] is True
    for layer in ("retrieval", "document", "judgement"):
        assert cast(dict[str, object], subject[layer])["state"] == NOT_COMPARABLE
        assert cast(dict[str, object], subject[layer])["changes"] == []
    assert subject["regression"] is None
    assert subject["changed"] is True
    assert "a different URL was graded" in human_report(compare_cohorts(before, after))


# --- regression detection and its third state -----------------------------------------------


def test_a_worse_letter_is_a_regression() -> None:
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], _row(before)["grade"])["grade"] = "A"
    cast(dict[str, object], _row(after)["grade"])["grade"] = "C"

    result = compare_cohorts(before, after)
    assert _subject(result)["regression"] is True
    assert cast(dict[str, object], result["summary"])["regressions"] == 1
    assert exit_code(result, fail_on_regression=True) == EXIT_REGRESSED
    assert exit_code(result, fail_on_regression=False) == EXIT_NO_REGRESSION


def test_a_better_letter_is_not_a_regression() -> None:
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], _row(before)["grade"])["grade"] = "D"
    cast(dict[str, object], _row(after)["grade"])["grade"] = "B"

    result = compare_cohorts(before, after)
    assert (
        cast(dict[str, object], cast(dict[str, object], _subject(result)["judgement"])["grade"])[
            "direction"
        ]
        == "better"
    )
    assert _subject(result)["regression"] is False
    assert exit_code(result, fail_on_regression=True) == EXIT_NO_REGRESSION


def test_a_new_error_finding_is_a_regression_even_when_the_letter_holds() -> None:
    """The bands are coarse: a file can acquire an error and keep its letter."""
    before = _one_subject()
    after = _later(before)
    dimensions = cast(dict[str, object], _row(after)["dimensions"])
    conformance = cast(dict[str, object], dimensions["conformance"])
    cast(list[object], conformance["findings"]).append(
        {
            "code": "a_new_error",
            "dimension": "conformance",
            "severity": "error",
            "occurrences": 1,
        }
    )

    result = compare_cohorts(before, after)
    grade = cast(dict[str, object], cast(dict[str, object], _subject(result)["judgement"])["grade"])
    assert grade["direction"] == "unchanged"
    assert _subject(result)["regression"] is True
    assert exit_code(result, fail_on_regression=True) == EXIT_REGRESSED


def test_a_disappearing_error_finding_is_not_a_regression() -> None:
    before = _one_subject()
    dimensions = cast(dict[str, object], _row(before)["dimensions"])
    conformance = cast(dict[str, object], dimensions["conformance"])
    cast(list[object], conformance["findings"]).append(
        {"code": "went_away", "dimension": "conformance", "severity": "error", "occurrences": 4}
    )
    after = _later(_one_subject())

    findings = cast(
        list[dict[str, object]],
        cast(dict[str, object], _subject(compare_cohorts(before, after))["judgement"])["findings"],
    )
    assert [(item["change"], item["code"]) for item in findings] == [("disappeared", "went_away")]
    assert _subject(compare_cohorts(before, after))["regression"] is False


def test_a_changed_occurrence_count_is_reported_without_being_a_regression() -> None:
    before = _one_subject()
    dimensions = cast(dict[str, object], _row(before)["dimensions"])
    conformance = cast(dict[str, object], dimensions["conformance"])
    cast(list[object], conformance["findings"]).append(
        {"code": "recurring", "dimension": "conformance", "severity": "error", "occurrences": 2}
    )
    after = copy.deepcopy(_later(before))
    after_findings = cast(
        list[dict[str, object]],
        cast(dict[str, object], cast(dict[str, object], _row(after)["dimensions"])["conformance"])[
            "findings"
        ],
    )
    after_findings[-1]["occurrences"] = 9

    subject = _subject(compare_cohorts(before, after))
    judgement = cast(dict[str, object], subject["judgement"])
    findings = cast(list[dict[str, object]], judgement["findings"])
    assert findings == [
        {
            "change": "occurrences",
            "code": "recurring",
            "dimension": "conformance",
            "severity": "error",
            "before_occurrences": 2,
            "after_occurrences": 9,
        }
    ]
    assert subject["regression"] is False


def test_fail_on_regression_cannot_pass_when_nothing_could_be_judged() -> None:
    """The gate that must not be a gate that cannot fail.

    Every subject's judgement layer is closed by a policy move, so the flag's question was never
    answered. Returning 0 would report a clean run over zero comparisons.
    """
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], cast(dict[str, object], after["cohort"])["comparison_scope"])[
        "assessment_policy_fingerprint"
    ] = "7" * 64

    result = compare_cohorts(before, after)
    summary = cast(dict[str, object], result["summary"])
    assert summary["judgement_compared"] == 0
    assert summary["judgement_undetermined"] == 1
    assert summary["regressions"] == 0
    assert exit_code(result, fail_on_regression=True) == EXIT_CANNOT_COMPARE
    assert exit_code(result, fail_on_regression=False) == EXIT_NO_REGRESSION


def test_one_judged_subject_beside_undetermined_ones_still_answers_the_question() -> None:
    before = _document(JSON_2026_08_19)
    after = _later(_document(JSON_2026_08_19))
    # One subject graded at a different URL, so its own layers close while the rest stay open.
    cast(list[dict[str, object]], after["files"])[1]["requested_url_sha256"] = "8" * 64

    result = compare_cohorts(before, after)
    summary = cast(dict[str, object], result["summary"])
    assert summary["judgement_undetermined"] == 1
    assert summary["judgement_compared"] == summary["subjects_in_both"] - 1
    assert exit_code(result, fail_on_regression=True) == EXIT_NO_REGRESSION


# --- the committed cohorts ------------------------------------------------------------------


def test_the_two_committed_json_cohorts_report_the_policy_move_they_actually_have() -> None:
    """Real data, and the reason this module exists.

    The six subjects graded in both cohorts are byte-identical in both, and the assessment
    policy moved between them. A diff that reported those six as "unchanged files" would be
    saying something true by accident; a diff that reported them as changed would be saying
    something false. Both are wrong for the same reason, so neither is what is published.
    """
    result = compare_cohorts(_document(JSON_2026_08_14), _document(JSON_2026_08_19))
    layers = cast(dict[str, object], result["layers"])
    assert cast(dict[str, object], layers["judgement"])["state"] == POLICY_CHANGED
    assert cast(dict[str, object], layers["retrieval"])["state"] == COMPARED
    assert cast(dict[str, object], layers["document"])["state"] == COMPARED

    summary = cast(dict[str, object], result["summary"])
    assert summary["subjects_in_both"] == 6
    assert summary["judgement_compared"] == 0
    assert summary["changed"] == 0
    assert exit_code(result, fail_on_regression=True) == EXIT_CANNOT_COMPARE

    for item in cast(list[dict[str, object]], result["subjects"]):
        if item["presence"] != "both":
            continue
        assert cast(dict[str, object], item["retrieval"])["changes"] == []
        assert cast(dict[str, object], item["judgement"])["findings"] == []


def test_a_cohort_diffed_against_itself_compares_everything_and_finds_nothing() -> None:
    """The control: identical inputs must exercise the comparing path, not the refusing one."""
    result = compare_cohorts(_document(JSON_2026_08_19), _document(JSON_2026_08_19))
    summary = cast(dict[str, object], result["summary"])
    assert summary["changed"] == 0
    assert summary["judgement_undetermined"] == 0
    assert summary["judgement_compared"] == summary["subjects_in_both"] > 0
    assert exit_code(result, fail_on_regression=True) == EXIT_NO_REGRESSION


def test_restricting_to_one_slug_diffs_only_that_subject() -> None:
    slug = "uc-health/west-chester-hospital"
    result = compare_cohorts(_document(JSON_2026_08_14), _document(JSON_2026_08_19), slug=slug)
    subjects = cast(list[dict[str, object]], result["subjects"])
    assert [item["slug"] for item in subjects] == [slug]


# --- the command line -----------------------------------------------------------------------


def _write(tmp_path: Path, name: str, document: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_cli_prints_a_human_report_and_succeeds_without_the_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = cli.main(["diff", str(JSON_2026_08_14), str(JSON_2026_08_19)])
    assert status == EXIT_NO_REGRESSION
    out = capsys.readouterr().out
    assert "policy changed, not compared" in out
    assert "6 subject(s) in both cohorts" in out


def test_the_cli_emits_a_canonical_json_document(capsys: pytest.CaptureFixture[str]) -> None:
    status = cli.main(["diff", str(JSON_2026_08_14), str(JSON_2026_08_19), "--format", "json"])
    assert status == EXIT_NO_REGRESSION
    document = json.loads(capsys.readouterr().out)
    assert document["diff_version"] == 1
    assert document["summary"]["subjects_in_both"] == 6


def test_the_cli_returns_the_regression_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _one_subject()
    after = _later(before)
    cast(dict[str, object], _row(before)["grade"])["grade"] = "A"
    cast(dict[str, object], _row(after)["grade"])["grade"] = "F"
    status = cli.main(
        [
            "diff",
            str(_write(tmp_path, "before.json", before)),
            str(_write(tmp_path, "after.json", after)),
            "--fail-on-regression",
        ]
    )
    assert status == EXIT_REGRESSED
    assert "worse" in capsys.readouterr().out


def test_the_cli_reports_a_refusal_as_the_third_state_and_never_as_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = cli.main(["diff", str(JSON_2026_08_19), str(CSV_2026_08_19)])
    assert status == EXIT_CANNOT_COMPARE
    assert "differ in profile" in capsys.readouterr().err
