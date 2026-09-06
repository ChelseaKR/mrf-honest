#!/usr/bin/env python3
"""Write the Code Quality figures from a real run, instead of typing them.

``tests/test_published_claims.py`` already re-derives *half* of what the
README's Code Quality row and ``docs/ROADMAP.md``'s metrics ledger publish:
``test_the_published_suite_size_is_the_suite_that_actually_collects`` collects
the suite in a subprocess and fails when ``passing + skipped`` is not the
number pytest collects. So the size cannot go stale.

The *split* could. "736 tests passing and 4 skipped" and "1 tests passing and
739 skipped" both satisfy that check, and so does every pair in between --
which is not a hypothetical: the four skips here are runtime ``pytest.skip()``
calls whose conditions depend on which cohorts are published and, in one case,
on whether a remote is reachable. Nothing offline can tell you how many of
them fired. The branch-coverage percentage on the same two lines is in the
same position, and the gate says so in terms: it is a measurement of a run,
and the run that would check it is the one in progress.

A number that only a complete run can produce should be written by a complete
run. That is what this does:

    python tools/publish_metrics.py            # run the suite, write both documents
    python tools/publish_metrics.py --check    # exit 1 if either is stale

``make metrics`` runs it. It is deliberately not part of ``make verify`` --
a gate that repairs what it checks cannot fail, and ``verify`` already carries
the half of this that *is* a gate.

**It refuses to publish anything from a run that did not finish.** A failed,
errored or interrupted run has no passing count to report, and writing one
anyway would be this repository's own worst case: an absence rendered as a
measurement. On anything but a clean run it prints what happened and exits
non-zero without touching a document.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOCUMENTS = ("README.md", "docs/ROADMAP.md")

# The two sentences, one per document. Both carry the same three figures, and
# `test_the_two_documents_state_one_measurement` holds them equal, so a writer
# that repaired one and forgot the other would fail the gate rather than leave
# the ledger and the README disagreeing -- which is exactly how they last
# diverged (docs/CORRECTIONS.md: "A ledger row said 262 tests when the merged
# stack had 324; the number came from one branch").
README_ROW = re.compile(
    r"(?P<head>Current: )[\d,]+ tests passing and [\d,]+ skipped, "
    r"[\d.]+% branch coverage"
)
README_DATE = re.compile(r"(?P<head>zero known vulnerabilities \()\d{4}-\d{2}-\d{2}(?=\))")
LEDGER_ROW = re.compile(
    r"(?P<head>\| )[\d.]+%, [\d,]+ tests passing and [\d,]+ skipped, \d{4}-\d{2}-\d{2}"
    r"(?= \|)"
)

_SUMMARY_PASSED = re.compile(r"(?<![\w.])(\d+) passed")
_SUMMARY_SKIPPED = re.compile(r"(?<![\w.])(\d+) skipped")
_SUMMARY_FAILED = re.compile(r"(?<![\w.])(\d+) (?:failed|error|errors)")

# pytest's closing line: counts, then "in 52.19s". It has to be found as a line
# and read on its own, because the same words appear earlier in the output --
# a failing assertion here prints "claims 737 tests passing and 4 skipped", and
# a scan of the whole run reads that 4 as the run's skip count and silently
# reports a suite one test smaller than the one that ran. Measured, not
# imagined: that is exactly what the first version of this file did.
_SUMMARY_LINE = re.compile(
    r"^=*\s*(?:\d+ (?:passed|failed|skipped|error|errors|xfailed|xpassed|deselected|warnings?)"
    r"(?:, )?)+ in [\d.]+s.*$",
    re.MULTILINE,
)


def summary_line(output: str) -> str:
    """pytest's own closing count line, and nothing that merely looks like it."""

    lines = list(_SUMMARY_LINE.finditer(output))
    if not lines:
        raise RunUnusable(
            "pytest printed no summary line, so the run's outcome is unknown.\n"
            + "\n".join(output.splitlines()[-15:])
        )
    return lines[-1].group(0)


_FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_TOTAL_COVERAGE = re.compile(r"Total coverage: ([\d.]+)%")
_COLLECTED = re.compile(r"(\d+) tests? collected")

# The two tests that assert the very figures this writes. They fail *because*
# the documents are stale, which is the condition this command exists to
# repair, so refusing to run while they are red would make the tool unusable
# in the only situation anyone reaches for it. Every other failure still
# refuses: a suite that is actually broken has no passing count to publish.
#
# Narrow on purpose, and named rather than pattern-matched. "ignore failures in
# tests/test_published_claims.py" would silently excuse a real regression in a
# file that holds eleven other claims.
SELF_REFERENTIAL = frozenset(
    {
        "tests/test_published_claims.py::"
        "test_the_published_suite_size_is_the_suite_that_actually_collects",
        "tests/test_published_claims.py::test_the_two_documents_state_one_measurement",
    }
)


class RunUnusable(Exception):
    """The run did not produce a measurement. Never a number, never a write."""


def _run(root: Path, arguments: list[str]) -> str:
    """pytest, in a subprocess, with the coverage plugin's env stripped.

    ``COV_*`` is dropped from the child's environment so that measuring from
    inside a coverage run cannot perturb the parent's -- the same precaution
    the collection subprocess in ``tests/test_published_claims.py`` takes.
    """

    environment = {key: value for key, value in os.environ.items() if not key.startswith("COV_")}
    # S603: the argument vector is this interpreter plus literals chosen in this
    # module -- `measure` is the only caller and passes no value from outside the
    # process. No shell, and `check=False` because a failing run is a result this
    # reads rather than an exception it wants.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout + completed.stderr


def measure(root: Path = ROOT) -> tuple[int, int, str]:
    """``(passing, skipped, coverage)``, from collection and one complete run.

    The size comes from ``--collect-only``, which is where the gate already
    takes it from, and the split comes from the run: ``passing = collected -
    skipped``. Deriving the passing count rather than reading ``N passed`` is
    what breaks the circle. The two tests that assert these figures are red
    exactly when the documents need writing, and their failure would otherwise
    both refuse the run and subtract themselves from the count it reports.
    """

    collection = _run(root, ["--collect-only", "-q"])
    collected = _COLLECTED.search(collection)
    if collected is None:
        raise RunUnusable(
            "pytest reported no collected count, so the size of the suite is "
            "unknown.\n" + "\n".join(collection.splitlines()[-15:])
        )

    output = _run(root, ["--cov", "--cov-report=term-missing", "-q"])
    unexpected = set(_FAILED_TEST.findall(output)) - SELF_REFERENTIAL
    if unexpected:
        raise RunUnusable(
            "the suite is failing, and not only on the claims this writes:\n  "
            + "\n  ".join(sorted(unexpected))
            + "\nFix those first; a broken run has no passing count to publish."
        )

    summary = summary_line(output)
    passed = _SUMMARY_PASSED.search(summary)
    coverage = _TOTAL_COVERAGE.search(output)
    if passed is None or coverage is None:
        raise RunUnusable(
            "the run finished but printed no passing count or no total coverage, "
            "so there is nothing to publish.\n" + "\n".join(output.splitlines()[-15:])
        )
    reported_skipped = _SUMMARY_SKIPPED.search(summary)
    reported_failed = _SUMMARY_FAILED.search(summary)
    skipped = int(reported_skipped.group(1)) if reported_skipped else 0
    failed = int(reported_failed.group(1)) if reported_failed else 0

    total = int(collected.group(1))
    accounted = int(passed.group(1)) + skipped + failed
    if accounted != total:
        raise RunUnusable(
            f"the run accounted for {accounted} tests and collection found {total}. "
            "Something was deselected or errored during collection, so this is not a "
            "measurement of the whole suite."
        )
    return total - skipped, skipped, coverage.group(1)


def rewrite(text: str, name: str, passing: int, skipped: int, coverage: str, on: str) -> str:
    """`text` with the Code Quality figures set to a measurement."""

    if name == "README.md":
        replaced, rows = README_ROW.subn(
            rf"\g<head>{passing} tests passing and {skipped} skipped, "
            f"{coverage}% branch coverage",
            text,
        )
        replaced, dates = README_DATE.subn(rf"\g<head>{on}", replaced)
        found = rows and dates
    else:
        replaced, found = LEDGER_ROW.subn(
            rf"\g<head>{coverage}%, {passing} tests passing and {skipped} skipped, {on}",
            text,
        )
    if not found:
        raise RunUnusable(
            f"{name} no longer states the Code Quality measurement in the shape this "
            "writes, so nothing was written. Restore the sentence, or re-aim the "
            "pattern in tools/publish_metrics.py -- do not leave it writing nothing."
        )
    return replaced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report stale documents and exit 1 without writing"
    )
    arguments = parser.parse_args(argv)

    try:
        passing, skipped, coverage = measure()
    except RunUnusable as unusable:
        print(f"metrics: {unusable}", file=sys.stderr)
        print("metrics: nothing was written.", file=sys.stderr)
        return 2

    on = date.today().isoformat()
    stale: list[str] = []
    for name in DOCUMENTS:
        path = ROOT / name
        before = path.read_text(encoding="utf-8")
        try:
            after = rewrite(before, name, passing, skipped, coverage, on)
        except RunUnusable as unusable:
            print(f"metrics: {unusable}", file=sys.stderr)
            return 2
        if before == after:
            continue
        stale.append(name)
        if not arguments.check:
            path.write_text(after, encoding="utf-8")

    measured = f"{passing} passing, {skipped} skipped, {coverage}% branch coverage"
    if not stale:
        print(f"metrics: both documents already state {measured}")
        return 0
    if arguments.check:
        print(
            f"metrics: stale in {', '.join(stale)} -- the run measured {measured}. "
            "Run `make metrics`.",
            file=sys.stderr,
        )
        return 1
    print(f"metrics: wrote {measured} ({on}) into {', '.join(stale)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
