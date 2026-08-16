"""Static site generation for one published cohort comparison.

Deterministic and dependency-free, matching the sibling scorecards: every page is real HTML with
its own title, description, canonical URL, and structured data, because a single-page report is
not something a person can find from a search or link a colleague to. Every number on every page
comes from the comparison document that ``mrf-honest compare`` generated; nothing is typed in.

The voice is fail-closed on purpose. A grade describes one published file under one stated
policy on one date. Absence of a check is stated, never implied as a pass, and `NOT_GRADED`
rows stay visible instead of disappearing from the cohort.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mrf_honest.cohort import LOCAL_DIMENSIONS, NOT_GRADED
from mrf_honest.inspect import FINDING_CATALOG
from mrf_honest.scorecard import RETRIEVAL_FINDING_CATALOG

DEFAULT_ORIGIN = "https://chelseakr.github.io/mrf-honest"

_GRADE_WORDS = {
    "A": "no error or warning findings in any assessed dimension",
    "B": "no structural errors; warnings were recorded",
    "C": "errors or missing evidence in one dimension",
    "D": "errors or missing evidence in two dimensions",
    "F": "not usable as retrieved, or errors in three or more dimensions",
    NOT_GRADED: "not graded; a local limit prevented assessment",
}

_STATUS_WORDS = {
    "OBSERVED": "no catalog finding emitted over the assessed scope",
    "FINDINGS": "at least one finding was emitted",
    "NOT_ASSESSED": "the evidence needed for this dimension was not available",
}

_DIMENSION_TITLES = {
    "retrievability": "Retrievability",
    "conformance": "Conformance",
    "completeness": "Completeness",
    "interpretability": "Interpretability",
    "freshness": "Freshness",
}

_CAVEAT = (
    "A grade describes one published file under one stated policy on one date. It does not rank "
    "hospitals, price care, or determine compliance with 45 CFR part 180, and it is not the "
    "official CMS validator."
)


@dataclass(frozen=True)
class Page:
    path: str  # site-relative directory; "" for the root page
    title: str
    description: str
    body: str
    changefreq: str = "weekly"
    priority: str = "0.5"


def _e(value: object) -> str:
    return html.escape(str(value))


def _json_ld(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload)
    for char, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        encoded = encoded.replace(char, escape)
    return f'<script type="application/ld+json">{encoded}</script>'


def _grade_badge(grade: str) -> str:
    word = _GRADE_WORDS.get(grade, "grade unavailable")
    label = "Not graded" if grade == NOT_GRADED else grade
    css = "ng" if grade == NOT_GRADED else grade.lower()
    return (
        f'<span class="grade grade-{css}" aria-label="{_e(label)}: {_e(word)}">{_e(label)}</span>'
    )


def _rows(comparison: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    return cast(Sequence[Mapping[str, object]], comparison["files"])


def _summary(comparison: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], comparison["summary"])


def _grade_of(row: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], row["grade"])


def _display_name(row: Mapping[str, object]) -> str:
    name = row.get("publisher_name")
    return str(name) if name else str(row["publisher_id"])


def _catalog_description(code: str) -> str:
    definition = FINDING_CATALOG.get(code) or RETRIEVAL_FINDING_CATALOG.get(code)
    return definition.description if definition is not None else ""


def _finding_item(finding: Mapping[str, object]) -> str:
    code = str(finding.get("code"))
    severity = str(finding.get("severity"))
    occurrences = finding.get("occurrences")
    count = f" x{occurrences}" if isinstance(occurrences, int) and occurrences > 1 else ""
    citations = "".join(
        f' <a href="{_e(url)}" rel="nofollow">source ↗</a>'
        for url in cast(Sequence[object], finding.get("citations") or ())
    )
    return (
        f'<li class="finding sev-{severity.lower()}">'
        f'<span class="sev" aria-label="severity {severity}">{severity}</span>'
        f'<span class="finding-copy"><a href="../../../how-we-grade/#{_e(code)}">'
        f"{_e(code)}</a>{count}: {_e(finding.get('message'))}{citations}</span></li>"
    )


def _dimension_section(name: str, dimension: Mapping[str, object]) -> str:
    status = str(dimension.get("status"))
    findings = cast(Sequence[Mapping[str, object]], dimension.get("findings") or ())
    items = "".join(_finding_item(finding) for finding in findings)
    note = dimension.get("note")
    note_html = f'<p class="dim-note">{_e(note)}</p>' if note else ""
    if not findings:
        items = (
            '<li class="finding none">No catalog finding was emitted for this dimension. '
            "That is not a certificate that the data is valid.</li>"
            if status == "OBSERVED"
            else '<li class="finding none">Not assessed; no findings could be produced.</li>'
        )
    return (
        '<section class="dimension">'
        f"<h3>{_e(_DIMENSION_TITLES.get(name, name))} "
        f'<span class="status status-{status.lower()}">{_e(status)}</span></h3>'
        f'<p class="status-word">{_e(_STATUS_WORDS.get(status, ""))}</p>'
        f'{note_html}<ul class="findings">{items}</ul></section>'
    )


def _counts_list(row: Mapping[str, object]) -> str:
    counts = row.get("counts")
    if not isinstance(counts, Mapping):
        return (
            "<p>No verified body was inspected, so no item, charge, or rate counts exist for "
            "this attempt.</p>"
        )
    fields = (
        ("item_count", "items"),
        ("code_count", "billing codes"),
        ("charge_group_count", "charge groups"),
        ("payer_rate_count", "payer rate entries"),
        ("dollar_rate_count", "dollar-denominated rates"),
        ("percentage_rate_count", "percentage rates"),
        ("algorithm_rate_count", "algorithm rates"),
    )
    items = "".join(
        f"<div><dt>{_e(label)}</dt><dd>{counts.get(key):,}</dd></div>"
        for key, label in fields
        if isinstance(counts.get(key), int)
    )
    return f'<dl class="facts">{items}</dl>'


def _lakehouse_section(row: Mapping[str, object]) -> str:
    lakehouse = row.get("lakehouse")
    if not isinstance(lakehouse, Mapping):
        return (
            "<h2>Warehouse contracts</h2><p>This file was not loaded into the contracted "
            "warehouse for this cohort, so no contract evidence exists for it. Absence of that "
            "check is stated here rather than implied as a pass.</p>"
        )
    counts = lakehouse.get("counts")
    counts_html = ""
    if isinstance(counts, Mapping):
        rows = "".join(
            f"<div><dt>{_e(key.replace('_', ' '))}</dt><dd>{value:,}</dd></div>"
            for key, value in counts.items()
            if isinstance(value, int) and not isinstance(value, bool)
        )
        counts_html = f'<dl class="facts">{rows}</dl>'
    return (
        "<h2>Warehouse contracts</h2>"
        "<p>The verified body was loaded into the local DuckDB + Parquet warehouse, where data "
        "contracts are enforced at every layer boundary; a contract violation fails the build "
        f"rather than warning. This load finished with status "
        f"<code>{_e(lakehouse.get('status'))}</code> under run "
        f"<code>{_e(lakehouse.get('run_id'))}</code>.</p>{counts_html}"
    )


def _provenance_section(row: Mapping[str, object]) -> str:
    sha = row.get("content_sha256")
    size = row.get("size_bytes")
    size_html = f"{size:,} bytes" if isinstance(size, int) else "no verified body"
    sha_html = f"<code>{_e(sha)}</code>" if isinstance(sha, str) else "none (no verified body)"
    return (
        '<section class="verification"><h2>Verification provenance</h2><dl class="facts">'
        f"<div><dt>Requested URL</dt><dd><code>{_e(row.get('requested_url'))}</code></dd></div>"
        f"<div><dt>Observed at</dt><dd>{_e(row.get('observed_at'))} (UTC)</dd></div>"
        f"<div><dt>Assessment date</dt><dd>{_e(row.get('as_of'))}</dd></div>"
        f"<div><dt>Decoded size</dt><dd>{size_html}</dd></div>"
        f"<div><dt>Content SHA-256</dt><dd>{sha_html}</dd></div>"
        f"<div><dt>File last_updated_on</dt><dd>{_e(row.get('last_updated_on') or 'not stated')}"
        "</dd></div>"
        f"<div><dt>Declared template version</dt>"
        f"<dd>{_e(row.get('template_version') or 'not stated')}</dd></div>"
        f"<div><dt>Assessment record digest</dt>"
        f"<dd><code>{_e(row.get('assessment_body_sha256'))}</code></dd></div>"
        "</dl><p>The retrieval was one identified, bounded request; the SHA-256 covers the exact "
        "decoded bytes that were inspected, and the record digest covers the complete persisted "
        "assessment.</p></section>"
    )


def file_page(row: Mapping[str, object], comparison: Mapping[str, object], origin: str) -> Page:
    grade = _grade_of(row)
    name = _display_name(row)
    location = str(row["location_id"])
    slug = str(row["slug"])
    grade_value = str(grade["grade"])
    title = f"{name}: {location} MRF file grade"
    if name.lower().replace(" ", "-") == location:
        title = f"{name} MRF file grade"
    dimensions = cast(Mapping[str, Mapping[str, object]], row["dimensions"])
    sections = "".join(
        _dimension_section(dim_name, dimensions[dim_name])
        for dim_name in ("retrievability", *LOCAL_DIMENSIONS)
    )
    jsonld = _json_ld(
        {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": f"{name} hospital price-transparency file assessment ({location})",
            "url": f"{origin}/hospital/{slug}/",
            "dateModified": str(row.get("as_of")),
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
            "isAccessibleForFree": True,
        }
    )
    body = (
        f'<nav class="crumbs"><a href="../../../">All graded files</a></nav>'
        f'<header class="hero"><p class="eyebrow">Hospital price-transparency file</p>'
        f'<h1>{_e(name)}</h1><p class="lede">Location <code>{_e(location)}</code>, assessed '
        f"{_e(row.get('as_of'))}.</p>{_grade_badge(grade_value)}"
        f'<p class="grade-reason">{_e(grade["reason"])}.</p></header>'
        f"<h2>Dimensions and findings</h2>{sections}"
        f"<h2>What the file contains</h2>{_counts_list(row)}"
        f"{_lakehouse_section(row)}"
        f"{_provenance_section(row)}"
        f'<p class="caveat">{_CAVEAT}</p>{jsonld}'
    )
    word = _GRADE_WORDS.get(grade_value, "")
    label = "not graded" if grade_value == NOT_GRADED else f"grade {grade_value}"
    return Page(
        path=f"hospital/{slug}",
        title=title,
        description=f"{name} ({location}): {label} — {word}.",
        priority="0.8",
        body=body,
    )


def _index_row(row: Mapping[str, object]) -> str:
    grade = _grade_of(row)
    slug = str(row["slug"])
    reason = str(grade["reason"])
    return (
        f'<li class="card">{_grade_badge(str(grade["grade"]))}'
        f'<div><h3><a href="hospital/{_e(slug)}/">{_e(_display_name(row))}</a></h3>'
        f'<p class="meta">Location <code>{_e(row["location_id"])}</code> · '
        f"assessed {_e(row.get('as_of'))}</p>"
        f'<p class="meta">{_e(reason)}.</p></div></li>'
    )


def _exclusion_rows(comparison: Mapping[str, object]) -> str:
    exclusions = cast(Sequence[Mapping[str, object]], comparison.get("exclusions") or ())
    rows = []
    for entry in exclusions:
        reviewed = entry.get("reviewed")
        method = ""
        if isinstance(reviewed, Mapping):
            method = f" Reviewed {_e(reviewed.get('date'))}: {_e(reviewed.get('method'))}."
        rows.append(
            f"<li><strong>{_e(entry.get('name'))}</strong> "
            f"(<code>{_e(entry.get('basis'))}</code>): {_e(entry.get('reason'))}.{method}</li>"
        )
    return "".join(rows)


def index_page(comparison: Mapping[str, object], origin: str) -> Page:
    summary = _summary(comparison)
    cohort = cast(Mapping[str, object], comparison["cohort"])
    rows = _rows(comparison)
    distribution = cast(Mapping[str, int], summary["grade_distribution"])
    dist_html = "".join(
        f'<span class="dist"><strong>{count}</strong> {letter}</span>'
        for letter, count in distribution.items()
        if count
    )
    not_graded = cast(int, summary["not_graded"])
    if not_graded:
        dist_html += f'<span class="dist"><strong>{not_graded}</strong> not graded</span>'
    cards = "".join(_index_row(row) for row in rows)
    coverage = (
        f"This cohort covers <strong>{summary['targeted']}</strong> machine-readable files, "
        f"discovered from hospital <code>cms-hpt.txt</code> documents and collected in one "
        f"identified run on {_e(cohort.get('as_of'))}. "
        f"{summary['verified_body_available']} of {summary['targeted']} targets produced a "
        f"verified body; {summary['graded']} were graded and {not_graded} "
        "recorded as not graded with the reason stated. The registry grows by discovering more "
        "hospitals' TXT documents; nothing here is a sample of hospitals as a class."
    )
    jsonld = _json_ld(
        {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "mrf-honest hospital price-transparency file grades",
            "url": f"{origin}/",
            "dateModified": str(cohort.get("as_of")),
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
            "isAccessibleForFree": True,
        }
    )
    body = (
        '<header class="hero"><h1>Hospital price-transparency files, graded</h1>'
        '<p class="lede">US hospitals must publish machine-readable files of their standard '
        "charges. This site grades each published <em>file</em> — never the hospital — on "
        "whether it can be retrieved, parsed, and interpreted, with every finding citing the "
        "rule or schema requirement it rests on.</p></header>"
        f'<p class="coverage">{coverage}</p>'
        f'<div class="dist-row">{dist_html}</div>'
        f'<ul class="cards">{cards}</ul>'
        "<h2>Checked and recorded, not graded</h2>"
        "<p>Targets this cohort reviewed but did not grade stay visible, with how far the "
        "review went and what it found. An absent or unreachable TXT at one origin is not "
        "evidence about the hospital's publication.</p>"
        f'<ul class="exclusions">{_exclusion_rows(comparison)}</ul>'
        '<p><a href="how-we-grade/">How grading works, and what it deliberately does not '
        'claim</a> · <a href="data/comparison.json">machine-readable comparison</a> · '
        '<a href="https://github.com/ChelseaKR/mrf-honest/tree/master/docs/findings">'
        "written-up findings with evidence</a></p>"
        f'<p class="caveat">{_CAVEAT}</p>{jsonld}'
    )
    return Page(
        path="",
        title="mrf-honest: hospital price-transparency file grades",
        description=(
            f"Deterministic, spec-cited grades for {summary['targeted']} hospital "
            "price-transparency files, with fail-closed coverage reporting."
        ),
        priority="1.0",
        body=body,
    )


def _matrix_section(comparison: Mapping[str, object]) -> str:
    matrix = cast(Sequence[Mapping[str, object]], comparison.get("finding_matrix") or ())
    if not matrix:
        return "<p>No finding codes were emitted by any file in this cohort.</p>"
    rows = []
    for entry in matrix:
        code = str(entry.get("code"))
        files = cast(Sequence[object], entry.get("files") or ())
        file_links = ", ".join(
            f'<a href="../hospital/{_e(slug)}/">{_e(slug)}</a>' for slug in files
        )
        rows.append(
            f'<section class="method-card" id="{_e(code)}">'
            f"<h3><code>{_e(code)}</code> "
            f'<span class="sev">{_e(entry.get("severity"))}</span></h3>'
            f"<p>{_e(_catalog_description(code))}</p>"
            f"<p>Dimension: {_e(entry.get('dimension'))} · emitted by: {file_links}</p>"
            "</section>"
        )
    return "".join(rows)


def methods_page(comparison: Mapping[str, object], origin: str) -> Page:
    cohort = cast(Mapping[str, object], comparison["cohort"])
    policy = cast(Mapping[str, object], cohort["grade_policy"])
    collection = cast(Mapping[str, object], comparison.get("collection") or {})
    body = (
        '<nav class="crumbs"><a href="../">All graded files</a></nav>'
        "<h1>How these files are graded</h1>"
        "<p>Every check is deterministic, cites the CMS hospital price-transparency rule "
        '(<a href="https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180" '
        'rel="nofollow">45 CFR part 180</a>, in particular '
        '<a href="https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/'
        'subpart-B/section-180.50" rel="nofollow">§ 180.50</a>) or the '
        '<a href="https://github.com/CMSgov/hospital-price-transparency" rel="nofollow">CMS '
        "schema documentation</a>, and can be explained in one sentence. There is no model "
        "anywhere in the grading path.</p>"
        "<h2>What is checked</h2>"
        "<p>Five independent dimensions per file: retrievability (one identified, bounded "
        "download attempt), conformance (selected CMS v3 envelope, structure, and "
        "accepted-value checks), completeness (presence and usability of the fields that make "
        "a rate interpretable), interpretability (whether rates are usable amounts or require "
        "separate treatment), and freshness (the file's own <code>last_updated_on</code> "
        "against the assessment date). The complete finding catalog with citations is in "
        '<a href="https://github.com/ChelseaKR/mrf-honest/blob/master/docs/how-we-grade.md" '
        'rel="nofollow">docs/how-we-grade.md</a>.</p>'
        "<h2>What is deliberately not checked</h2>"
        "<ul>"
        "<li>This is not the official CMS validator and not exhaustive schema validation; an "
        "<em>A</em> means the implemented checks emitted nothing, not that the file is "
        "valid.</li>"
        "<li>No determination of legal compliance with 45 CFR part 180 or any other law.</li>"
        "<li>No verification that published prices, payer names, or attestations are factually "
        "accurate.</li>"
        "<li>No price comparison of any kind: dollar, percentage, and algorithm rates are "
        "structurally separated, and no rate statistic is published without the suppression and "
        "uncertainty work this project has not yet done.</li>"
        "<li>No long-run availability measurement: retrievability reflects one bounded attempt "
        "from one vantage on one date.</li>"
        "</ul>"
        "<h2>The letter grade</h2>"
        f"<p>Grade policy <code>{_e(policy.get('version'))}</code>, fingerprint "
        f"<code>{_e(str(policy.get('fingerprint'))[:16])}…</code>. The full rule table ships "
        "in the machine-readable comparison. In evaluation order: a failed download is an "
        "<strong>F</strong> with the dated reason; a local limit (invalid input, the project's "
        "size ceiling, cache trouble) is <strong>not graded</strong> and never conflated with "
        "an F; an incomplete stream is an <strong>F</strong>; then errors (or missing "
        "evidence) in zero dimensions is an <strong>A</strong> (warnings make it a "
        "<strong>B</strong>), one dimension a <strong>C</strong>, two a <strong>D</strong>, "
        "three or more an <strong>F</strong>. Tolerated INFO observations never lower a "
        "grade.</p>"
        "<h2>How this cohort was collected</h2>"
        f"<p>{_e(collection.get('description'))}</p>"
        "<p>Hosts' <code>robots.txt</code> files were checked for every retrieved URL before "
        "the run; a site that blocks is recorded and skipped, never circumvented. Retrieval "
        "of these files is what the transparency rule exists to allow: CMS requires them to be "
        "public, machine-readable, and accessible without barriers.</p>"
        "<h2>Finding codes emitted in this cohort</h2>"
        f"{_matrix_section(comparison)}"
        f'<p class="caveat">{_CAVEAT}</p>'
    )
    return Page(
        path="how-we-grade",
        title="How mrf-honest grades hospital price-transparency files",
        description=(
            "Deterministic, spec-cited grading of hospital MRF files: what is checked, what is "
            "deliberately not checked, and the exact letter-grade policy."
        ),
        changefreq="monthly",
        priority="0.6",
        body=body,
    )


def not_found_page() -> Page:
    return Page(
        path="404",
        title="Page not found",
        description="This page does not exist.",
        body=(
            "<h1>Page not found</h1><p>This page does not exist or has moved. "
            'Start from <a href="/mrf-honest/">the graded-file index</a>.</p>'
        ),
    )


def sitemap(pages: Sequence[Page], origin: str) -> str:
    entries = "".join(
        f"<url><loc>{_e(origin)}/{_e(page.path) + '/' if page.path else ''}</loc>"
        f"<changefreq>{page.changefreq}</changefreq>"
        f"<priority>{page.priority}</priority></url>"
        for page in pages
        if page.path != "404"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


def robots(origin: str) -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n"


def _shell(page: Page, origin: str, generated_at: str) -> str:
    canonical = f"{origin}/{page.path + '/' if page.path else ''}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(page.title)}</title>
<meta name="description" content="{_e(page.description)}">
<link rel="canonical" href="{_e(canonical)}">
<style>{_STYLE}</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
<main id="main" tabindex="-1">
{page.body}
</main>
<footer>
<p>Generated {_e(generated_at)} from the committed comparison document. Only public,
CMS-mandated machine-readable files are read; retrieval is identified, bounded, and respects
robots.txt. <a href="https://github.com/ChelseaKR/mrf-honest">Source and methodology</a>.</p>
</footer>
</body>
</html>
"""


def write_page(out_dir: Path, page: Page, origin: str, generated_at: str) -> Path:
    directory = out_dir / page.path if page.path else out_dir
    directory.mkdir(parents=True, exist_ok=True)
    if page.path == "404":
        target = out_dir / "404.html"
    else:
        target = directory / "index.html"
    target.write_text(_shell(page, origin, generated_at), encoding="utf-8")
    return target


def render_site(
    comparison: Mapping[str, object],
    out_dir: Path,
    *,
    origin: str = DEFAULT_ORIGIN,
) -> list[Path]:
    """Render the complete static site for one comparison document."""
    generated_at = str(comparison.get("generated_at"))
    pages = [
        index_page(comparison, origin),
        methods_page(comparison, origin),
        *(file_page(row, comparison, origin) for row in _rows(comparison)),
        not_found_page(),
    ]
    written = [write_page(out_dir, page, origin, generated_at) for page in pages]
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "comparison.json"
    data_path.write_text(
        json.dumps(comparison, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    written.append(data_path)
    sitemap_path = out_dir / "sitemap.xml"
    sitemap_path.write_text(sitemap(pages, origin), encoding="utf-8")
    written.append(sitemap_path)
    robots_path = out_dir / "robots.txt"
    robots_path.write_text(robots(origin), encoding="utf-8")
    written.append(robots_path)
    return written


_STYLE = """
:root {
  --ink: #1e242b; --muted: #55616e; --paper: #ffffff; --wash: #f4f6f8;
  --line: #d9dee3; --accent: #00666a;
  --a: #19734b; --b: #00666a; --c: #a35d00; --d: #a43b2a; --f: #8f2430; --ng: #55616e;
}
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--ink); background: var(--paper);
  font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { max-width: 46rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
footer { max-width: 46rem; margin: 0 auto; padding: 1rem; color: var(--muted);
  font-size: .85rem; border-top: 1px solid var(--line); }
h1 { font-size: 1.7rem; line-height: 1.25; margin: .4rem 0; }
h2 { font-size: 1.2rem; margin-top: 2rem; }
h3 { font-size: 1rem; margin: 0 0 .3rem; }
a { color: var(--accent); }
code { background: var(--wash); padding: .1em .3em; border-radius: 3px;
  font-size: .85em; overflow-wrap: anywhere; }
.skip-link { position: absolute; left: -999px; top: 0; background: var(--ink);
  color: var(--paper); padding: .5rem 1rem; z-index: 10; }
.skip-link:focus { left: 0; }
.eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: .75rem;
  color: var(--muted); margin: 0; }
.lede { color: var(--muted); }
.grade { display: inline-block; font-weight: 700; font-size: 1.4rem; line-height: 1;
  padding: .45rem .8rem; border-radius: 6px; color: #fff; }
.grade-a { background: var(--a); } .grade-b { background: var(--b); }
.grade-c { background: var(--c); } .grade-d { background: var(--d); }
.grade-f { background: var(--f); }
.grade-ng { background: var(--ng); font-size: 1rem; }
.grade-reason { color: var(--muted); }
.cards { list-style: none; padding: 0; }
.cards .card { display: flex; gap: 1rem; align-items: flex-start;
  border: 1px solid var(--line); border-radius: 8px; padding: 1rem; margin: .6rem 0; }
.cards .grade { font-size: 1.1rem; min-width: 2.2rem; text-align: center; }
.card .meta { color: var(--muted); font-size: .85rem; margin: .15rem 0; }
.dist-row { margin: 1rem 0; }
.dist { display: inline-block; background: var(--wash); border-radius: 999px;
  padding: .2rem .8rem; margin-right: .5rem; }
.coverage { background: var(--wash); border-left: 3px solid var(--accent);
  padding: .8rem 1rem; }
.dimension { border: 1px solid var(--line); border-radius: 8px;
  padding: .8rem 1rem; margin: .8rem 0; }
.status { font-size: .7rem; font-weight: 700; letter-spacing: .05em;
  padding: .15rem .5rem; border-radius: 999px; vertical-align: middle; }
.status-observed { background: #e2f0e9; color: var(--a); }
.status-findings { background: #f6ead8; color: var(--c); }
.status-not_assessed { background: var(--wash); color: var(--muted); }
.status-word, .dim-note { color: var(--muted); font-size: .85rem; margin: .2rem 0; }
.findings { list-style: none; padding: 0; margin: .4rem 0 0; }
.finding { display: flex; gap: .6rem; padding: .35rem 0;
  border-top: 1px dashed var(--line); font-size: .9rem; }
.finding.none { color: var(--muted); }
.sev { font-size: .65rem; font-weight: 700; align-self: flex-start;
  padding: .1rem .4rem; border-radius: 3px; background: var(--wash); color: var(--muted); }
.sev-error .sev { background: #f3dcda; color: var(--f); }
.sev-warning .sev { background: #f6ead8; color: var(--c); }
.finding-copy { overflow-wrap: anywhere; }
.facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  gap: .5rem 1rem; margin: .6rem 0; }
.facts div { border: 1px solid var(--line); border-radius: 6px; padding: .5rem .7rem; }
.facts dt { font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.exclusions li { margin: .5rem 0; }
.method-card { border: 1px solid var(--line); border-radius: 8px;
  padding: .8rem 1rem; margin: .8rem 0; }
.caveat { margin-top: 2.5rem; padding-top: .8rem; border-top: 1px solid var(--line);
  color: var(--muted); font-size: .85rem; }
.crumbs { font-size: .85rem; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
@media print { footer, .skip-link { display: none; } }
"""
