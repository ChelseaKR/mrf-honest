"""Count what discovery already found, apart from what the cohorts graded.

A cohort answers "how good is this file". This module answers a cheaper and much larger
question that nothing here was asking: **which files does this project know the address of?**

The two are not the same size, and the gap is the point. Grading one hospital file costs a mean
of 182 MB of somebody else's bandwidth; reading the ``cms-hpt.txt`` at an origin costs a few
kilobytes and yields every location that origin lists at once. So the expensive half of answering
"what about *this* named hospital" is not the grade -- it is resolving the facility to the website
hosting its file, which ``docs/SAMPLING-FRAME.md`` calls "the one manual step in the frame, and
the frame's weakest joint", and which was wrong on **ten of the first forty-eight** candidate
origins. Every retrieved ``cms-hpt.txt`` resolves that joint for every location it names, for
free, and until now nothing counted them.

**This module counts. It never fetches, and it never grades.** It reads the local discovery
registry and the persisted assessment rows, opens no socket, and derives no letter.

Four refusals are structural rather than stylistic, and each is enforced below rather than
documented:

**A URL extension is a candidate, never a determination.** ``.json`` in a path is what the
publisher named the file, not what the server serves; the 2026-08-19 run downloaded 669,479,338
bytes from four hospitals to learn that four extensionless targets were CSV, which is why
``mrf-honest probe`` exists. Every profile here is labeled ``candidate``, and a URL that carries
no usable extension is ``format_not_determinable_from_the_url`` -- its own population, never
folded into "outside the implemented profiles", because "we cannot tell from here" and "this is a
format we do not grade" are different statements about a named hospital.

**An origin that could not be read is not a hospital that did not publish.** ``robots.txt``
disallow, an HTTP status, a TLS failure from one client on one date: these are facts about a
request, counted in their own population with their own reason, and they never enter any
numerator or denominator about publication.

**"Not graded here" is this project's collection scope.** A discovered file that no cohort graded
is not a finding, not a failure and not a gap in the publisher's compliance. It is a file nobody
targeted. ``systems.py`` already holds this line for one system at a time; this holds it for the
whole inventory.

**No share is computed against the 3,024-hospital frame.** The frame enumerates *facilities*;
this enumerates *locations named by documents*, and the join between them does not exist --
neither CMS's dataset nor any other public dataset records which website hosts a given facility's
file. Reporting "N of 3,024" would require silently treating every unresolved facility as
something, and the honest answer is that the resolution is unknown. The frame's own numbers are
carried alongside, labeled, with the two counts kept apart.

**Contact details are excluded, deliberately and by construction.** ``cms-hpt.txt`` carries a
named person and their email for each location. ``docs/CORRECTIONS.md`` promises that this
project does not publish "contact details gathered during discovery", so ``_location_row`` reads
the four fields it needs by name and never copies an entry wholesale.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import cast
from urllib.parse import urlsplit

from mrf_honest.discover import parse_mrf_filename
from mrf_honest.scorecard import public_url, text_digest

#: Bumped when this document's shape changes.
CENSUS_VERSION = 1

#: Candidate profile labels. "Candidate" is load-bearing: see the module docstring.
JSON_CANDIDATE = "cms-hospital-json-v3-candidate"
CSV_CANDIDATE = "cms-hospital-csv-v3-candidate"
OUTSIDE_PROFILES = "outside_the_implemented_profiles"
NOT_DETERMINABLE = "format_not_determinable_from_the_url"

#: Extensions this project implements a profile for, and the ones it knowingly does not. Anything
#: else -- including no extension at all -- is not determinable from a URL.
_PROFILE_BY_EXTENSION = {
    "json": JSON_CANDIDATE,
    "csv": CSV_CANDIDATE,
    "zip": OUTSIDE_PROFILES,
    "xml": OUTSIDE_PROFILES,
    "xls": OUTSIDE_PROFILES,
    "xlsx": OUTSIDE_PROFILES,
    "pdf": OUTSIDE_PROFILES,
    "txt": OUTSIDE_PROFILES,
}

PROFILE_BASIS = (
    "read from the URL the origin's cms-hpt.txt published, and never from the bytes: a path "
    "ending .json is what the publisher named the file. Every label here is a candidate. A URL "
    "with no usable extension is format_not_determinable_from_the_url, which is its own "
    "population and is never counted as a format this project does not grade -- `mrf-honest "
    "probe` exists to answer that with one bounded ranged request."
)

NOT_GRADED_BASIS = (
    "this project has the address of the file and no cohort targeted it. That is this project's "
    "collection scope, not a fact about the publisher: it is not a missing file, not a finding, "
    "and never a compliance statement."
)

NOT_RETRIEVED_BASIS = (
    "the newest attempt at this origin's cms-hpt.txt on or before the census date produced no "
    "body at all. A robots.txt disallow, an HTTP status or a network error is a fact about a "
    "request from one client on one date, and is never counted as an origin that publishes "
    "nothing."
)

#: Kept strictly apart from the population above, and the separation is the point. "The server
#: refused this request" and "the server answered 200 with a web page" are different facts about
#: a named organization, and the second is this portfolio's dominant defect class seen from the
#: outside: an HTTP 200 error page read as content. Measured on the committed registry, three
#: origins are in this state and every one of them carries the parser's own
#: "served HTML rather than a cms-hpt.txt document".
NO_LOCATION_BASIS = (
    "a body was retrieved from this origin and no location entry could be read out of it. That "
    "is not a refused request and it is not an empty publication set: the parser's own problems "
    "are carried here, and the commonest is that the server answered with a web page rather "
    "than a cms-hpt.txt."
)

#: A census that read at least one cms-hpt.txt, and one that read none. The discriminator exists
#: for the same reason ``systems.py``'s does: with no document in hand, "no retrieved cms-hpt.txt
#: declares this URL" is not a statement anybody is in a position to make, and a count of zero
#: located files beside a count of graded rows that match none of them reads as a finding about
#: every one of those rows. Measured on an empty registry before this discriminator existed: the
#: report said "17 graded row(s) match no located file", having looked at nothing.
COUNTED = "counted"
NO_DISCOVERY_EVIDENCE = "no_discovery_evidence"

NO_EVIDENCE_REASON = (
    "no discovery record on or before this census date produced a readable cms-hpt.txt, so "
    "nothing was located and nothing is claimed. This is an absence of evidence about the "
    "discovery documents, not evidence that the documents declare nothing."
)

#: Expected, and stated so that it is not read as a defect. Measured on the committed evidence,
#: all four of these are one of two things: a hospital moved its file, so the URL graded on an
#: earlier date is no longer the one its cms-hpt.txt publishes (three Azure shared access
#: signatures rotated between 2026-08-19 and 2026-09-12); or the origin's newest document could
#: not be retrieved at all, so it currently declares nothing (msh.ms.gov). Neither is a statement
#: about the file that was graded, and neither is a statement about the publisher.
UNMATCHED_GRADED_BASIS = (
    "graded rows whose URL no retrieved cms-hpt.txt in this census declares. A grade is a "
    "statement about bytes at a URL on a date; a discovery document is a statement about what an "
    "origin publishes today. A hospital that moves its file, and an origin whose document could "
    "not be retrieved, both produce this without anything being wrong with either record."
)

FRAME_BASIS = (
    "the frame enumerates CMS facilities; this census enumerates locations named by retrieved "
    "cms-hpt.txt documents. No public dataset records which website hosts a given facility's "
    "file, so the join between the two does not exist and no share is computed across it. The "
    "two counts are carried side by side and never divided."
)

#: A parsed block carrying neither a location name nor an MRF URL is not a location entry -- the
#: same rule ``systems.py`` applies, for the same measured reason (UPMC's ASCII-art banner).
_NOT_A_LOCATION = (
    "blocks in a cms-hpt.txt that carry neither a location name nor an mrf-url: a banner, an "
    "instruction to a human reader. They are not counted as listed locations."
)


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _blocks(record: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    discovery = _mapping(record.get("discovery"))
    if discovery is None:
        return ()
    raw = discovery.get("entries")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _is_location_entry(entry: Mapping[str, object]) -> bool:
    name, url = entry.get("location_name"), entry.get("mrf_url")
    return bool(isinstance(name, str) and name.strip()) or bool(
        isinstance(url, str) and url.strip()
    )


def _entries(record: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    return tuple(block for block in _blocks(record) if _is_location_entry(block))


def candidate_profile(url: str) -> str:
    """The profile a URL *suggests*, from the CMS filename convention or the path extension."""
    facts = parse_mrf_filename(url)
    extension = facts.extension
    if extension is None:
        path = urlsplit(url).path
        filename = path.rsplit("/", 1)[-1]
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else None
    if extension is None:
        return NOT_DETERMINABLE
    return _PROFILE_BY_EXTENSION.get(extension, NOT_DETERMINABLE)


def newest_attempt_per_origin(
    records: Iterable[Mapping[str, object]], *, as_of: str
) -> dict[str, Mapping[str, object]]:
    """The newest attempt at each origin on or before ``as_of``, parsed or not.

    Deliberately not "the newest that parsed": an origin whose latest attempt failed is a
    population of this census, and selecting past the failure would hide it.
    """
    newest: dict[str, Mapping[str, object]] = {}
    for record in records:
        attempted = str(record.get("attempted_at"))
        if attempted[:10] > as_of:
            continue
        domain = str(record.get("domain"))
        current = newest.get(domain)
        if current is None or attempted > str(current.get("attempted_at")):
            newest[domain] = record
    return newest


def graded_files(records: Iterable[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    """Every graded file, keyed by the digest of the exact URL it was requested at.

    The join is the digest and not the published string, for the reason ``systems.py`` measured:
    two unrelated publishers serve files from one vendor endpoint differing only in a query
    parameter, which the published (redacted) string cannot tell apart.
    """
    from mrf_honest.cohort import grade_assessment

    files: dict[str, dict[str, object]] = {}
    for record in records:
        subject = cast(Mapping[str, object], record["subject"])
        publisher = cast(Mapping[str, object], subject["publisher"])
        digest = str(subject.get("requested_url_sha256"))
        graded_on = str(record.get("as_of"))
        existing = files.get(digest)
        if existing is not None and str(existing["as_of"]) >= graded_on:
            continue
        files[digest] = {
            "slug": f"{publisher.get('identifier')}/{subject.get('location_id')}",
            "grade": grade_assessment(record).grade,
            "as_of": graded_on,
            "assessment_policy_version": record.get("assessment_policy_version"),
        }
    return files


def _location_row(
    entry: Mapping[str, object], graded: Mapping[str, dict[str, object]]
) -> dict[str, object]:
    """One listed location, reading only the fields this project publishes.

    Four fields by name, never the entry wholesale: a ``cms-hpt.txt`` entry also carries a named
    person and their email address, and `docs/CORRECTIONS.md` promises those are not published.
    """
    raw = entry.get("mrf_url")
    url = raw if isinstance(raw, str) and raw.strip() else None
    if url is None:
        return {
            "location_name": entry.get("location_name"),
            "mrf_url": None,
            "mrf_url_sha256": None,
            "candidate_profile": None,
            "graded": None,
        }
    digest = text_digest(url)
    row = graded.get(digest)
    return {
        "location_name": entry.get("location_name"),
        "mrf_url": public_url(url),
        "mrf_url_sha256": digest,
        "candidate_profile": candidate_profile(url),
        "graded": dict(row) if row is not None else None,
    }


def _origin_row(
    domain: str, record: Mapping[str, object], rows: Sequence[object]
) -> dict[str, object]:
    fetch = _mapping(record.get("fetch")) or {}
    return {
        "domain": domain,
        "url": record.get("url"),
        "retrieved_at": record.get("attempted_at"),
        "content_sha256": fetch.get("content_sha256"),
        "locations_listed": len(rows),
        "blocks_that_are_not_location_entries": {
            "count": len(_blocks(record)) - len(rows),
            "note": _NOT_A_LOCATION,
        },
        "locations": list(rows),
    }


def _not_retrieved_row(domain: str, record: Mapping[str, object]) -> dict[str, object]:
    fetch = _mapping(record.get("fetch")) or {}
    return {
        "domain": domain,
        "url": record.get("url"),
        "attempted_at": record.get("attempted_at"),
        "status": fetch.get("status"),
        "http_status": fetch.get("http_status"),
        "reason": fetch.get("error"),
    }


def _named_no_location_row(domain: str, record: Mapping[str, object]) -> dict[str, object]:
    """A body came back and no location could be read out of it, with the parser's own reason."""
    discovery = _mapping(record.get("discovery")) or {}
    fetch = _mapping(record.get("fetch")) or {}
    raw = discovery.get("problems")
    problems = (
        [str(item) for item in raw]
        if isinstance(raw, Sequence) and not isinstance(raw, str)
        else []
    )
    return {
        "domain": domain,
        "url": record.get("url"),
        "retrieved_at": record.get("attempted_at"),
        "http_status": fetch.get("http_status"),
        "content_sha256": fetch.get("content_sha256"),
        "blocks_that_are_not_location_entries": len(_blocks(record)),
        "problems": problems,
    }


def build_census(
    discovery_records: Iterable[Mapping[str, object]],
    assessment_records: Iterable[Mapping[str, object]],
    *,
    as_of: str,
    frame: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Count the files discovery has already located, beside the files the cohorts graded."""
    graded = graded_files(assessment_records)
    newest = newest_attempt_per_origin(discovery_records, as_of=as_of)

    origins: list[dict[str, object]] = []
    not_retrieved: list[dict[str, object]] = []
    named_no_location: list[dict[str, object]] = []
    for domain in sorted(newest):
        record = newest[domain]
        entries = _entries(record)
        if entries:
            origins.append(
                _origin_row(domain, record, [_location_row(entry, graded) for entry in entries])
            )
        elif _mapping(record.get("discovery")) is None:
            not_retrieved.append(_not_retrieved_row(domain, record))
        else:
            named_no_location.append(_named_no_location_row(domain, record))

    return {
        "census_version": CENSUS_VERSION,
        "as_of": as_of,
        "status": COUNTED if origins else NO_DISCOVERY_EVIDENCE,
        "reason": None if origins else NO_EVIDENCE_REASON,
        "basis": {
            "candidate_profile": PROFILE_BASIS,
            "not_graded_here": NOT_GRADED_BASIS,
            "origin_not_retrieved": NOT_RETRIEVED_BASIS,
            "origin_named_no_location": NO_LOCATION_BASIS,
            "graded_rows_no_located_file_matches": UNMATCHED_GRADED_BASIS,
            "frame": FRAME_BASIS,
        },
        "populations": _populations(origins, not_retrieved, named_no_location, graded),
        "frame": _frame_block(frame),
        "origins": origins,
        "origins_not_retrieved": not_retrieved,
        "origins_whose_response_named_no_location": named_no_location,
    }


def _populations(
    origins: Sequence[Mapping[str, object]],
    not_retrieved: Sequence[object],
    named_no_location: Sequence[object],
    graded: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    """Every count this census makes, with the ones that must not be merged kept apart."""
    rows = [
        cast(Mapping[str, object], row)
        for origin in origins
        for row in cast(Sequence[object], origin["locations"])
    ]
    with_url = [row for row in rows if row["mrf_url_sha256"] is not None]
    digests = {str(row["mrf_url_sha256"]) for row in with_url}
    by_profile: dict[str, set[str]] = {}
    for row in with_url:
        by_profile.setdefault(str(row["candidate_profile"]), set()).add(str(row["mrf_url_sha256"]))
    graded_digests = digests & set(graded)
    return {
        # Undetermined rather than counted when no document was read: see COUNTED above. Every
        # other count here is a count of something that was examined, so zero is the truth for
        # them; this one would be a statement about graded rows made without looking.
        "graded_rows_no_located_file_matches": (len(set(graded) - digests) if origins else None),
        "origins_with_a_readable_document": len(origins),
        "origins_not_retrieved": len(not_retrieved),
        "origins_whose_response_named_no_location": len(named_no_location),
        "locations_listed": len(rows),
        "locations_listed_without_an_mrf_url": len(rows) - len(with_url),
        "distinct_files_located": len(digests),
        "distinct_files_located_by_candidate_profile": {
            profile: len(members) for profile, members in sorted(by_profile.items())
        },
        "distinct_files_graded_in_a_committed_cohort": len(graded_digests),
        "distinct_files_located_and_not_graded_here": len(digests - graded_digests),
    }


def _frame_block(frame: Mapping[str, object] | None) -> dict[str, object]:
    """The frame's own counts, carried beside this census and never divided into it."""
    if frame is None:
        return {"basis": FRAME_BASIS, "frame_id": None, "eligible_count": None, "attempts": None}
    attempts = frame.get("attempts")
    return {
        "basis": FRAME_BASIS,
        "frame_id": frame.get("frame_id"),
        "eligible_count": frame.get("eligible_count"),
        "attempts": len(attempts) if isinstance(attempts, Sequence) else None,
    }


def human_report(document: Mapping[str, object]) -> str:
    """A deterministic plain-text report of one census."""
    counts = cast(Mapping[str, object], document["populations"])
    frame = cast(Mapping[str, object], document["frame"])
    lines = [
        f"census as of {document['as_of']}",
        f"{counts['origins_with_a_readable_document']} origin(s) with a readable cms-hpt.txt; "
        f"{counts['origins_not_retrieved']} produced no body "
        "(a fact about a request, never about a publisher); "
        f"{counts['origins_whose_response_named_no_location']} answered with something no "
        "location could be read out of",
        f"{counts['locations_listed']} location(s) listed, "
        f"{counts['locations_listed_without_an_mrf_url']} of them with no mrf-url",
        f"{counts['distinct_files_located']} distinct file(s) located",
    ]
    profiles = cast(Mapping[str, object], counts["distinct_files_located_by_candidate_profile"])
    for profile, count in profiles.items():
        lines.append(f"  {count} {profile}")
    lines.extend(
        [
            f"{counts['distinct_files_graded_in_a_committed_cohort']} of those graded in a "
            "committed cohort; "
            f"{counts['distinct_files_located_and_not_graded_here']} located and not graded here "
            "(this project's collection scope, not a defect)",
        ]
    )
    if document["status"] != COUNTED:
        # The failed origins below are still worth printing -- they are what was attempted -- so
        # this states the refusal in place of the count rather than truncating the report.
        lines.append(f"~ {document['reason']}")
    elif counts["graded_rows_no_located_file_matches"]:
        lines.append(
            f"~ {counts['graded_rows_no_located_file_matches']} graded row(s) match no located "
            "file: no retrieved cms-hpt.txt in this census declares that URL, which a hospital "
            "moving its file produces without anything being wrong"
        )
    if frame["eligible_count"] is not None:
        lines.extend(
            [
                "",
                f"frame {frame['frame_id']}: {frame['eligible_count']} eligible facility(ies), "
                f"{frame['attempts']} attempted.",
                "That count is NOT a denominator for the counts above: no public dataset records "
                "which website hosts a given facility's file, so the two do not divide.",
            ]
        )
    for entry in cast(Sequence[object], document["origins_not_retrieved"]):
        row = cast(Mapping[str, object], entry)
        lines.append(f"- {row['domain']}: {row['status']} ({row['reason']})")
    for entry in cast(Sequence[object], document["origins_whose_response_named_no_location"]):
        row = cast(Mapping[str, object], entry)
        problems = "; ".join(cast(Sequence[str], row["problems"])) or "no problem recorded"
        lines.append(f"~ {row['domain']}: a body was retrieved and named no location ({problems})")
    return "\n".join(lines)
