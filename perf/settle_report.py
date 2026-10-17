"""Settle a Lighthouse performance score that missed its floor, by median of three.

Why this exists (issue #140). On 2026-10-17 the ``accessibility`` workflow failed on master with
``/404.html: performance 0.93 below the floor of 0.95``. The same commit passed on re-run, and the
page is one HTML file with no sub-resource. Lighthouse's performance category is computed from
metrics that are *simulated* from a single trace, so a hosted runner that is briefly busy, or a
browser on its first page after launch, moves the number without anything in the page changing.
The accessibility, best-practices and SEO categories are rule results and do not move this way.

The fix is not a lower floor. Every page is still audited once. Only a page whose performance
score came back **below the floor** is audited twice more, and the report with the median
performance score of the three becomes the page's report. A real regression scores low in all
three runs and still fails; a one-off outlier does not. A page at or above the floor costs
nothing extra, so the job's runtime is unchanged on a quiet run.

Fail-closed rules, the same ones ``score_lighthouse.py`` holds:

* a report that cannot be read, or has no performance score, ranks as the worst possible run
  rather than being skipped, so a broken re-run cannot be dropped to make the median kinder;
* the median needs exactly the runs it is given, and an empty list is an error.

Usage in the workflow::

    python perf/settle_report.py below-floor REPORT --baseline perf/baseline.json   # exit 0: yes
    python perf/settle_report.py median OUT RUN1 RUN2 RUN3                          # writes OUT
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

WORST = -1.0


def performance_of(path: Path) -> float | None:
    """The report's performance category score, or None if unreadable or absent."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(report, Mapping):
        return None
    categories = report.get("categories")
    if not isinstance(categories, Mapping):
        return None
    category = categories.get("performance")
    if not isinstance(category, Mapping):
        return None
    score = category.get("score")
    return float(score) if isinstance(score, int | float) else None


def below_floor(path: Path, baseline: Mapping[str, Any]) -> bool:
    """True when the report's performance score is under the committed floor.

    An unreadable or score-less report is not "below the floor" here: the scorer fails it as a
    broken audit, which is a different problem from variance and should not be re-rolled away.
    """
    floor = baseline["floors"].get("performance")
    score = performance_of(path)
    return floor is not None and score is not None and score < floor


def median_report(paths: Sequence[Path]) -> Path:
    """The report whose performance score is the median of the given runs.

    With an even count the lower middle is taken, so the choice never rounds up to a kinder run.
    Ties keep the earlier argument, so the result is deterministic.
    """
    if not paths:
        raise ValueError("no reports to take a median of")
    ranked = sorted(
        enumerate(paths),
        key=lambda pair: (
            WORST if (score := performance_of(pair[1])) is None else score,
            pair[0],
        ),
    )
    return ranked[(len(ranked) - 1) // 2][1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("below-floor", help="exit 0 if the report missed the performance floor")
    check.add_argument("report", type=Path)
    check.add_argument("--baseline", required=True, type=Path)

    settle = sub.add_parser("median", help="copy the median-performance report to OUT")
    settle.add_argument("out", type=Path)
    settle.add_argument("runs", nargs="+", type=Path)

    args = parser.parse_args(argv)
    if args.command == "below-floor":
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        return 0 if below_floor(args.report, baseline) else 1

    chosen = median_report(args.runs)
    scores = [performance_of(run) for run in args.runs]
    if chosen.resolve() != args.out.resolve():
        shutil.copyfile(chosen, args.out)
    print(f"performance across {len(args.runs)} runs: {scores}; kept {chosen.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
