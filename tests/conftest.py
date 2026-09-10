"""Records every finding the package emits, and prints the census on every run.

The census is the point. ``0 findings`` over a rule that cannot fire and ``0
findings`` over a rule that fired and found nothing print the same line, and this
repository shipped four ERROR rules no test executed for exactly that reason. So
the terminal summary now carries the denominator, on a passing run as much as a
failing one, because a passing run is where it was needed.

Only constructions whose caller is a module of ``mrf_honest`` are counted. A
``Finding`` rebuilt from a committed report is a code being read back, not a rule
firing, and counting it would make a fixture look like a gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from catalog_coverage import (
    NOT_EXERCISED_HERE,
    EmissionCensus,
    assess,
    catalogued_codes,
    discover_catalogs,
)
from frame_coverage import census_lines as frame_census_lines

import mrf_honest
from mrf_honest.inspect import Finding

#: code -> the module basenames of ``mrf_honest`` that constructed it this run.
EMITTED: dict[str, set[str]] = {}

_PACKAGE_ROOT = Path(mrf_honest.__file__).resolve().parent

#: Set when the recorder is installed. ``test_finding_catalog_coverage.py``
#: asserts it, so deleting this plugin fails a test rather than silently turning
#: the census into a statement about nothing.
RECORDER_INSTALLED = False


def pytest_configure(config: pytest.Config) -> None:
    global RECORDER_INSTALLED
    original = Finding.__init__

    def record(self: Finding, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        caller = Path(sys._getframe(1).f_code.co_filename).resolve()
        if caller.is_relative_to(_PACKAGE_ROOT):
            EMITTED.setdefault(self.code, set()).add(caller.name)

    Finding.__init__ = record  # type: ignore[method-assign]
    RECORDER_INSTALLED = True


def whole_run(config: pytest.Config) -> tuple[bool, str]:
    """Whether this invocation ran the whole suite, and the sentence saying so.

    A filtered run is not a smaller version of the full one for this purpose: it
    cannot tell a rule that never fires from a test that did not run. So the
    discriminator is the invocation, not a count of tests -- a count would have to
    be compared against a number somebody typed.
    """
    # ``getattr`` rather than attribute access: ``-p no:cacheprovider`` removes
    # ``--lf`` and ``--ff`` from the parser entirely, and reading them off the
    # namespace then raises inside a hook rather than reporting anything.
    selectors = {
        "-k": ("keyword",),
        "-m": ("markexpr",),
        "--deselect": ("deselect",),
        "--lf/--ff": ("lf", "failedfirst"),
        "--co": ("collectonly",),
    }
    named = sorted(
        flag
        for flag, options in selectors.items()
        if any(getattr(config.option, option, None) for option in options)
    )
    if named:
        return False, f"a filtered run ({', '.join(named)}); this census is not evidence"
    testpaths = list(config.getini("testpaths"))
    if list(config.args) != testpaths:
        return False, (
            f"pytest was given {list(config.args)!r} rather than the whole suite "
            f"({testpaths!r}); this census is not evidence"
        )
    return True, "the whole suite ran"


def census(config: pytest.Config, *, failures: int) -> EmissionCensus:
    catalogs = discover_catalogs()
    catalogued = frozenset(catalogued_codes(catalogs))
    ran_all, reason = whole_run(config)
    if ran_all and failures:
        ran_all, reason = (
            False,
            (
                f"{failures} test(s) failed, so a rule may be unemitted because its own "
                "test broke; this census is not evidence"
            ),
        )
    return assess(
        emitted=frozenset(EMITTED),
        catalogued=catalogued,
        reasons=NOT_EXERCISED_HERE,
        whole_run=ran_all,
        whole_run_reason=reason,
    )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    result = census(config, failures=len(terminalreporter.stats.get("failed", [])))
    for line in result.lines():
        terminalreporter.write_line(line)
    # The sampling-frame census is a property of the committed documents and the gates' scope
    # rules rather than of this invocation, so unlike the emission census above it needs no
    # third value for a filtered run: it is equally true when nothing ran.
    for line in frame_census_lines():
        terminalreporter.write_line(line)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    result = census(session.config, failures=session.testsfailed)
    if not result.ok:
        session.exitstatus = 1
