"""The scorer is the gate, so the tests here are all about how it fails.

Every case below is a way an accessibility job reports success without having checked
anything. Testing that a good report passes proves almost nothing; testing that a broken run
is loud is the whole point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from score_lighthouse import budget_failures, main, route_of, score, slug_of

BUDGET: dict[str, Any] = {
    "max_transfer_bytes": {"document": 61440, "script": 0, "total": 61440},
    "max_request_count": {"script": 0, "total": 1},
}

BASELINE: dict[str, Any] = {
    "floors": {"accessibility": 1.0, "best-practices": 1.0, "seo": 1.0, "performance": 0.95},
    "metrics": {"accessibility": 1.0, "performance": 1.0},
    "direction": {"accessibility": "higher_is_better", "performance": "higher_is_better"},
}


def _report(
    *,
    accessibility: float | None = 1.0,
    performance: float | None = 1.0,
    resource_summary: bool = True,
    script_bytes: int = 0,
    script_requests: int = 0,
    seo: float | None = 1.0,
    crawlable: float | None = 1.0,
) -> dict[str, Any]:
    categories: dict[str, Any] = {"best-practices": {"score": 1.0}}
    if seo is not None:
        categories["seo"] = {"score": seo}
    if accessibility is not None:
        categories["accessibility"] = {"score": accessibility}
    if performance is not None:
        categories["performance"] = {"score": performance}
    audits: dict[str, Any] = {}
    if resource_summary:
        audits["resource-summary"] = {
            "details": {
                "items": [
                    {"resourceType": "document", "transferSize": 11781, "requestCount": 1},
                    {
                        "resourceType": "script",
                        "transferSize": script_bytes,
                        "requestCount": script_requests,
                    },
                    {
                        "resourceType": "total",
                        "transferSize": 11781 + script_bytes,
                        "requestCount": 1 + script_requests,
                    },
                ]
            }
        }
    if crawlable is not None:
        audits["is-crawlable"] = {"score": crawlable}
    return {"categories": categories, "audits": audits}


def _write(directory: Path, route: str, report: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{slug_of(route)}.json").write_text(json.dumps(report), encoding="utf-8")


def _floors_only() -> dict[str, Any]:
    """Floors with no baseline ratchet, for tests about a single specific failure."""
    return {"floors": BASELINE["floors"], "metrics": {}, "direction": {}}


def test_route_and_slug_round_trip() -> None:
    assert route_of("site/index.html") == "/"
    assert route_of("site/how-we-grade/index.html") == "/how-we-grade/"
    assert route_of("site/404.html") == "/404.html"
    assert route_of("site/hospital/uc-health/west-chester-hospital/index.html") == (
        "/hospital/uc-health/west-chester-hospital/"
    )
    assert slug_of("/how-we-grade/") == "_how_we_grade_"
    assert slug_of("/404.html") == "_404_html"


def test_an_empty_page_list_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """The bug this whole module exists for: `exit "$fail"` over an empty loop exits 0."""
    failures = score([], tmp_path, BASELINE, BUDGET)
    assert failures
    assert "zero pages" in failures[0]


def test_a_missing_report_is_a_failure(tmp_path: Path) -> None:
    _write(tmp_path, "/", _report())
    failures = score(
        ["site/index.html", "site/how-we-grade/index.html"], tmp_path, BASELINE, BUDGET
    )
    assert any("how-we-grade" in failure and "never audited" in failure for failure in failures)
    assert any("audited 1 of 2" in failure for failure in failures)


def test_a_missing_category_is_a_failure_not_a_default_pass(tmp_path: Path) -> None:
    _write(tmp_path, "/", _report(accessibility=None))
    failures = score(["site/index.html"], tmp_path, BASELINE, BUDGET)
    assert any("'accessibility' is missing or null" in failure for failure in failures)


def test_a_null_score_is_a_failure(tmp_path: Path) -> None:
    report = _report()
    report["categories"]["accessibility"] = {"score": None}
    _write(tmp_path, "/", report)
    failures = score(["site/index.html"], tmp_path, BASELINE, BUDGET)
    assert any("'accessibility' is missing or null" in failure for failure in failures)


def test_the_error_page_must_be_non_indexable_and_is_not_scored_for_seo(
    tmp_path: Path,
) -> None:
    """`/404.html` declares `robots: noindex`, so Lighthouse's `seo` score drops by design.

    `seo` embeds the `is-crawlable` audit, which fails on any page blocked from indexing.
    The error page is reached by a URL that does not exist, so there is nothing there to
    index and being non-indexable is correct. Measured in CI: it scored 0.63 against a floor
    of 1.0. Lowering the floor for all 45 pages to accommodate one would be the wrong trade,
    so the floor is not applied to this route and the intent is asserted directly instead.
    """
    # Correctly non-indexable: the low seo score is not a failure here.
    _write(tmp_path, "/404.html", _report(seo=0.63, crawlable=0.0))
    assert score(["site/404.html"], tmp_path, _floors_only(), BUDGET) == []

    # The same low score on a page that is NOT meant to be blocked is still a failure.
    _write(tmp_path, "/", _report(seo=0.63, crawlable=1.0))
    failures = score(["site/index.html"], tmp_path, _floors_only(), BUDGET)
    assert any("seo 0.63 below the floor of 1.0" in failure for failure in failures)


def test_an_error_page_that_became_indexable_is_a_failure(tmp_path: Path) -> None:
    """The check runs in both directions, so the exemption cannot hide a regression.

    Skipping the `seo` floor for this route means a category score can no longer notice that
    its `noindex` was dropped. Asserting `is-crawlable` directly is what replaces it.
    """
    _write(tmp_path, "/404.html", _report(crawlable=1.0))
    failures = score(["site/404.html"], tmp_path, _floors_only(), BUDGET)
    assert any("is indexable, and must not be" in failure for failure in failures)


def test_a_real_page_blocked_from_indexing_is_a_failure(tmp_path: Path) -> None:
    """A page that should be found and cannot be is the more expensive direction."""
    _write(tmp_path, "/", _report(crawlable=0.0))
    failures = score(["site/index.html"], tmp_path, _floors_only(), BUDGET)
    assert any("is blocked from indexing" in failure for failure in failures)


def test_a_missing_or_null_crawlable_audit_is_a_failure(tmp_path: Path) -> None:
    """A broken audit is a failure. Assuming it passed is how a red run turns green."""
    _write(tmp_path, "/", _report(crawlable=None))
    failures = score(["site/index.html"], tmp_path, _floors_only(), BUDGET)
    assert any("no is-crawlable audit" in failure for failure in failures)

    report = _report()
    report["audits"]["is-crawlable"] = {"score": None}
    _write(tmp_path, "/", report)
    failures = score(["site/index.html"], tmp_path, _floors_only(), BUDGET)
    assert any("is-crawlable has no score" in failure for failure in failures)


def test_a_score_below_the_floor_is_a_failure(tmp_path: Path) -> None:
    # 0.98 is exactly what the index scored before the heading-order fix.
    _write(tmp_path, "/", _report(accessibility=0.98))
    failures = score(["site/index.html"], tmp_path, BASELINE, BUDGET)
    assert any("accessibility 0.98 below the floor of 1.0" in failure for failure in failures)


def test_a_report_with_no_resource_summary_fails_the_budget(tmp_path: Path) -> None:
    _write(tmp_path, "/", _report(resource_summary=False))
    failures = score(["site/index.html"], tmp_path, _floors_only(), BUDGET)
    assert any("budget was never measured" in failure for failure in failures)


def test_adding_a_script_fails_the_budget() -> None:
    failures = budget_failures(_report(script_bytes=4096, script_requests=1), BUDGET)
    assert any("script transferred 4096 bytes" in failure for failure in failures)
    assert any("script made 1 requests" in failure for failure in failures)
    assert any("total made 2 requests" in failure for failure in failures)


def _clean_report_for(page: str) -> dict[str, Any]:
    """A passing report for one route, as the real site produces it.

    The error page is `robots: noindex`, so its `is-crawlable` audit scores 0 and its `seo`
    category drops with it. A fixture that showed `/404.html` as indexable would describe a
    site this repository does not publish, and would quietly assert the opposite of what
    `NOT_INDEXABLE_ROUTES` exists to hold.
    """
    if route_of(page) == "/404.html":
        return _report(seo=0.63, crawlable=0.0)
    return _report()


def test_a_clean_cohort_passes(tmp_path: Path) -> None:
    pages = ["site/index.html", "site/how-we-grade/index.html", "site/404.html"]
    for page in pages:
        _write(tmp_path, route_of(page), _clean_report_for(page))
    assert score(pages, tmp_path, BASELINE, BUDGET) == []


def test_main_returns_nonzero_when_a_page_went_unaudited(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(reports, "/", _report())
    pages = tmp_path / "pages.txt"
    pages.write_text("site/index.html\nsite/how-we-grade/index.html\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(BASELINE), encoding="utf-8")
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(BUDGET), encoding="utf-8")
    exit_code = main(
        [
            "--pages",
            str(pages),
            "--reports",
            str(reports),
            "--baseline",
            str(baseline),
            "--budget",
            str(budget),
        ]
    )
    assert exit_code == 1


def test_main_returns_zero_on_a_clean_run(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    for page in ("site/index.html", "site/404.html"):
        _write(reports, route_of(page), _clean_report_for(page))
    pages = tmp_path / "pages.txt"
    pages.write_text("site/index.html\n\nsite/404.html\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(BASELINE), encoding="utf-8")
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(BUDGET), encoding="utf-8")
    assert (
        main(
            [
                "--pages",
                str(pages),
                "--reports",
                str(reports),
                "--baseline",
                str(baseline),
                "--budget",
                str(budget),
            ]
        )
        == 0
    )


def test_the_committed_baseline_and_budget_are_the_ones_the_workflow_names() -> None:
    """A baseline the job does not read is a number nobody enforces."""
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/accessibility.yml").read_text(encoding="utf-8")
    assert "--baseline perf/baseline.json" in workflow
    assert "--budget perf/resource-budget.json" in workflow
    assert "perf/score_lighthouse.py" in workflow
    # And the flag that does nothing is not silently back. It is named in a comment, which is
    # why this looks at executable lines only: a comment explaining why a flag is absent must
    # not be able to satisfy or to break the check.
    executable = [line for line in workflow.splitlines() if not line.lstrip().startswith("#")]
    assert not any("--budget-path" in line for line in executable)
    assert any("--budget-path" in line for line in workflow.splitlines()), (
        "the comment explaining why Lighthouse's own budget flag is not used has gone missing"
    )

    baseline = json.loads((root / "perf/baseline.json").read_text(encoding="utf-8"))
    assert baseline["floors"]["accessibility"] == 1.0
    budget = json.loads((root / "perf/resource-budget.json").read_text(encoding="utf-8"))
    assert budget["max_transfer_bytes"]["script"] == 0
    assert budget["max_request_count"]["total"] == 1


@pytest.mark.parametrize("category", ["accessibility", "performance"])
def test_every_declared_floor_is_actually_checked(tmp_path: Path, category: str) -> None:
    """Guards against a floor that exists in the JSON and is never compared to anything."""
    scores: dict[str, float] = {"accessibility": 1.0, "performance": 1.0}
    scores[category] = 0.5
    _write(tmp_path, "/", _report(**scores))  # type: ignore[arg-type]
    failures = score(["site/index.html"], tmp_path, BASELINE, BUDGET)
    assert any(f"{category} 0.5 below the floor" in failure for failure in failures)
