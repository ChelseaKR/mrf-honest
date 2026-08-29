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

from mrf_honest.cohort import INGEST_REFUSED, LOCAL_DIMENSIONS, NOT_GRADED
from mrf_honest.dataset import api_documents, dataset_csv, encode, table_schema
from mrf_honest.inspect import FINDING_CATALOG
from mrf_honest.inspect_csv import CSV_FINDING_CATALOG
from mrf_honest.scorecard import RETRIEVAL_FINDING_CATALOG

DEFAULT_ORIGIN = "https://chelseakr.github.io/mrf-honest"

# Every colour the stylesheet uses, in one place, so the contrast of each text-on-background
# combination is asserted by a test instead of hoped for. `--c-ink` exists because the amber
# that reads well as a badge background does not clear 4.5:1 as small bold text on the amber
# wash: axe measured 4.28:1, and that is what shipped on every file page carrying a warning
# finding until an audit was finally pointed at the site.
PALETTE: dict[str, str] = {
    "ink": "#1e242b",
    "muted": "#55616e",
    "paper": "#ffffff",
    "wash": "#f4f6f8",
    "line": "#d9dee3",
    "accent": "#00666a",
    "a": "#19734b",
    "b": "#00666a",
    "c": "#a35d00",
    "c-ink": "#8a4f00",
    "d": "#a43b2a",
    "f": "#8f2430",
    "ng": "#55616e",
    "observed-wash": "#e2f0e9",
    "findings-wash": "#f6ead8",
    "error-wash": "#f3dcda",
}

# Border-only tokens carry no text and so have no contrast pair to declare. Naming them is
# what lets the test insist every *other* token is accounted for: a colour added to PALETTE
# and used nowhere in the table below fails the suite rather than shipping unchecked.
NON_TEXT_TOKENS: frozenset[str] = frozenset({"line"})

# (foreground token, background token, where it appears). SC 1.4.3 wants 4.5:1 for normal text
# and 3:1 for large; nothing here claims the large-text exemption, because the smallest of
# these is 0.65rem and arguing a badge into "large text" is how contrast bugs get argued
# instead of fixed.
TEXT_ON_BACKGROUND: tuple[tuple[str, str, str], ...] = (
    ("ink", "paper", "body copy"),
    ("ink", "wash", "code spans, .dist pills, and .shares column headers"),
    ("muted", "paper", ".lede, .meta, .caveat, .eyebrow, footer, .facts dt"),
    ("muted", "wash", ".status-not_assessed and the default .sev chip"),
    ("accent", "paper", "links"),
    ("accent", "wash", "links inside .coverage and .dist"),
    ("paper", "ink", ".skip-link"),
    ("paper", "a", ".grade-a badge"),
    ("paper", "b", ".grade-b badge"),
    ("paper", "c", ".grade-c badge"),
    ("paper", "d", ".grade-d badge"),
    ("paper", "f", ".grade-f badge"),
    ("paper", "ng", ".grade-ng badge"),
    ("a", "observed-wash", ".status-observed chip"),
    ("c-ink", "findings-wash", ".status-findings chip and .sev-warning .sev"),
    ("f", "error-wash", ".sev-error .sev"),
)

MIN_CONTRAST = 4.5


def _channel(value: int) -> float:
    fraction = value / 255
    if fraction <= 0.04045:
        return fraction / 12.92
    return float(((fraction + 0.055) / 1.055) ** 2.4)


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a ``#rrggbb`` colour."""
    digits = color.lstrip("#")
    red, green, blue = (int(digits[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two ``#rrggbb`` colours."""
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _root_block() -> str:
    declarations = " ".join(f"--{token}: {value};" for token, value in PALETTE.items())
    return f":root {{ {declarations} }}\n"


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

#: The one heading and the one sentence the deploy path looks for, so the check and the render
#: cannot drift apart into two different strings.
CORRECTIONS_URL = "https://github.com/ChelseaKR/mrf-honest/blob/master/docs/CORRECTIONS.md"

SHARES_HEADING = "What share of the drawn sample this is"
SHARES_REFUSAL = "No share is published for this cohort"

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
    definition = (
        FINDING_CATALOG.get(code)
        or CSV_FINDING_CATALOG.get(code)
        or RETRIEVAL_FINDING_CATALOG.get(code)
    )
    return definition.description if definition is not None else ""


#: Human titles for the assessment profiles a cohort's comparison scope can carry.
_PROFILE_TITLES = {
    "cms-hospital-json-v3": "CMS hospital JSON v3",
    "cms-hospital-csv-v3": "CMS hospital CSV v3 (Tall and Wide)",
}


def _cohort_profile(comparison: Mapping[str, object]) -> str:
    cohort = cast(Mapping[str, object], comparison["cohort"])
    scope = cast(Mapping[str, object], cohort.get("comparison_scope") or {})
    return str(scope.get("profile") or "cms-hospital-json-v3")


def _cohort_title(comparison: Mapping[str, object]) -> str:
    profile = _cohort_profile(comparison)
    return _PROFILE_TITLES.get(profile, profile)


def _cohort_data_name(comparison: Mapping[str, object]) -> str:
    cohort = cast(Mapping[str, object], comparison["cohort"])
    return f"{cohort.get('cohort_id')}.json"


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
        ("row_count", "data rows"),
        ("charge_group_count", "charge groups"),
        ("payer_plan_combination_count", "payer-plan combinations"),
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
            "<h2>Warehouse contracts</h2><p>No warehouse ingest was recorded for this file in "
            "this cohort, so no contract evidence exists for it. Absence of that check is "
            "stated here rather than implied as a pass.</p>"
        )
    if lakehouse.get("status") == INGEST_REFUSED:
        # The reason is the whole point of this branch. A refusal rendered as a bare absence
        # is a project limit that reads like an unnamed defect in the file, which is exactly
        # what docs/how-we-compare.md refuses to publish.
        return (
            "<h2>Warehouse contracts</h2>"
            "<p>This project's local warehouse declined to load the verified body, so no "
            "contract evidence exists for this file. The refusal is a limit of what this "
            "project implements, not a finding about the file, and it does not affect the "
            "grade above: warehouse evidence is never a grading input. It is stated here with "
            "the reason the warehouse gave rather than left as a silent absence.</p>"
            '<dl class="facts">'
            f"<div><dt>Warehouse refusal</dt><dd><code>{_e(lakehouse.get('reason'))}</code>"
            "</dd></div>"
            f"<div><dt>Warehouse implements</dt>"
            f"<dd>{_e(lakehouse.get('implemented_scope'))}</dd></div>"
            f"<div><dt>This file declares</dt>"
            f"<dd>{_e(lakehouse.get('observed_scope'))}</dd></div></dl>"
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


def _cross_references(comparisons: Sequence[Mapping[str, object]]) -> dict[str, tuple[str, str]]:
    """Publisher display name -> (slug, cohort title), across every rendered cohort.

    A ``format_outside_profile`` exclusion in one cohort names a facility another cohort may
    have graded. The link is derived from the data (the sibling row's publisher name matching
    the exclusion's recorded name), never typed in, so a facility graded nowhere gets no claim.
    """
    references: dict[str, tuple[str, str]] = {}
    for comparison in comparisons:
        title = _cohort_title(comparison)
        for row in _rows(comparison):
            name = row.get("publisher_name")
            if isinstance(name, str) and name:
                references[name] = (str(row["slug"]), title)
    return references


def _exclusion_rows(
    comparison: Mapping[str, object],
    references: Mapping[str, tuple[str, str]],
) -> str:
    exclusions = cast(Sequence[Mapping[str, object]], comparison.get("exclusions") or ())
    rows = []
    for entry in exclusions:
        reviewed = entry.get("reviewed")
        method = ""
        if isinstance(reviewed, Mapping):
            method = f" Reviewed {_e(reviewed.get('date'))}: {_e(reviewed.get('method'))}."
        cross = ""
        name = entry.get("name")
        if entry.get("basis") == "format_outside_profile" and isinstance(name, str):
            reference = references.get(name)
            if reference is not None:
                slug, cohort_title = reference
                cross = (
                    f' Graded under the {_e(cohort_title)} profile as <a href="hospital/'
                    f'{_e(slug)}/">{_e(slug)}</a>.'
                )
        rows.append(
            f"<li><strong>{_e(entry.get('name'))}</strong> "
            f"(<code>{_e(entry.get('basis'))}</code>): {_e(entry.get('reason'))}."
            f"{method}{cross}</li>"
        )
    return "".join(rows)


def _distribution_html(summary: Mapping[str, object]) -> str:
    distribution = cast(Mapping[str, int], summary["grade_distribution"])
    dist_html = "".join(
        f'<span class="dist"><strong>{count}</strong> {letter}</span>'
        for letter, count in distribution.items()
        if count
    )
    not_graded = cast(int, summary["not_graded"])
    if not_graded:
        dist_html += f'<span class="dist"><strong>{not_graded}</strong> not graded</span>'
    return dist_html


def _coverage_sentence(comparison: Mapping[str, object]) -> str:
    summary = _summary(comparison)
    cohort = cast(Mapping[str, object], comparison["cohort"])
    not_graded = cast(int, summary["not_graded"])
    # The closing sentence is not decoration. A grade distribution reads as a picture of the
    # landscape unless the page says what it is a picture of, and the honest answer depends on
    # whether this cohort has a sampling frame -- so it is derived, never hard-coded.
    collection = cast(Mapping[str, object], comparison.get("collection") or {})
    framed = isinstance(collection.get("sampling_frame"), Mapping)
    provenance = (
        "Subjects were drawn under a stated sampling frame; the counts above describe this "
        'cohort, not hospitals as a class. <a href="how-we-grade/">How these subjects were '
        "chosen</a>."
        if framed
        else (
            "The registry grows by discovering more hospitals' TXT documents; nothing here is a "
            "sample of hospitals as a class."
        )
    )
    return (
        f"This cohort covers <strong>{summary['targeted']}</strong> machine-readable files, "
        f"discovered from hospital <code>cms-hpt.txt</code> documents and collected in one "
        f"identified run on {_e(cohort.get('as_of'))}. "
        f"{summary['verified_body_available']} of {summary['targeted']} targets produced a "
        f"verified body; {summary['graded']} were graded and {not_graded} "
        f"recorded as not graded with the reason stated. {provenance}"
    )


def missing_shares(comparison: Mapping[str, object], html: str) -> list[str]:
    """Report every published share that did not reach the rendered page.

    The deploy path calls this. `make verify` is a separate workflow, so a red run there does
    not stop a deployment; a share that is computed and never rendered is not published, and a
    refusal that is silently dropped teaches a reader that the absence of a number means there
    was nothing to say. An empty list means the page carries what the document says.
    """

    statistics = comparison.get("statistics")
    if not isinstance(statistics, Mapping):
        return ["the comparison document carries no statistics block"]
    if SHARES_HEADING not in html:
        return [f"the page does not carry the heading {SHARES_HEADING!r}"]
    estimates = statistics.get("estimates")
    listed = list(estimates) if isinstance(estimates, Sequence) else []
    if not listed:
        if statistics.get("refusal") is None:
            return ["the document carries neither an estimate nor a stated refusal"]
        if SHARES_REFUSAL not in html:
            return ["a refused cohort rendered no refusal on the page"]
        return []
    problems: list[str] = []
    for estimate in listed:
        entry = cast(Mapping[str, object], estimate)
        needle = f"{entry.get('numerator')} of {entry.get('denominator')}"
        if needle not in html:
            problems.append(f"{needle} is in the document but not on the page")
    return problems


def _statistics_section(comparison: Mapping[str, object]) -> str:
    """Render the cohort's population shares, or render the refusal as a refusal.

    A refusal is a paragraph, not an omitted section. A page that simply drops the block when
    nothing could be estimated teaches a reader that the absence of a number means there was
    nothing to say, which is the one reading this project exists to prevent.
    """

    statistics = comparison.get("statistics")
    if not isinstance(statistics, Mapping):
        return (
            f"<h3>{SHARES_HEADING}</h3>"
            "<p>This comparison document predates the statistics layer and carries no shares. "
            "Regenerate it with <code>mrf-honest compare</code> to see them.</p>"
        )
    refusal = statistics.get("refusal")
    if isinstance(refusal, Mapping):
        return (
            f"<h3>{SHARES_HEADING}</h3>"
            f'<p class="refusal"><strong>{SHARES_REFUSAL}:</strong> '
            f"{_e(refusal.get('reason'))}."
            f"{_refusal_detail(refusal)}</p>"
        )
    estimates = statistics.get("estimates")
    if not isinstance(estimates, Sequence) or not estimates:
        return (
            f"<h3>{SHARES_HEADING}</h3>"
            f'<p class="refusal"><strong>{SHARES_REFUSAL}.</strong> The '
            "document carries neither an estimate nor a stated refusal, which is a defect in "
            "whatever produced it.</p>"
        )
    rows = "".join(_estimate_row(cast(Mapping[str, object], e)) for e in estimates)
    return (
        f"<h3>{SHARES_HEADING}</h3>"
        f"<p>{_e(statistics.get('basis'))} Every share carries its own denominator and a "
        f"{_percent(statistics.get('confidence'))} interval computed by the "
        f"{_e(statistics.get('method'))} method.</p>"
        '<table class="shares"><thead><tr><th scope="col">Disposition</th>'
        '<th scope="col">Count</th><th scope="col">Share</th>'
        '<th scope="col">Interval</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _refusal_detail(refusal: Mapping[str, object]) -> str:
    """Name the denominator a refusal saw, where it saw one, so the reader can check it."""

    denominator = refusal.get("denominator")
    if not isinstance(denominator, int) or isinstance(denominator, bool):
        return ""
    return f" The accounting this document carries covers {denominator} facilities."


def _percent(value: object) -> str:
    return f"{float(cast(float, value)) * 100:.0f}%" if isinstance(value, int | float) else "?"


def _share(value: object) -> str:
    return f"{float(cast(float, value)) * 100:.1f}%" if isinstance(value, int | float) else "?"


def _estimate_row(estimate: Mapping[str, object]) -> str:
    return (
        f'<tr><th scope="row">{_e(estimate.get("label"))}</th>'
        f"<td>{_e(estimate.get('numerator'))} of {_e(estimate.get('denominator'))}</td>"
        f"<td>{_share(estimate.get('point'))}</td>"
        f"<td>{_share(estimate.get('interval_low'))} to "
        f"{_share(estimate.get('interval_high'))}</td></tr>"
    )


def _cohort_section(
    comparison: Mapping[str, object],
    references: Mapping[str, tuple[str, str]],
) -> str:
    cohort = cast(Mapping[str, object], comparison["cohort"])
    summary = _summary(comparison)
    cards = "".join(_index_row(row) for row in _rows(comparison))
    exclusions = _exclusion_rows(comparison, references)
    exclusion_block = (
        "<h3>Checked and recorded, not graded</h3>"
        "<p>Targets this cohort reviewed but did not grade stay visible, with how far the "
        "review went and what it found. An absent or unreachable TXT at one origin is not "
        "evidence about the hospital's publication.</p>"
        f'<ul class="exclusions">{exclusions}</ul>'
        if exclusions
        else ""
    )
    # Cohorts are separate sections on purpose: their rows were assessed under different
    # profiles and must never be pooled into one distribution (docs/how-we-compare.md).
    return (
        f"<h2>Files graded under the {_e(_cohort_title(comparison))} profile "
        f"({_e(cohort.get('as_of'))})</h2>"
        f'<p class="coverage">{_coverage_sentence(comparison)}</p>'
        f'<div class="dist-row">{_distribution_html(summary)}</div>'
        f"{_statistics_section(comparison)}"
        f'<ul class="cards">{cards}</ul>'
        f"{exclusion_block}"
    )


def index_page(comparisons: Sequence[Mapping[str, object]], origin: str) -> Page:
    references = _cross_references(comparisons)
    first_cohort = cast(Mapping[str, object], comparisons[0]["cohort"])
    sections = "".join(_cohort_section(comparison, references) for comparison in comparisons)
    data_links = " · ".join(
        f'<a href="data/{_e(_cohort_data_name(comparison))}">machine-readable comparison '
        f"({_e(_cohort_title(comparison))})</a>"
        for comparison in comparisons
    )
    # The dataset and the API are generated by this render from these same documents, so a
    # consumer that takes the CSV is reading the numbers on this page rather than a second
    # pipeline's answer to the same question.
    export_links = (
        '<a href="dataset.csv">dataset.csv</a> · '
        '<a href="dataset.schema.json">its Table Schema</a> · '
        '<a href="api/index.json">JSON API</a>'
    )
    jsonld = _json_ld(
        {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "mrf-honest hospital price-transparency file grades",
            "url": f"{origin}/",
            "dateModified": str(first_cohort.get("as_of")),
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
            "isAccessibleForFree": True,
        }
    )
    body = (
        '<header class="hero"><h1>Hospital price-transparency files, graded</h1>'
        '<p class="lede">US hospitals must publish machine-readable files of their standard '
        "charges. This site grades each published <em>file</em> — never the hospital — on "
        "whether it can be retrieved, parsed, and interpreted, with every finding citing the "
        "rule or schema requirement it rests on. JSON and CSV publications are graded under "
        "separate profiles and never pooled into one distribution.</p></header>"
        f"{sections}"
        '<p><a href="how-we-grade/">How grading works, and what it deliberately does not '
        f"claim</a> · {data_links} · {export_links} · "
        '<a href="https://github.com/ChelseaKR/mrf-honest/tree/master/docs/findings">'
        "written-up findings with evidence</a></p>"
        f'<p class="caveat">{_CAVEAT}</p>{jsonld}'
    )
    return Page(
        path="",
        title="mrf-honest: hospital price-transparency file grades",
        # No count. The number that used to sit here was the sum of every cohort's
        # `targeted`, and it was the only place in the project that added them up: the page
        # renders each cohort in its own section precisely because their rows were assessed
        # under different profiles and must never be pooled into one distribution
        # (docs/how-we-compare.md, and the comment above `_cohort_section`). A figure the
        # page declines to state is not a figure its search result should state either, and
        # a preview card is the one surface where nobody rechecks it.
        description=(
            "Deterministic, spec-cited grades for hospital price-transparency files, "
            "with fail-closed coverage reporting."
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


def _sampling_frame_section(collection: Mapping[str, object]) -> str:
    """Publish how the cohort's subjects were chosen, or say plainly that they were not.

    A grade distribution invites a reader to generalize from it whether or not the page invites
    them to. The first cohort had no frame and its pages did not say so, which left the
    disclaimer to the reader's charity. A cohort that states a frame renders it here; a cohort
    that has none renders that fact rather than an empty heading.
    """
    frame = collection.get("sampling_frame")
    if not isinstance(frame, Mapping):
        return (
            "<h3>How these subjects were chosen</h3>"
            "<p>This cohort has no stated sampling frame: its subjects were reached for because "
            "their files were discoverable, not because they were drawn from a defined "
            "population. It describes the files in it and supports no statement about hospital "
            "price-transparency publishing in general.</p>"
        )
    frame_map = cast(Mapping[str, object], frame)
    document = frame_map.get("document")
    link = (
        f' <a href="https://github.com/ChelseaKR/mrf-honest/blob/master/{_e(document)}"'
        f' rel="nofollow">{_e(document)}</a> states it in full.'
        if isinstance(document, str)
        else ""
    )
    return (
        "<h3>How these subjects were chosen</h3>"
        f"<p>{_e(frame_map.get('summary'))}{link}</p>"
        f"<p>{_e(frame_map.get('format_rule'))}</p>"
    )


def _cohort_methods_section(comparison: Mapping[str, object]) -> str:
    cohort = cast(Mapping[str, object], comparison["cohort"])
    policy = cast(Mapping[str, object], cohort["grade_policy"])
    collection = cast(Mapping[str, object], comparison.get("collection") or {})
    title = _cohort_title(comparison)
    return (
        f"<h2>The {_e(title)} cohort ({_e(cohort.get('as_of'))})</h2>"
        f"<p>Grade policy <code>{_e(policy.get('version'))}</code>, fingerprint "
        f"<code>{_e(str(policy.get('fingerprint'))[:16])}…</code>; the full rule table ships "
        "in this cohort's machine-readable comparison.</p>"
        f"<p>{_e(collection.get('description'))}</p>"
        f"{_sampling_frame_section(collection)}"
        f"<h3>Finding codes emitted in this cohort</h3>"
        f"{_matrix_section(comparison)}"
    )


def methods_page(comparisons: Sequence[Mapping[str, object]], origin: str) -> Page:
    cohort_sections = "".join(_cohort_methods_section(c) for c in comparisons)
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
        "download attempt), conformance (selected CMS v3 envelope or general-element, "
        "structure, and accepted-value checks), completeness (presence and usability of the "
        "fields that make a rate interpretable), interpretability (whether rates are usable "
        "amounts or require separate treatment), and freshness (the file's own "
        "<code>last_updated_on</code> against the assessment date). JSON publications are "
        "assessed under the CMS JSON v3 profile and CSV publications under the CMS CSV v3 "
        "profile (Tall and Wide); the two are never pooled. The complete finding catalogs with "
        "citations are in "
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
        "<p>In evaluation order: a failed download is an "
        "<strong>F</strong> with the dated reason; a local limit (invalid input, the project's "
        "size ceiling, cache trouble) is <strong>not graded</strong> and never conflated with "
        "an F; an incomplete stream is an <strong>F</strong>; then errors (or missing "
        "evidence) in zero dimensions is an <strong>A</strong> (warnings make it a "
        "<strong>B</strong>), one dimension a <strong>C</strong>, two a <strong>D</strong>, "
        "three or more an <strong>F</strong>. Tolerated INFO observations never lower a "
        "grade. Each cohort names the exact policy its grades were minted under below.</p>"
        "<h2>Retrieval conduct</h2>"
        "<p>Hosts' <code>robots.txt</code> files were checked for every retrieved URL before "
        "the run; a site that blocks is recorded and skipped, never circumvented. Retrieval "
        "of these files is what the transparency rule exists to allow: CMS requires them to be "
        "public, machine-readable, and accessible without barriers.</p>"
        f"{cohort_sections}"
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


def _discovery_tags(page: Page, origin: str) -> str:
    """Canonical and social-card tags, or the refusal to issue them for the error page.

    The error page is written to `404.html` but its `Page.path` is `"404"`, so the canonical
    this function used to build for it was `{origin}/404/` — an address that does not exist
    and itself returns 404. An error page should not canonicalise anywhere, and should not
    be indexed at all; it now says so instead of pointing at nothing.

    For every real page the canonical is ABSOLUTE and carries the `/mrf-honest/` path
    segment, because `origin` does. That matters more than it would elsewhere: this is one
    of six project sites on the shared `chelseakr.github.io` origin, served from paths
    rather than from domains of their own, so a canonical of "/" would name a different
    site's address rather than a shorter form of this one, and all six would claim it.

    There is no `og:image`. This repository ships no image of any kind, deliberately:
    `perf/resource-budget.json` sets `max_request_count.image` to 0 so that adding one is a
    failed build rather than a change nobody noticed. `twitter:card` is therefore `summary`,
    which promises no image, rather than `summary_large_image`, which does.
    """
    if page.path == "404":
        return '<meta name="robots" content="noindex">'
    canonical = f"{origin}/{page.path + '/' if page.path else ''}"
    return "\n".join(
        [
            f'<link rel="canonical" href="{_e(canonical)}">',
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="mrf-honest">',
            f'<meta property="og:title" content="{_e(page.title)}">',
            f'<meta property="og:description" content="{_e(page.description)}">',
            f'<meta property="og:url" content="{_e(canonical)}">',
            '<meta property="og:locale" content="en_US">',
            '<meta name="twitter:card" content="summary">',
        ]
    )


def _shell(page: Page, origin: str, generated_at: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(page.title)}</title>
<meta name="description" content="{_e(page.description)}">
{_discovery_tags(page, origin)}
<link rel="icon" href="data:,">
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
<p>Something here wrong about your file? <a href="{CORRECTIONS_URL}">Corrections, disputes and
removal</a>. A removal request is honoured on request: you are not asked to prove anything.</p>
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


def write_dataset(
    out_dir: Path, comparisons: Sequence[Mapping[str, object]], origin: str
) -> list[Path]:
    """Write `dataset.csv`, its Table Schema, and the static JSON API.

    Written by the render, from the same comparison documents the pages were rendered from, so
    the dataset cannot describe a cohort the site does not show or carry a number the page does
    not carry.
    """

    written: list[Path] = []
    dataset_path = out_dir / "dataset.csv"
    dataset_path.write_text(dataset_csv(comparisons), encoding="utf-8", newline="")
    written.append(dataset_path)
    schema_path = out_dir / "dataset.schema.json"
    schema_path.write_text(encode(table_schema()), encoding="utf-8")
    written.append(schema_path)
    for relative, document in sorted(api_documents(comparisons, origin).items()):
        path = out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encode(document), encoding="utf-8")
        written.append(path)
    return written


def render_site(
    comparison: Mapping[str, object] | Sequence[Mapping[str, object]],
    out_dir: Path,
    *,
    origin: str = DEFAULT_ORIGIN,
) -> list[Path]:
    """Render the complete static site for one or more cohort comparison documents.

    Each comparison stays its own clearly scoped section; nothing is pooled across profiles.
    A single mapping is accepted for compatibility with single-cohort callers.
    """
    comparisons: list[Mapping[str, object]] = (
        [comparison] if isinstance(comparison, Mapping) else list(comparison)
    )
    if not comparisons:
        raise ValueError("render_site needs at least one comparison document")
    slugs: dict[str, str] = {}
    for entry in comparisons:
        cohort_id = str(cast(Mapping[str, object], entry["cohort"]).get("cohort_id"))
        for row in _rows(entry):
            slug = str(row["slug"])
            if slug in slugs:
                raise ValueError(
                    f"file slug {slug!r} appears in both {slugs[slug]!r} and {cohort_id!r}; "
                    "every file page needs exactly one source row"
                )
            slugs[slug] = cohort_id
    generated_at = str(comparisons[0].get("generated_at"))
    pages = [
        index_page(comparisons, origin),
        methods_page(comparisons, origin),
        *(file_page(row, entry, origin) for entry in comparisons for row in _rows(entry)),
        not_found_page(),
    ]
    written = [write_page(out_dir, page, origin, generated_at) for page in pages]
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for index, entry in enumerate(comparisons):
        encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        data_path = data_dir / _cohort_data_name(entry)
        data_path.write_text(encoded, encoding="utf-8")
        written.append(data_path)
        if index == 0:
            # The pre-profile site published exactly one document at this URL; keep it alive.
            compat_path = data_dir / "comparison.json"
            compat_path.write_text(encoded, encoding="utf-8")
            written.append(compat_path)
    written.extend(write_dataset(out_dir, comparisons, origin))
    sitemap_path = out_dir / "sitemap.xml"
    sitemap_path.write_text(sitemap(pages, origin), encoding="utf-8")
    written.append(sitemap_path)
    robots_path = out_dir / "robots.txt"
    robots_path.write_text(robots(origin), encoding="utf-8")
    written.append(robots_path)
    return written


_RULES = """
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
.status-observed { background: var(--observed-wash); color: var(--a); }
.status-findings { background: var(--findings-wash); color: var(--c-ink); }
.status-not_assessed { background: var(--wash); color: var(--muted); }
.status-word, .dim-note { color: var(--muted); font-size: .85rem; margin: .2rem 0; }
.findings { list-style: none; padding: 0; margin: .4rem 0 0; }
.finding { display: flex; gap: .6rem; padding: .35rem 0;
  border-top: 1px dashed var(--line); font-size: .9rem; }
.finding.none { color: var(--muted); }
.sev { font-size: .65rem; font-weight: 700; align-self: flex-start;
  padding: .1rem .4rem; border-radius: 3px; background: var(--wash); color: var(--muted); }
.sev-error .sev { background: var(--error-wash); color: var(--f); }
.sev-warning .sev { background: var(--findings-wash); color: var(--c-ink); }
.finding-copy { overflow-wrap: anywhere; }
.facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  gap: .5rem 1rem; margin: .6rem 0; }
.facts div { border: 1px solid var(--line); border-radius: 6px; padding: .5rem .7rem; }
.facts dt { font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.exclusions li { margin: .5rem 0; }
.shares { border-collapse: collapse; width: 100%; margin: .6rem 0; font-size: .9rem; }
.shares th, .shares td { border: 1px solid var(--line); padding: .4rem .6rem;
  text-align: left; vertical-align: top; }
.shares thead th { background: var(--wash); }
.shares tbody th { font-weight: 400; }
.refusal { border-left: 3px solid var(--line); padding-left: .8rem; margin: .6rem 0; }
.method-card { border: 1px solid var(--line); border-radius: 8px;
  padding: .8rem 1rem; margin: .8rem 0; }
.caveat { margin-top: 2.5rem; padding-top: .8rem; border-top: 1px solid var(--line);
  color: var(--muted); font-size: .85rem; }
.crumbs { font-size: .85rem; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
@media print { footer, .skip-link { display: none; } }
"""

_STYLE = _root_block() + _RULES
