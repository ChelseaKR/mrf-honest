"""Static-site generation tests: real comparisons in, accessible fail-closed HTML out."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import cast

import pytest
from test_cohort import (
    GENERATED_AT,
    _failure_record,
    _framed_manifest,
    _manifest,
    _subject,
    _success_record,
    _two_records,
)

from mrf_honest.cohort import build_comparison
from mrf_honest.fetch import FetchStatus
from mrf_honest.site import (
    CORRECTIONS_URL,
    DEFAULT_ORIGIN,
    MIN_CONTRAST,
    NON_TEXT_TOKENS,
    PALETTE,
    SHARES_HEADING,
    SHARES_REFUSAL,
    TEXT_ON_BACKGROUND,
    contrast_ratio,
    missing_shares,
    render_site,
)


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
    page = (out / "hospital" / "beta-health" / "north" / "index.html").read_text(encoding="utf-8")
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
    page = (out / "hospital" / "alpha-health" / "main" / "index.html").read_text(encoding="utf-8")
    assert "Warehouse contracts" in page
    assert "<code>success</code>" in page
    assert "<code>r1</code>" in page


def test_refused_ingest_page_states_the_reason_not_a_bare_absence(tmp_path: Path) -> None:
    """The published page has to say *why* no contract evidence exists.

    Cedars-Sinai's page shipped the bare-absence sentence for a file this project's own v3-only
    warehouse had refused. docs/how-we-compare.md: "A project limit or operator problem is not a
    publisher failure ... The reason is always stated." It was not stated, and a reader had no
    way to tell a project limit from an unnamed defect in the hospital's file.
    """
    records = _two_records(tmp_path / "bodies")
    content = cast(dict[str, object], records[1]["retrieval"])["content_sha256"]
    comparison = build_comparison(
        records,
        _manifest(),
        ingest_results=[
            {
                "status": "refused",
                "source_file_id": content,
                "publisher_id": "beta-health",
                "reason": "unsupported hospital JSON template version: '2.0.0'",
                "implemented_scope": "CMS hospital JSON template version 3.0.0",
                "observed_scope": "CMS hospital JSON template version 2.0.0",
            }
        ],
        generated_at=GENERATED_AT,
    )
    out = _render(tmp_path, comparison)
    page = (out / "hospital" / "beta-health" / "north" / "index.html").read_text(encoding="utf-8")
    assert "unsupported hospital JSON template version: &#x27;2.0.0&#x27;" in page
    assert "CMS hospital JSON template version 3.0.0" in page
    assert "CMS hospital JSON template version 2.0.0" in page
    assert "limit of what this project implements, not a finding about the file" in page
    assert "does not affect the grade above" in page
    # and it must not fall back to the sentence used when nothing was ever attempted
    assert "No warehouse ingest was recorded" not in page


def test_observed_dimension_is_not_presented_as_a_certificate(tmp_path: Path) -> None:
    out = _render(tmp_path, _comparison(tmp_path))
    page = (out / "hospital" / "alpha-health" / "main" / "index.html").read_text(encoding="utf-8")
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
    page = (out / "hospital" / "gamma-health" / "main" / "index.html").read_text(encoding="utf-8")
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


def test_every_indexable_page_names_itself_and_not_the_shared_origin(
    tmp_path: Path,
) -> None:
    """Canonical and card tags on every page, naming that page under this project's path.

    These pages are one of six project sites on the shared `chelseakr.github.io` origin,
    served from PATHS rather than from domains of their own. So a canonical of "/" is not
    a shorter spelling of this site's root: it is a different address, and all six sites
    would claim the identical one. A crawler that believes them folds six unrelated
    projects into one document.

    Every page already had a canonical. None had any Open Graph or Twitter tag, so a
    shared link to a hospital's grade previewed as a bare URL with no title and no
    description. This holds both, and holds them against each other, so a card cannot
    drift into describing a different page than the one it sits on.
    """
    out = _render(tmp_path, _comparison(tmp_path))
    expected = {
        "index.html": f"{DEFAULT_ORIGIN}/",
        "how-we-grade/index.html": f"{DEFAULT_ORIGIN}/how-we-grade/",
        "hospital/alpha-health/main/index.html": (f"{DEFAULT_ORIGIN}/hospital/alpha-health/main/"),
    }
    for name, url in expected.items():
        html = (out / name).read_text(encoding="utf-8")

        canonical = re.search(r'<link rel="canonical" href="([^"]*)">', html)
        assert canonical, f"{name} has no canonical URL"
        assert canonical.group(1) == url, (
            f"{name} canonicalises to {canonical.group(1)!r}, not {url!r}"
        )
        assert canonical.group(1).rstrip("/") != "https://chelseakr.github.io", (
            f"{name} points at the shared origin, which is a different site"
        )

        def meta(attribute: str, key: str, *, page: str = html, where: str = name) -> str:
            found = re.search(rf'<meta {attribute}="{key}" content="([^"]*)">', page)
            assert found, f"{where} has no {key}"
            return found.group(1)

        assert meta("property", "og:url") == url, f"{name} og:url disagrees with canonical"
        title = re.search(r"<title>([^<]*)</title>", html)
        assert title, f"{name} has no title"
        assert meta("property", "og:title") == title.group(1), f"{name} og:title disagrees"
        assert meta("property", "og:description") == meta("name", "description"), name
        assert meta("property", "og:type") == "website", name

        # This project ships no image at all, and `perf/resource-budget.json` sets
        # `max_request_count.image` to 0 so that adding one is a failed build rather than
        # a change nobody noticed. The card must therefore not promise an image it has
        # none of. If an image is ever committed, this fails until og:image comes with it.
        card = meta("name", "twitter:card")
        assert card in ("summary", "summary_large_image"), f"{name} card is {card!r}"
        if card == "summary_large_image":
            assert meta("property", "og:image"), f"{name} promises an image it lacks"


def test_the_error_page_canonicalises_nowhere_and_is_not_indexable(
    tmp_path: Path,
) -> None:
    """The error page is written to `404.html`, but its `Page.path` is `"404"`.

    So the canonical built from that path was `{origin}/404/`, an address that does not
    exist and that itself returns 404: the page told crawlers its preferred URL was a
    dead one. An error page has no canonical URL to state, and should not be indexed at
    all, so it now says `noindex` and states nothing.
    """
    out = _render(tmp_path, _comparison(tmp_path))
    not_found = (out / "404.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex">' in not_found
    assert 'rel="canonical"' not in not_found, (
        "the error page claims a canonical URL; the one it used to claim was a 404"
    )
    assert "og:url" not in not_found


def test_the_index_does_not_pool_cohorts_its_own_page_keeps_apart(
    tmp_path: Path,
) -> None:
    """The meta description must not state a total the page refuses to state.

    Cohorts are rendered as separate sections on purpose: their rows were assessed under
    different profiles and must never be pooled into one distribution
    (`docs/how-we-compare.md`). The description was the one place in the project that
    summed every cohort's `targeted` into a single figure, and a search result is the
    surface where nobody rechecks it. It now states no count.
    """
    out = _render(tmp_path, _comparison(tmp_path))
    index = (out / "index.html").read_text(encoding="utf-8")
    description = re.search(r'<meta name="description" content="([^"]*)">', index)
    assert description, "the index has no description"
    assert not re.search(r"[0-9]", description.group(1)), (
        f"the description states a count: {description.group(1)!r}"
    )


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


def test_every_palette_colour_is_covered_by_a_declared_contrast_pair() -> None:
    """A new colour token must declare where it is read, or the gate is decorative.

    Without this the contrast test would only ever check the pairs someone remembered to add,
    which is the failure mode it exists to prevent.
    """
    declared = {token for pair in TEXT_ON_BACKGROUND for token in pair[:2]}
    unaccounted = set(PALETTE) - declared - NON_TEXT_TOKENS
    assert unaccounted == set(), (
        f"palette tokens with no declared contrast pair and not marked non-text: {unaccounted}"
    )
    assert NON_TEXT_TOKENS <= set(PALETTE)


@pytest.mark.parametrize(("foreground", "background", "where"), TEXT_ON_BACKGROUND)
def test_declared_text_pairs_meet_wcag_aa(foreground: str, background: str, where: str) -> None:
    ratio = contrast_ratio(PALETTE[foreground], PALETTE[background])
    assert ratio >= MIN_CONTRAST, (
        f"{where}: --{foreground} on --{background} is {ratio:.2f}:1, "
        f"below the {MIN_CONTRAST}:1 floor of WCAG 2.2 SC 1.4.3"
    )


def test_contrast_ratio_matches_known_wcag_values() -> None:
    """Anchor the maths, so a broken formula cannot quietly pass every pair above."""
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    # The exact combination that shipped: --c on the amber wash, measured by axe at 4.28.
    assert contrast_ratio("#a35d00", "#f6ead8") == pytest.approx(4.29, abs=0.01)
    assert contrast_ratio("#a35d00", "#f6ead8") < MIN_CONTRAST


def test_stylesheet_is_generated_from_the_palette(tmp_path: Path) -> None:
    """The audited page must embed the same colours the test above checks."""
    out = _render(tmp_path, _comparison(tmp_path))
    index = (out / "index.html").read_text(encoding="utf-8")
    for token, value in PALETTE.items():
        assert f"--{token}: {value};" in index
    # and no colour is hard-coded past the token layer
    hexes = set(re.findall(r"#[0-9a-fA-F]{6}", index.split("<style>")[1].split("</style>")[0]))
    assert hexes <= set(PALETTE.values()), (
        f"stylesheet hard-codes colours: {hexes - set(PALETTE.values())}"
    )


def test_index_headings_descend_without_skipping_a_level(tmp_path: Path) -> None:
    """axe `heading-order`: the cards' <h3> used to follow <h1> with no <h2> between them."""
    out = _render(tmp_path, _comparison(tmp_path))
    for page in sorted(out.rglob("*.html")):
        levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", page.read_text(encoding="utf-8"))]
        assert levels and levels[0] == 1, f"{page} does not start at h1: {levels}"
        assert levels.count(1) == 1, f"{page} has {levels.count(1)} h1 elements"
        for previous, current in itertools.pairwise(levels):
            assert current <= previous + 1, f"{page} jumps from h{previous} to h{current}"


def test_the_index_and_methods_pages_state_whether_the_cohort_has_a_sampling_frame(
    tmp_path: Path,
) -> None:
    """A grade distribution is read as a picture of the landscape unless the page says otherwise.

    The first cohort had no sampling frame and its pages did not say so, which left the caveat to
    the reader's charity. Both sentences are derived from the manifest rather than hard-coded, so
    a framed cohort cannot render the unframed disclaimer and an unframed one cannot imply a
    frame it does not have.
    """
    unframed = _comparison(tmp_path)
    out = _render(tmp_path / "unframed", unframed)
    index = (out / "index.html").read_text(encoding="utf-8")
    methods = (out / "how-we-grade" / "index.html").read_text(encoding="utf-8")
    assert "nothing here is a sample of hospitals as a class" in index
    assert "This cohort has no stated sampling frame" in methods

    framed = _comparison(tmp_path)
    cast(dict[str, object], framed["collection"])["sampling_frame"] = {
        "document": "docs/SAMPLING-FRAME.md",
        "summary": "A seeded random draw from a stated universe.",
        "format_rule": "Only CMS hospital JSON documents are graded.",
    }
    out = _render(tmp_path / "framed", framed)
    index = (out / "index.html").read_text(encoding="utf-8")
    methods = (out / "how-we-grade" / "index.html").read_text(encoding="utf-8")
    assert "Subjects were drawn under a stated sampling frame" in index
    assert "nothing here is a sample of hospitals as a class" not in index
    assert "A seeded random draw from a stated universe." in methods
    assert "Only CMS hospital JSON documents are graded." in methods
    assert "docs/SAMPLING-FRAME.md" in methods
    assert "This cohort has no stated sampling frame" not in methods


# --- the statistics section (phase 7) -------------------------------------------------------


def _index_html(tmp_path: Path, manifest: dict[str, object]) -> str:
    comparison = build_comparison(
        _two_records(tmp_path / "bodies"), manifest, generated_at=GENERATED_AT
    )
    out = tmp_path / "site"
    render_site(comparison, out, origin=DEFAULT_ORIGIN)
    return (out / "index.html").read_text(encoding="utf-8")


def test_shares_reach_the_page_with_their_denominator_and_interval(tmp_path: Path) -> None:
    html = _index_html(tmp_path, _framed_manifest(sample_size=22, exclusions=20))
    assert "What share of the drawn sample this is" in html
    assert "2 of 22" in html
    assert "20 of 22" in html
    assert "95% interval" in html or "95%" in html
    assert "wilson-score" in html


def test_a_refusal_renders_as_a_refusal_and_not_as_a_missing_section(tmp_path: Path) -> None:
    """The section is the point. Dropping it would teach a reader that no number means there
    was nothing to say, which is the reading this project exists to prevent."""

    html = _index_html(tmp_path, _framed_manifest(sample_size=48, exclusions=20))
    assert "What share of the drawn sample this is" in html
    assert "No share is published for this cohort" in html
    assert "accounts for only part of its stratum" in html
    assert "covers 22 facilities" in html


def test_an_unframed_cohort_states_that_it_has_no_population(tmp_path: Path) -> None:
    html = _index_html(tmp_path, _manifest())
    assert "No share is published for this cohort" in html
    assert "records no sampling frame" in html


def test_a_document_predating_the_statistics_layer_says_so(tmp_path: Path) -> None:
    """A version-2 document rendered by version-3 code must not render as a refusal, because
    "this cohort could not be estimated" and "nobody asked" are different facts."""

    comparison = build_comparison(
        _two_records(tmp_path / "bodies"),
        _framed_manifest(sample_size=22, exclusions=20),
        generated_at=GENERATED_AT,
    )
    del comparison["statistics"]
    out = tmp_path / "legacy"
    render_site(comparison, out, origin=DEFAULT_ORIGIN)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "predates the statistics layer" in html
    assert "No share is published for this cohort" not in html


def test_the_shares_table_keeps_its_heading_order_and_scopes(tmp_path: Path) -> None:
    html = _index_html(tmp_path, _framed_manifest(sample_size=22, exclusions=20))
    assert '<table class="shares">' in html
    assert '<th scope="col">Disposition</th>' in html
    assert '<th scope="row">published as a row of this cohort</th>' in html


def test_missing_shares_is_silent_when_the_page_carries_the_document(tmp_path: Path) -> None:
    comparison = build_comparison(
        _two_records(tmp_path / "bodies"),
        _framed_manifest(sample_size=22, exclusions=20),
        generated_at=GENERATED_AT,
    )
    out = tmp_path / "site"
    render_site(comparison, out, origin=DEFAULT_ORIGIN)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert missing_shares(comparison, html) == []


def test_missing_shares_names_each_share_the_page_dropped(tmp_path: Path) -> None:
    """This is the deploy path's check. It has to be able to fail, so here it is failing."""

    comparison = build_comparison(
        _two_records(tmp_path / "bodies"),
        _framed_manifest(sample_size=22, exclusions=20),
        generated_at=GENERATED_AT,
    )
    out = tmp_path / "site"
    render_site(comparison, out, origin=DEFAULT_ORIGIN)
    html = (out / "index.html").read_text(encoding="utf-8")
    stripped = re.sub(r'<table class="shares">.*?</table>', "", html, flags=re.S)
    problems = missing_shares(comparison, stripped)
    assert "2 of 22 is in the document but not on the page" in problems
    assert "20 of 22 is in the document but not on the page" in problems


def test_missing_shares_catches_a_dropped_refusal(tmp_path: Path) -> None:
    comparison = build_comparison(
        _two_records(tmp_path / "bodies"),
        _framed_manifest(sample_size=48, exclusions=20),
        generated_at=GENERATED_AT,
    )
    out = tmp_path / "site"
    render_site(comparison, out, origin=DEFAULT_ORIGIN)
    html = (out / "index.html").read_text(encoding="utf-8").replace(SHARES_REFUSAL, "nothing here")
    assert missing_shares(comparison, html) == ["a refused cohort rendered no refusal on the page"]


def test_missing_shares_catches_a_document_with_no_block_at_all(tmp_path: Path) -> None:
    assert missing_shares({}, "<html></html>") == [
        "the comparison document carries no statistics block"
    ]


def test_missing_shares_catches_a_page_that_lost_the_whole_section(tmp_path: Path) -> None:
    comparison = build_comparison(
        _two_records(tmp_path / "bodies"),
        _framed_manifest(sample_size=22, exclusions=20),
        generated_at=GENERATED_AT,
    )
    problems = missing_shares(comparison, "<html>no section</html>")
    assert problems == [f"the page does not carry the heading {SHARES_HEADING!r}"]


def test_missing_shares_catches_a_block_with_neither_estimate_nor_reason(tmp_path: Path) -> None:
    comparison = {"statistics": {"estimates": [], "refusal": None}}
    html = f"<h3>{SHARES_HEADING}</h3>"
    assert missing_shares(comparison, html) == [
        "the document carries neither an estimate nor a stated refusal"
    ]


def test_every_page_carries_the_route_to_a_correction(tmp_path: Path) -> None:
    """A subject who finds a wrong grade lands on that file's page, not on the index, so the
    route out has to be on the page they actually reach."""

    comparison = build_comparison(
        _two_records(tmp_path / "bodies"), _manifest(), generated_at=GENERATED_AT
    )
    out = tmp_path / "site"
    render_site(comparison, out, origin=DEFAULT_ORIGIN)
    pages = list(out.rglob("*.html"))
    assert pages
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert CORRECTIONS_URL in html, f"{page} has no correction route"
        assert "you are not asked to prove anything" in html.lower()
