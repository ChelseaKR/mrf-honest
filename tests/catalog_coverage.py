"""The finding catalogs this package can emit, discovered rather than listed.

Two gates in this repository reported success over inputs they could not see.

``test_grading_document_covers_the_authoritative_catalogs`` asserted

    documented == set(FINDING_CATALOG) | set(RETRIEVAL_FINDING_CATALOG)

against ``docs/how-we-grade.md``. There are three catalogs. ``CSV_FINDING_CATALOG``
was not a term in that union, so the equality held *because* the CSV catalog was
outside it: 46 rows documented, 67 CSV codes documented nowhere, several of them
published in the cohort comparisons and rendered on the site. The test's own name
says *catalogs*, plural.

And nothing anywhere asked whether a catalogued rule ever fires. Four ERROR rules
-- ``CMS_V3_CHARGE_GROUP_NOT_OBJECT``, ``CMS_V3_CHARGE_VALUE_MISSING``,
``CMS_V3_PAYERS_INFORMATION_INVALID`` and ``CMS_V3_PAYER_RATE_NOT_OBJECT`` --
appeared in exactly two files each: their emit site and their catalog row. Renaming
all four produced a byte-identical failure set at identical coverage.

So this module answers one question with a number instead of a shape: **how many
of the codes the catalogs declare did this run actually observe the source emit,
out of how many there are.** ``tests/conftest.py`` records every ``Finding`` the
package constructs, filtered to constructions whose caller is a module of
``mrf_honest`` -- a code read back out of a fixture is not a rule firing -- and
prints the census on every run, passing or failing.

Three refusals keep the census from becoming the next thing that reports green
over a subset:

* the universe is **discovered**, not listed. A fourth catalog is in scope the
  moment it exists, and a catalog that stops loading takes the discovery below
  its floor rather than shrinking the denominator;
* ``NOT_EXERCISED_HERE`` is self-limiting in both directions: an entry for a code
  no catalog holds, or for a code that is now emitted, fails until it is deleted;
* a code emitted from the source that no catalog declares fails too, so the two
  sides cannot drift apart in either direction.

And the verdict has three values rather than two. On a filtered run -- one file,
``-k``, ``--lf`` -- the recorder has seen part of the suite, and reporting that as
a pass would be this defect one level up. It reports ``not_checked_here`` with the
reason, which is why ``make test`` is the run that gates and a single-file run is
not evidence about anything.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Mapping
from dataclasses import dataclass

import mrf_honest
from mrf_honest.inspect import FindingDefinition

#: Fewer catalogs than this means the discovery is broken, not that the package
#: shrank. Three are shipped today (``mrf_honest.inspect``,
#: ``mrf_honest.inspect_csv``, ``mrf_honest.scorecard``); a floor rather than an
#: equality, so adding a fourth is not a merge conflict for every open branch.
MINIMUM_CATALOGS = 3

#: Codes that this suite deliberately does not make the source emit, each with the
#: written reason. Empty is the correct state and is the state today: every code in
#: every catalog is emitted by a document some test in this suite runs the inspector
#: over. An entry here is a claim that a rule cannot be tripped from a test, and it
#: fails as soon as that stops being true.
NOT_EXERCISED_HERE: Mapping[str, str] = {}


@dataclass(frozen=True)
class Catalog:
    """One discovered catalog, and every module that exposes it.

    ``modules`` is a tuple because a catalog is re-exported: ``FINDING_CATALOG``
    is reachable as ``mrf_honest.FINDING_CATALOG`` and as
    ``mrf_honest.inspect.FINDING_CATALOG``, and ``CSV_FINDING_CATALOG`` is also
    imported by ``mrf_honest.ai.narrate``. Deduplicating by identity keeps one
    catalog from being counted three times; naming every module that exposes it
    keeps the record of where it came from.
    """

    attribute: str
    modules: tuple[str, ...]
    entries: Mapping[str, FindingDefinition]

    @property
    def label(self) -> str:
        return f"{self.attribute} ({', '.join(self.modules)})"


def discover_catalogs() -> tuple[Catalog, ...]:
    """Every finding catalog any module of ``mrf_honest`` exports.

    A catalog is a module-level mapping whose name ends in ``FINDING_CATALOG`` and
    whose values are :class:`FindingDefinition`.
    """
    seen: dict[tuple[str, int], tuple[list[str], Mapping[str, FindingDefinition]]] = {}
    modules = [mrf_honest.__name__] + [
        info.name for info in pkgutil.walk_packages(mrf_honest.__path__, prefix="mrf_honest.")
    ]
    for name in modules:
        module = importlib.import_module(name)
        for attribute, value in sorted(vars(module).items()):
            if not attribute.endswith("FINDING_CATALOG") or not isinstance(value, Mapping):
                continue
            if not all(isinstance(entry, FindingDefinition) for entry in value.values()):
                continue
            where, _ = seen.setdefault((attribute, id(value)), ([], value))
            where.append(name)
    return tuple(
        sorted(
            (
                Catalog(attribute, tuple(sorted(where)), entries)
                for (attribute, _), (where, entries) in seen.items()
            ),
            key=lambda catalog: catalog.attribute,
        )
    )


def catalogued_codes(catalogs: tuple[Catalog, ...]) -> dict[str, FindingDefinition]:
    """The union of every discovered catalog, keyed by code.

    Two codes are declared by more than one catalog. Where that happens the
    definitions must agree, because ``mrf-honest explain CODE`` resolves one
    answer for a reader and the grading document carries one row.
    """
    union: dict[str, FindingDefinition] = {}
    for catalog in catalogs:
        for code, definition in catalog.entries.items():
            existing = union.get(code)
            if existing is not None and existing != definition:
                raise AssertionError(
                    f"{code} is declared by more than one catalog with different "
                    f"definitions: {existing!r} and {definition!r}"
                )
            union[code] = definition
    return union


@dataclass(frozen=True)
class EmissionCensus:
    """What one run observed, and what it could have observed."""

    verdict: str
    reason: str
    examined: int
    available: int
    never_emitted: tuple[str, ...]
    unexplained: tuple[str, ...]
    stale_reasons: tuple[str, ...]
    outside_catalog: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """A failing census is the only thing that moves an exit code."""
        return not (self.unexplained or self.stale_reasons or self.outside_catalog)

    def lines(self) -> tuple[str, ...]:
        head = (
            f"finding codes: {self.examined} of {self.available} emitted by "
            f"src/mrf_honest during this run [{self.verdict}]"
        )
        detail = [head, f"  {self.reason}"]
        if self.never_emitted:
            explained = [code for code in self.never_emitted if code in NOT_EXERCISED_HERE]
            detail.append(
                f"  {len(self.never_emitted)} not emitted here "
                f"({len(explained)} with a written reason)"
            )
        for label, codes in (
            ("no test trips this rule and no reason is written", self.unexplained),
            ("a written reason that no longer exempts anything", self.stale_reasons),
            ("emitted by the source and declared by no catalog", self.outside_catalog),
        ):
            if codes:
                detail.append(f"  {label}: {', '.join(codes)}")
        return tuple(detail)


def assess(
    *,
    emitted: frozenset[str],
    catalogued: frozenset[str],
    reasons: Mapping[str, str],
    whole_run: bool,
    whole_run_reason: str,
) -> EmissionCensus:
    """The verdict, as a pure function of what was observed.

    ``whole_run`` is not an escape hatch. A filtered run reports
    ``not_checked_here`` and moves no exit code, because a recorder that saw one
    test file cannot distinguish "this rule never fires" from "that test did not
    run" -- which is the defect this module exists to refuse, one level up.
    """
    never = tuple(sorted(catalogued - emitted))
    outside = tuple(sorted(emitted - catalogued))
    stale = tuple(
        sorted(code for code in reasons if code in emitted or code not in catalogued),
    )
    if not whole_run:
        return EmissionCensus(
            verdict="not_checked_here",
            reason=whole_run_reason,
            examined=len(emitted & catalogued),
            available=len(catalogued),
            never_emitted=never,
            unexplained=(),
            stale_reasons=(),
            outside_catalog=(),
        )
    unexplained = tuple(code for code in never if code not in reasons)
    return EmissionCensus(
        verdict="checked",
        reason=whole_run_reason,
        examined=len(emitted & catalogued),
        available=len(catalogued),
        never_emitted=never,
        unexplained=unexplained,
        stale_reasons=stale,
        outside_catalog=outside,
    )
