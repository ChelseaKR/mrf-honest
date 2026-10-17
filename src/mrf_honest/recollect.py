"""Scheduled re-collection: re-run discovery and assessment for a committed watchlist.

``mrf-honest scorecard`` grades one URL an operator names. A cohort is a run of that command over
a chosen set of targets, attested by hand. This module is the same retrieval and the same grading
driven from a committed list of publishers instead of a person's shell history, so that the
retrieval can be repeated on a schedule and two dated results can be related with
``mrf-honest diff``. It adds no grading rule and no retrieval rule:

* every file is fetched through :func:`mrf_honest.scorecard.assess_hospital_url`, so the
  ``robots.txt`` gate, the per-host interval, ``Retry-After``, conditional revalidation and the
  size ceiling are the ones every cohort was collected under;
* one :class:`~mrf_honest.politeness.Politeness` object spans the whole run, so the per-host
  interval holds across publishers and not only within one;
* the grade letters come from :func:`mrf_honest.cohort.grade_assessment`, unchanged.

**A failure is "no evidence", never a pass.** A publisher whose ``cms-hpt.txt`` could not be
retrieved, a location whose file could not be retrieved, and a URL whose format this run will not
guess are each recorded with the reason and with no letter. The per-file ``grade`` field of the
result document is ``null`` unless a verified body was read, so a reader scanning the document
cannot mistake an unreachable server for a conforming file, and a downstream alert cannot mistake
it for a regression either (see :mod:`mrf_honest.alert`).

**Dated, idempotent, versioned.** A run writes ``<date>.recollection.json`` last and atomically;
that file is the completion marker. Run again on the same UTC date with the same watchlist and
nothing is fetched. A run that was interrupted leaves its assessments file behind, and the next
run skips the files already assessed that day. A different watchlist on a date that already
completed is refused rather than merged.

**Bounded by default.** At most :data:`DEFAULT_MAX_FILES_PER_PUBLISHER` distinct files per
publisher are fetched in one run. A larger system's remaining files are listed as not attempted,
with the reason, so a cap reads as a cap and not as an absence. The measured cost of a full
re-collection is in ``docs/findings/what-a-re-collection-actually-cost-2026-09-12.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

from mrf_honest.cohort import NOT_GRADED, CohortError, build_comparison, grade_assessment
from mrf_honest.discover import DiscoveryEntry
from mrf_honest.fetch import FetchPolicy
from mrf_honest.politeness import Politeness
from mrf_honest.registry import Registry, discover_domain
from mrf_honest.scorecard import (
    CSV_PROFILE,
    JSON_PROFILE,
    AssessmentProfile,
    AssessmentRegistry,
    AssessmentRegistryError,
    AssessmentSubject,
    PublisherType,
    URLProvenance,
    assess_hospital_url,
    public_url,
    text_digest,
)
from mrf_honest.types import Backoff, Clock, Opener, PublisherRef, Sleeper

#: Bumped whenever the shape of the result document changes.
RECOLLECTION_SCHEMA_VERSION = 1

#: Bumped whenever the shape of the watchlist changes.
WATCHLIST_SCHEMA_VERSION = 1

#: Distinct files fetched per publisher in one run unless the operator says otherwise.
DEFAULT_MAX_FILES_PER_PUBLISHER = 5

RECOLLECTION_NOTICE = (
    "A re-collection records what one identified, polite retrieval observed on one UTC date. "
    "A publisher or file with outcome no_evidence carries no letter: the retrieval did not "
    "produce a verified file, which is a statement about the retrieval and says nothing about "
    "the file. No evidence is not a pass."
)

OUTCOME_ASSESSED = "assessed"
OUTCOME_NO_EVIDENCE = "no_evidence"
OUTCOME_NOT_ATTEMPTED = "not_attempted"

#: Result-file name suffix per profile, matching the committed cohorts' naming (the JSON cohort
#: has no suffix, the CSV cohort is ``-csv``) so ``mrf-honest compare`` and ``diff`` take them
#: without special cases.
_PROFILE_SUFFIX = {JSON_PROFILE.name: "", CSV_PROFILE.name: "-csv"}

_PROFILE_BY_EXTENSION: Mapping[str, AssessmentProfile] = {
    ".json": JSON_PROFILE,
    ".csv": CSV_PROFILE,
}

_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_SLUG_LIMIT = 120


class RecollectionError(Exception):
    """Raised when a watchlist or a prior result cannot be used as given."""


@dataclass(frozen=True)
class WatchedPublisher:
    """One publisher whose ``https://<domain>/cms-hpt.txt`` is re-collected."""

    publisher_id: str
    name: str
    domain: str


@dataclass(frozen=True)
class Watchlist:
    """The committed list of publishers, with the digest of the bytes it was read from."""

    publishers: tuple[WatchedPublisher, ...]
    sha256: str


def load_watchlist(path: Path) -> Watchlist:
    """Read and validate a watchlist; anything unusable is refused with the reason."""
    try:
        raw = path.read_bytes()
        document: object = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise RecollectionError(f"could not read watchlist {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RecollectionError("the watchlist must be a JSON object")
    body = cast(dict[str, object], document)
    if body.get("schema_version") != WATCHLIST_SCHEMA_VERSION:
        raise RecollectionError(
            f"the watchlist declares schema_version {body.get('schema_version')!r}; "
            f"this build reads {WATCHLIST_SCHEMA_VERSION}"
        )
    listed = body.get("publishers")
    if not isinstance(listed, list) or not listed:
        raise RecollectionError("the watchlist must list at least one publisher")
    publishers: list[WatchedPublisher] = []
    seen_ids: set[str] = set()
    seen_domains: set[str] = set()
    for index, item in enumerate(listed):
        publisher = _watched(item, index)
        if publisher.publisher_id in seen_ids or publisher.domain in seen_domains:
            raise RecollectionError(
                f"publishers[{index}] repeats an identifier or domain already listed"
            )
        seen_ids.add(publisher.publisher_id)
        seen_domains.add(publisher.domain)
        publishers.append(publisher)
    return Watchlist(
        publishers=tuple(sorted(publishers, key=lambda item: item.publisher_id)),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _watched(item: object, index: int) -> WatchedPublisher:
    where = f"publishers[{index}]"
    if not isinstance(item, dict):
        raise RecollectionError(f"{where} must be an object")
    entry = cast(dict[str, object], item)
    identifier, name, domain = (entry.get(key) for key in ("publisher_id", "name", "domain"))
    if not isinstance(identifier, str) or not _ID_PATTERN.match(identifier):
        raise RecollectionError(f"{where}.publisher_id must be a lowercase slug")
    if not isinstance(name, str) or not name.strip() or "\n" in name:
        raise RecollectionError(f"{where}.name must be a single non-empty line")
    if not isinstance(domain, str) or not _DOMAIN_PATTERN.match(domain):
        raise RecollectionError(
            f"{where}.domain must be a bare lowercase host name with no scheme or path"
        )
    return WatchedPublisher(publisher_id=identifier, name=name.strip(), domain=domain)


@dataclass(frozen=True)
class PlannedFile:
    """One distinct file URL a publisher's ``cms-hpt.txt`` lists, and the locations naming it."""

    location_id: str
    location_names: tuple[str, ...]
    url: str
    profile: AssessmentProfile


def slugify(text: str) -> str:
    """Lowercase letters, digits and single hyphens, bounded in length."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:_SLUG_LIMIT].strip("-")


def profile_for_url(url: str) -> AssessmentProfile | None:
    """The profile a URL's own extension names, or ``None``. This run never guesses a format."""
    path = unquote(urlsplit(url).path).lower()
    for extension, profile in _PROFILE_BY_EXTENSION.items():
        if path.endswith(extension):
            return profile
    return None


def _location_slug(names: Sequence[str], url: str) -> str:
    for name in names:
        slug = slugify(name)
        if slug:
            return slug
    stem = unquote(urlsplit(url).path).rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return slugify(stem) or "file"


def plan_files(
    entries: Sequence[DiscoveryEntry], *, max_files: int
) -> tuple[list[PlannedFile], list[dict[str, object]]]:
    """Turn a discovery document into the files to fetch and the entries deliberately skipped.

    Files are de-duplicated by exact URL, because several locations commonly name one file and
    fetching it once per location would multiply the load on one server for no new evidence.
    Order is the document's own, so the cap keeps the same files from one run to the next.
    """
    by_url: dict[str, list[str]] = {}
    skipped: list[dict[str, object]] = []
    for entry in entries:
        name = (entry.location_name or "").strip()
        if not entry.usable or entry.mrf_url is None:
            skipped.append(
                {
                    "locations": [name] if name else [],
                    "url": None,
                    "code": "no_usable_mrf_url",
                    "reason": "; ".join(entry.problems) or "the entry has no https mrf-url",
                }
            )
            continue
        names = by_url.setdefault(entry.mrf_url, [])
        if name and name not in names:
            names.append(name)

    planned: list[PlannedFile] = []
    taken: set[str] = set()
    for url, names in by_url.items():
        profile = profile_for_url(url)
        if profile is None:
            skipped.append(
                {
                    "locations": names,
                    "url": public_url(url),
                    "code": "format_not_named_by_url",
                    "reason": (
                        "the URL does not end in .json or .csv, and this run does not guess a "
                        "format; classify it with `mrf-honest probe` first"
                    ),
                }
            )
            continue
        if len(planned) >= max_files:
            skipped.append(
                {
                    "locations": names,
                    "url": public_url(url),
                    "code": "over_per_publisher_cap",
                    "reason": f"this run fetches at most {max_files} files per publisher",
                }
            )
            continue
        base = _location_slug(names, url)
        location_id, counter = base, 2
        while location_id in taken:
            location_id = f"{base}-{counter}"
            counter += 1
        taken.add(location_id)
        planned.append(PlannedFile(location_id, tuple(names), url, profile))
    return planned, skipped


@dataclass(frozen=True)
class RecollectionResult:
    """What a run produced, or found already produced."""

    document: dict[str, object]
    path: Path
    already_complete: bool
    comparison_paths: tuple[Path, ...]

    @property
    def infrastructure_failures(self) -> int:
        summary = cast(Mapping[str, object], self.document["summary"])
        return int(cast(int, summary["infrastructure_failures"]))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _discovery_record(
    publisher: WatchedPublisher,
    *,
    registry: Registry,
    cache_dir: Path,
    policy: FetchPolicy,
    politeness: Politeness,
    opener: Opener | None,
    sleep: Sleeper,
    backoff: Backoff | None,
    clock: Clock | None,
) -> tuple[dict[str, object], list[DiscoveryEntry]]:
    record = discover_domain(
        publisher.domain,
        registry=registry,
        cache_dir=cache_dir,
        policy=policy,
        politeness=politeness,
        opener=opener,
        sleep=sleep,
        backoff=backoff,
        clock=clock,
    )
    fetch = record.fetch
    block: dict[str, object] = {
        "url": record.url,
        "attempted_at": fetch.attempted_at,
        "fetch_status": fetch.status.value,
        "http_status": fetch.http_status,
        "document_sha256": fetch.content_sha256,
        "locations_listed": 0,
        "outcome": OUTCOME_NO_EVIDENCE,
        "reason": None,
        "document_problems": [],
    }
    discovery = record.discovery
    if not fetch.ok:
        detail = f", HTTP {fetch.http_status}" if fetch.http_status is not None else ""
        block["reason"] = f"cms-hpt.txt could not be retrieved ({fetch.status.value}{detail})" + (
            f": {fetch.error}" if fetch.error else ""
        )
        return block, []
    if discovery is None:
        block["reason"] = "; ".join(record.problems) or "cms-hpt.txt could not be read"
        return block, []
    block["locations_listed"] = len(discovery.entries)
    block["document_problems"] = list(discovery.document_problems)
    if not discovery.usable:
        block["reason"] = "cms-hpt.txt was retrieved but lists no usable https mrf-url"
        return block, []
    block["outcome"] = "discovered"
    return block, list(discovery.entries)


def _file_record(planned: PlannedFile, assessment: Mapping[str, object]) -> dict[str, object]:
    coverage = cast(Mapping[str, object], assessment["coverage"])
    retrieval = cast(Mapping[str, object], assessment["retrieval"])
    subject = cast(Mapping[str, object], assessment["subject"])
    verified = coverage.get("verified_body_available") is True
    grade = grade_assessment(assessment)
    record: dict[str, object] = {
        "location_id": planned.location_id,
        "locations": list(planned.location_names),
        "url": subject["requested_url"],
        "url_sha256": subject["requested_url_sha256"],
        "profile": planned.profile.name,
        "attempted_at": retrieval.get("attempted_at"),
        "fetch_status": retrieval.get("status"),
        "http_status": retrieval.get("http_status"),
        "content_sha256": retrieval.get("content_sha256"),
        "wire_size_bytes": retrieval.get("wire_size_bytes"),
        "assessment_id": assessment.get("assessment_id"),
        "outcome": OUTCOME_ASSESSED if verified else OUTCOME_NO_EVIDENCE,
        # A letter is carried only when a verified body was read. A retrieval failure keeps the
        # cohort policy's own letter inside the comparison document; here it is no evidence.
        "grade": grade.grade if verified and grade.grade != NOT_GRADED else None,
        "reason": None if verified else grade.reason,
        "infrastructure_failure": False,
    }
    return record


def _failed_file_record(planned: PlannedFile, reason: str) -> dict[str, object]:
    return {
        "location_id": planned.location_id,
        "locations": list(planned.location_names),
        "url": public_url(planned.url),
        "url_sha256": text_digest(planned.url),
        "profile": planned.profile.name,
        "attempted_at": None,
        "fetch_status": None,
        "http_status": None,
        "content_sha256": None,
        "wire_size_bytes": None,
        "assessment_id": None,
        "outcome": OUTCOME_NO_EVIDENCE,
        "grade": None,
        "reason": reason,
        "infrastructure_failure": True,
    }


def _skipped_file_record(skip: Mapping[str, object]) -> dict[str, object]:
    return {
        "location_id": None,
        "locations": skip["locations"],
        "url": skip["url"],
        "url_sha256": None,
        "profile": None,
        "attempted_at": None,
        "fetch_status": None,
        "http_status": None,
        "content_sha256": None,
        "wire_size_bytes": None,
        "assessment_id": None,
        "outcome": OUTCOME_NOT_ATTEMPTED,
        "grade": None,
        "code": skip["code"],
        "reason": skip["reason"],
        "infrastructure_failure": False,
    }


def _manifest(
    profile: AssessmentProfile,
    *,
    collected_on: str,
    user_agent: str,
    watchlist: Watchlist,
    max_files_per_publisher: int,
) -> dict[str, object]:
    kind = "csv" if profile is CSV_PROFILE else "json"
    return {
        "policy": (
            "A re-collection manifest attests the collection context that assessment rows "
            "cannot carry by themselves. It records no sampling frame, so the statistics block "
            "of its comparison refuses rather than computing a share of a population nobody drew."
        ),
        "cohort_id": f"recollection-{kind}-v3-{collected_on}",
        "collection": {
            "operator_controlled_single_run": True,
            "utc_date": collected_on,
            "user_agent": user_agent,
            "description": (
                "One serial run of `mrf-honest recollect` over the committed watchlist, under the "
                "default retrieval policy: identified User-Agent with a contact, HTTPS only, "
                "robots.txt fetched and obeyed in code before every request, one per-host minimum "
                "interval held across the whole run, conditional revalidation against the "
                "verified cache, and retry backoff. Every URL is the one a publisher's own "
                "cms-hpt.txt listed on the day of the run; none was replayed from an earlier run. "
                f"At most {max_files_per_publisher} distinct files are fetched per publisher."
            ),
            "population": {
                "kind": "committed_watchlist",
                "watchlist_sha256": watchlist.sha256,
                "publishers": len(watchlist.publishers),
            },
        },
        "discovery": {
            "method": (
                "`mrf-honest discover` semantics: https://<domain>/cms-hpt.txt retrieved through "
                "the politeness gate on the date of the run."
            ),
            "date": collected_on,
            "origins_checked": len(watchlist.publishers),
            "domains": [
                {"domain": item.domain, "publisher_id": item.publisher_id}
                for item in watchlist.publishers
            ],
        },
        "exclusions": [],
    }


def _build_comparisons(
    registries: Mapping[str, AssessmentRegistry],
    *,
    collected_on: str,
    started_at: str,
    user_agent: str,
    watchlist: Watchlist,
    max_files_per_publisher: int,
    out_dir: Path,
) -> tuple[dict[str, object], list[Path]]:
    states: dict[str, object] = {}
    written: list[Path] = []
    for profile in (JSON_PROFILE, CSV_PROFILE):
        registry = registries.get(profile.name)
        rows = registry.records() if registry is not None else ()
        if not rows:
            continue
        prefix = f"{collected_on}{_PROFILE_SUFFIX[profile.name]}"
        manifest = _manifest(
            profile,
            collected_on=collected_on,
            user_agent=user_agent,
            watchlist=watchlist,
            max_files_per_publisher=max_files_per_publisher,
        )
        try:
            comparison = build_comparison(rows, manifest, generated_at=started_at)
        except (CohortError, ValueError) as exc:
            states[profile.name] = {"status": "not_built", "reason": str(exc)}
            continue
        manifest_path = out_dir / f"{prefix}.json"
        comparison_path = out_dir / f"{prefix}.comparison.json"
        _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        _atomic_write(
            comparison_path,
            json.dumps(comparison, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n",
        )
        written.append(comparison_path)
        states[profile.name] = {"status": "built", "comparison": comparison_path.name}
    return states, written


def _summary(publishers: Sequence[Mapping[str, object]]) -> dict[str, object]:
    files = [
        cast(Mapping[str, object], item)
        for publisher in publishers
        for item in cast(Sequence[object], publisher["files"])
    ]

    def count(outcome: str) -> int:
        return sum(1 for item in files if item["outcome"] == outcome)

    discovered = sum(
        1
        for publisher in publishers
        if cast(Mapping[str, object], publisher["discovery"])["outcome"] == "discovered"
    )
    return {
        "publishers": len(publishers),
        "publishers_discovered": discovered,
        "publishers_no_evidence": len(publishers) - discovered,
        "files_assessed": count(OUTCOME_ASSESSED),
        "files_no_evidence": count(OUTCOME_NO_EVIDENCE),
        "files_not_attempted": count(OUTCOME_NOT_ATTEMPTED),
        "infrastructure_failures": sum(1 for item in files if item["infrastructure_failure"]),
    }


def _existing_result(path: Path, watchlist: Watchlist) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecollectionError(f"{path} exists and cannot be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RecollectionError(f"{path} exists and is not a result document")
    document = cast(dict[str, object], loaded)
    if document.get("schema_version") != RECOLLECTION_SCHEMA_VERSION:
        raise RecollectionError(
            f"{path} declares schema_version {document.get('schema_version')!r}; "
            f"this build reads {RECOLLECTION_SCHEMA_VERSION}"
        )
    held = cast(Mapping[str, object], document.get("watchlist") or {})
    if held.get("sha256") != watchlist.sha256:
        raise RecollectionError(
            f"{path} was completed from a different watchlist; refusing to merge two lists "
            "into one dated result. Remove it deliberately, or wait for the next UTC date."
        )
    return document


def recollect(
    watchlist: Watchlist,
    *,
    out_dir: Path,
    cache_dir: Path,
    policy: FetchPolicy,
    politeness: Politeness,
    max_files_per_publisher: int = DEFAULT_MAX_FILES_PER_PUBLISHER,
    opener: Opener | None = None,
    sleep: Sleeper = time.sleep,
    backoff: Backoff | None = None,
    clock: Clock | None = None,
) -> RecollectionResult:
    """Re-collect every watched publisher once for today's UTC date, or report it done."""
    if max_files_per_publisher < 1:
        raise RecollectionError("max_files_per_publisher must be at least 1")
    now = (clock or (lambda: datetime.now(UTC)))()
    collected_on = now.astimezone(UTC).date().isoformat()
    started_at = _timestamp(now)
    result_path = out_dir / f"{collected_on}.recollection.json"

    prior = _existing_result(result_path, watchlist)
    if prior is not None:
        paths = tuple(
            out_dir / str(cast(Mapping[str, object], state)["comparison"])
            for state in cast(Mapping[str, object], prior["comparisons"]).values()
            if cast(Mapping[str, object], state).get("status") == "built"
        )
        return RecollectionResult(prior, result_path, already_complete=True, comparison_paths=paths)

    discovery_log = Registry(out_dir / f"{collected_on}.discovery.jsonl")
    registries = {
        profile.name: AssessmentRegistry(
            out_dir / f"{collected_on}{_PROFILE_SUFFIX[profile.name]}.assessments.jsonl"
        )
        for profile in (JSON_PROFILE, CSV_PROFILE)
    }
    done: dict[tuple[str, str], dict[str, object]] = {}
    for name, registry in registries.items():
        for row in registry.records():
            digest = str(cast(Mapping[str, object], row["subject"])["requested_url_sha256"])
            done[(name, digest)] = row

    publishers_out: list[dict[str, object]] = []
    for publisher in watchlist.publishers:
        block, entries = _discovery_record(
            publisher,
            registry=discovery_log,
            cache_dir=cache_dir,
            policy=policy,
            politeness=politeness,
            opener=opener,
            sleep=sleep,
            backoff=backoff,
            clock=clock,
        )
        planned, skipped = plan_files(entries, max_files=max_files_per_publisher)
        files: list[dict[str, object]] = []
        for item in planned:
            files.append(
                _assess_one(
                    publisher,
                    item,
                    registries[item.profile.name],
                    done,
                    cache_dir=cache_dir,
                    policy=policy,
                    politeness=politeness,
                    opener=opener,
                    sleep=sleep,
                    backoff=backoff,
                    clock=clock,
                )
            )
        files.extend(_skipped_file_record(skip) for skip in skipped)
        publishers_out.append(
            {
                "publisher_id": publisher.publisher_id,
                "name": publisher.name,
                "domain": publisher.domain,
                "discovery": block,
                "files": files,
            }
        )

    comparisons, comparison_paths = _build_comparisons(
        registries,
        collected_on=collected_on,
        started_at=started_at,
        user_agent=policy.user_agent,
        watchlist=watchlist,
        max_files_per_publisher=max_files_per_publisher,
        out_dir=out_dir,
    )
    document: dict[str, object] = {
        "schema_version": RECOLLECTION_SCHEMA_VERSION,
        "notice": RECOLLECTION_NOTICE,
        "collected_on": collected_on,
        "started_at": started_at,
        "watchlist": {"sha256": watchlist.sha256, "publishers": len(watchlist.publishers)},
        "limits": {
            "max_files_per_publisher": max_files_per_publisher,
            "max_bytes": policy.max_bytes,
            "min_interval_seconds": politeness.min_interval_seconds,
        },
        "publishers": publishers_out,
        "comparisons": comparisons,
        "summary": _summary(publishers_out),
    }
    # Written last and atomically: its presence is what says this date is complete.
    _atomic_write(result_path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return RecollectionResult(
        document, result_path, already_complete=False, comparison_paths=tuple(comparison_paths)
    )


def _assess_one(
    publisher: WatchedPublisher,
    planned: PlannedFile,
    registry: AssessmentRegistry,
    done: Mapping[tuple[str, str], dict[str, object]],
    *,
    cache_dir: Path,
    policy: FetchPolicy,
    politeness: Politeness,
    opener: Opener | None,
    sleep: Sleeper,
    backoff: Backoff | None,
    clock: Clock | None,
) -> dict[str, object]:
    earlier = done.get((planned.profile.name, text_digest(planned.url)))
    if earlier is not None:
        return _file_record(planned, earlier)
    try:
        subject = AssessmentSubject(
            publisher=PublisherRef(
                identifier=publisher.publisher_id, name=publisher.name, source_url=planned.url
            ),
            publisher_type=PublisherType.HOSPITAL,
            location_id=planned.location_id,
            requested_url=planned.url,
            url_provenance=URLProvenance.CMS_HPT,
        )
        assessment = assess_hospital_url(
            subject,
            cache_dir,
            policy=policy,
            politeness=politeness,
            registry=registry,
            opener=opener,
            sleep=sleep,
            backoff=backoff,
            clock=clock,
            profile=planned.profile,
        )
    except (ValueError, AssessmentRegistryError, OSError) as exc:
        return _failed_file_record(planned, f"the assessment could not be completed: {exc}")
    record = _file_record(planned, assessment.to_dict())
    if not assessment.operationally_complete:
        record["infrastructure_failure"] = True
    return record
