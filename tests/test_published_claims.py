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
* the page counts in ``perf/baseline.json``, against what the render actually produces.
* the size of the audited dependency set, against ``uv.lock``.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import cast

import pytest

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
