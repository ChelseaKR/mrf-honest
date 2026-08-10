from __future__ import annotations

import pytest

from mrf_honest.discover import cms_hpt_url, parse_cms_hpt, parse_mrf_filename

# The URL shape was observed live on 2026-08-05; POC values are synthetic parser fixtures.
REAL = """location-name: Stanford Health Care
source-page-url: https://stanfordhealthcare.org/for-patients-visitors/price-transparency.html
mrf-url: https://stanfordhealthcare.org/content/dam/SHC/946174066_stanford-health-care_standardcharges.json
contact-name: Stanford MRF Team
contact-email: mrf@stanfordhealthcare.org
"""


def test_parses_a_real_shaped_file() -> None:
    d = parse_cms_hpt(REAL, domain="stanfordhealthcare.org")
    assert d.usable
    assert d.location_name == "Stanford Health Care"
    assert d.mrf_url.endswith("_standardcharges.json")
    assert d.contact_name == "Stanford MRF Team"
    assert d.contact_email == "mrf@stanfordhealthcare.org"
    assert len(d.entries) == 1
    assert d.problems == ()


def test_html_error_page_is_distinguished_from_a_missing_file() -> None:
    """A 200 that serves HTML is misconfigured, which is a different fact from absent."""
    d = parse_cms_hpt("<!DOCTYPE html><html><body>404</body></html>", domain="x.test")
    assert not d.usable
    assert "served HTML" in d.problems[0]


def test_empty_file_is_recorded() -> None:
    assert "empty" in parse_cms_hpt("   \n\n", domain="x.test").problems[0]


def test_bom_and_crlf_and_comments_tolerated() -> None:
    """Encoding and formatting noise showed up immediately in the wild; tolerate it."""
    text = "﻿# published per CMS\r\nMRF_URL:  https://x.test/a.json  \r\n"
    d = parse_cms_hpt(text, domain="x.test")
    assert d.usable and d.mrf_url == "https://x.test/a.json"


def test_non_https_mrf_url_is_refused_not_upgraded() -> None:
    """A price file fetched over plaintext is not verifiable, so it is refused outright."""
    d = parse_cms_hpt("mrf-url: http://x.test/a.json", domain="x.test")
    assert not d.usable
    assert any("not https" in p for p in d.problems)


@pytest.mark.parametrize(
    "url",
    [
        "https://[broken",
        "https://x.test:invalid/prices.json",
        "https://x.test:/prices.json",
        "https:///prices.json",
        "https://:443/prices.json",
    ],
)
def test_malformed_https_urls_are_findings_not_exceptions(url: str) -> None:
    d = parse_cms_hpt(f"mrf-url: {url}", domain="x.test")
    assert not d.usable
    assert any("not a resolvable URL" in problem for problem in d.problems)


@pytest.mark.parametrize(
    "url",
    [
        "https://x.test/a file.json",
        "https://x.test/a\tfile.json",
        "https://x.test/a\x00file.json",
    ],
)
def test_mrf_urls_with_whitespace_or_controls_are_refused(url: str) -> None:
    d = parse_cms_hpt(f"mrf-url: {url}", domain="x.test")
    assert not d.usable
    assert any("whitespace or control" in problem for problem in d.problems)


def test_mrf_url_with_credentials_is_not_usable_by_the_fetch_pipeline() -> None:
    d = parse_cms_hpt("mrf-url: https://user:secret@x.test/prices.json", domain="x.test")
    assert not d.usable
    assert any("credentials" in problem for problem in d.problems)


def test_complete_entry_records_invalid_source_page_and_contact_email() -> None:
    text = """location-name: Example Hospital
source-page-url: not-a-url
mrf-url: https://files.example.test/prices.json
contact-name: Example Team
contact-email: not-an-email
"""

    discovery = parse_cms_hpt(text, domain="example.test")

    assert discovery.usable
    assert any("source-page-url" in problem for problem in discovery.problems)
    assert any("contact-email" in problem for problem in discovery.problems)


def test_missing_and_malformed_fields_become_problems_not_exceptions() -> None:
    d = parse_cms_hpt("location-name: Only A Name\nnonsense line\n", domain="x.test")
    assert not d.usable
    assert any("no mrf-url" in p for p in d.problems)
    assert any("unparseable line" in p for p in d.problems)


def test_duplicate_field_keeps_first_and_records_it() -> None:
    d = parse_cms_hpt(
        "mrf-url: https://a.test/1.json\nmrf-url: https://b.test/2.json", domain="x.test"
    )
    assert d.mrf_url == "https://a.test/1.json"
    assert any("more than once" in p for p in d.problems)


def test_multiple_complete_location_blocks_are_distinct_entries() -> None:
    text = """location-name: Hospital East
source-page-url: https://system.test/prices
mrf-url: https://system.test/east.json
contact-name: East MRF Team
contact-email: east@system.test
vendor-id: east-1

location-name: Hospital West
source-page-url: https://system.test/prices
mrf-url: https://system.test/west.json
contact-name: West MRF Team
contact-email: west@system.test
vendor-id: west-2
"""

    discovery = parse_cms_hpt(text, domain="system.test")

    assert discovery.usable
    assert discovery.problems == ()
    assert len(discovery.entries) == 2
    assert discovery.entries[0].mrf_url == "https://system.test/east.json"
    assert discovery.entries[0].extra_fields == (("vendor-id", "east-1"),)
    assert discovery.entries[1].mrf_url == "https://system.test/west.json"
    assert discovery.entries[1].extra_fields == (("vendor-id", "west-2"),)


def test_adjacent_generator_blocks_can_repeat_location_name_without_duplicate_problem() -> None:
    text = """location-name: Hospital East
source-page-url: https://system.test/prices
mrf-url: https://system.test/east.json
contact-name: MRF Team
contact-email: mrf@system.test
location-name: Hospital West
source-page-url: https://system.test/prices
mrf-url: https://system.test/west.json
contact-name: MRF Team
contact-email: mrf@system.test
"""

    discovery = parse_cms_hpt(text, domain="system.test")

    assert len(discovery.entries) == 2
    assert discovery.problems == ()


def test_adjacent_complete_blocks_can_restart_with_any_known_field() -> None:
    text = """location-name: Hospital East
source-page-url: https://system.test/prices
mrf-url: https://system.test/east.json
contact-name: East Team
contact-email: east@system.test
mrf-url: https://system.test/west.json
contact-email: west@system.test
contact-name: West Team
location-name: Hospital West
source-page-url: https://system.test/prices
"""

    discovery = parse_cms_hpt(text, domain="system.test")

    assert len(discovery.entries) == 2
    assert discovery.entries[1].location_name == "Hospital West"
    assert discovery.entries[1].mrf_url == "https://system.test/west.json"
    assert discovery.problems == ()


def test_each_entry_retains_its_own_required_field_problems() -> None:
    text = """location-name: Incomplete
mrf-url: https://system.test/incomplete.json

location-name: Complete
source-page-url: https://system.test/prices
mrf-url: https://system.test/complete.json
contact-name: MRF Team
contact-email: mrf@system.test
"""

    discovery = parse_cms_hpt(text, domain="system.test")

    assert discovery.usable
    assert any("no contact-name" in problem for problem in discovery.entries[0].problems)
    assert any("no contact-email" in problem for problem in discovery.entries[0].problems)
    assert discovery.entries[1].problems == ()


def test_empty_value_recorded() -> None:
    d = parse_cms_hpt("mrf-url:\nlocation-name: X", domain="x.test")
    assert not d.usable
    assert any("present but empty" in p for p in d.problems)


def test_unknown_fields_are_preserved_as_evidence() -> None:
    d = parse_cms_hpt(f"{REAL}vendor-id: hospital-42\n", domain="x.test")
    assert ("vendor-id", "hospital-42") in d.extra_fields


def test_filename_convention_yields_the_filer_ein() -> None:
    f = parse_mrf_filename("https://x.test/dam/946174066_stanford-health-care_standardcharges.json")
    assert f.follows_convention
    assert f.ein == "946174066"
    assert f.hospital_name == "stanford health care"
    assert f.extension == "json"


@pytest.mark.parametrize(
    "url,ext",
    [
        ("https://x.test/prices.json", "json"),
        ("https://x.test/charges.csv", "csv"),
        ("https://x.test/123456789_hospital_standardcharges.xlsx", "xlsx"),
        ("https://x.test/nofile", None),
    ],
)
def test_nonconforming_filenames_still_report_what_they_can(url: str, ext: str | None) -> None:
    f = parse_mrf_filename(url)
    assert not f.follows_convention
    assert f.ein is None
    assert f.extension == ext


def test_cms_hpt_url_from_domain_or_url() -> None:
    assert cms_hpt_url("x.test") == "https://x.test/cms-hpt.txt"
    assert cms_hpt_url("https://x.test/some/page") == "https://x.test/cms-hpt.txt"
    assert cms_hpt_url(" x.test/ ") == "https://x.test/cms-hpt.txt"
    assert cms_hpt_url("x.test/some/page") == "https://x.test/cms-hpt.txt"
