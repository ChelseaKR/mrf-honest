"""A one-off low performance score is re-rolled; a real regression is not (issue #140).

Both directions are exercised in the same module: an outlier among three runs must settle to a
passing report, and three low runs must still settle to a failing one that the scorer then
refuses. A median that could only ever come out kind would be a lowered floor with extra steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from score_lighthouse import score
from settle_report import below_floor, main, median_report, performance_of

ROOT = Path(__file__).resolve().parent.parent
BASELINE = json.loads((ROOT / "perf" / "baseline.json").read_text(encoding="utf-8"))
BUDGET: dict[str, Any] = {
    "max_transfer_bytes": {"document": 65536, "total": 65536},
    "max_request_count": {"total": 1},
}


def _report(performance: float | None, *, crawlable: float = 0.0) -> dict[str, Any]:
    categories: dict[str, Any] = {
        "accessibility": {"score": 1.0},
        "best-practices": {"score": 1.0},
        "seo": {"score": 1.0},
    }
    if performance is not None:
        categories["performance"] = {"score": performance}
    return {
        "categories": categories,
        "audits": {
            "is-crawlable": {"score": crawlable},
            "resource-summary": {
                "details": {
                    "items": [
                        {"resourceType": "document", "transferSize": 8540, "requestCount": 1},
                        {"resourceType": "total", "transferSize": 8540, "requestCount": 1},
                    ]
                }
            },
        },
    }


def _write(path: Path, performance: float | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_report(performance)), encoding="utf-8")
    return path


def test_the_committed_performance_floor_is_still_0_95() -> None:
    """The fix re-audits a missed page; it must not have moved the floor."""
    assert BASELINE["floors"]["performance"] == 0.95


def test_a_score_under_the_floor_asks_for_confirmation_and_one_at_the_floor_does_not(
    tmp_path: Path,
) -> None:
    assert below_floor(_write(tmp_path / "a.json", 0.93), BASELINE)
    assert not below_floor(_write(tmp_path / "b.json", 0.95), BASELINE)
    assert not below_floor(_write(tmp_path / "c.json", 1.0), BASELINE)


def test_a_broken_report_is_not_re_rolled_it_is_left_for_the_scorer_to_fail(
    tmp_path: Path,
) -> None:
    assert not below_floor(_write(tmp_path / "null.json", None), BASELINE)
    assert not below_floor(tmp_path / "missing.json", BASELINE)
    garbled = tmp_path / "garbled.json"
    garbled.write_text("{not json", encoding="utf-8")
    assert not below_floor(garbled, BASELINE)
    assert performance_of(garbled) is None


def test_one_outlier_among_three_settles_to_a_passing_report(tmp_path: Path) -> None:
    runs = [
        _write(tmp_path / "r1.json", 0.93),
        _write(tmp_path / "r2.json", 1.0),
        _write(tmp_path / "r3.json", 1.0),
    ]
    out = tmp_path / "page.json"
    assert main(["median", str(out), *map(str, runs)]) == 0
    assert performance_of(out) == 1.0
    # The settled report is what the scorer reads for the page, and it passes the floor.
    settled = tmp_path / "_404_html.json"
    settled.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    assert score(["site/404.html"], tmp_path, BASELINE, BUDGET) == []


def test_three_low_runs_still_settle_to_a_failing_report_the_scorer_refuses(
    tmp_path: Path,
) -> None:
    runs = [
        _write(tmp_path / "r1.json", 0.93),
        _write(tmp_path / "r2.json", 0.91),
        _write(tmp_path / "r3.json", 0.92),
    ]
    out = tmp_path / "_404_html.json"
    assert main(["median", str(out), *map(str, runs)]) == 0
    assert performance_of(out) == 0.92
    failures = score(["site/404.html"], tmp_path, BASELINE, BUDGET)
    assert any("performance 0.92 below the floor of 0.95" in failure for failure in failures)


def test_two_outliers_among_three_do_not_settle_to_a_passing_report(tmp_path: Path) -> None:
    runs = [
        _write(tmp_path / "r1.json", 0.93),
        _write(tmp_path / "r2.json", 0.94),
        _write(tmp_path / "r3.json", 1.0),
    ]
    assert performance_of(median_report(runs)) == 0.94


def test_a_broken_re_run_ranks_worst_so_it_cannot_make_the_median_kinder(tmp_path: Path) -> None:
    runs = [
        _write(tmp_path / "r1.json", 0.93),
        _write(tmp_path / "r2.json", None),
        _write(tmp_path / "r3.json", 1.0),
    ]
    assert median_report(runs) == runs[0]


def test_the_median_of_an_even_count_takes_the_lower_middle(tmp_path: Path) -> None:
    runs = [_write(tmp_path / "r1.json", 1.0), _write(tmp_path / "r2.json", 0.9)]
    assert performance_of(median_report(runs)) == 0.9


def test_the_median_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="no reports"):
        median_report([])


def test_the_below_floor_command_exits_0_only_when_the_page_missed(tmp_path: Path) -> None:
    baseline = ROOT / "perf" / "baseline.json"
    low = _write(tmp_path / "low.json", 0.93)
    fine = _write(tmp_path / "fine.json", 1.0)
    assert main(["below-floor", str(low), "--baseline", str(baseline)]) == 0
    assert main(["below-floor", str(fine), "--baseline", str(baseline)]) == 1


def test_the_workflow_audits_a_missed_page_three_times_and_keeps_the_median() -> None:
    document = cast(
        dict[str, Any],
        yaml.safe_load((ROOT / ".github" / "workflows" / "accessibility.yml").read_text("utf-8")),
    )
    steps = cast(list[dict[str, Any]], document["jobs"]["lighthouse"]["steps"])
    audit = next(step for step in steps if str(step.get("name", "")) == "Audit every page")
    script = str(audit["run"])
    assert "settle_report.py below-floor" in script
    assert "settle_report.py median" in script
    # Run 1 is kept, runs 2 and 3 are the extra audits, and the settled report replaces run 1.
    assert script.count('audit "${route}"') == 3
    assert script.index("below-floor") < script.index("settle_report.py median")
    # The scorer still reads exactly `<slug>.json` per page, from the directory it always did.
    scoring = next(step for step in steps if str(step.get("name", "")).startswith("Score them"))
    assert "--reports /tmp/lh" in str(scoring["run"])
