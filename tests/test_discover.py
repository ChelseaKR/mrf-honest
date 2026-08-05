from __future__ import annotations

import pytest

from mrf_honest.discover import cms_hpt_url, parse_cms_hpt, parse_mrf_filename

# Shape observed live from stanfordhealthcare.org on 2026-08-05.
REAL = """location-name: Stanford Health Care
source-page-url: https://stanfordhealthcare.org/for-patients-visitors/price-transparency.html
mrf-url: https://stanfordhealthcare.org/content/dam/SHC/946174066_stanford-health-care_standardcharges.json
"""


def test_parses_a_real_shaped_file() -> None:
    d = parse_cms_hpt(REAL, domain="stanfordhealthcare.org")
    assert d.usable
    assert d.location_name == "Stanford Health Care"
    assert d.mrf_url.endswith("_standardcharges.json")
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


def test_missing_and_malformed_fields_become_problems_not_exceptions() -> None:
    d = parse_cms_hpt("location-name: Only A Name\nnonsense line\n", domain="x.test")
    assert not d.usable
    assert any("no mrf-url" in p for p in d.problems)
    assert any("unparseable line" in p for p in d.problems)


def test_duplicate_field_keeps_first_and_records_it() -> None:
    d = parse_cms_hpt("mrf-url: https://a.test/1.json\nmrf-url: https://b.test/2.json",
                      domain="x.test")
    assert d.mrf_url == "https://a.test/1.json"
    assert any("more than once" in p for p in d.problems)


def test_empty_value_recorded() -> None:
    d = parse_cms_hpt("mrf-url:\nlocation-name: X", domain="x.test")
    assert not d.usable
    assert any("present but empty" in p for p in d.problems)


def test_unknown_fields_are_preserved_as_evidence() -> None:
    d = parse_cms_hpt(f"{REAL}contact-email: billing@x.test\n", domain="x.test")
    assert ("contact-email", "billing@x.test") in d.extra_fields


def test_filename_convention_yields_the_filer_ein() -> None:
    f = parse_mrf_filename(
        "https://x.test/dam/946174066_stanford-health-care_standardcharges.json")
    assert f.follows_convention
    assert f.ein == "946174066"
    assert f.hospital_name == "stanford health care"
    assert f.extension == "json"


@pytest.mark.parametrize("url,ext", [
    ("https://x.test/prices.json", "json"),
    ("https://x.test/charges.csv", "csv"),
    ("https://x.test/nofile", None),
])
def test_nonconforming_filenames_still_report_what_they_can(url: str, ext: str | None) -> None:
    f = parse_mrf_filename(url)
    assert not f.follows_convention
    assert f.ein is None
    assert f.extension == ext


def test_cms_hpt_url_from_domain_or_url() -> None:
    assert cms_hpt_url("x.test") == "https://x.test/cms-hpt.txt"
    assert cms_hpt_url("https://x.test/some/page") == "https://x.test/cms-hpt.txt"
    assert cms_hpt_url(" x.test/ ") == "https://x.test/cms-hpt.txt"
