"""The census that says how much of the catalog this suite actually exercises.

``tests/catalog_coverage.py`` holds the reasoning. This file holds the gates: that
the discovery finds every catalog, that the grading document's rows say what the
catalog says rather than only naming the same codes, that the recorder is installed
at all, and that the verdict function refuses in each of the four directions it
claims to.

The verdict itself is enforced from ``pytest_sessionfinish``, not from a test,
because a test cannot assert what a run emitted while the run is still emitting it.
That is why :func:`assess` is a pure function with its own tests here: the logic a
hook runs is the logic these cases exercise.
"""

from __future__ import annotations

import re
from pathlib import Path

import conftest
import pytest
from catalog_coverage import (
    MINIMUM_CATALOGS,
    NOT_EXERCISED_HERE,
    assess,
    catalogued_codes,
    discover_catalogs,
)

from mrf_honest.inspect import Finding

GRADING_DOCUMENT = Path("docs/how-we-grade.md")

#: ``| `CODE` | SEVERITY | description | citations |``
ROW = re.compile(r"^\| `([A-Z0-9_]+)` \| ([A-Z]+) \| (.*?) \| (.*?) \|\s*$", re.MULTILINE)
LINK = re.compile(r"^\[([^\]]+)\]: (\S+)$", re.MULTILINE)


def _normalise(text: str) -> str:
    """The document writes identifiers in backticks; the catalog writes them bare.

    Only the markup differs, so only the markup is normalised -- an escaped table
    pipe, a backtick, and the single quotes the catalog uses where the document
    uses code style. Nothing else is stripped, so a description that drifts in
    substance still fails.
    """
    return re.sub(r"[`']", "", text.replace("\\|", "|"))


def test_the_recorder_is_installed() -> None:
    """Deleting the plugin must fail a test, not quietly empty the census.

    The census is computed from ``conftest.EMITTED``, which only fills if
    ``pytest_configure`` wrapped ``Finding.__init__``. Without this assertion,
    removing that wrapper would leave every code unemitted -- and a suite that
    reports nothing looks exactly like a suite that found nothing wrong.
    """
    assert conftest.RECORDER_INSTALLED
    assert Finding.__init__.__qualname__.endswith("record"), Finding.__init__


def test_every_shipped_catalog_is_discovered_and_named() -> None:
    catalogs = discover_catalogs()
    where = {catalog.attribute: catalog.modules for catalog in catalogs}

    assert len(catalogs) >= MINIMUM_CATALOGS, where
    assert "mrf_honest.inspect" in where["FINDING_CATALOG"]
    assert "mrf_honest.inspect_csv" in where["CSV_FINDING_CATALOG"]
    assert "mrf_honest.scorecard" in where["RETRIEVAL_FINDING_CATALOG"]
    assert all(catalog.entries for catalog in catalogs), where


def test_a_catalog_is_counted_once_however_many_modules_re_export_it() -> None:
    catalogs = discover_catalogs()
    identities = {id(catalog.entries) for catalog in catalogs}

    assert len(identities) == len(catalogs)


def test_the_grading_document_rows_say_what_the_catalog_says() -> None:
    """Naming the same codes is not the same as documenting them.

    The previous gate compared code *names* only, so a row could carry the wrong
    severity or a description of a rule that had since changed and stay green.
    Severity and citations are compared exactly; the description is compared
    modulo the markup the document adds.
    """
    document = GRADING_DOCUMENT.read_text(encoding="utf-8")
    links = dict(LINK.findall(document))
    catalogued = catalogued_codes(discover_catalogs())
    rows = ROW.findall(document)

    assert len(rows) == len(catalogued), (
        f"{len(rows)} rows parsed against {len(catalogued)} catalogued codes; a row "
        "this pattern cannot read is a row nothing checks"
    )
    for code, severity, description, citations in rows:
        definition = catalogued[code]
        assert severity == definition.severity, code
        assert _normalise(description) == _normalise(definition.description), code
        labels = [label.strip(" []") for label in citations.split("], [")]
        assert tuple(links[label] for label in labels) == definition.citations, code


def test_no_code_is_declared_twice_with_two_meanings() -> None:
    """Two codes are shared between the JSON and CSV catalogs.

    ``mrf-honest explain CODE`` resolves one answer and the document carries one
    row, so the definitions have to agree. :func:`catalogued_codes` raises if they
    do not; this asserts the shared codes are really shared rather than that the
    check is unreachable.
    """
    catalogs = discover_catalogs()
    seen: dict[str, set[str]] = {}
    for catalog in catalogs:
        for code in catalog.entries:
            seen.setdefault(code, set()).add(catalog.label)

    shared = {code: labels for code, labels in seen.items() if len(labels) > 1}
    assert shared, "no code is shared, so the agreement check above never runs"
    assert catalogued_codes(catalogs)  # raises if two catalogs disagree


def test_the_written_reason_list_is_empty_and_the_gate_says_why() -> None:
    """Empty is the state today. The list is here so that stops being invisible."""
    assert dict(NOT_EXERCISED_HERE) == {}


# --- the verdict function, in each direction it claims to refuse -----------------------------


def _assess(**overrides: object) -> object:
    base: dict[str, object] = {
        "emitted": frozenset({"A", "B"}),
        "catalogued": frozenset({"A", "B"}),
        "reasons": {},
        "whole_run": True,
        "whole_run_reason": "the whole suite ran",
    }
    base.update(overrides)
    return assess(**base)  # type: ignore[arg-type]


def test_a_complete_run_over_a_fully_exercised_catalog_passes() -> None:
    census = _assess()

    assert census.verdict == "checked"
    assert census.ok
    assert (census.examined, census.available) == (2, 2)


def test_a_catalogued_code_nothing_emits_fails_with_no_written_reason() -> None:
    census = _assess(emitted=frozenset({"A"}))

    assert census.never_emitted == ("B",)
    assert census.unexplained == ("B",)
    assert not census.ok


def test_a_written_reason_excuses_that_code_and_only_that_code() -> None:
    census = _assess(emitted=frozenset({"A"}), reasons={"B": "no document trips it"})

    assert census.never_emitted == ("B",)
    assert census.unexplained == ()
    assert census.ok


def test_a_written_reason_for_a_code_that_now_fires_fails_until_it_is_deleted() -> None:
    census = _assess(reasons={"B": "no document trips it"})

    assert census.stale_reasons == ("B",)
    assert not census.ok


def test_a_written_reason_for_a_code_no_catalog_holds_fails_too() -> None:
    census = _assess(reasons={"GONE": "a rule that was deleted"})

    assert census.stale_reasons == ("GONE",)
    assert not census.ok


def test_a_code_the_source_emits_that_no_catalog_declares_fails() -> None:
    census = _assess(emitted=frozenset({"A", "B", "C"}))

    assert census.outside_catalog == ("C",)
    assert not census.ok


def test_a_filtered_run_reports_a_third_value_rather_than_a_pass() -> None:
    """The whole point. A partial run must not read as a clean sweep."""
    census = _assess(
        emitted=frozenset({"A"}),
        whole_run=False,
        whole_run_reason="a filtered run (-k); this census is not evidence",
    )

    assert census.verdict == "not_checked_here"
    assert census.never_emitted == ("B",)
    assert census.unexplained == ()
    assert census.ok  # it moves no exit code ...
    assert "not evidence" in "\n".join(census.lines())  # ... and it says so


class _Option:
    def __init__(self, **flags: object) -> None:
        self.__dict__.update(flags)


class _Config:
    """The three things :func:`conftest.whole_run` reads, and nothing else."""

    def __init__(self, args: list[str], **flags: object) -> None:
        self.args = args
        self.option = _Option(**flags)

    def getini(self, name: str) -> list[str]:
        assert name == "testpaths"
        return ["tests"]


@pytest.mark.parametrize(
    ("config", "expected", "expected_reason"),
    [
        (_Config(["tests"]), True, "the whole suite ran"),
        (_Config(["tests/test_inspect.py"]), False, "rather than the whole suite"),
        (_Config(["tests"], keyword="csv"), False, "-k"),
        (_Config(["tests"], markexpr="slow"), False, "-m"),
        (_Config(["tests"], deselect=["tests/test_inspect.py::x"]), False, "--deselect"),
        (_Config(["tests"], lf=True), False, "--lf"),
        (_Config(["tests"], failedfirst=True), False, "--lf"),
        (_Config(["tests"], collectonly=True), False, "--co"),
    ],
)
def test_the_whole_run_discriminator_reads_the_invocation(
    config: object, expected: bool, expected_reason: str
) -> None:
    """A count of collected tests would have to be compared against a typed number.

    Every flag is read with ``getattr``: ``-p no:cacheprovider`` removes ``--lf``
    and ``--ff`` from the parser entirely, and attribute access then raises inside
    a hook -- which is how this check first broke a ``--collect-only`` subprocess
    that another gate runs.
    """
    ran_all, reason = conftest.whole_run(config)  # type: ignore[arg-type]

    assert ran_all is expected
    assert expected_reason in reason


def test_a_missing_flag_is_read_as_unset_rather_than_raising() -> None:
    ran_all, reason = conftest.whole_run(_Config(["tests"]))  # type: ignore[arg-type]

    assert ran_all and reason == "the whole suite ran"


def test_the_census_line_carries_both_numbers() -> None:
    census = _assess(emitted=frozenset({"A"}))
    head = census.lines()[0]

    assert "1 of 2" in head
    assert "[checked]" in head
