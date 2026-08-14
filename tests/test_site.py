"""Static-site generation tests: real comparisons in, accessible fail-closed HTML out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from test_cohort import (
    GENERATED_AT,
    _failure_record,
    _manifest,
    _subject,
    _success_record,
    _two_records,
)

from mrf_honest.cohort import build_comparison
from mrf_honest.fetch import FetchStatus
from mrf_honest.site import DEFAULT_ORIGIN, render_site


def _comparison(tmp_path: Path) -> dict[str, object]:
    records = _two_records(tmp_path / "bodies")
    content = cast(dict[str, object], records[0]["retrieval"])["content_sha256"]
    ingest = {
        "run_id": "r1",
        "source_file_id": content,
        "publisher_id": "alpha-health",
        "status": "success",
        "reused": False,
        "counts": {"items": 1, "payer_rates": 1},
    }
    return build_comparison(
        records, _manifest(), ingest_results=[ingest], generated_at=GENERATED_AT
    )


def _render(tmp_path: Path, comparison: dict[str, object]) -> Path:
    out = tmp_path / "site"
    render_site(comparison, out)
    return out


def test_index_lists_every_file_with_generated_numbers(tmp_path: Path) -> None:
    out = _render(tmp_path, _comparison(tmp_path))
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "<strong>2</strong> machine-readable files" in index
    assert 'href="hospital/alpha-health/main/"' in index
    assert 'href="hospital/beta-health/north/"' in index
    assert "held-out" not in index  # exclusion ids are internal; names are rendered
    assert "out of profile" in index  # the exclusion reason is stated
    assert f'<link rel="canonical" href="{DEFAULT_ORIGIN}/">' in index
    assert '<html lang="en">' in index
    assert "Skip to main content" in index


def test_file_page_carries_grade_findings_and_provenance(tmp_path: Path) -> None:
    comparison = _comparison(tmp_path)
    out = _render(tmp_path, comparison)
    page = (out / "hospital" / "beta-health" / "north" / "index.html").read_text(
        encoding="utf-8"
    )
    assert ">B<" in page
    assert "FRESHNESS_ANNUAL_UPDATE_OVERDUE" in page
    assert "how-we-grade/#FRESHNESS_ANNUAL_UPDATE_OVERDUE" in page
    assert "Content SHA-256" in page
    rows = cast(list[dict[str, object]], comparison["files"])
    beta = next(row for row in rows if row["slug"] == "beta-health/north")
    assert str(beta["content_sha256"]) in page
    # This file was not ingested; the absence of contract evidence is stated, never implied.
    assert "no contract evidence exists" in page
    assert "Observed at" in page


def test_ingested_file_page_states_contract_evidence(tmp_path: Path) -> None:
    out = _render(tmp_path, _comparison(tmp_path))
    page = (out / "hospital" / "alpha-health" / "main" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "Warehouse contracts" in page
    assert "<code>success</code>" in page
    assert "<code>r1</code>" in page


def test_observed_dimension_is_not_presented_as_a_certificate(tmp_path: Path) -> None:
    out = _render(tmp_path, _comparison(tmp_path))
    page = (out / "hospital" / "alpha-health" / "main" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "not a certificate that the data is valid" in page


def test_not_graded_target_stays_visible_with_its_reason(tmp_path: Path) -> None:
    records = [
        _success_record(tmp_path / "bodies", subject=_subject("alpha-health", "main")),
        _failure_record(FetchStatus.TOO_LARGE, subject=_subject("gamma-health", "main")),
    ]
    comparison = build_comparison(records, _manifest(), generated_at=GENERATED_AT)
    out = _render(tmp_path, comparison)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Not graded" in index
    page = (out / "hospital" / "gamma-health" / "main" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "decoded-byte ceiling" in page
    assert "no item, charge, or rate counts exist" in page


def test_methods_page_documents_the_policy_and_emitted_codes(tmp_path: Path) -> None:
    out = _render(tmp_path, _comparison(tmp_path))
    methods = (out / "how-we-grade" / "index.html").read_text(encoding="utf-8")
    assert "cms-hospital-json-v3-file-grade-v1" in methods
    assert 'id="FRESHNESS_ANNUAL_UPDATE_OVERDUE"' in methods
    assert "ecfr.gov" in methods
    assert "What is deliberately not checked" in methods
    assert "not the official CMS validator" in methods


def test_sitemap_robots_404_and_data_export(tmp_path: Path) -> None:
    comparison = _comparison(tmp_path)
    out = _render(tmp_path, comparison)
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert f"<loc>{DEFAULT_ORIGIN}/hospital/alpha-health/main/</loc>" in sitemap
    assert "404" not in sitemap
    robots = (out / "robots.txt").read_text(encoding="utf-8")
    assert robots == f"User-agent: *\nAllow: /\nSitemap: {DEFAULT_ORIGIN}/sitemap.xml\n"
    assert (out / "404.html").is_file()
    exported = json.loads((out / "data" / "comparison.json").read_text(encoding="utf-8"))
    assert exported == comparison


def test_untrusted_text_is_escaped(tmp_path: Path) -> None:
    records = _two_records(tmp_path / "bodies")
    mutated = cast(dict[str, object], records[0])
    subject = cast(dict[str, object], dict(cast(dict[str, object], mutated["subject"])))
    publisher = dict(cast(dict[str, object], subject["publisher"]))
    publisher["name"] = 'Alpha & <script>alert("x")</script> Health'
    subject["publisher"] = publisher
    # Rebuild through the comparison path is impossible with a tampered record (integrity
    # digests), so escape-check the renderer directly on the built comparison instead.
    comparison = build_comparison(records, _manifest(), generated_at=GENERATED_AT)
    rows = cast(list[dict[str, object]], comparison["files"])
    rows[0]["publisher_name"] = publisher["name"]
    out = _render(tmp_path, comparison)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "<script>alert" not in index
    assert "Alpha &amp; &lt;script&gt;" in index


def test_cli_site_renders_from_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from mrf_honest.cli import main

    comparison = _comparison(tmp_path)
    comparison_path = tmp_path / "comparison.json"
    comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
    out = tmp_path / "public"
    status = main(
        [
            "site",
            "--comparison",
            str(comparison_path),
            "--out",
            str(out),
            "--origin",
            "https://example.test/mrf-honest/",
        ]
    )
    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files_written"] > 0
    index = (out / "index.html").read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://example.test/mrf-honest/">' in index
