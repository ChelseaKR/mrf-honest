"""The inventory census, and the four things it refuses to merge.

The graded side of every fixture is a **committed** assessment record, read through
``AssessmentRegistry`` exactly as the tool reads it, so a shape change in the assessment artifact
cannot leave these tests exercising a record the project no longer writes. Only the discovery
side is synthesized, because that is the side with branches to cover -- and because the local
discovery registry is deliberately not committed (`.gitignore`: ``data/registry*.jsonl``), which
is why this is a command rather than published output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import mrf_honest.cli as cli
from mrf_honest.census import (
    COUNTED,
    CSV_CANDIDATE,
    JSON_CANDIDATE,
    NO_DISCOVERY_EVIDENCE,
    NOT_DETERMINABLE,
    OUTSIDE_PROFILES,
    build_census,
    candidate_profile,
    graded_files,
    human_report,
    newest_attempt_per_origin,
)
from mrf_honest.scorecard import AssessmentRegistry

ROOT = Path(__file__).resolve().parent.parent
COHORT = ROOT / "data" / "cohorts" / "2026-08-14.assessments.jsonl"
STANFORD = "stanford-health-care/stanford-health-care"
TRI_VALLEY = "stanford-health-care/stanford-health-care-tri-valley"


def _records() -> list[dict[str, object]]:
    return [dict(record) for record in AssessmentRegistry(COHORT).records()]


def _by_slug() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for record in _records():
        subject = cast(dict[str, object], record["subject"])
        publisher = cast(dict[str, object], subject["publisher"])
        rows[f"{publisher['identifier']}/{subject['location_id']}"] = record
    return rows


def _url(slug: str) -> str:
    return str(cast(dict[str, object], _by_slug()[slug]["subject"])["requested_url"])


def _rows(*slugs: str) -> list[dict[str, object]]:
    by_slug = _by_slug()
    return [by_slug[slug] for slug in slugs]


def _entry(name: str | None, url: str | None, **extra: object) -> dict[str, object]:
    """One cms-hpt.txt block, including the two contact fields a real one carries."""
    return {
        "contact_email": "someone@example.org",
        "contact_name": "A Named Person",
        "extra_fields": [],
        "location_name": name,
        "mrf_url": url,
        "problems": [],
        "source_page_url": None,
        **extra,
    }


def _discovery(
    domain: str,
    entries: list[dict[str, object]],
    *,
    attempted_at: str = "2026-08-14T00:00:00Z",
    problems: list[str] | None = None,
) -> dict[str, object]:
    url = f"https://{domain}/cms-hpt.txt"
    return {
        "attempted_at": attempted_at,
        "discovery": {"domain": domain, "entries": entries, "problems": problems or []},
        "domain": domain,
        "fetch": {
            "attempted_at": attempted_at,
            "attempts": 1,
            "content_sha256": "a" * 64,
            "http_status": 200,
            "status": "fetched",
            "url": url,
        },
        "kind": "discovery",
        "problems": [],
        "url": url,
        "version": 2,
    }


def _no_body(
    domain: str, *, attempted_at: str = "2026-08-14T00:00:00Z", status: str = "robots_disallowed"
) -> dict[str, object]:
    url = f"https://{domain}/cms-hpt.txt"
    return {
        "attempted_at": attempted_at,
        "discovery": None,
        "domain": domain,
        "fetch": {
            "attempted_at": attempted_at,
            "attempts": 0,
            "content_sha256": None,
            "error": "unreachable: HTTP 301 (RFC 9309 2.3.1.4)",
            "http_status": 301,
            "status": status,
            "url": url,
        },
        "kind": "discovery",
        "problems": [],
        "url": url,
        "version": 2,
    }


def _stanford(**kwargs: object) -> dict[str, object]:
    return _discovery(
        "stanfordhealthcare.org",
        [
            _entry("Stanford Health Care", _url(STANFORD)),
            _entry("Stanford Health Care Tri-Valley", _url(TRI_VALLEY)),
            _entry(
                "Somewhere Else", "https://stanfordhealthcare.org/1_not-drawn_standardcharges.json"
            ),
        ],
        **cast(dict[str, str], kwargs),
    )


def _counts(document: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], document["populations"])


# --- what the census is for: located files, beside graded ones --------------------------------


def test_the_census_counts_files_it_located_that_no_cohort_graded() -> None:
    """The whole point. Two graded files, three located, and the difference is stated."""
    document = build_census([_stanford()], _rows(STANFORD, TRI_VALLEY), as_of="2026-08-14")
    counts = _counts(document)
    assert counts["distinct_files_located"] == 3
    assert counts["distinct_files_graded_in_a_committed_cohort"] == 2
    assert counts["distinct_files_located_and_not_graded_here"] == 1
    assert "not a fact about the publisher" in str(
        cast(dict[str, object], document["basis"])["not_graded_here"]
    )


def test_a_located_file_carries_its_grade_when_a_cohort_graded_it() -> None:
    document = build_census([_stanford()], _rows(STANFORD, TRI_VALLEY), as_of="2026-08-14")
    origin = cast(list[dict[str, object]], document["origins"])[0]
    locations = cast(list[dict[str, object]], origin["locations"])
    graded = [row for row in locations if row["graded"] is not None]
    assert len(graded) == 2
    assert all(str(cast(dict[str, object], row["graded"])["grade"]) in "ABCDF" for row in graded)
    ungraded = [row for row in locations if row["graded"] is None]
    assert len(ungraded) == 1
    assert ungraded[0]["mrf_url_sha256"] is not None, (
        "a file with no grade still has an address; that is the inventory this census counts"
    )


# --- the refusals ------------------------------------------------------------------------------


def test_contact_details_gathered_during_discovery_are_never_carried() -> None:
    """docs/CORRECTIONS.md promises this in terms, so it is asserted over the whole document."""
    document = build_census([_stanford()], _rows(STANFORD), as_of="2026-08-14")
    rendered = json.dumps(document)
    assert "someone@example.org" not in rendered
    assert "A Named Person" not in rendered
    assert "contact_email" not in rendered
    assert "contact_name" not in rendered


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://h.example/123456789_a-hospital_standardcharges.json", JSON_CANDIDATE),
        ("https://h.example/123456789_a-hospital_standardcharges.csv", CSV_CANDIDATE),
        ("https://h.example/prices.json", JSON_CANDIDATE),
        ("https://h.example/prices.csv", CSV_CANDIDATE),
        ("https://h.example/prices.zip", OUTSIDE_PROFILES),
        ("https://h.example/prices.xml", OUTSIDE_PROFILES),
        ("https://h.example/Handler.ashx?id=7", NOT_DETERMINABLE),
        ("https://h.example/api/v1/standardcharges", NOT_DETERMINABLE),
        ("https://h.example/", NOT_DETERMINABLE),
    ],
)
def test_a_url_extension_is_a_candidate_and_an_absent_one_is_its_own_population(
    url: str, expected: str
) -> None:
    """`.ashx` is the measured case: four such URLs cost 669,479,338 bytes to classify once."""
    assert candidate_profile(url) == expected


def test_an_undeterminable_format_is_never_folded_into_outside_the_profiles() -> None:
    """ "We cannot tell from here" and "we do not grade that" are different statements."""
    document = build_census(
        [
            _discovery(
                "vendor.example",
                [
                    _entry("A", "https://vendor.example/Handler.ashx?id=1"),
                    _entry("B", "https://vendor.example/prices.zip"),
                ],
            )
        ],
        _rows(STANFORD),
        as_of="2026-08-14",
    )
    by_profile = cast(
        dict[str, int], _counts(document)["distinct_files_located_by_candidate_profile"]
    )
    assert by_profile[NOT_DETERMINABLE] == 1
    assert by_profile[OUTSIDE_PROFILES] == 1


def test_an_origin_that_produced_no_body_is_its_own_population_with_its_reason() -> None:
    document = build_census(
        [_stanford(), _no_body("refused.example")], _rows(STANFORD), as_of="2026-08-14"
    )
    counts = _counts(document)
    assert counts["origins_with_a_readable_document"] == 1
    assert counts["origins_not_retrieved"] == 1
    assert counts["origins_whose_response_named_no_location"] == 0
    row = cast(list[dict[str, object]], document["origins_not_retrieved"])[0]
    assert row["domain"] == "refused.example"
    assert row["status"] == "robots_disallowed"
    assert "RFC 9309" in str(row["reason"])
    assert "never counted as an origin that publishes nothing" in str(
        cast(dict[str, object], document["basis"])["origin_not_retrieved"]
    )


def test_a_body_that_named_no_location_is_kept_apart_from_a_refused_request() -> None:
    """An HTTP 200 web page read as a discovery document is this portfolio's dominant defect.

    Three origins in the committed registry are in exactly this state, every one of them with
    the parser's own "served HTML rather than a cms-hpt.txt document". Counting them beside the
    robots-disallowed origins would say the server refused a request it answered.
    """
    document = build_census(
        [
            _stanford(),
            _discovery("html.example", [], problems=["served HTML rather than a cms-hpt.txt"]),
            _no_body("refused.example"),
        ],
        _rows(STANFORD),
        as_of="2026-08-14",
    )
    counts = _counts(document)
    assert counts["origins_not_retrieved"] == 1
    assert counts["origins_whose_response_named_no_location"] == 1
    row = cast(list[dict[str, object]], document["origins_whose_response_named_no_location"])[0]
    assert row["domain"] == "html.example"
    assert row["problems"] == ["served HTML rather than a cms-hpt.txt"]


def test_no_share_is_computed_against_the_frame() -> None:
    """The join between CMS facilities and the websites hosting their files does not exist."""
    frame = json.loads(
        (ROOT / "data" / "frames" / "2026-08-19.frame.json").read_text(encoding="utf-8")
    )
    document = build_census([_stanford()], _rows(STANFORD), as_of="2026-08-14", frame=frame)
    block = cast(dict[str, object], document["frame"])
    assert block["eligible_count"] == 3024
    assert "never divided" in str(block["basis"])

    counts = _counts(document)
    assert not any("rate" in key or "share" in key or "percent" in key for key in counts), counts
    assert all(isinstance(value, (int, dict)) for value in counts.values()), (
        "every population is a count; a float here would be a share across a join that does "
        "not exist"
    )
    report = human_report(document)
    assert "NOT a denominator" in report


def test_a_location_listed_with_no_mrf_url_is_counted_and_not_profiled() -> None:
    document = build_census(
        [_discovery("h.example", [_entry("A Hospital", None)])], _rows(STANFORD), as_of="2026-08-14"
    )
    counts = _counts(document)
    assert counts["locations_listed"] == 1
    assert counts["locations_listed_without_an_mrf_url"] == 1
    assert counts["distinct_files_located"] == 0
    row = cast(list[dict[str, object]], document["origins"])[0]
    location = cast(list[dict[str, object]], row["locations"])[0]
    assert location["candidate_profile"] is None, "there is no URL to read a candidate out of"


# --- selection ---------------------------------------------------------------------------------


def test_the_newest_attempt_is_taken_whether_or_not_it_parsed() -> None:
    """Selecting the newest that *parsed* would hide an origin that stopped being retrievable."""
    newest = newest_attempt_per_origin(
        [
            _stanford(attempted_at="2026-08-14T00:00:00Z"),
            _no_body("stanfordhealthcare.org", attempted_at="2026-08-14T12:00:00Z"),
        ],
        as_of="2026-08-14",
    )
    assert newest["stanfordhealthcare.org"]["attempted_at"] == "2026-08-14T12:00:00Z"


def test_an_attempt_after_the_census_date_is_not_read_back_onto_it() -> None:
    newest = newest_attempt_per_origin(
        [_stanford(attempted_at="2026-09-12T00:00:00Z")], as_of="2026-08-14"
    )
    assert newest == {}


def test_a_block_that_is_not_a_location_entry_is_counted_separately() -> None:
    """UPMC's ASCII-art banner, which `systems.py` measured as four invented locations."""
    document = build_census(
        [
            _discovery(
                "banner.example",
                [
                    _entry(None, None),
                    _entry("A Hospital", "https://banner.example/prices.json"),
                ],
            )
        ],
        _rows(STANFORD),
        as_of="2026-08-14",
    )
    origin = cast(list[dict[str, object]], document["origins"])[0]
    assert origin["locations_listed"] == 1
    blocks = cast(dict[str, object], origin["blocks_that_are_not_location_entries"])
    assert blocks["count"] == 1


def test_the_graded_join_is_the_digest_and_keeps_the_newest_row_per_url() -> None:
    """Joining on the published string cannot tell two vendor URLs apart (`systems.py`)."""
    files = graded_files(_rows(STANFORD, TRI_VALLEY))
    assert len(files) == 2
    assert all(len(digest) == 64 for digest in files)


def test_a_graded_row_no_located_file_matches_is_stated_rather_than_hidden() -> None:
    """A hospital that moved its file produces this, and it is not a defect in either record."""
    document = build_census(
        [_discovery("h.example", [_entry("A", "https://h.example/prices.json")])],
        _rows(STANFORD),
        as_of="2026-08-14",
    )
    assert _counts(document)["graded_rows_no_located_file_matches"] == 1
    assert "without anything being wrong" in human_report(document)


# --- the command -------------------------------------------------------------------------------


def test_the_cli_writes_a_json_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = tmp_path / "registry.jsonl"
    registry.write_text(json.dumps(_stanford(), sort_keys=True) + "\n", encoding="utf-8")
    status = cli.main(
        [
            "census",
            "--discovery",
            str(registry),
            "--assessments",
            str(COHORT),
            "--as-of",
            "2026-08-14",
            "--format",
            "json",
        ]
    )
    assert status == 0
    document = json.loads(capsys.readouterr().out)
    assert document["census_version"] == 1
    assert document["as_of"] == "2026-08-14"
    assert cast(dict[str, object], document["populations"])["distinct_files_located"] == 3


def test_the_cli_prints_a_human_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = tmp_path / "registry.jsonl"
    registry.write_text(json.dumps(_stanford(), sort_keys=True) + "\n", encoding="utf-8")
    status = cli.main(["census", "--discovery", str(registry), "--as-of", "2026-08-14"])
    assert status == 0
    out = capsys.readouterr().out
    assert "3 distinct file(s) located" in out
    assert "this project's collection scope, not a defect" in out


# --- a census that read nothing says so, rather than counting from nothing -------------------


def test_a_census_that_read_no_document_leaves_the_unmatched_count_undetermined() -> None:
    """Caught on the real registry-less path before this discriminator existed.

    With an empty registry the report said "17 graded row(s) match no located file", having
    looked at nothing. Zero located files beside a count of graded rows matching none of them
    reads as a finding about every one of those rows, and it is a statement nobody with no
    document in hand is in a position to make. `systems.py` carries the same discriminator for
    the same reason.
    """
    document = build_census([], _rows(STANFORD, TRI_VALLEY), as_of="2026-08-14")
    assert document["status"] == NO_DISCOVERY_EVIDENCE
    assert "absence of evidence" in str(document["reason"])
    counts = _counts(document)
    assert counts["graded_rows_no_located_file_matches"] is None
    assert counts["distinct_files_located"] == 0, (
        "the counts of things that *were* examined stay zero; zero is the truth for them"
    )
    report = human_report(document)
    assert "graded row(s) match no located file" not in report
    assert "absence of evidence" in report


def test_origins_that_produced_no_body_do_not_make_the_count_determined() -> None:
    """Attempting an origin and failing is not reading a document."""
    document = build_census([_no_body("refused.example")], _rows(STANFORD), as_of="2026-08-14")
    assert document["status"] == NO_DISCOVERY_EVIDENCE
    assert _counts(document)["graded_rows_no_located_file_matches"] is None
    assert _counts(document)["origins_not_retrieved"] == 1
    assert "refused.example" in human_report(document), (
        "what was attempted is still worth printing; the refusal replaces the count, not the report"
    )


def test_one_readable_document_is_enough_to_make_the_count_determined() -> None:
    document = build_census([_stanford()], _rows(STANFORD, TRI_VALLEY), as_of="2026-08-14")
    assert document["status"] == COUNTED
    assert document["reason"] is None
    assert _counts(document)["graded_rows_no_located_file_matches"] == 0
