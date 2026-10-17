"""Regression alerts: what is reported, and what is refused.

As in ``test_diff.py``, the fixtures are deep copies of a *committed* comparison document, so a
shape change in ``mrf_honest.cohort`` cannot leave this module testing a document the project no
longer publishes.

The behavior worth reading twice is the negative space. A file that could not be retrieved is
never a regression, because the cohort grade policy gives it a letter and a scheduled job that
alarmed on every bad night at a publisher's server would teach its readers to ignore it.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

import pytest

import mrf_honest.cli as cli
from mrf_honest.alert import (
    ALERT_VERSION,
    CATEGORY_NO_EVIDENCE,
    CATEGORY_UNDETERMINED,
    build_alert,
    default_feed_id,
    exit_code,
    render_atom,
    render_markdown,
)
from mrf_honest.cohort import NOT_GRADED
from mrf_honest.diff import (
    EXIT_CANNOT_COMPARE,
    EXIT_NO_REGRESSION,
    EXIT_REGRESSED,
    DiffError,
)

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "data" / "cohorts" / "2026-09-12.comparison.json"
COMMITTED_CSV = ROOT / "data" / "cohorts" / "2026-09-12-csv.comparison.json"
ATOM = "{http://www.w3.org/2005/Atom}"


def _document(path: Path = COMMITTED) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _verified_row(document: dict[str, object]) -> dict[str, object]:
    for row in cast(list[dict[str, object]], document["files"]):
        coverage = cast(dict[str, object], row["coverage"])
        if coverage["verified_body_available"] is True:
            return row
    raise AssertionError("the committed cohort has no verified row to build a fixture from")


def _one_subject() -> dict[str, object]:
    document = _document()
    document["files"] = [copy.deepcopy(_verified_row(document))]
    return document


def _row(document: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], document["files"])[0]


def _grade(document: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], _row(document)["grade"])


def _later(document: dict[str, object], *, as_of: str = "2026-10-12") -> dict[str, object]:
    after = copy.deepcopy(document)
    cohort = cast(dict[str, object], after["cohort"])
    cohort["cohort_id"] = f"{cohort['cohort_id']}-again"
    cohort["as_of"] = as_of
    cast(dict[str, object], cohort["comparison_scope"])["as_of"] = as_of
    _row(after)["as_of"] = as_of
    return after


def _pair(before_letter: str, after_letter: str) -> tuple[dict[str, object], dict[str, object]]:
    before = _one_subject()
    after = _later(before)
    _grade(before)["grade"] = before_letter
    _grade(after)["grade"] = after_letter
    return before, after


def _unverify(document: dict[str, object]) -> None:
    cast(dict[str, object], _row(document)["coverage"])["verified_body_available"] = False


# --- what is reported -----------------------------------------------------------------------


def test_a_worse_letter_on_two_verified_files_is_the_only_thing_called_a_regression() -> None:
    before, after = _pair("A", "C")
    alert = build_alert(before, after)

    assert alert["alert_version"] == ALERT_VERSION
    [regression] = cast(list[dict[str, object]], alert["regressions"])
    assert (regression["grade_before"], regression["grade_after"]) == ("A", "C")
    assert "moved from A to C" in str(regression["summary"])
    summary = cast(dict[str, object], alert["summary"])
    assert (summary["regressions"], summary["improvements"], summary["judged"]) == (1, 0, 1)
    assert exit_code(alert, fail_on_regression=True) == EXIT_REGRESSED
    assert exit_code(alert, fail_on_regression=False) == EXIT_NO_REGRESSION


def test_a_better_letter_is_an_improvement_and_does_not_fail_a_gate() -> None:
    before, after = _pair("D", "B")
    alert = build_alert(before, after)

    assert len(cast(list[object], alert["improvements"])) == 1
    assert alert["regressions"] == []
    assert exit_code(alert, fail_on_regression=True) == EXIT_NO_REGRESSION


def test_a_new_error_finding_is_a_regression_when_the_letter_holds() -> None:
    before = _one_subject()
    after = _later(before)
    dimensions = cast(dict[str, object], _row(after)["dimensions"])
    conformance = cast(dict[str, object], dimensions["conformance"])
    cast(list[object], conformance["findings"]).append(
        {"code": "a_new_error", "dimension": "conformance", "severity": "error", "occurrences": 1}
    )

    [regression] = cast(list[dict[str, object]], build_alert(before, after)["regressions"])
    assert regression["new_error_findings"] == ["a_new_error"]
    assert "gained error finding(s) a_new_error" in str(regression["summary"])


def test_an_unchanged_pair_says_nothing_and_still_counts_as_judged() -> None:
    before, after = _pair("B", "B")
    alert = build_alert(before, after)

    for key in ("regressions", "improvements", "no_evidence", "undetermined"):
        assert alert[key] == []
    summary = cast(dict[str, object], alert["summary"])
    assert (summary["judged"], summary["unchanged"]) == (1, 1)
    assert exit_code(alert, fail_on_regression=True) == EXIT_NO_REGRESSION


# --- what is refused ------------------------------------------------------------------------


def test_an_unreachable_file_is_no_evidence_even_when_the_policy_gave_it_an_f() -> None:
    """The control for the whole module: the cohort policy grades a repeatedly unreachable file
    F, the diff reads A to F as a regression, and the alert must not."""
    before, after = _pair("A", "F")
    _unverify(after)
    cast(dict[str, object], _row(after)["grade"])["reason"] = "the server answered HTTP 503"

    # The diff alone would call this a regression. That is what makes the assertion below mean
    # something: it removes a regression the underlying comparison does report.
    from mrf_honest.diff import compare_cohorts

    assert compare_cohorts(before, after)["summary"]["regressions"] == 1  # type: ignore[index]

    alert = build_alert(before, after)
    assert alert["regressions"] == []
    [item] = cast(list[dict[str, object]], alert["no_evidence"])
    assert item["category"] == CATEGORY_NO_EVIDENCE
    assert "the server answered HTTP 503" in str(item["summary"])
    assert exit_code(alert, fail_on_regression=True) == EXIT_CANNOT_COMPARE


def test_a_file_with_no_verified_body_in_the_earlier_collection_cannot_regress() -> None:
    before, after = _pair("F", "A")
    _unverify(before)

    alert = build_alert(before, after)
    assert alert["improvements"] == []
    [item] = cast(list[dict[str, object]], alert["undetermined"])
    assert item["category"] == CATEGORY_UNDETERMINED
    assert "no verified file" in str(item["summary"])


def test_a_move_to_not_graded_is_not_a_regression() -> None:
    before, after = _pair("A", NOT_GRADED)
    alert = build_alert(before, after)
    assert alert["regressions"] == []
    assert len(cast(list[object], alert["undetermined"])) == 1


def test_a_different_url_is_undetermined_and_not_a_change_in_one_file() -> None:
    before, after = _pair("A", "C")
    _row(after)["requested_url"] = "https://elsewhere.example.test/other.json"
    _row(after)["requested_url_sha256"] = "5" * 64

    alert = build_alert(before, after)
    assert alert["regressions"] == []
    assert len(cast(list[object], alert["undetermined"])) == 1


def test_subjects_in_only_one_collection_are_counted_and_never_alerted() -> None:
    before, after = _pair("A", "A")
    _row(after)["slug"] = "someone-else/another-file"

    alert = build_alert(before, after)
    summary = cast(dict[str, object], alert["summary"])
    assert (summary["only_in_before"], summary["only_in_after"]) == (1, 1)
    assert summary["subjects_in_both"] == 0
    assert exit_code(alert, fail_on_regression=True) == EXIT_CANNOT_COMPARE


def test_a_moved_policy_fingerprint_leaves_the_judgement_uncompared() -> None:
    before, after = _pair("A", "C")
    cast(dict[str, object], cast(dict[str, object], after["cohort"])["grade_policy"])[
        "fingerprint"
    ] = "0" * 64

    alert = build_alert(before, after)
    assert alert["regressions"] == []
    assert len(cast(list[object], alert["undetermined"])) == 1
    assert "judgement" in render_markdown(alert)


def test_two_profiles_are_refused_not_alerted_on() -> None:
    with pytest.raises(DiffError, match="differ in profile"):
        build_alert(_document(), _document(COMMITTED_CSV))


# --- renderings -----------------------------------------------------------------------------


def test_markdown_states_the_notice_and_each_group() -> None:
    before, after = _pair("A", "C")
    text = render_markdown(build_alert(before, after))

    assert "### Grade regressions" in text
    assert "No evidence is" not in text  # that sentence belongs to the re-collection notice
    assert "never scored as a regression or as a pass" in text
    assert "None of this is a compliance finding" in text
    assert text.endswith("\n")


def test_atom_is_well_formed_and_carries_one_entry_per_item() -> None:
    before, after = _pair("A", "C")
    alert = build_alert(before, after)
    feed = ElementTree.fromstring(render_atom(alert))  # noqa: S314 - our own output

    assert feed.tag == f"{ATOM}feed"
    assert feed.findtext(f"{ATOM}id") == default_feed_id(alert)
    entries = feed.findall(f"{ATOM}entry")
    assert len(entries) == 1
    [category] = entries[0].findall(f"{ATOM}category")
    assert category.get("term") == "regression"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", feed.findtext(f"{ATOM}updated") or "")


def test_atom_escapes_markup_in_a_publisher_name() -> None:
    before, after = _pair("A", "C")
    cast(dict[str, object], _row(after))["publisher_name"] = "A & B <Health>"
    cast(dict[str, object], _row(before))["publisher_name"] = "A & B <Health>"

    document = render_atom(build_alert(before, after))
    feed = ElementTree.fromstring(document)  # noqa: S314 - our own output
    assert "A & B <Health>" in (feed.find(f"{ATOM}entry/{ATOM}title").text or "")  # type: ignore[union-attr]
    assert "<Health>" not in document


def test_the_same_two_inputs_give_the_same_bytes_and_the_same_entry_id() -> None:
    before, after = _pair("A", "C")
    first = render_atom(build_alert(before, after))
    second = render_atom(build_alert(copy.deepcopy(before), copy.deepcopy(after)))
    assert first == second


def test_an_empty_alert_is_still_a_valid_feed() -> None:
    before, after = _pair("B", "B")
    feed = ElementTree.fromstring(render_atom(build_alert(before, after)))  # noqa: S314
    assert feed.findall(f"{ATOM}entry") == []


def test_a_self_link_is_included_only_when_given() -> None:
    before, after = _pair("B", "B")
    alert = build_alert(before, after)
    assert "rel=" not in render_atom(alert)
    assert 'rel="self" href="https://example.test/alerts.atom"' in render_atom(
        alert, self_url="https://example.test/alerts.atom"
    )


# --- the command ----------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, document: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("output_format", ["markdown", "atom", "json"])
def test_the_command_renders_each_format_and_returns_the_regression_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], output_format: str
) -> None:
    before, after = _pair("A", "C")
    paths = [_write(tmp_path, "before.json", before), _write(tmp_path, "after.json", after)]

    status = cli.main(
        ["alert", *map(str, paths), "--format", output_format, "--fail-on-regression"]
    )

    out = capsys.readouterr().out
    assert status == EXIT_REGRESSED
    assert {"markdown": "Grade regressions", "atom": "<feed", "json": '"regressions"'}[
        output_format
    ] in out


def test_the_command_without_the_flag_is_a_report_and_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before, after = _pair("A", "C")
    status = cli.main(
        ["alert", str(_write(tmp_path, "b.json", before)), str(_write(tmp_path, "a.json", after))]
    )
    capsys.readouterr()
    assert status == EXIT_NO_REGRESSION


def test_the_command_reports_a_refusal_as_the_third_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(
        [
            "alert",
            str(_write(tmp_path, "b.json", _document())),
            str(_write(tmp_path, "a.json", _document(COMMITTED_CSV))),
        ]
    )
    assert status == EXIT_CANNOT_COMPARE
    assert "differ in profile" in capsys.readouterr().err
