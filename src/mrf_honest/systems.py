"""Reconcile a cohort's graded files against the locations a system's ``cms-hpt.txt`` lists.

CMS's convention is one machine-readable file per hospital location, and a system publishes one
``cms-hpt.txt`` naming every location with the file that serves it. The cohort grades *files*.
The README's rule is that a grade never becomes a statement about a hospital, so a fact about a
system's whole *publication set* -- fifteen locations listed, one of them with no file at all --
has nowhere to live: no per-file letter can express it, and no per-file letter should absorb it.

This module is that separate place. It reads committed evidence only (persisted assessments and
the local discovery registry), opens no socket, and touches no grade.

**The join is an exact URL hash, and it has to be.** A published row carries both
``requested_url`` -- with its query string and any userinfo redacted -- and
``requested_url_sha256``, which is the digest of the *unredacted* URL. Joining on the redacted
string is the obvious thing and it is wrong: measured on the committed cohorts, two entirely
different publishers (Bay Area Hospital and Taylor Regional Hospital) publish files at one vendor
endpoint that differ only in a query parameter, so a redacted-string join reported each system as
listing the other's file. Joining on ``requested_url_sha256`` against ``sha256(mrf_url)`` gives an
exact match, and matches every one of the 48 committed rows across the three cohorts.

**Two locations sharing one file is normal, and is stated rather than flagged.** The JSON
dictionary defines ``location_name`` as "an array of strings of the unique name(s) of the hospital
location(s)", so one file may legitimately serve several listed locations. What that makes
checkable is whether the file's own ``location_name`` array covers the locations its system's
discovery file pointed at it -- a cross-source consistency check that neither document supports
alone.

**Two absences that must not read alike.** A listed location with no graded file in this cohort is
almost never a publication defect: these cohorts are drawn samples, so most of a system's
locations were simply never targeted. That is reported as this project's sampling scope, in its
own field, with the reason. It is kept strictly apart from the two things that *are* facts about
the publication set: a listed location whose entry carries no ``mrf-url`` at all, and a graded row
whose URL no listed location declares.

**Every element difference is published; only two are counted.** The CMS general data elements
identify the file, and where a system's files differ on any of them that difference is published
with both files named. Only ``version`` and ``last_updated_on`` are *counted* as a disagreement:
they describe the template a file was written to and the date it was last updated, so one
system's files should agree on them. The rest are defined per location by the dictionary itself
("the legal business name of the hospital associated with the file"), and #74 asked for
``license_information`` among the counted ones before anybody had run it -- on the committed
2026-08-14 cohort that produces exactly one row, Stanford Health Care's licence beside Stanford
Health Care Tri-Valley's, which is two separately licensed hospitals in one system publishing
correctly. Counting it would have manufactured a finding out of that.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from mrf_honest.cohort import grade_assessment
from mrf_honest.scorecard import require_comparable

#: Bumped when this document's shape changes.
SYSTEMS_VERSION = 1

#: The CMS general data elements this reconciliation reads, in dictionary order.
GENERAL_DATA_ELEMENTS = (
    "hospital_name",
    "location_name",
    "hospital_address",
    "license_information",
    "version",
    "last_updated_on",
)

#: The elements a system's files should agree on: the template they were written to and the date
#: they were last updated. One location of a system publishing an older template, or a file
#: months staler than its siblings, is a fact about the publication set.
ELEMENTS_THAT_MUST_AGREE = ("version", "last_updated_on")

#: Every other general data element is defined *per location* by the CMS dictionary, so a
#: system's files differing on it is those files doing their job. The difference is still
#: published -- suppressing it would hide the data the reader needs to judge -- but it is
#: labelled, and it is never counted as a system whose files disagree.
MUST_AGREE = "must_agree_within_a_system"
EXPECTED_TO_VARY = "expected_to_vary_by_location"

#: Measured, and the reason ``license_information`` is labelled rather than counted. On the
#: committed 2026-08-14 cohort it produces exactly one row: Stanford Health Care
#: (``070000662``) beside Stanford Health Care Tri-Valley (``140000114``). Those are two
#: separately licensed hospitals in one system, which is correct publication. Counting it as a
#: disagreement would have manufactured a finding out of a system doing the right thing, and the
#: issue that asked for this row (#74) did not have that measurement in front of it.
ELEMENT_BASIS_REASON = (
    "version and last_updated_on describe the template a file was written to and the date it was "
    "last updated, so one system's files should agree on them. Every other general data element "
    "is defined per location by the CMS data dictionary -- hospital_name is 'the legal business "
    "name of the hospital associated with the file' -- so a system's files differing on it is "
    "correct publication. Those differences are published here and labelled, never counted."
)

#: What the sampling field means, stated in the document so nobody has to infer it.
NOT_ASSESSED_BASIS = (
    "locations this system's cms-hpt.txt lists that this cohort did not grade. A cohort draws a "
    "sample, so this is this project's scope and not a fact about the publisher: it is not a "
    "missing file, and it is never a finding."
)

#: The three things here that *are* facts about a publication set.
RECONCILIATION_BASIS = (
    "for each publisher whose retrieved cms-hpt.txt lists more than one location: the locations "
    "listed with no mrf-url at all, the graded files no listed location declares, whether each "
    "graded file's own location_name array covers the locations its discovery file pointed at "
    "it, and where the system's files disagree about the elements that identify them. No file's "
    "grade or findings are read into any of it."
)

CMS_DISCOVERY_CITATION = (
    "https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/README.md"
)

_LOCATION_NAME_CITATION = (
    "CMS data dictionary, location_name: an array of strings of the unique name(s) of the "
    "hospital location(s) absent any acronyms"
)

#: The reconciliation ran against evidence that declares at least one of this cohort's rows.
RECONCILED = "reconciled"
#: No discovery record in the supplied evidence declares any URL this cohort graded, so nothing
#: was reconciled. Never rendered as a cohort of undeclared files: with no evidence in hand, "no
#: listed location declares this file" is not a statement anybody is in a position to make.
NO_DISCOVERY_EVIDENCE = "no_discovery_evidence"

#: Why a reconciliation was not attempted, stated in the document rather than left to inference.
NO_EVIDENCE_REASON = (
    "no discovery record on or before this cohort's date declares any URL it graded, so this "
    "cohort was not reconciled. This is an absence of evidence about the discovery files, not "
    "evidence that the files do not declare these locations."
)

#: Agreement states for a file's own location list against the locations pointed at it.
COVERS = "covers_the_listed_locations"
FILE_NAMES_FEWER = "file_names_fewer_locations_than_are_listed_against_it"
FILE_NAMES_MORE = "file_names_more_locations_than_are_listed_against_it"
NOT_INSPECTED = "not_inspected"


class SystemsError(ValueError):
    """Raised when a reconciliation cannot be performed honestly."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


#: A parsed block carrying neither a location name nor an MRF URL is not a location entry.
#:
#: Measured on the committed evidence: UPMC's cms-hpt.txt opens with an ASCII-art banner and five
#: lines of instructions for a human reader, and the parser -- correctly, since it records
#: everything it could not read -- splits those into four blocks with no fields at all. Counting
#: them as listed locations makes UPMC publish 42 locations instead of 38 and four locations
#: "listed with no mrf-url", which is a defect in a named system's publication invented out of
#: this project's own parser. They are counted and stated separately, and never as locations.
UNPARSED_BLOCK_NOTE = (
    "blocks in this cms-hpt.txt that carry neither a location name nor an mrf-url. They are what "
    "the parser made of text that is not a location entry -- a banner, an instruction to a human "
    "reader -- and they are not counted as listed locations, because a location this system never "
    "claimed to list is not a location it failed to publish a file for."
)


def _all_blocks(record: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    discovery = _mapping(record.get("discovery"))
    if discovery is None:
        return ()
    raw = discovery.get("entries")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _is_location_entry(entry: Mapping[str, object]) -> bool:
    """A block claims to be a location when it names one or points at a file. Neither is not."""
    name = entry.get("location_name")
    url = entry.get("mrf_url")
    return bool(isinstance(name, str) and name.strip()) or bool(
        isinstance(url, str) and url.strip()
    )


def _entries(record: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """The blocks of a retrieved cms-hpt.txt that claim to be location entries."""
    return tuple(block for block in _all_blocks(record) if _is_location_entry(block))


def _unparsed_blocks(record: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    return tuple(block for block in _all_blocks(record) if not _is_location_entry(block))


def select_discovery_evidence(
    records: Iterable[Mapping[str, object]],
    *,
    as_of: str,
    url_digests: frozenset[str],
) -> tuple[Mapping[str, object], ...]:
    """The discovery records a cohort's rows actually came from, frozen at the cohort's date.

    Selection is deterministic and does two things a naive filter gets wrong.

    It takes the **latest successful attempt on or before** ``as_of`` per domain, not the attempts
    made on that date. Six rows of the committed 2026-08-19 JSON cohort were carried over from the
    2026-08-14 run and their ``cms-hpt.txt`` was retrieved on the earlier date; a same-day filter
    drops them and the reconciliation silently loses a quarter of the cohort.

    It then keeps only records that declare a URL this cohort actually graded, so a domain
    discovered for a different profile's cohort cannot contribute an empty system.
    """
    latest: dict[str, Mapping[str, object]] = {}
    for record in records:
        attempted = str(record.get("attempted_at"))
        if attempted[:10] > as_of:
            continue
        if not _entries(record):
            continue
        domain = str(record.get("domain"))
        current = latest.get(domain)
        if current is None or attempted > str(current.get("attempted_at")):
            latest[domain] = record
    selected = [
        record
        for record in latest.values()
        if any(
            _digest(str(entry.get("mrf_url"))) in url_digests
            for entry in _entries(record)
            if entry.get("mrf_url")
        )
    ]
    return tuple(sorted(selected, key=lambda record: str(record.get("url"))))


def _element_values(envelope: Mapping[str, object] | None) -> dict[str, object]:
    if envelope is None:
        return {name: None for name in GENERAL_DATA_ELEMENTS}
    return {name: envelope.get(name) for name in GENERAL_DATA_ELEMENTS}


def _file_view(record: Mapping[str, object]) -> dict[str, object]:
    subject = cast(Mapping[str, object], record["subject"])
    publisher = cast(Mapping[str, object], subject["publisher"])
    inspection = _mapping(record.get("inspection"))
    envelope = _mapping(inspection.get("envelope")) if inspection is not None else None
    grade = grade_assessment(record)
    return {
        "slug": f"{publisher.get('identifier')}/{subject.get('location_id')}",
        "publisher_id": str(publisher.get("identifier")),
        "publisher_name": publisher.get("name"),
        "location_id": str(subject.get("location_id")),
        "requested_url": subject.get("requested_url"),
        "requested_url_sha256": str(subject.get("requested_url_sha256")),
        "grade": grade.grade,
        "inspected": envelope is not None,
        "general_data_elements": _element_values(envelope),
    }


def _location_names(value: object) -> tuple[str, ...]:
    """The file's own ``location_name``, whether the profile encodes it as an array or a string."""
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if isinstance(item, str) and item.strip())
    return ()


def _agreement(listed: Sequence[str], view: Mapping[str, object]) -> dict[str, object]:
    """Whether a file's own location list covers the locations listed against it."""
    if not view["inspected"]:
        return {
            "state": NOT_INSPECTED,
            "listed_against_this_file": list(listed),
            "named_by_this_file": [],
            "citation": _LOCATION_NAME_CITATION,
        }
    elements = cast(Mapping[str, object], view["general_data_elements"])
    named = _location_names(elements.get("location_name"))
    if len(named) < len(listed):
        state = FILE_NAMES_FEWER
    elif len(named) > len(listed):
        state = FILE_NAMES_MORE
    else:
        state = COVERS
    return {
        "state": state,
        "listed_against_this_file": list(listed),
        "named_by_this_file": list(named),
        "citation": _LOCATION_NAME_CITATION,
    }


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _disagreements(views: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """One row per element the system's inspected files do not agree on.

    Grouped by value rather than emitted pairwise: a fifteen-file system disagreeing on one
    element would otherwise produce a hundred rows of the same fact.
    """
    inspected = [view for view in views if view["inspected"]]
    rows: list[dict[str, object]] = []
    for element in GENERAL_DATA_ELEMENTS:
        by_value: dict[str, list[str]] = {}
        for view in inspected:
            elements = cast(Mapping[str, object], view["general_data_elements"])
            by_value.setdefault(_canonical(elements.get(element)), []).append(str(view["slug"]))
        if len(by_value) < 2:
            continue
        rows.append(
            {
                "element": element,
                "basis": MUST_AGREE if element in ELEMENTS_THAT_MUST_AGREE else EXPECTED_TO_VARY,
                "citation": CMS_DISCOVERY_CITATION,
                "values": [
                    {"value": json.loads(encoded), "files": sorted(slugs)}
                    for encoded, slugs in sorted(by_value.items())
                ],
            }
        )
    return rows


def _listed_entry(entry: Mapping[str, object]) -> dict[str, object]:
    return {
        "location_name": entry.get("location_name"),
        "mrf_url": entry.get("mrf_url"),
        "source_page_url": entry.get("source_page_url"),
        "problems": list(cast(Sequence[object], entry.get("problems") or ())),
    }


def _system_entry(
    discovery: Mapping[str, object],
    views: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    entries = _entries(discovery)
    digests: dict[str, list[Mapping[str, object]]] = {}
    without_url: list[dict[str, object]] = []
    for entry in entries:
        url = entry.get("mrf_url")
        if not isinstance(url, str) or not url:
            without_url.append(_listed_entry(entry))
            continue
        digests.setdefault(_digest(url), []).append(entry)

    by_digest = {str(view["requested_url_sha256"]): view for view in views}
    assessed: list[dict[str, object]] = []
    shared: list[dict[str, object]] = []
    matched_digests: set[str] = set()
    for digest in sorted(digests):
        listed = [str(entry.get("location_name")) for entry in digests[digest]]
        view = by_digest.get(digest)
        if view is None:
            continue
        matched_digests.add(digest)
        assessed.append(
            {
                "slug": view["slug"],
                "location_id": view["location_id"],
                "grade": view["grade"],
                "requested_url": view["requested_url"],
                "general_data_elements": view["general_data_elements"],
                "location_name_agreement": _agreement(listed, view),
            }
        )
        if len(digests[digest]) > 1:
            shared.append(
                {
                    "slug": view["slug"],
                    "listed_locations": listed,
                    "note": (
                        "this system's cms-hpt.txt points several listed locations at one file; "
                        "the CMS dictionary permits it, and the file's own location_name array "
                        "is checked against the list above"
                    ),
                }
            )

    not_assessed = [
        {"location_name": str(entry.get("location_name")), "mrf_url": entry.get("mrf_url")}
        for digest in sorted(digests)
        if digest not in matched_digests
        for entry in digests[digest]
    ]
    assessed.sort(key=lambda item: str(item["slug"]))
    served = sum(len(digests[digest]) for digest in matched_digests)
    fetch = _mapping(discovery.get("fetch")) or {}
    return {
        "publisher_id": str(views[0]["publisher_id"]),
        "publisher_name": views[0]["publisher_name"],
        "discovery": {
            "url": discovery.get("url"),
            "domain": discovery.get("domain"),
            "retrieved_at": discovery.get("attempted_at"),
            "content_sha256": fetch.get("content_sha256"),
            "citation": CMS_DISCOVERY_CITATION,
        },
        "locations_listed": len(entries),
        "locations_served_by_a_graded_file": served,
        "blocks_that_are_not_location_entries": {
            "count": len(_unparsed_blocks(discovery)),
            "note": UNPARSED_BLOCK_NOTE,
        },
        "locations_listed_without_an_mrf_url": without_url,
        "locations_assessed_in_this_cohort": assessed,
        "locations_not_assessed_in_this_cohort": {
            "basis": NOT_ASSESSED_BASIS,
            "count": len(not_assessed),
            "locations": not_assessed,
        },
        "files_serving_several_listed_locations": shared,
        "disagreements": _disagreements(
            [view for view in views if str(view["requested_url_sha256"]) in matched_digests]
        ),
        "element_basis": {
            "must_agree_within_a_system": list(ELEMENTS_THAT_MUST_AGREE),
            "expected_to_vary_by_location": [
                name for name in GENERAL_DATA_ELEMENTS if name not in ELEMENTS_THAT_MUST_AGREE
            ],
            "reason": ELEMENT_BASIS_REASON,
        },
    }


UNLISTED_NOTE = (
    "this cohort graded a file that no retrieved cms-hpt.txt in the selected discovery evidence "
    "declares; the cohort's own discovery claim is that every graded URL came from a location "
    "entry, so this is a defect in that claim and not a statement about the publisher"
)


def _route_rows(
    views: Sequence[Mapping[str, object]],
    selected: Sequence[Mapping[str, object]],
) -> tuple[dict[str, list[Mapping[str, object]]], list[dict[str, object]]]:
    """Each graded row filed under the discovery record that declares its URL, or unlisted."""
    declared_by: dict[str, Mapping[str, object]] = {}
    for record in selected:
        for entry in _entries(record):
            url = entry.get("mrf_url")
            if isinstance(url, str) and url:
                declared_by.setdefault(_digest(url), record)

    by_source: dict[str, list[Mapping[str, object]]] = {}
    unlisted: list[dict[str, object]] = []
    for view in views:
        source = declared_by.get(str(view["requested_url_sha256"]))
        if source is None:
            unlisted.append(
                {
                    "slug": view["slug"],
                    "requested_url": view["requested_url"],
                    "note": UNLISTED_NOTE,
                }
            )
            continue
        by_source.setdefault(str(source.get("url")), []).append(view)
    return by_source, unlisted


def _unreconciled(
    rows: Sequence[Mapping[str, object]],
    views: Sequence[Mapping[str, object]],
    as_of: str,
) -> dict[str, object]:
    """The document for a cohort no supplied discovery evidence speaks to.

    Every count that would otherwise read as a finding is absent rather than zero, and
    ``graded_without_a_listed_location`` is empty rather than "all of them": with no evidence
    selected, this build has not looked at a discovery file and is in no position to say that
    none declares a given URL. An empty reconciliation reading as "checked, nothing to report"
    is the exact defect this discriminator exists to prevent.
    """
    return {
        "systems_version": SYSTEMS_VERSION,
        "status": NO_DISCOVERY_EVIDENCE,
        "reason": NO_EVIDENCE_REASON,
        "basis": RECONCILIATION_BASIS,
        "cohort": {
            "as_of": as_of,
            "comparison_scope": dict(
                cast(Mapping[str, object], rows[0].get("comparison_scope") or {})
            ),
        },
        "discovery_evidence": {"records_selected": 0, "records": []},
        "coverage": {
            "rows": len(views),
            "rows_declared_by_a_listed_location": None,
            "rows_no_listed_location_declares": None,
        },
        "graded_without_a_listed_location": [],
        "systems": [],
        "summary": None,
    }


def reconcile(
    records: Sequence[Mapping[str, object]],
    discovery_records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Reconcile one cohort's assessments against the discovery evidence they came from.

    ``records`` are integrity-verified persisted assessment rows, as ``AssessmentRegistry``
    returns them. ``discovery_records`` are local discovery-registry records; pass the whole
    registry and this selects from it, or pass a frozen per-cohort subset.
    """
    rows = tuple(records)
    if not rows:
        raise SystemsError("a system reconciliation needs at least one assessment row")
    if len(rows) > 1:
        require_comparable(*rows)
    views = [_file_view(record) for record in rows]
    as_of = str(rows[0].get("as_of"))
    digests = frozenset(str(view["requested_url_sha256"]) for view in views)
    selected = select_discovery_evidence(discovery_records, as_of=as_of, url_digests=digests)

    if not selected:
        return _unreconciled(rows, views, as_of)

    by_source, unlisted = _route_rows(views, selected)

    selected_by_url = {str(record.get("url")): record for record in selected}
    publishers: list[dict[str, object]] = []
    for url in sorted(by_source):
        discovery = selected_by_url[url]
        if len(_entries(discovery)) < 2:
            # Single-location publishers get no system entry: there is nothing to reconcile
            # across, and an empty section would read as a checked-and-clean result.
            continue
        publishers.append(_system_entry(discovery, by_source[url]))

    return {
        "systems_version": SYSTEMS_VERSION,
        "status": RECONCILED,
        "reason": None,
        "basis": RECONCILIATION_BASIS,
        "cohort": {
            "as_of": as_of,
            "comparison_scope": dict(
                cast(Mapping[str, object], rows[0].get("comparison_scope") or {})
            ),
        },
        "discovery_evidence": {
            "records_selected": len(selected),
            "records": [
                {"url": record.get("url"), "retrieved_at": record.get("attempted_at")}
                for record in selected
            ],
        },
        "coverage": {
            "rows": len(views),
            "rows_declared_by_a_listed_location": len(views) - len(unlisted),
            "rows_no_listed_location_declares": len(unlisted),
        },
        "graded_without_a_listed_location": unlisted,
        "systems": publishers,
        "summary": _summary(publishers, unlisted),
    }


def _summary(
    publishers: Sequence[Mapping[str, object]], unlisted: Sequence[object]
) -> dict[str, object]:
    return {
        "multi_location_systems": len(publishers),
        "locations_listed": sum(cast(int, system["locations_listed"]) for system in publishers),
        "locations_assessed_in_this_cohort": sum(
            len(cast(Sequence[object], system["locations_assessed_in_this_cohort"]))
            for system in publishers
        ),
        "locations_listed_without_an_mrf_url": sum(
            len(cast(Sequence[object], system["locations_listed_without_an_mrf_url"]))
            for system in publishers
        ),
        "files_serving_several_listed_locations": sum(
            len(cast(Sequence[object], system["files_serving_several_listed_locations"]))
            for system in publishers
        ),
        "systems_whose_files_disagree_on_an_element_that_must_agree": sum(
            1
            for system in publishers
            if any(
                cast(Mapping[str, object], row)["basis"] == MUST_AGREE
                for row in cast(Sequence[object], system["disagreements"])
            )
        ),
        "rows_no_listed_location_declares": len(unlisted),
    }


def human_report(document: Mapping[str, object]) -> str:
    """A deterministic plain-text report of one reconciliation."""
    cohort = cast(Mapping[str, object], document["cohort"])
    coverage = cast(Mapping[str, object], document["coverage"])
    if document["status"] != RECONCILED:
        return (
            f"cohort as of {cohort['as_of']}: {coverage['rows']} graded file(s), "
            f"not reconciled.\n{document['reason']}"
        )
    summary = cast(Mapping[str, object], document["summary"])
    lines = [
        f"cohort as of {cohort['as_of']}: {coverage['rows']} graded file(s), "
        f"{coverage['rows_declared_by_a_listed_location']} declared by a listed location, "
        f"{coverage['rows_no_listed_location_declares']} declared by none",
        f"{summary['multi_location_systems']} multi-location system(s)",
        "",
    ]
    for entry in cast(Sequence[object], document["graded_without_a_listed_location"]):
        row = cast(Mapping[str, object], entry)
        lines.append(f"! {row['slug']}: no retrieved cms-hpt.txt declares this file")
    for entry in cast(Sequence[object], document["systems"]):
        system = cast(Mapping[str, object], entry)
        discovery = cast(Mapping[str, object], system["discovery"])
        assessed = cast(Sequence[object], system["locations_assessed_in_this_cohort"])
        not_assessed = cast(Mapping[str, object], system["locations_not_assessed_in_this_cohort"])
        lines.append(
            f"{system['publisher_id']} ({discovery['url']}, retrieved {discovery['retrieved_at']})"
        )
        lines.append(
            f"  {system['locations_listed']} location(s) listed; "
            f"{system['locations_served_by_a_graded_file']} served by a file graded here "
            f"({len(assessed)} file(s)); {not_assessed['count']} not assessed in this cohort "
            "(this project's sampling scope, not a defect)"
        )
        blocks = cast(Mapping[str, object], system["blocks_that_are_not_location_entries"])
        if blocks["count"]:
            lines.append(
                f"  ({blocks['count']} block(s) in the file are not location entries and are "
                "not counted above)"
            )
        for missing in cast(Sequence[object], system["locations_listed_without_an_mrf_url"]):
            entry = cast(Mapping[str, object], missing)
            lines.append(f"  ! listed with no mrf-url: {entry['location_name']}")
        for shared in cast(Sequence[object], system["files_serving_several_listed_locations"]):
            share = cast(Mapping[str, object], shared)
            listed = ", ".join(cast(Sequence[str], share["listed_locations"]))
            lines.append(f"  one file for several listed locations: {share['slug']} <- {listed}")
        for graded in assessed:
            file_row = cast(Mapping[str, object], graded)
            agreement = cast(Mapping[str, object], file_row["location_name_agreement"])
            lines.append(
                f"  {file_row['slug']}: grade {file_row['grade']}, location names "
                f"{agreement['state']}"
            )
        for raw_disagreement in cast(Sequence[object], system["disagreements"]):
            disagreement = cast(Mapping[str, object], raw_disagreement)
            mark = "!" if disagreement["basis"] == MUST_AGREE else "-"
            label = (
                "files disagree on"
                if disagreement["basis"] == MUST_AGREE
                else "files differ on (expected to vary by location)"
            )
            lines.append(f"  {mark} {label} {disagreement['element']}:")
            for value in cast(Sequence[object], disagreement["values"]):
                pair = cast(Mapping[str, object], value)
                files = ", ".join(cast(Sequence[str], pair["files"]))
                lines.append(f"      {pair['value']!r} ({files})")
    lines.append("")
    lines.append(
        f"{summary['locations_listed']} location(s) listed across those systems, "
        f"{summary['locations_assessed_in_this_cohort']} graded here, "
        f"{summary['locations_listed_without_an_mrf_url']} listed with no mrf-url, "
        f"{summary['systems_whose_files_disagree_on_an_element_that_must_agree']} system(s) "
        "whose files disagree on an element they should agree on."
    )
    lines.append(
        "No file's grade or findings are read into any row above, and no row above is a finding "
        "against any file."
    )
    return "\n".join(lines)
