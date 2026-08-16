"""Score a directory of Lighthouse reports, fail-closed.

The scoring, not the auditing, is where a11y jobs usually stop being gates. The failure modes
this module exists to make impossible, each of which has been observed in the wild:

* **An empty or short page list.** ``for report in $reports; do ...; done; exit "$fail"``
  exits 0 when the list is empty, so a run that audited nothing reports success. Here the
  expected page list is passed in explicitly and a mismatch is an error.
* **A missing report.** Lighthouse writing no file is a failed audit, not an absent one.
* **A missing category.** ``report["categories"].get("accessibility", {}).get("score", 1.0)``
  turns a broken run into a green check. A category that is absent or ``null`` fails.
* **A budget flag that is not a budget.** Lighthouse's ``--budget-path`` does not exist in
  Lighthouse 12. Measured on 12.8.2: ``--help`` lists no budget option, ``configSettings
  .budgets`` is ``null``, and no ``performance-budget`` audit is emitted -- while the CLI
  silently accepts the unknown flag and exits 0, exactly as it does for
  ``--this-flag-does-not-exist=42``. The budget is therefore asserted here, against the
  ``resource-summary`` audit that reports really do contain, and a report without that audit
  is an error rather than a pass.

Thresholds live in ``perf/baseline.json`` with the direction of each metric, per
PERFORMANCE-STANDARD section 2: an absolute floor and a "no worse than 10% off the committed
baseline" ratchet, both of which must hold.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

CATEGORIES = ("accessibility", "best-practices", "seo", "performance")


def route_of(page: str, root: str = "site") -> str:
    """Site path to served route.

    ``site/how-we-grade/index.html`` -> ``/how-we-grade/``, ``site/404.html`` -> ``/404.html``.
    """
    route = page[len(root) :] if page.startswith(root) else page
    return route[: -len("index.html")] if route.endswith("index.html") else route


def slug_of(route: str) -> str:
    """Mirror the workflow's ``tr -c 'A-Za-z0-9' '_'`` so report filenames line up."""
    return "".join(char if char.isalnum() and char.isascii() else "_" for char in route)


def _category_score(report: Mapping[str, Any], name: str) -> float | None:
    categories = report.get("categories")
    if not isinstance(categories, Mapping):
        return None
    category = categories.get(name)
    if not isinstance(category, Mapping):
        return None
    score = category.get("score")
    return float(score) if isinstance(score, int | float) else None


def _resource_summary(report: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    audits = report.get("audits")
    if not isinstance(audits, Mapping):
        return None
    audit = audits.get("resource-summary")
    if not isinstance(audit, Mapping):
        return None
    details = audit.get("details")
    if not isinstance(details, Mapping):
        return None
    items = details.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, Mapping)]


def budget_failures(report: Mapping[str, Any], budget: Mapping[str, Any]) -> list[str]:
    """Assert the resource budget against the report's own resource-summary audit."""
    items = _resource_summary(report)
    if items is None:
        return [
            "no resource-summary audit in the report, so the resource budget was never "
            "measured. An unmeasured budget is a failure, not a pass."
        ]
    observed = {str(item.get("resourceType")): item for item in items}
    failures: list[str] = []
    for kind, limit in budget["max_transfer_bytes"].items():
        item = observed.get(kind)
        if item is None:
            failures.append(f"budget: resource-summary reported no '{kind}' row to measure")
            continue
        size = int(item.get("transferSize") or 0)
        if size > limit:
            failures.append(f"budget: {kind} transferred {size} bytes, over the {limit}-byte cap")
    for kind, limit in budget["max_request_count"].items():
        item = observed.get(kind)
        if item is None:
            failures.append(f"budget: resource-summary reported no '{kind}' row to measure")
            continue
        count = int(item.get("requestCount") or 0)
        if count > limit:
            failures.append(f"budget: {kind} made {count} requests, over the cap of {limit}")
    return failures


def _category_failures(report: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    floors = baseline["floors"]
    metrics = baseline["metrics"]
    direction = baseline["direction"]
    failures: list[str] = []
    for category in CATEGORIES:
        observed = _category_score(report, category)
        if observed is None:
            failures.append(
                f"category '{category}' is missing or null. A broken audit is a failure; "
                "defaulting it to 1.0 is how a red run turns green."
            )
            continue
        floor = floors.get(category)
        if floor is not None and observed < floor:
            failures.append(f"{category} {observed} below the floor of {floor}")
        recorded = metrics.get(category)
        if recorded is None:
            continue
        worse = (
            observed < recorded * 0.90
            if direction.get(category) == "higher_is_better"
            else observed > recorded * 1.10
        )
        if worse:
            failures.append(
                f"{category} {observed} is more than 10% worse than the committed baseline "
                f"{recorded} (perf/baseline.json)"
            )
    return failures


def _load_report(path: Path) -> Mapping[str, Any] | str:
    """The parsed report, or a string explaining why there is not one."""
    if not path.is_file():
        return f"no report at {path}; lighthouse never audited it"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return f"report unreadable ({error})"
    if not isinstance(report, Mapping):
        return "report is not a JSON object"
    return report


def score(
    pages: Iterable[str],
    reports_dir: Path,
    baseline: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> list[str]:
    """Return the list of failures. An empty list means every page passed every threshold."""
    page_list = list(pages)
    if not page_list:
        return ["no pages were listed; a gate that audited zero pages is not a pass"]

    failures: list[str] = []
    audited = 0
    for page in page_list:
        route = route_of(page)
        report = _load_report(reports_dir / f"{slug_of(route)}.json")
        if isinstance(report, str):
            failures.append(f"{route}: {report}")
            continue
        audited += 1
        problems = budget_failures(report, budget) + _category_failures(report, baseline)
        failures.extend(f"{route}: {problem}" for problem in problems)

    if audited != len(page_list):
        failures.append(
            f"audited {audited} of {len(page_list)} rendered pages; a pass over a partial set "
            "is not a pass"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", required=True, type=Path, help="file of rendered HTML paths")
    parser.add_argument("--reports", required=True, type=Path, help="directory of LH reports")
    parser.add_argument("--baseline", required=True, type=Path, help="perf/baseline.json")
    parser.add_argument("--budget", required=True, type=Path, help="perf/resource-budget.json")
    args = parser.parse_args(argv)

    pages = [line.strip() for line in args.pages.read_text(encoding="utf-8").splitlines()]
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    budget = json.loads(args.budget.read_text(encoding="utf-8"))
    failures = score([page for page in pages if page], args.reports, baseline, budget)

    for page in pages:
        if page:
            print(f"audited {route_of(page)}")
    for failure in failures:
        print(f"::error title=Lighthouse gate::{failure}")
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print(f"{len([p for p in pages if p])} page(s) passed every category floor and the budget")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
