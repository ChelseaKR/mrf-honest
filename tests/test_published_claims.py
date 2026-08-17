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

import json
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
    comparison = json.loads(PUBLISHED[-1].read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "perf" / "baseline.json").read_text(encoding="utf-8"))
    out = tmp_path / "site"
    render_site(comparison, out)
    assert baseline["meta"]["file_pages"] == len(cast(list[object], comparison["files"])), (
        "perf/baseline.json counts a different number of file pages than the cohort has"
    )
    assert baseline["meta"]["pages_audited"] == len(list(out.rglob("*.html"))), (
        "perf/baseline.json counts a different number of pages than the render produces"
    )


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
