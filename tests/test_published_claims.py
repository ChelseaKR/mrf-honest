"""Committed artifacts, and the published numbers describing them, checked against the source.

``docs/CONTEXT.md`` makes it a house rule that every published figure traces to a run or a
query, and the metrics ledger carries a "fabricated figures in docs: 0" row. A rule with no
gate behind it is a promise, and three of these claims had already drifted or had never been
true. Each test here reads a published claim and re-derives it:

* the committed comparison document, against the assessments, manifest, and warehouse evidence
  it was generated from. The publish workflow renders that file and checks the HTML agrees with
  it, which is the right shape -- but nothing checked the *comparison* itself, so a change to
  ``build_comparison``, the grade policy, or the finding catalog could ship green while the
  artifact on disk, and therefore every number on the site, described the old behaviour. The
  generator was gated; its output was not. The evidence files under
  ``data/cohorts/<date>.ingest/`` exist for this: before them, the only copy of each ingest
  result lived inside the derived artifact, so the derivation had no inputs to be re-run
  against and could not be checked at all.
* the page counts in ``perf/baseline.json``, against what the render actually produces, and
  the README's Performance row against that same baseline.
* the size of the audited dependency set, against ``uv.lock``.
* the size of this suite, as the README and the metrics ledger publish it, against collection.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from mrf_honest.ai.corpus import CorpusIndex
from mrf_honest.ai.eval import summarize
from mrf_honest.cohort import build_comparison
from mrf_honest.scorecard import AssessmentRegistry
from mrf_honest.site import render_site

ROOT = Path(__file__).resolve().parent.parent
COHORTS = ROOT / "data" / "cohorts"
PUBLISHED = sorted(COHORTS.glob("*.comparison.json"))


def _canonical(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_at_least_one_cohort_is_published() -> None:
    """An empty glob would make every parametrized case below vacuous."""
    assert PUBLISHED, f"no committed comparison documents under {COHORTS}"


@pytest.mark.parametrize("comparison_path", PUBLISHED, ids=lambda path: path.name)
def test_committed_comparison_is_reproducible_from_committed_inputs(
    comparison_path: Path,
) -> None:
    prefix = comparison_path.name.removesuffix(".comparison.json")
    committed = json.loads(comparison_path.read_text(encoding="utf-8"))
    manifest = json.loads((COHORTS / f"{prefix}.json").read_text(encoding="utf-8"))
    registry = AssessmentRegistry(COHORTS / f"{prefix}.assessments.jsonl")
    ingest_results = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((COHORTS / f"{prefix}.ingest").glob("*.json"))
    ]
    profile = str(committed["cohort"]["comparison_scope"]["profile"])
    if profile == "cms-hospital-json-v3":
        # The warehouse implements the JSON profile only; a JSON cohort committed without its
        # ingest evidence would silently re-derive with every warehouse column blank.
        assert ingest_results, f"no ingest evidence committed for cohort {prefix}"

    rebuilt = build_comparison(
        registry.records(),
        manifest,
        ingest_results=ingest_results,
        # The one field that legitimately differs between two runs of the same derivation.
        generated_at=str(committed["generated_at"]),
    )

    assert _canonical(rebuilt) == _canonical(committed), (
        f"{comparison_path.name} is not what the current code derives from "
        f"{prefix}.assessments.jsonl, {prefix}.json and {prefix}.ingest/. The published site "
        "renders the committed file, so it is now describing behaviour the code no longer has. "
        "Regenerate it with `mrf-honest compare`."
    )


def test_the_perf_baseline_counts_the_pages_that_are_actually_rendered(tmp_path: Path) -> None:
    """The baseline describes the audited surface, so its page counts are a claim about it.

    ``perf/baseline.json`` said the scored surface was "the index, how-we-grade, seven file
    pages and 404.html" for a cohort of six graded files -- nine pages described as ten. The
    Lighthouse job enumerates pages from the render and never reads that sentence, so nothing
    could notice. These two numbers are read, and drift now fails a build.
    """
    comparisons = _rendered_comparisons()
    baseline = json.loads((ROOT / "perf" / "baseline.json").read_text(encoding="utf-8"))
    out = tmp_path / "site"
    render_site(comparisons, out)
    file_rows = sum(len(cast(list[object], c["files"])) for c in comparisons)
    assert baseline["meta"]["file_pages"] == file_rows, (
        "perf/baseline.json counts a different number of file pages than the cohorts have"
    )
    assert baseline["meta"]["pages_audited"] == len(list(out.rglob("*.html"))), (
        "perf/baseline.json counts a different number of pages than the render produces"
    )


def test_the_readme_performance_row_states_what_the_baseline_measured() -> None:
    """The README retypes the baseline's headline figures, and nothing read them back.

    The test above binds ``perf/baseline.json`` to the render, so the file cannot describe a
    surface the site does not have. The README's Performance row then retypes that file's
    date, page count and heaviest-page figures into a sentence, and nothing reads a sentence:
    the row still said "Measured 2026-08-15 across all nine pages: 1.0 performance, 12,197
    bytes" long after the baseline had been re-measured on 2026-08-19 over 45 pages with a
    heaviest page of 52,404 bytes. That is the defect the docstring above describes, one
    document further out -- and the stale figure was the flattering one, four times smaller
    than the truth. Every figure in the sentence is now read back from the file it cites.
    """
    baseline = json.loads((ROOT / "perf" / "baseline.json").read_text(encoding="utf-8"))
    meta = cast(dict[str, object], baseline["meta"])
    metrics = cast(dict[str, object], baseline["metrics"])
    # Wrapped prose puts line breaks inside the sentence this pattern reads.
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    stated = re.search(
        r"Measured (\S+) across all ([\d,]+) pages: ([\d.]+) performance, "
        r"([\d,]+) bytes and one request on the heaviest page",
        readme,
    )
    assert stated is not None, "the README no longer states what perf/baseline.json measured"
    date, pages, performance, transfer = stated.groups()

    assert date == meta["date"], "the README dates the measurement to a day the baseline does not"
    assert int(pages.replace(",", "")) == meta["pages_audited"], (
        "the README counts a different number of audited pages than the baseline"
    )
    assert float(performance) == metrics["performance"], (
        "the README states a performance score the baseline does not"
    )
    assert int(transfer.replace(",", "")) == metrics["max_total_transfer_bytes"], (
        "the README states a heaviest-page transfer size the baseline does not"
    )
    assert metrics["max_requests_per_page"] == 1, (
        "the README's Performance row says one request on the heaviest page, and the baseline "
        "no longer measures one"
    )


def _rendered_comparisons() -> list[dict[str, object]]:
    """The comparisons the publish workflow renders: the newest of each profile, JSON first.

    This mirrors the selection in .github/workflows/pages.yml so the audited surface and the
    deployed surface are the same set of pages.
    """
    newest: dict[str, tuple[str, str, Path]] = {}
    for path in PUBLISHED:
        document = json.loads(path.read_text(encoding="utf-8"))
        cohort = cast(dict[str, object], document["cohort"])
        scope = cast(dict[str, object], cohort["comparison_scope"])
        profile = str(scope["profile"])
        key = (str(cohort["as_of"]), path.name, path)
        if profile not in newest or key > newest[profile]:
            newest[profile] = key
    order = ["cms-hospital-json-v3", "cms-hospital-csv-v3"]
    ordered = [p for p in order if p in newest] + sorted(set(newest) - set(order))
    return [
        cast(dict[str, object], json.loads(newest[profile][2].read_text(encoding="utf-8")))
        for profile in ordered
    ]


@pytest.mark.parametrize("comparison_path", PUBLISHED, ids=lambda path: path.name)
def test_every_published_page_explains_missing_contract_evidence(
    comparison_path: Path,
    tmp_path: Path,
) -> None:
    """No published file page may show an absence of contract evidence without a reason.

    Cedars-Sinai's page said the file "was not loaded into the contracted warehouse for this
    cohort" and stopped there, when the cause was this project's own v3-only warehouse refusing
    a file that declares template 2.0.0. docs/how-we-compare.md is explicit that a project limit
    is not a publisher failure and that the reason is always stated.
    """
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    out = tmp_path / "site"
    render_site(comparison, out)
    for row in cast(list[dict[str, object]], comparison["files"]):
        page = (out / "hospital" / str(row["slug"]) / "index.html").read_text(encoding="utf-8")
        assert "Warehouse contracts" in page
        lakehouse = row["lakehouse"]
        if isinstance(lakehouse, dict) and lakehouse.get("status") == "refused":
            reason = str(lakehouse["reason"]).replace("'", "&#x27;")
            assert reason in page, f"{row['slug']} hides why the warehouse refused it"
            assert "not a finding about the file" in page
        elif lakehouse is None:
            assert "No warehouse ingest was recorded" in page
        else:
            assert "<code>success</code>" in page


def test_the_audited_dependency_count_in_the_ledger_matches_the_lockfile() -> None:
    """The ledger said 116 pinned distributions. The lockfile has never held that many.

    ``uv.lock`` is byte-identical to the commit that introduced both the audit gate and the
    claim, and it resolves 52 packages: 51 distributions plus this project, which
    ``--no-emit-project`` excludes from the audited export. The figure was not a measurement
    that went stale, so a date refresh would not have caught it -- only re-deriving it does.

    Counted from the committed lockfile rather than by shelling out to ``uv``, so the check
    needs no tool on PATH and no network, and still fails the moment a dependency is added
    without the ledger following.
    """
    locked = (ROOT / "uv.lock").read_text(encoding="utf-8").count("[[package]]")
    exported = locked - 1  # `uv export --no-emit-project` drops mrf-honest itself
    ledger = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    claimed = re.search(r"(\d+) pinned distributions", ledger)
    assert claimed is not None, "the metrics ledger no longer states the audited set's size"
    assert int(claimed.group(1)) == exported, (
        f"docs/ROADMAP.md claims {claimed.group(1)} pinned distributions in the audited export; "
        f"uv.lock resolves {locked} packages, so the export carries {exported}."
    )


def test_the_published_suite_size_is_the_suite_that_actually_collects() -> None:
    """The README and the metrics ledger both publish how large this suite is.

    Both said "644 tests passing and 4 skipped" while the suite collected 655. It is the one
    figure in the Code Quality row that a gate already computes and then throws away: pytest
    prints the count at the end of every ``make verify`` and nothing compares it to the prose,
    so each test added since the last hand-edit made the published figure a little more wrong
    without failing anything.

    Collection is re-run in a subprocess rather than read off the running session, so the
    answer is the whole suite however this test was invoked -- under ``-k``, or over one file.
    The coverage plugin's subprocess hooks are stripped from the child's environment so that
    counting the tests cannot perturb the coverage the parent reports.

    The branch-coverage percentage on the same two lines is deliberately not checked here: it
    is a measurement of a run, the run that would check it is the one in progress, and a
    partial invocation would read a number that means nothing. The floor behind it, 85, is
    enforced by ``fail_under`` instead.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("COV_")}
    collection = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    reported = re.search(r"(\d+) tests? collected", collection.stdout)
    assert reported is not None, f"pytest reported no collected count:\n{collection.stdout}"
    collected = int(reported.group(1))

    for name in ("README.md", "docs/ROADMAP.md"):
        document = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        claimed = re.search(r"([\d,]+) tests passing and ([\d,]+) skipped", document)
        assert claimed is not None, f"{name} no longer states the size of the suite"
        passing, skipped = (int(group.replace(",", "")) for group in claimed.groups())
        assert passing + skipped == collected, (
            f"{name} claims {passing} tests passing and {skipped} skipped, {passing + skipped} "
            f"in total; pytest collects {collected}."
        )


# --- the sampling frame -------------------------------------------------------------------
#
# A cohort with a stated frame makes two new claims that a reader cannot check by hand: that the
# random stratum really is the seeded draw it says it is, and that nothing drawn was quietly
# dropped. Both are exactly the kind of claim this project exists to distrust, so both are
# re-derived here. Without the first, "random sample" is a word; without the second, a cohort
# could be curated after the fact by deleting whichever targets embarrassed it, which is the
# defect this repository was built to catch.

FRAMES = ROOT / "data" / "frames"


def _frame_for(comparison_path: Path) -> Path | None:
    prefix = comparison_path.name.removesuffix(".comparison.json")
    candidate = FRAMES / f"{prefix}.frame.json"
    return candidate if candidate.exists() else None


@pytest.mark.parametrize("comparison_path", PUBLISHED, ids=lambda path: path.name)
def test_the_random_stratum_is_the_seeded_draw_it_claims_to_be(comparison_path: Path) -> None:
    """Re-run the documented draw and require the recorded sample to be exactly its output.

    ``docs/SAMPLING-FRAME.md`` states a universe, a filter, a seed, and a sample size, and the
    honesty of every proportion computed over the random stratum rests on the recorded sample
    being that draw rather than a list someone assembled and labelled one. The eligible identifier
    list is committed because CMS refreshes the dataset: a frame that cannot be reconstructed is
    not a frame. Cohorts predating the frame carry no frame file and are skipped rather than
    failed -- they were convenience samples and say so.
    """
    frame_path = _frame_for(comparison_path)
    if frame_path is None:
        pytest.skip(f"{comparison_path.name} predates the sampling frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    ids_path = ROOT / str(frame["eligible_facility_ids"])
    lines = ids_path.read_text(encoding="utf-8").splitlines()
    ids = [line.strip() for line in lines if line.strip()]

    assert len(ids) == frame["eligible_count"], (
        f"{frame_path.name} claims {frame['eligible_count']} eligible facilities; "
        f"{ids_path.name} holds {len(ids)}"
    )
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    assert digest == frame["eligible_facility_id_sha256"], (
        f"{ids_path.name} is not the list {frame_path.name} was drawn from"
    )

    draw = frame["draw"]
    # S311 is suppressed below because a seeded, reproducible draw is the entire point. A
    # cryptographic generator would make the sample unverifiable, which is what this checks.
    expected = random.Random(draw["seed"]).sample(ids, draw["sample_size"])  # noqa: S311
    attempts = sorted(
        cast(list[dict[str, object]], frame["attempts"]),
        key=lambda row: cast(int, row["draw_position"]),
    )
    recorded = [str(row["facility_id"]) for row in attempts]
    assert recorded == expected, (
        f"{frame_path.name} records a sample that seed {draw['seed']} does not produce from "
        f"{ids_path.name}. Either the sample was edited or the draw was re-run differently; "
        "in both cases the random stratum is no longer a random sample."
    )


@pytest.mark.parametrize("comparison_path", PUBLISHED, ids=lambda path: path.name)
def test_no_drawn_facility_is_missing_from_the_published_cohort(comparison_path: Path) -> None:
    """Every facility drawn is published as a graded row or as a recorded exclusion.

    This is the gate on the rule that makes the frame worth stating: a target that could not be
    retrieved, or whose publication is in a format this profile does not read, stays visible with
    its reason. A cohort quietly pruned of its failures would grade better and describe less, and
    docs/SAMPLING-FRAME.md promises the opposite in as many words.
    """
    frame_path = _frame_for(comparison_path)
    if frame_path is None:
        pytest.skip(f"{comparison_path.name} predates the sampling frame")
    frame = json.loads(frame_path.read_text(encoding="utf-8"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    slugs = {str(row["slug"]) for row in cast(list[dict[str, object]], comparison["files"])}
    excluded = {
        str(entry["id"]) for entry in cast(list[dict[str, object]], comparison["exclusions"])
    }

    attempts = cast(list[dict[str, object]], frame["attempts"])
    assert len(attempts) == frame["draw"]["sample_size"], (
        "the frame records fewer attempts than it drew; every drawn facility must be attempted"
    )
    for row in attempts:
        ccn = str(row["facility_id"])
        if row["outcome"] == "graded":
            assert str(row["detail"]) in slugs, (
                f"facility {ccn} is recorded as graded but {row['detail']!r} is not a published "
                "file row"
            )
        else:
            assert f"ccn-{ccn}" in excluded, (
                f"facility {ccn} was drawn and not graded, but no exclusion explains why. A "
                "drawn target may be excluded with a stated reason; it may never simply vanish."
            )


def test_the_readme_lead_states_the_cohort_the_comparison_actually_contains() -> None:
    """Re-derive every quantity the README's lead asserts about the published cohort.

    A prior audit checked these figures by hand and found them true; the point of this test is
    that "true when someone last looked" is the condition every stale number in this repository
    was once in. Each claim below is parsed out of the prose and recomputed from the newest
    committed comparison, so growing the cohort without editing the lead fails the build.
    """
    comparison = json.loads(PUBLISHED[-1].read_text(encoding="utf-8"))
    files = cast(list[dict[str, object]], comparison["files"])
    # Wrapped prose puts line breaks inside the sentences these patterns read, and a claim that
    # happened to wrap would otherwise go unchecked rather than fail.
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())

    def claimed(pattern: str, label: str) -> int:
        match = re.search(pattern, readme)
        assert match is not None, f"the README no longer states {label}"
        return int(match.group(1).replace(",", ""))

    assert claimed(r"(\d+) machine-readable files", "how many files the cohort holds") == len(files)

    summary = cast(dict[str, object], comparison["summary"])
    distribution = cast(dict[str, int], summary["grade_distribution"])
    pattern = (
        r"distribution is (\d+) \*\*A\*\*, (\d+) \*\*B\*\*, (\d+) \*\*C\*\*, "
        r"(\d+) \*\*F\*\*, and (\d+) not graded"
    )
    letters = re.search(pattern, readme)
    assert letters is not None, "the README no longer states the grade distribution"
    assert [int(group) for group in letters.groups()] == [
        distribution["A"],
        distribution["B"],
        distribution["C"],
        distribution["F"],
        summary["not_graded"],
    ], "the README's grade distribution is not the one the comparison carries"

    publishers = {str(row["publisher_id"]) for row in files}
    assert claimed(r"files across (\d+) publishers", "how many publishers") == len(publishers)

    bom = sum(
        1
        for row in files
        for dimension in cast(dict[str, dict[str, object]], row["dimensions"]).values()
        for finding in cast(list[dict[str, object]], dimension["findings"])
        if finding["code"] == "JSON_UTF8_BOM_PRESENT"
    )
    assert claimed(r"([\d,]+) of the (?:\d+) files begin with a UTF-8", "the BOM count") == bom

    sizes = [row["size_bytes"] for row in files if isinstance(row["size_bytes"], int)]
    assert claimed(r"cohort is ([\d,]+) bytes", "the largest file's size") == max(sizes)

    contracted = sum(
        1
        for row in files
        if isinstance(row["lakehouse"], dict) and row["lakehouse"].get("status") == "success"
    )
    assert claimed(r"([\d,]+) of the cohort files are contracted", "the warehouse count") == (
        contracted
    )


def test_every_drawn_facility_is_accounted_for_across_both_profile_cohorts() -> None:
    """The two 2026-08-19 cohorts together must cover the one committed draw exactly.

    The frame promised that every drawn facility is published or explained. With two profile
    cohorts that promise could silently break in a new way: a facility could be excluded from
    the JSON cohort as CSV-retrievable and then never appear in the CSV cohort, vanishing in
    the seam between the two documents. This walks the seam: every CSV-retrievable format
    exclusion of the JSON cohort is a declared target of the CSV cohort, every declared target
    is a published row there, and nothing is graded twice.
    """
    json_path = COHORTS / "2026-08-19.comparison.json"
    csv_path = COHORTS / "2026-08-19-csv.comparison.json"
    if not (json_path.exists() and csv_path.exists()):
        pytest.skip("the paired 2026-08-19 profile cohorts are not both published")
    json_cohort = json.loads(json_path.read_text(encoding="utf-8"))
    csv_cohort = json.loads(csv_path.read_text(encoding="utf-8"))
    csv_manifest = json.loads((COHORTS / "2026-08-19-csv.json").read_text(encoding="utf-8"))

    targets = cast(
        list[dict[str, object]],
        cast(dict[str, object], csv_manifest["discovery"])["targets"],
    )
    target_by_ccn = {str(target["ccn"]): target for target in targets}
    assert len(target_by_ccn) == len(targets), "duplicate ccn in the CSV cohort targets"

    csv_slugs = {str(row["slug"]) for row in cast(list[dict[str, object]], csv_cohort["files"])}
    json_slugs = {str(row["slug"]) for row in cast(list[dict[str, object]], json_cohort["files"])}
    assert not csv_slugs & json_slugs, "a subject appears as a row in both profile cohorts"

    for target in targets:
        slug = f"{target['publisher_id']}/{target['location_id']}"
        assert slug in csv_slugs, (
            f"declared CSV target {slug} (ccn {target['ccn']}) has no published row; a target "
            "may fail or be stated, but it may never vanish"
        )
    assert len(csv_slugs) == len(targets), (
        "the CSV cohort publishes rows that no declared target explains"
    )

    csv_retrievable = 0
    for entry in cast(list[dict[str, object]], json_cohort["exclusions"]):
        if entry.get("basis") != "format_outside_profile":
            continue
        ccn = str(entry["id"]).removeprefix("ccn-")
        reason = str(entry.get("reason"))
        if "zip" in reason.rsplit("—", 1)[-1]:
            assert ccn not in target_by_ccn, (
                f"ZIP publication ccn {ccn} must stay an exclusion, not become a CSV target"
            )
            continue
        csv_retrievable += 1
        assert ccn in target_by_ccn, (
            f"facility ccn {ccn} was excluded as CSV-retrievable but is not a CSV cohort "
            "target; it vanished in the seam between the two cohorts"
        )
    assert csv_retrievable == len(targets), (
        "the CSV cohort's target count disagrees with the sibling cohort's CSV-retrievable "
        "exclusions"
    )


def test_the_readme_lead_states_the_csv_cohort_the_comparison_actually_contains() -> None:
    """Re-derive every quantity the README asserts about the published CSV cohort.

    Same rule as the JSON-cohort test above: "true when someone last looked" is the condition
    every stale number in this repository was once in, so each claim is parsed out of the prose
    and recomputed from the committed comparison.
    """
    comparisons = _rendered_comparisons()
    csv_cohorts = [
        c
        for c in comparisons
        if cast(dict[str, object], cast(dict[str, object], c["cohort"])["comparison_scope"])[
            "profile"
        ]
        == "cms-hospital-csv-v3"
    ]
    if not csv_cohorts:
        pytest.skip("no CSV-profile cohort is published")
    comparison = csv_cohorts[0]
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    summary = cast(dict[str, object], comparison["summary"])

    def claimed(pattern: str, label: str) -> int:
        match = re.search(pattern, readme)
        assert match is not None, f"the README no longer states {label}"
        return int(match.group(1).replace(",", ""))

    assert claimed(r"grades all (\d+) CSV targets", "the CSV target count") == summary["targeted"]

    pattern = (
        r"The CSV distribution is (\d+) \*\*A\*\*, (\d+) \*\*B\*\*, (\d+) \*\*C\*\*, "
        r"(\d+) \*\*D\*\*, (\d+) \*\*F\*\*, and (\d+) not graded"
    )
    letters = re.search(pattern, readme)
    assert letters is not None, "the README no longer states the CSV grade distribution"
    distribution = cast(dict[str, int], summary["grade_distribution"])
    assert [int(group) for group in letters.groups()] == [
        distribution["A"],
        distribution["B"],
        distribution["C"],
        distribution["D"],
        distribution["F"],
        cast(int, summary["not_graded"]),
    ], "the README's CSV grade distribution is not the one the comparison carries"

    matrix = {
        str(entry["code"]): entry
        for entry in cast(list[dict[str, object]], comparison["finding_matrix"])
    }

    def occurrences(code: str) -> int:
        entry = matrix.get(code)
        return cast(int, entry["occurrence_total"]) if entry is not None else 0

    assert claimed(
        r"([\d,]+) payer or plan names are encoded with no charge", "the payer-without-charge count"
    ) == occurrences("CMS_CSV_PAYER_WITHOUT_CHARGE")
    assert claimed(
        r"([\d,]+) methodology values outside", "the invalid-methodology count"
    ) == occurrences("CMS_CSV_METHODOLOGY_INVALID")
    assert claimed(r"(\d+) files are not valid UTF-8", "the non-UTF-8 count") == len(
        cast(list[object], matrix["CMS_CSV_ENCODING_NOT_UTF8"]["files"])
    )
    assert claimed(r"(\d+) of the 25 begin with a UTF-8", "the CSV BOM count") == len(
        cast(list[object], matrix["CMS_CSV_UTF8_BOM_PRESENT"]["files"])
    )


# --- published shares (phase 7) --------------------------------------------------------------


@pytest.mark.parametrize("comparison_path", PUBLISHED, ids=lambda path: path.name)
def test_every_published_share_is_re_derivable_from_the_document_it_sits_in(
    comparison_path: Path,
) -> None:
    """A share on the site must be recomputable from the same document's own rows.

    The byte-for-byte re-derivation above proves the document is what the code produces. This
    proves the numbers inside it are what the document's own contents say, so a share cannot
    drift from the rows a reader can count for themselves.
    """

    document = json.loads(comparison_path.read_text(encoding="utf-8"))
    statistics = document["statistics"]
    assert statistics["policy_version"] == "population-statistics-v1"
    estimates = statistics["estimates"]
    if not estimates:
        assert statistics["refusal"], f"{comparison_path.name} carries neither estimate nor reason"
        assert statistics["refusal"]["reason"].strip()
        return
    assert statistics["refusal"] is None

    carry_forward = set(
        document["collection"].get("sampling_frame", {}).get("stratum_a_carry_forward", [])
    )
    published = sum(1 for row in document["files"] if row["slug"] not in carry_forward)
    by_basis: dict[str, int] = {}
    for exclusion in document["exclusions"]:
        by_basis.setdefault(exclusion.get("basis") or "unstated_basis", 0)
        by_basis[exclusion.get("basis") or "unstated_basis"] += 1
    expected = {"published as a row of this cohort": published}
    expected.update({f"excluded: {basis}": count for basis, count in by_basis.items()})

    assert {estimate["label"]: estimate["numerator"] for estimate in estimates} == expected
    denominator = published + sum(by_basis.values())
    for estimate in estimates:
        assert estimate["denominator"] == denominator
        assert estimate["point"] == estimate["numerator"] / denominator
        assert estimate["interval_low"] <= estimate["point"] <= estimate["interval_high"]
    assert sum(estimate["numerator"] for estimate in estimates) == denominator


@pytest.mark.parametrize("comparison_path", PUBLISHED, ids=lambda path: path.name)
def test_a_published_share_reaches_the_rendered_page(comparison_path: Path, tmp_path: Path) -> None:
    """The share is only published if it is on the page. A block computed and never rendered
    would satisfy every test above and tell a reader nothing."""

    document = json.loads(comparison_path.read_text(encoding="utf-8"))
    render_site(document, tmp_path, origin="https://example.test")
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "What share of the drawn sample this is" in html
    estimates = document["statistics"]["estimates"]
    if estimates:
        for estimate in estimates:
            assert f"{estimate['numerator']} of {estimate['denominator']}" in html
    else:
        assert "No share is published for this cohort" in html


# ---------------------------------------------------------------------------
# The recorded narration-grounding evaluations.
#
# `evals/ai/results/*.json` is the only committed artifact in this repository whose
# *generator* cannot be re-run by a gate: producing it calls a hosted model, so the rows are a
# record of one live run and are not reproducible offline. Nothing followed from that to the
# parts of the file that ARE reproducible, and so nothing checked them at all. Before these
# tests the only gate over the directory was `test_committed_results_carry_provenance`, which
# reads the `run` block and `summary.records` and never looks at a single number the
# CHANGELOG quotes.
#
# Three things in these files are pure functions of the file's own committed contents, and
# every one of them is a published figure:
#
#   * the `summary` block, which is `summarize()` over the `records` list;
#   * each row's claim counts, which are the lengths of that row's own `claims` and
#     `withheld_reasons`;
#   * the verdict "shown", which is `CorpusIndex.verify_quote` over the committed `corpus/`.
#
# The third is the load-bearing one. "39 of 48 claims shown" is a statement about what the
# verifier does, and the verifier and the corpus are both committed here. A change to
# `normalize_for_match`, to `MIN_QUOTE_CHARS`, or to a retained document would leave every
# published grounding percentage describing a verifier this repository no longer has, and the
# result files, the CHANGELOG, and the README would go on quoting it.
# ---------------------------------------------------------------------------

EVAL_RESULTS = sorted((ROOT / "evals" / "ai" / "results").glob("*.json"))


def test_at_least_one_evaluation_is_recorded() -> None:
    """An empty glob would make every parametrized case below vacuous."""
    assert EVAL_RESULTS, f"no recorded evaluations under {ROOT / 'evals' / 'ai' / 'results'}"


@pytest.mark.parametrize("result_path", EVAL_RESULTS, ids=lambda path: path.name)
def test_the_recorded_summary_is_what_the_code_computes_from_its_own_rows(
    result_path: Path,
) -> None:
    """Every figure the summary publishes, re-derived by `summarize` from the same file's rows.

    Scoped to the keys the committed summary actually publishes, and every one of them must
    still be a key `summarize` produces, so a metric that is renamed, dropped, or redefined
    fails here rather than being quoted forever from a file nobody re-read.

    `records_refused_before_model_call` is deliberately outside the comparison: it was added
    after both committed runs (CHANGELOG, "narrate called the model on a record with nothing to
    quote"), the rows of a pre-fix run carry no `model_called` field for it to be counted from,
    and asserting a value those runs never measured would be writing a number by hand, which
    `CONTRIBUTING.md` forbids. It is the one key `summarize` emits that these files do not, and
    the assertion below names that so the exclusion cannot silently widen.
    """

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    committed = payload["summary"]
    rederived = summarize(payload["records"])

    missing = sorted(set(committed) - set(rederived))
    assert not missing, (
        f"{result_path.name} publishes {missing}, which `summarize` no longer computes. "
        "The recorded run describes a measurement this code does not make."
    )
    assert sorted(set(rederived) - set(committed)) == ["records_refused_before_model_call"], (
        f"{result_path.name} is missing more than the one field its run predates: "
        f"{sorted(set(rederived) - set(committed))}"
    )
    for key in sorted(committed):
        assert committed[key] == rederived[key], (
            f"{result_path.name} publishes {key}={committed[key]!r}; `summarize` computes "
            f"{rederived[key]!r} from the rows in that same file."
        )


@pytest.mark.parametrize("result_path", EVAL_RESULTS, ids=lambda path: path.name)
def test_every_recorded_row_counts_the_claims_it_actually_carries(result_path: Path) -> None:
    """A row's counts are the lengths of its own lists, so the summary cannot be built on a
    row that already disagrees with itself."""

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["records"], f"{result_path.name} records no rows"
    for row in payload["records"]:
        shown = len(row["claims"])
        withheld = len(row["withheld_reasons"])
        assert row["claims_shown"] == shown, f"{result_path.name} row {row['index']}"
        assert row["claims_withheld"] == withheld, f"{result_path.name} row {row['index']}"
        assert row["claims_generated"] == shown + withheld, (
            f"{result_path.name} row {row['index']} says {row['claims_generated']} claims "
            f"generated; it carries {shown} shown and {withheld} withheld."
        )


@pytest.mark.parametrize("result_path", EVAL_RESULTS, ids=lambda path: path.name)
def test_every_shown_claim_still_verifies_against_the_committed_corpus(result_path: Path) -> None:
    """The recorded grounding rate is a claim about the verifier, re-run against the corpus.

    A claim is shown only because every quote it cites verified verbatim against the retained
    document. Both the verifier and the retained documents are committed, so that verdict is
    reproducible offline even though the model call that produced the sentence is not. If a
    quote no longer verifies, the published percentage is describing a verifier this repository
    no longer has, and the recorded run has to be re-run rather than re-quoted.
    """

    corpus = CorpusIndex.load(ROOT)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    checked = 0
    for row in payload["records"]:
        for position, claim in enumerate(row["claims"]):
            assert claim["citations"], (
                f"{result_path.name} row {row['index']} claim {position} is recorded as shown "
                "with no citation; the verifier withholds an uncited claim."
            )
            for citation in claim["citations"]:
                passage_id = str(citation["passage_id"])
                source_id = passage_id.partition("#")[0]
                assert corpus.passage(passage_id) is not None, (
                    f"{result_path.name} row {row['index']} cites {passage_id}, which the "
                    "committed corpus no longer contains."
                )
                assert corpus.verify_quote(source_id, str(citation["quote"])) is not None, (
                    f"{result_path.name} row {row['index']} was recorded as shown on a quote "
                    f"that no longer verifies against {source_id}: {citation['quote']!r}"
                )
                checked += 1
    assert checked, f"{result_path.name} records no citation to re-verify"


def test_the_changelog_states_the_evaluations_that_are_actually_recorded() -> None:
    """The CHANGELOG retypes both grounding results, and nothing read them back.

    The entry names the cohort each run scored, so each sentence is matched to the result whose
    `records_file` scored that cohort rather than to whichever file sorts first. The counts are
    compared exactly; the percentage is compared to a tenth of a point, because 81.25 rounds to
    81.3 by the convention a reader uses and to 81.2 by the one `format` uses, and pinning the
    prose to Python's tie-breaking would be asserting a formatting accident rather than a fact.
    """

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    by_cohort = {
        Path(str(json.loads(path.read_text(encoding="utf-8"))["run"]["records_file"])).name: (
            path,
            json.loads(path.read_text(encoding="utf-8"))["summary"],
        )
        for path in EVAL_RESULTS
    }
    sentences = {
        "2026-08-19.assessments.jsonl": (
            r"the (\d+) records of the 2026-08-19 JSON cohort produced (\d+)\s+claims, "
            r"(\d+) shown \((\d+\.\d)%\), (\d+) withheld"
        ),
        "2026-08-19-csv.assessments.jsonl": (
            r"(\d+) records\s+of the 2026-08-19 CSV cohort produced (\d+) claims, "
            r"(\d+) shown \((\d+\.\d)%\), (\d+) withheld"
        ),
    }
    for records_file, pattern in sentences.items():
        assert records_file in by_cohort, (
            f"the CHANGELOG describes a run over {records_file}; no committed result under "
            f"evals/ai/results/ names that records file."
        )
        path, summary = by_cohort[records_file]
        match = re.search(pattern, changelog)
        assert match, f"the CHANGELOG no longer states the result recorded in {path.name}"
        records, generated, shown, percent, withheld = match.groups()
        assert int(records) == summary["records"], path.name
        assert int(generated) == summary["claims_generated"], path.name
        assert int(shown) == summary["claims_shown"], path.name
        assert int(withheld) == summary["claims_withheld"], path.name
        recorded = summary["fraction_claims_with_verified_citations"] * 100
        assert abs(float(percent) - recorded) < 0.1, (
            f"the CHANGELOG says {percent}% of the claims in {path.name} were shown; the file "
            f"records {recorded}%."
        )
