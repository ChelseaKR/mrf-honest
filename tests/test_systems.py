"""System-level reconciliation tests.

The graded side of every fixture is a **committed** assessment record, read through
``AssessmentRegistry`` exactly as the tool reads it, so a shape change in the assessment artifact
cannot leave these tests exercising a record the project no longer writes. Only the discovery side
is synthesised, because that is the side with branches to cover -- and because the local discovery
registry is deliberately not committed (`.gitignore`: ``data/registry*.jsonl``), which is itself
the reason this reconciliation is a command rather than published output.

Every fixture URL is taken from the 2026-08-14 cohort, whose six rows carry no query string, so
``requested_url`` is byte-identical to the URL that was hashed into ``requested_url_sha256``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import mrf_honest.cli as cli
from mrf_honest.cohort import grade_assessment
from mrf_honest.scorecard import AssessmentRegistry

# The persisted assessment carries an integrity digest over its own body, and
# ``require_comparable`` -- which this module calls -- re-verifies it. A fixture derived by
# editing a committed record is therefore refused unless the digest is re-minted, which is a
# property worth keeping rather than working around: it is why ``reconcile`` cannot be handed a
# doctored row. The two fixtures below that need an edited record re-mint it with the project's
# own hash rather than reimplementing the canonical form here.
from mrf_honest.scorecard import _digest as _assessment_digest
from mrf_honest.systems import (
    COVERS,
    EXPECTED_TO_VARY,
    FILE_NAMES_FEWER,
    FILE_NAMES_MORE,
    MUST_AGREE,
    NO_DISCOVERY_EVIDENCE,
    NOT_INSPECTED,
    RECONCILED,
    SystemsError,
    human_report,
    reconcile,
    select_discovery_evidence,
)

ROOT = Path(__file__).resolve().parent.parent
COHORT = ROOT / "data" / "cohorts" / "2026-08-14.assessments.jsonl"
#: The 2026-08-19 cohort, for the one fixture that needs a row nothing inspected. Doctoring a
#: committed record into that state is not possible and should not be: the persisted verifier
#: refuses a record whose retrievability dimension disagrees with its retrieval evidence.
LATER_COHORT = ROOT / "data" / "cohorts" / "2026-08-19.assessments.jsonl"
NORTHSIDE = "northside-hospital/northside-hospital-duluth"

STANFORD = "stanford-health-care/stanford-health-care"
TRI_VALLEY = "stanford-health-care/stanford-health-care-tri-valley"
UC_MAIN = "uc-health/university-of-cincinnati-medical-center"
WEST_CHESTER = "uc-health/west-chester-hospital"


def _records(path: Path = COHORT) -> list[dict[str, object]]:
    return [dict(record) for record in AssessmentRegistry(path).records()]


def _by_slug(path: Path = COHORT) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for record in _records(path):
        subject = cast(dict[str, object], record["subject"])
        publisher = cast(dict[str, object], subject["publisher"])
        rows[f"{publisher['identifier']}/{subject['location_id']}"] = record
    return rows


def _url(slug: str) -> str:
    subject = cast(dict[str, object], _by_slug()[slug]["subject"])
    return str(subject["requested_url"])


def _remint(record: dict[str, object]) -> dict[str, object]:
    """Re-mint a doctored fixture's integrity digests so the real verifier accepts it."""
    subject = cast(dict[str, object], record["subject"])
    publisher = cast(dict[str, object], subject["publisher"])
    inspection = record.get("inspection")
    identity_body = {
        "as_of": record.get("as_of"),
        "assessment_policy_fingerprint": record.get("assessment_policy_fingerprint"),
        "inspection_source_sha256": (
            cast(dict[str, object], inspection).get("source_sha256")
            if isinstance(inspection, dict)
            else None
        ),
        "observed_at": record.get("observed_at"),
        "retrieval_evidence_sha256": record.get("retrieval_evidence_sha256"),
        "retrieval_policy_fingerprint": cast(dict[str, object], record["retrieval_policy"]).get(
            "fingerprint"
        ),
        "subject": {
            "location_id": subject.get("location_id"),
            "publisher_identifier": publisher.get("identifier"),
            "publisher_type": subject.get("publisher_type"),
            "requested_url": subject.get("requested_url"),
            "requested_url_sha256": subject.get("requested_url_sha256"),
            "url_provenance": subject.get("url_provenance"),
        },
        "version": record.get("version"),
    }
    reminted = dict(record)
    reminted["assessment_id"] = _assessment_digest(identity_body)
    reminted["assessment_body_sha256"] = _assessment_digest(
        {
            key: value
            for key, value in reminted.items()
            if key not in {"assessment_id", "assessment_body_sha256"}
        }
    )
    return reminted


def _entry(name: str | None, url: str | None) -> dict[str, object]:
    return {
        "contact_email": None,
        "contact_name": None,
        "extra_fields": [],
        "location_name": name,
        "mrf_url": url,
        "problems": [],
        "source_page_url": None,
    }


def _discovery(
    domain: str,
    entries: list[dict[str, object]],
    *,
    attempted_at: str = "2026-08-14T00:00:00Z",
) -> dict[str, object]:
    url = f"https://{domain}/cms-hpt.txt"
    return {
        "attempted_at": attempted_at,
        "discovery": {"domain": domain, "entries": entries, "problems": []},
        "domain": domain,
        "fetch": {
            "attempted_at": attempted_at,
            "attempts": 1,
            "content_sha256": "a" * 64,
            "status": "fetched",
            "url": url,
        },
        "kind": "discovery",
        "problems": [],
        "url": url,
        "version": 2,
    }


def _stanford_system(**kwargs: object) -> dict[str, object]:
    """Both graded Stanford files, plus a third listed location nothing graded."""
    return _discovery(
        "stanfordhealthcare.org",
        [
            _entry("Stanford Health Care", _url(STANFORD)),
            _entry("Stanford Health Care Tri-Valley", _url(TRI_VALLEY)),
            _entry("Somewhere Else", "https://stanfordhealthcare.org/not-drawn.json"),
        ],
        **cast(dict[str, str], kwargs),
    )


def _rows(*slugs: str) -> list[dict[str, object]]:
    by_slug = _by_slug()
    return [by_slug[slug] for slug in slugs]


def _system(document: dict[str, object], index: int = 0) -> dict[str, object]:
    return cast(list[dict[str, object]], document["systems"])[index]


# --- the join ---------------------------------------------------------------------------------


def test_the_committed_rows_used_as_fixtures_carry_no_redacted_query_string() -> None:
    """The fixtures only join because these six URLs are their own unredacted form.

    Asserted rather than assumed: if a future cohort of this name carried a query string, every
    join below would silently stop matching and the tests would go on passing about nothing.
    """
    import hashlib

    for record in _records():
        subject = cast(dict[str, object], record["subject"])
        raw = str(subject["requested_url"])
        assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == subject["requested_url_sha256"]


def test_a_url_differing_only_in_a_query_string_does_not_join() -> None:
    """The measured trap: the published URL is redacted, and the digest is of the raw URL.

    Two different publishers in the committed CSV cohort publish through one vendor endpoint that
    differs only in a query parameter. A join on the redacted string reports each system as
    listing the other's file; a join on ``requested_url_sha256`` cannot.
    """
    discovery = _discovery(
        "stanfordhealthcare.org",
        [
            _entry("Stanford Health Care", f"{_url(STANFORD)}?token=abc"),
            _entry("Stanford Health Care Tri-Valley", f"{_url(TRI_VALLEY)}?token=def"),
        ],
    )
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [discovery])
    assert document["status"] == NO_DISCOVERY_EVIDENCE
    assert document["systems"] == []


def test_a_graded_row_no_listed_location_declares_is_named() -> None:
    document = reconcile(_rows(STANFORD, TRI_VALLEY, UC_MAIN, WEST_CHESTER), [_stanford_system()])
    unlisted = cast(list[dict[str, object]], document["graded_without_a_listed_location"])
    assert sorted(str(row["slug"]) for row in unlisted) == [UC_MAIN, WEST_CHESTER]
    coverage = cast(dict[str, object], document["coverage"])
    assert coverage["rows"] == 4
    assert coverage["rows_declared_by_a_listed_location"] == 2
    assert coverage["rows_no_listed_location_declares"] == 2


# --- absence is never a value -----------------------------------------------------------------


def test_no_discovery_evidence_at_all_is_a_refusal_not_a_cohort_of_undeclared_files() -> None:
    """With no evidence in hand, "no listed location declares this file" is not a statement."""
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [])
    assert document["status"] == NO_DISCOVERY_EVIDENCE
    assert document["graded_without_a_listed_location"] == []
    assert document["systems"] == []
    assert document["summary"] is None
    coverage = cast(dict[str, object], document["coverage"])
    assert coverage["rows"] == 2
    assert coverage["rows_no_listed_location_declares"] is None
    assert "not reconciled" in human_report(document)


def test_evidence_that_declares_none_of_these_rows_is_the_same_refusal() -> None:
    unrelated = _discovery(
        "elsewhere.example.test", [_entry("Elsewhere", "https://elsewhere.example.test/a.json")]
    )
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [unrelated])
    assert document["status"] == NO_DISCOVERY_EVIDENCE
    assert document["graded_without_a_listed_location"] == []


def test_a_listed_location_this_cohort_did_not_grade_is_sampling_scope_and_not_a_defect() -> None:
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [_stanford_system()])
    system = _system(document)
    not_assessed = cast(dict[str, object], system["locations_not_assessed_in_this_cohort"])
    assert not_assessed["count"] == 1
    assert cast(list[dict[str, object]], not_assessed["locations"])[0]["location_name"] == (
        "Somewhere Else"
    )
    assert "not a fact about the publisher" in str(not_assessed["basis"])
    # It is not any of the three things that *are* facts about the publication set.
    assert system["locations_listed_without_an_mrf_url"] == []
    assert document["graded_without_a_listed_location"] == []
    report = human_report(document)
    assert "not assessed in this cohort (this project's sampling scope, not a defect)" in report


def test_the_summary_never_folds_sampling_scope_into_a_publication_defect() -> None:
    """Against literals, because the whole point is the two counts staying apart.

    Measured: a sabotage that added ``locations_not_assessed_in_this_cohort`` into the summary's
    ``locations_listed_without_an_mrf_url`` -- publishing "this cohort did not draw that location"
    as "that system listed a location with no file" -- passed the entire suite before this test
    existed. The fixture has exactly one not-assessed location and exactly zero without an
    mrf-url, so folding one into the other cannot go unnoticed.
    """
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [_stanford_system()])
    summary = cast(dict[str, object], document["summary"])
    system = _system(document)
    not_assessed = cast(dict[str, object], system["locations_not_assessed_in_this_cohort"])

    assert not_assessed["count"] == 1
    assert summary["locations_listed_without_an_mrf_url"] == 0
    assert summary["locations_listed"] == 3
    assert summary["locations_assessed_in_this_cohort"] == 2
    assert summary["multi_location_systems"] == 1
    assert summary["rows_no_listed_location_declares"] == 0
    # No summary field carries the sampling count at all: it belongs to the system entry, where
    # it is published beside the sentence that says what it means.
    assert (
        1 not in [value for key, value in summary.items() if key != "multi_location_systems"]
        or summary["locations_listed_without_an_mrf_url"] == 0
    )


def test_a_listed_location_with_no_file_is_counted_and_a_not_drawn_one_is_not() -> None:
    """The same two counts, with the defect present, so the assertion above is not vacuous."""
    discovery = _discovery(
        "stanfordhealthcare.org",
        [
            _entry("Stanford Health Care", _url(STANFORD)),
            _entry("Stanford Health Care Tri-Valley", _url(TRI_VALLEY)),
            _entry("Somewhere Else", "https://stanfordhealthcare.org/not-drawn.json"),
            _entry("A Location With No File At All", None),
        ],
    )
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [discovery])
    summary = cast(dict[str, object], document["summary"])
    not_assessed = cast(
        dict[str, object], _system(document)["locations_not_assessed_in_this_cohort"]
    )
    assert summary["locations_listed_without_an_mrf_url"] == 1
    assert not_assessed["count"] == 1


def test_a_block_with_no_name_and_no_url_is_not_a_listed_location() -> None:
    """UPMC's cms-hpt.txt opens with an ASCII-art banner. The parser records it; this does not
    turn it into four locations the system failed to publish files for."""
    discovery = _discovery(
        "stanfordhealthcare.org",
        [
            _entry("Stanford Health Care", _url(STANFORD)),
            _entry("Stanford Health Care Tri-Valley", _url(TRI_VALLEY)),
            _entry(None, None),
            _entry(None, None),
        ],
    )
    system = _system(reconcile(_rows(STANFORD, TRI_VALLEY), [discovery]))
    assert system["locations_listed"] == 2
    assert system["locations_listed_without_an_mrf_url"] == []
    blocks = cast(dict[str, object], system["blocks_that_are_not_location_entries"])
    assert blocks["count"] == 2
    assert "not counted as listed locations" in str(blocks["note"])


def test_a_named_location_with_no_mrf_url_is_a_publication_defect() -> None:
    """The difference from the block above is that this one claims to be a location."""
    discovery = _discovery(
        "stanfordhealthcare.org",
        [
            _entry("Stanford Health Care", _url(STANFORD)),
            _entry("Stanford Health Care Tri-Valley", _url(TRI_VALLEY)),
            _entry("Harrison Community Hospital", None),
        ],
    )
    system = _system(reconcile(_rows(STANFORD, TRI_VALLEY), [discovery]))
    assert system["locations_listed"] == 3
    missing = cast(list[dict[str, object]], system["locations_listed_without_an_mrf_url"])
    assert [row["location_name"] for row in missing] == ["Harrison Community Hospital"]
    assert cast(dict[str, object], system["blocks_that_are_not_location_entries"])["count"] == 0
    assert "! listed with no mrf-url: Harrison Community Hospital" in human_report(
        reconcile(_rows(STANFORD, TRI_VALLEY), [discovery])
    )


def test_the_listed_count_is_the_locations_served_plus_the_locations_not_assessed() -> None:
    """Arithmetic a reader can do by hand, over entries rather than files.

    Counting *files* instead of entries made a system of four listed locations read as
    ``4 listed, 1 graded, 2 not assessed``, because two entries shared one file.
    """
    document = reconcile(_rows(TRI_VALLEY), [_stanford_system()])
    system = _system(document)
    not_assessed = cast(dict[str, object], system["locations_not_assessed_in_this_cohort"])
    assert cast(int, system["locations_listed"]) == cast(
        int, system["locations_served_by_a_graded_file"]
    ) + cast(int, not_assessed["count"])


# --- one file for several listed locations ----------------------------------------------------


def test_one_file_listed_for_several_locations_is_stated_and_cross_checked() -> None:
    """Permitted by the dictionary, so it is stated. What it makes checkable is the file's own
    ``location_name`` array against the locations pointed at that file."""
    discovery = _discovery(
        "uchealth.com",
        [
            _entry("University of Cincinnati Medical Center, LLC", _url(UC_MAIN)),
            _entry("University of Cincinnati Medical Center - Psych", _url(UC_MAIN)),
            _entry("West Chester Hospital, LLC", _url(WEST_CHESTER)),
        ],
    )
    system = _system(reconcile(_rows(UC_MAIN, WEST_CHESTER), [discovery]))
    shared = cast(list[dict[str, object]], system["files_serving_several_listed_locations"])
    assert [row["slug"] for row in shared] == [UC_MAIN]
    assert cast(list[str], shared[0]["listed_locations"]) == [
        "University of Cincinnati Medical Center, LLC",
        "University of Cincinnati Medical Center - Psych",
    ]
    assessed = {
        str(row["slug"]): row
        for row in cast(list[dict[str, object]], system["locations_assessed_in_this_cohort"])
    }
    agreement = cast(dict[str, object], assessed[UC_MAIN]["location_name_agreement"])
    assert agreement["state"] == COVERS
    assert len(cast(list[str], agreement["named_by_this_file"])) == 2


def test_a_file_naming_fewer_locations_than_are_listed_against_it_is_reported() -> None:
    discovery = _discovery(
        "uchealth.com",
        [
            _entry("West Chester Hospital, LLC", _url(WEST_CHESTER)),
            _entry("A Second Location Pointed At The Same File", _url(WEST_CHESTER)),
        ],
    )
    system = _system(reconcile(_rows(WEST_CHESTER, UC_MAIN), [discovery]))
    assessed = cast(list[dict[str, object]], system["locations_assessed_in_this_cohort"])
    agreement = cast(dict[str, object], assessed[0]["location_name_agreement"])
    assert agreement["state"] == FILE_NAMES_FEWER
    assert "location_name" in str(agreement["citation"])


def test_a_file_naming_more_locations_than_are_listed_against_it_is_reported() -> None:
    discovery = _discovery(
        "uchealth.com",
        [
            _entry("University of Cincinnati Medical Center, LLC", _url(UC_MAIN)),
            _entry("West Chester Hospital, LLC", _url(WEST_CHESTER)),
        ],
    )
    system = _system(reconcile(_rows(UC_MAIN, WEST_CHESTER), [discovery]))
    assessed = {
        str(row["slug"]): row
        for row in cast(list[dict[str, object]], system["locations_assessed_in_this_cohort"])
    }
    agreement = cast(dict[str, object], assessed[UC_MAIN]["location_name_agreement"])
    assert agreement["state"] == FILE_NAMES_MORE


def test_a_file_that_was_never_inspected_has_no_location_names_to_compare() -> None:
    """An unretrieved file's agreement is stated as unknown, never as agreement.

    The fixture is a real committed row -- ``northside-hospital-duluth``, graded ``F``, whose
    document never streamed -- rather than an edited one. It has to be: the persisted verifier
    refuses a record whose retrievability dimension no longer matches its retrieval evidence, so
    "delete the inspection" is not a state a record can be doctored into, which is the right
    answer and is why this reads a row that genuinely is in that state.
    """
    record = _by_slug(LATER_COHORT)[NORTHSIDE]
    url = str(cast(dict[str, object], record["subject"])["requested_url"])
    discovery = _discovery(
        "northside.com",
        [_entry("Northside Hospital Duluth", url), _entry("Northside Hospital Atlanta", None)],
        attempted_at="2026-08-19T00:00:00Z",
    )
    system = _system(reconcile([record], [discovery]))
    assessed = cast(list[dict[str, object]], system["locations_assessed_in_this_cohort"])
    agreement = cast(dict[str, object], assessed[0]["location_name_agreement"])
    assert assessed[0]["grade"] == "F"
    assert agreement["state"] == NOT_INSPECTED
    assert agreement["named_by_this_file"] == []
    assert agreement["listed_against_this_file"] == ["Northside Hospital Duluth"]


# --- element disagreements --------------------------------------------------------------------


def test_a_template_version_disagreement_is_counted_and_names_both_files() -> None:
    record = dict(_by_slug()[TRI_VALLEY])
    inspection = dict(cast(dict[str, object], record["inspection"]))
    envelope = dict(cast(dict[str, object], inspection["envelope"]))
    envelope["version"] = "3.0.1"
    inspection["envelope"] = envelope
    record["inspection"] = inspection
    record = _remint(record)

    document = reconcile([_by_slug()[STANFORD], record], [_stanford_system()])
    system = _system(document)
    rows = {
        str(row["element"]): row for row in cast(list[dict[str, object]], system["disagreements"])
    }
    assert rows["version"]["basis"] == MUST_AGREE
    files = sorted(
        file
        for value in cast(list[dict[str, object]], rows["version"]["values"])
        for file in cast(list[str], value["files"])
    )
    assert files == [STANFORD, TRI_VALLEY]
    summary = cast(dict[str, object], document["summary"])
    assert summary["systems_whose_files_disagree_on_an_element_that_must_agree"] == 1
    assert "! files disagree on version" in human_report(document)


def test_a_per_location_element_is_published_as_a_difference_and_never_counted() -> None:
    """#74 asked for ``license_information`` among the counted elements. Measured on the
    committed cohort that produces one row -- two separately licensed hospitals in one system
    publishing correctly -- so it is published, labelled, and not counted."""
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [_stanford_system()])
    system = _system(document)
    rows = {
        str(row["element"]): row for row in cast(list[dict[str, object]], system["disagreements"])
    }
    assert rows["license_information"]["basis"] == EXPECTED_TO_VARY
    assert rows["hospital_name"]["basis"] == EXPECTED_TO_VARY
    values = cast(list[dict[str, object]], rows["license_information"]["values"])
    assert sorted(file for value in values for file in cast(list[str], value["files"])) == [
        STANFORD,
        TRI_VALLEY,
    ]
    summary = cast(dict[str, object], document["summary"])
    assert summary["systems_whose_files_disagree_on_an_element_that_must_agree"] == 0
    basis = cast(dict[str, object], system["element_basis"])
    assert basis["must_agree_within_a_system"] == ["version", "last_updated_on"]
    assert "license_information" in cast(list[str], basis["expected_to_vary_by_location"])


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = set(cast(dict[str, object], value))
        for item in cast(dict[str, object], value).values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in cast(list[object], value):
            found |= _keys(item)
        return found
    return set()


def test_the_reconciliation_carries_no_findings_and_no_dimension_evidence() -> None:
    """The boundary #74 exists to keep. Asserted over the document's *keys*, not its text: the
    notice sentence contains the word "findings", and a substring assertion would fail on the
    very sentence that states the boundary."""
    document = reconcile(_rows(STANFORD, TRI_VALLEY, UC_MAIN, WEST_CHESTER), [_stanford_system()])
    assert _keys(document).isdisjoint({"findings", "dimensions", "severity", "finding_matrix"})


def test_no_grade_is_derived_here_and_no_input_record_is_mutated() -> None:
    """Every letter in the document is the letter the grader already gave that record, and the
    records go in and come out unchanged: this section cannot move a grade even by accident."""
    rows = _rows(STANFORD, TRI_VALLEY)
    before = json.dumps(rows, sort_keys=True)
    document = reconcile(rows, [_stanford_system()])
    assert json.dumps(rows, sort_keys=True) == before
    expected = {slug: grade_assessment(_by_slug()[slug]).grade for slug in (STANFORD, TRI_VALLEY)}
    published = {
        str(row["slug"]): row["grade"]
        for row in cast(
            list[dict[str, object]],
            _system(document)["locations_assessed_in_this_cohort"],
        )
    }
    assert published == expected


# --- selecting the evidence -------------------------------------------------------------------


def test_the_latest_attempt_on_or_before_the_cohort_date_is_the_one_used() -> None:
    """Six rows of the committed 2026-08-19 cohort were discovered on 2026-08-14; a same-day
    filter drops them and the reconciliation silently loses a quarter of the cohort."""
    older = _stanford_system(attempted_at="2026-08-10T00:00:00Z")
    newer = _stanford_system(attempted_at="2026-08-13T00:00:00Z")
    selected = select_discovery_evidence(
        [older, newer],
        as_of="2026-08-14",
        url_digests=frozenset(
            {str(cast(dict[str, object], _by_slug()[STANFORD]["subject"])["requested_url_sha256"])}
        ),
    )
    assert [record["attempted_at"] for record in selected] == ["2026-08-13T00:00:00Z"]


def test_an_attempt_after_the_cohort_date_is_not_used() -> None:
    later = _stanford_system(attempted_at="2026-09-01T00:00:00Z")
    selected = select_discovery_evidence(
        [later],
        as_of="2026-08-14",
        url_digests=frozenset(
            {str(cast(dict[str, object], _by_slug()[STANFORD]["subject"])["requested_url_sha256"])}
        ),
    )
    assert selected == ()


def test_a_failed_discovery_attempt_carries_no_entries_and_is_not_selected() -> None:
    failed = {
        "attempted_at": "2026-08-14T00:00:00Z",
        "discovery": None,
        "domain": "stanfordhealthcare.org",
        "fetch": {
            "attempted_at": "2026-08-14T00:00:00Z",
            "attempts": 0,
            "status": "robots_disallowed",
            "url": "https://stanfordhealthcare.org/cms-hpt.txt",
        },
        "kind": "discovery",
        "problems": [],
        "url": "https://stanfordhealthcare.org/cms-hpt.txt",
        "version": 2,
    }
    document = reconcile(_rows(STANFORD, TRI_VALLEY), [failed])
    assert document["status"] == NO_DISCOVERY_EVIDENCE


# --- scope ------------------------------------------------------------------------------------


def test_a_single_location_publisher_gets_no_system_entry() -> None:
    """ "Single-location publishers get no system page and no empty section" (#74). An empty
    section would read as "reconciled, nothing to report"."""
    discovery = _discovery(
        "uchealth.com", [_entry("West Chester Hospital, LLC", _url(WEST_CHESTER))]
    )
    document = reconcile(_rows(WEST_CHESTER), [discovery])
    assert document["status"] == RECONCILED
    assert document["systems"] == []
    coverage = cast(dict[str, object], document["coverage"])
    assert coverage["rows_declared_by_a_listed_location"] == 1


def test_an_empty_cohort_is_refused() -> None:
    with pytest.raises(SystemsError, match="at least one assessment row"):
        reconcile([], [_stanford_system()])


# --- the command line ---------------------------------------------------------------------------


def test_the_cli_writes_a_json_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = tmp_path / "registry.jsonl"
    registry.write_text(json.dumps(_stanford_system(), sort_keys=True) + "\n", encoding="utf-8")
    status = cli.main(
        [
            "systems",
            "--assessments",
            str(COHORT),
            "--discovery",
            str(registry),
            "--format",
            "json",
        ]
    )
    assert status == 0
    document = json.loads(capsys.readouterr().out)
    assert document["systems_version"] == 1
    assert document["status"] == RECONCILED
    assert [system["publisher_id"] for system in document["systems"]] == ["stanford-health-care"]


def test_the_cli_prints_a_human_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = tmp_path / "registry.jsonl"
    registry.write_text(json.dumps(_stanford_system(), sort_keys=True) + "\n", encoding="utf-8")
    status = cli.main(["systems", "--assessments", str(COHORT), "--discovery", str(registry)])
    assert status == 0
    out = capsys.readouterr().out
    assert "stanford-health-care" in out
    assert "No file's grade or findings are read into any row above" in out
