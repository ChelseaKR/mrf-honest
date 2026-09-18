"""The origin-scoped collection run of 2026-09-13, held to the evidence it was written from.

``data/origins/`` holds a different kind of run from ``data/cohorts/``. A cohort there is a
seeded probability sample against a committed frame; this is a **complete enumeration of one
origin's ``cms-hpt.txt``**, chosen on cost. The two must not be mixed, and two of the tests below
exist to keep them apart mechanically rather than by intention:

* the publish workflow renders "the newest committed comparison of each profile" from
  ``data/cohorts/*.comparison.json``. An origin run committed one directory over would have
  replaced the published JSON sample with itself and silently changed what the site's
  Wilson-score estimates estimate. ``test_the_origin_run_is_outside_the_rendered_set`` reads the
  workflow's own glob rather than trusting the directory name.
* the origin run's manifest deliberately records no ``sampling_frame``, so its statistics block
  refuses instead of computing a share of a population it did not sample.

The rest of this module does for ``docs/findings/what-one-origin-cost-2026-09-13.md`` what
``tests/test_published_claims.py`` does for the README: every figure the document publishes is
read back out of the prose and re-derived from the committed evidence. Byte counts and
dispositions come from the assessment rows; wall clock, pacing, robots and process memory come
from the cost record, which carries only what an assessment row cannot. No number has two
sources here, so none of them can disagree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from mrf_honest.cohort import build_comparison
from mrf_honest.scorecard import AssessmentRegistry

ROOT = Path(__file__).resolve().parent.parent
ORIGINS = ROOT / "data" / "origins"
PREFIX = "2026-09-13-chihealth-com"
ASSESSMENTS = ORIGINS / f"{PREFIX}.assessments.jsonl"
MANIFEST = ORIGINS / f"{PREFIX}.json"
COMPARISON = ORIGINS / f"{PREFIX}.comparison.json"
COST = ORIGINS / f"{PREFIX}.cost.json"
FINDING = ROOT / "docs" / "findings" / "what-one-origin-cost-2026-09-13.md"

# The default decoded-size ceiling, restated here so a change to FetchPolicy that silently
# widened it would fail this module rather than quietly change what "nothing hit the ceiling"
# meant on 2026-09-13.
DEFAULT_MAX_BYTES = 1 << 30


def _rows() -> list[dict[str, object]]:
    return [cast(dict[str, object], record) for record in AssessmentRegistry(ASSESSMENTS).records()]


def _retrieval(row: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], row["retrieval"])


def _subject(row: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], row["subject"])


def _cost() -> dict[str, object]:
    return cast(dict[str, object], json.loads(COST.read_text(encoding="utf-8")))


def _prose() -> str:
    """The finding as one line, so a wrapped sentence still matches."""
    return " ".join(FINDING.read_text(encoding="utf-8").split())


def _stated(pattern: str) -> str:
    match = re.search(pattern, _prose())
    assert match is not None, f"the finding no longer states {pattern!r}"
    return match.group(1)


def _stated_int(pattern: str) -> int:
    return int(_stated(pattern).replace(",", ""))


def test_the_origin_comparison_is_reproducible_from_its_committed_inputs() -> None:
    """The same gate the published cohorts get, for a run published in a different directory.

    Without it the comparison is a derived artifact nothing re-derives, which is exactly the
    hole ``test_committed_comparison_is_reproducible_from_committed_inputs`` was written to
    close for ``data/cohorts/``.
    """
    committed = json.loads(COMPARISON.read_text(encoding="utf-8"))
    rebuilt = build_comparison(
        AssessmentRegistry(ASSESSMENTS).records(),
        json.loads(MANIFEST.read_text(encoding="utf-8")),
        generated_at=str(committed["generated_at"]),
    )
    canonical = {
        "committed": json.dumps(committed, ensure_ascii=False, sort_keys=True),
        "rebuilt": json.dumps(rebuilt, ensure_ascii=False, sort_keys=True),
    }
    assert canonical["rebuilt"] == canonical["committed"], (
        f"{COMPARISON.name} is not what the current code derives from {ASSESSMENTS.name} and "
        f"{MANIFEST.name}. Regenerate it with `mrf-honest compare`."
    )


def test_the_origin_run_is_outside_the_rendered_set() -> None:
    """The site renders the newest comparison of each profile from ``data/cohorts/`` only.

    This run is a complete enumeration of one origin chosen on cost. Its ``as_of`` is later than
    every published cohort's, so had it been committed one directory over it would have won that
    selection and replaced the published JSON probability sample with itself. The check reads the
    glob out of ``.github/workflows/pages.yml`` rather than asserting a directory name, because
    the defect being guarded against is the workflow selecting this file, not the file sitting
    somewhere in particular.
    """
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    globs = set(re.findall(r'glob\.glob\("([^"]+)"\)', workflow))
    assert globs, "pages.yml no longer selects comparisons with a glob this test can read"
    for pattern in globs:
        rendered = {path.resolve() for path in ROOT.glob(pattern)}
        assert COMPARISON.resolve() not in rendered, (
            f"the publish workflow's glob {pattern!r} now selects {COMPARISON.name}. An "
            "origin-scoped enumeration rendered beside the seeded samples changes what the "
            "site's interval estimates are estimates of."
        )


def test_the_origin_run_estimates_nothing_about_the_sampling_frame() -> None:
    """A convenience selection must publish a refusal, not a share."""
    comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
    statistics = cast(dict[str, object], comparison["statistics"])
    assert statistics["estimates"] == []
    refusal = cast(dict[str, object], statistics["refusal"])
    assert refusal["outcome"] == "refused"
    assert refusal["code"] == "no_sampling_frame"
    collection = cast(dict[str, object], comparison["collection"])
    assert "sampling_frame" not in collection, (
        "the origin run's manifest now records a sampling frame; a complete enumeration of one "
        "origin chosen on cost is not a draw from data/frames/2026-08-19.frame.json"
    )


def test_no_row_publishes_a_grade_for_bytes_it_did_not_read() -> None:
    """The house rule, checked on this run rather than assumed from it."""
    for row in _rows():
        retrieval = _retrieval(row)
        coverage = cast(dict[str, object], row["coverage"])
        if coverage["verified_body_available"] is not True:
            assert retrieval["content_sha256"] is None
            assert retrieval["size_bytes"] is None
            assert retrieval["error"] is not None
            continue
        assert isinstance(retrieval["content_sha256"], str)
        assert isinstance(retrieval["size_bytes"], int)
        inspection = cast(dict[str, object], row["inspection"])
        assert inspection["source_sha256"] == retrieval["content_sha256"]
        assert inspection["scan_completed"] is True


def test_the_finding_states_the_dispositions_the_rows_carry() -> None:
    rows = _rows()
    listed = r"\| Locations the origin's `cms-hpt.txt` listed \| ([\d,]+) \|"
    assert _stated_int(listed) == len(rows)
    distinct = {_subject(row)["requested_url_sha256"] for row in rows}
    assert _stated_int(r"\| Distinct files those locations point at \| \*\*([\d,]+)\*\* \|") == len(
        distinct
    )
    lettered = sum(
        1
        for row in _rows()
        if cast(dict[str, object], row["coverage"])["verified_body_available"] is True
    )
    stated_lettered, stated_total = re.search(  # type: ignore[union-attr]
        r"\| Locations that returned a letter \| \*\*(\d+) of (\d+)\*\* \|", _prose()
    ).groups()
    assert (int(stated_lettered), int(stated_total)) == (lettered, len(rows))
    comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
    summary = cast(dict[str, object], comparison["summary"])
    assert _stated_int(r"\| Locations not assessed \| \*\*([\d,]+)\*\* \|") == summary["not_graded"]
    distribution = cast(dict[str, int], summary["grade_distribution"])
    assert _stated_int(r"\| Grades \| \*\*A, all ([\d,]+)\*\* \|") == distribution["A"]
    assert sum(distribution.values()) == distribution["A"], (
        "the finding says every row graded A; the comparison no longer agrees"
    )


def test_the_finding_states_the_byte_totals_the_rows_carry() -> None:
    rows = _rows()
    wire = sum(cast(int, _retrieval(row)["wire_size_bytes"]) for row in rows)
    decoded = sum(cast(int, _retrieval(row)["size_bytes"]) for row in rows)
    assert _stated_int(r"\| Wire bytes, whole collection \| \*\*([\d,]+)\*\*") == wire
    assert _stated_int(r"\| Decoded bytes \| ([\d,]+) ") == decoded
    assert _stated(r"\| Wire : decoded \| \*\*1 : ([\d.]+)\*\* \|") == f"{decoded / wire:.1f}"
    largest = max(cast(int, _retrieval(row)["size_bytes"]) for row in rows)
    stated_largest, stated_ceiling = re.search(  # type: ignore[union-attr]
        r"\| Largest file, decoded \| ([\d,]+) bytes, against a ceiling of ([\d,]+) \|", _prose()
    ).groups()
    assert int(stated_largest.replace(",", "")) == largest
    assert int(stated_ceiling.replace(",", "")) == DEFAULT_MAX_BYTES


def test_nothing_in_this_run_reached_the_size_ceiling() -> None:
    """The finding says `too_large` was zero, and the run's own policy says what the bound was."""
    rows = _rows()
    too_large = [row for row in rows if _retrieval(row)["status"] == "too_large"]
    assert _stated_int(r"\| `too_large` \| \*\*([\d,]+)\*\* \|") == len(too_large)
    assert not too_large
    for row in rows:
        policy = cast(dict[str, object], row["retrieval_policy"])
        assert policy["max_bytes"] == DEFAULT_MAX_BYTES, (
            "a row was collected under a different size ceiling than the finding states"
        )
    assert _cost()["collection"]["max_bytes"] == DEFAULT_MAX_BYTES  # type: ignore[index]


def test_the_finding_states_the_wall_clock_and_the_memory_the_cost_record_carries() -> None:
    cost = _cost()
    collection = cast(dict[str, object], cost["collection"])
    probe_pass = cast(dict[str, object], cost["probe_pass"])
    discovery = cast(dict[str, object], cost["discovery"])
    assert _stated(r"\| Wall clock, collection \| \*\*([\d.]+) s\*\* \|") == (
        f"{cast(float, collection['elapsed_seconds']):.1f}"
    )
    stated_probe, stated_document = re.search(  # type: ignore[union-attr]
        r"pre-pass \| ([\d.]+) s, plus ([\d,]+) wire bytes for the document itself \|", _prose()
    ).groups()
    assert stated_probe == f"{cast(float, probe_pass['elapsed_seconds']):.1f}"
    assert int(stated_document.replace(",", "")) == discovery["wire_size_bytes"]
    assert (
        _stated_int(r"\| Peak RSS, whole process \| \*\*([\d,]+) bytes\*\* \|")
        == (collection["peak_rss_bytes"])
    )


def test_the_finding_states_what_the_repeated_retrievals_cost() -> None:
    """The two listed locations that share a file, and the bytes and seconds that cost."""
    rows = _rows()
    seen: set[str] = set()
    repeated: list[dict[str, object]] = []
    for row in rows:
        url_sha256 = cast(str, _subject(row)["requested_url_sha256"])
        if url_sha256 in seen:
            repeated.append(row)
        seen.add(url_sha256)
    wire_again = sum(cast(int, _retrieval(row)["wire_size_bytes"]) for row in repeated)
    assert _stated_int(r"\*\*([\d,]+) wire bytes and [\d.]+ seconds\*\*") == wire_again

    cost = _cost()
    collection = cast(dict[str, object], cost["collection"])
    seconds = {
        cast(str, target["location_id"]): cast(float, target["seconds"])
        for target in cast(list[dict[str, object]], collection["targets"])
    }
    again = sum(seconds[cast(str, _subject(row)["location_id"])] for row in repeated)
    assert _stated(r"\*\*[\d,]+ wire bytes and ([\d.]+) seconds\*\*") == f"{again:.1f}"

    elapsed = cast(float, collection["elapsed_seconds"])
    total_wire = sum(cast(int, _retrieval(row)["wire_size_bytes"]) for row in rows)
    stated_bytes_share, stated_clock_share = re.search(  # type: ignore[union-attr]
        r"([\d.]+)% of the collection's bandwidth and ([\d.]+)% of its wall clock", _prose()
    ).groups()
    assert stated_bytes_share == f"{wire_again / total_wire * 100:.1f}"
    assert stated_clock_share == f"{again / elapsed * 100:.1f}"


def test_the_finding_states_what_the_probe_pass_could_and_could_not_answer() -> None:
    """The pre-purchase primitive learned the format and not the size, and the doc says so."""
    probe_pass = cast(dict[str, object], _cost()["probe_pass"])
    assert probe_pass["declared_size_known"] == 0
    assert probe_pass["range_honored"] == 0
    assert probe_pass["sniffed_json"] == probe_pass["probes"]
    prose = _prose()
    stated = re.search(
        r"All (\d+) probes succeeded and all (\d+) correctly identified JSON\. "
        r"\*\*All (\d+) came back HTTP 200 rather than 206",
        prose,
    )
    assert stated is not None, "the finding no longer states what the probe pass returned"
    assert {int(group) for group in stated.groups()} == {cast(int, probe_pass["probes"])}
    assert "cannot state a byte budget" in prose


def test_the_cost_record_and_the_assessments_describe_the_same_targets() -> None:
    """Two evidence files, one run. A row in either that the other does not know about is a bug."""
    collection = cast(dict[str, object], _cost()["collection"])
    targets = cast(list[dict[str, object]], collection["targets"])
    assert [target["location_id"] for target in targets] == [
        _subject(row)["location_id"] for row in _rows()
    ], "the cost record and the assessment registry disagree about which locations were collected"
    assert collection["concurrency"] == 1
    assert {target["robots_status"] for target in targets} == {"allowed"}
    assert {target["robots_crawl_delay_seconds"] for target in targets} == {None}


@pytest.mark.parametrize(
    "claim",
    [
        "it did not enter `data/cohorts/`",
        "did not raise concurrency, retry around a refusal, or replay a stored url",
        "did not grade anything it did not read",
    ],
)
def test_the_finding_keeps_its_three_refusals(claim: str) -> None:
    """These three sentences are the run's constraints. Losing one silently is the failure mode."""
    assert claim in _prose().lower()
