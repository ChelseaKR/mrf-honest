"""Append-only evidence registry for publisher discovery and retrieval attempts."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import cast

from mrf_honest.discover import Discovery, DiscoveryEntry, cms_hpt_url, parse_cms_hpt
from mrf_honest.fetch import Backoff, Clock, FetchOutcome, FetchPolicy, Opener, Sleeper, fetch_url

_REGISTRY_VERSION = 2
_READABLE_REGISTRY_VERSIONS = frozenset({1, _REGISTRY_VERSION})
_DISCOVERY_MAX_BYTES = 1 << 20


class AttemptKind(StrEnum):
    """The two network observations retained by the registry."""

    DISCOVERY = "discovery"
    FETCH = "fetch"


class RegistryError(Exception):
    """Raised when an existing registry cannot be interpreted or appended to."""


@dataclass(frozen=True)
class RegistryRecord:
    """One dated attempt, including failures and any parsed discovery evidence."""

    domain: str
    kind: AttemptKind
    url: str
    attempted_at: str
    fetch: FetchOutcome
    discovery: Discovery | None = None
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        parsed_ok = self.discovery is None or (
            self.discovery.usable and not self.discovery.all_problems
        )
        return self.fetch.ok and parsed_ok and not self.problems

    def to_dict(self) -> dict[str, object]:
        discovery: dict[str, object] | None = None
        if self.discovery is not None:
            discovery = {
                "domain": self.discovery.domain,
                "entries": [_entry_to_dict(entry) for entry in self.discovery.entries],
                "problems": list(self.discovery.document_problems),
            }
        return {
            "version": _REGISTRY_VERSION,
            "domain": self.domain,
            "kind": self.kind.value,
            "url": self.url,
            "attempted_at": self.attempted_at,
            "fetch": self.fetch.to_dict(),
            "discovery": discovery,
            "problems": list(self.problems),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RegistryRecord:
        version = _integer(data, "version")
        if version not in _READABLE_REGISTRY_VERSIONS:
            raise ValueError("unsupported registry record version")
        raw_fetch = data.get("fetch")
        if not isinstance(raw_fetch, dict):
            raise ValueError("'fetch' must be an object")
        fetch = FetchOutcome.from_dict(cast(dict[str, object], raw_fetch))
        raw_discovery = data.get("discovery")
        discovery = _discovery_from_object(raw_discovery, version=version)
        problems = _string_tuple(data.get("problems"), "problems")
        domain = _string(data, "domain")
        kind = AttemptKind(_string(data, "kind"))
        url = _string(data, "url")
        attempted_at = _string(data, "attempted_at")
        if (url, attempted_at) != (fetch.url, fetch.attempted_at):
            raise ValueError("registry and nested fetch identity do not match")
        if discovery is not None and discovery.domain != domain:
            raise ValueError("registry and nested discovery domains do not match")
        if kind is AttemptKind.FETCH and discovery is not None:
            raise ValueError("fetch registry records cannot contain discovery evidence")
        return cls(
            domain=domain,
            kind=kind,
            url=url,
            attempted_at=attempted_at,
            fetch=fetch,
            discovery=discovery,
            problems=problems,
        )


def _string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key!r} must be a string")
    return value


def _integer(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key!r} must be an integer")
    return value


def _nullable_string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{key!r} must be a string or null")


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name!r} must be an array of strings")
    return tuple(cast(list[str], value))


def _entry_to_dict(entry: DiscoveryEntry) -> dict[str, object]:
    return {
        "location_name": entry.location_name,
        "source_page_url": entry.source_page_url,
        "mrf_url": entry.mrf_url,
        "contact_name": entry.contact_name,
        "contact_email": entry.contact_email,
        "extra_fields": [list(pair) for pair in entry.extra_fields],
        "problems": list(entry.problems),
    }


def _extra_fields(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("'extra_fields' must be an array")
    extra: list[tuple[str, str]] = []
    for pair in value:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], str)
        ):
            raise ValueError("each extra field must be a string pair")
        extra.append((pair[0], pair[1]))
    return tuple(extra)


def _entry_from_object(value: object) -> DiscoveryEntry:
    if not isinstance(value, dict):
        raise ValueError("each discovery entry must be an object")
    data = cast(dict[str, object], value)
    return DiscoveryEntry(
        location_name=_nullable_string(data, "location_name"),
        source_page_url=_nullable_string(data, "source_page_url"),
        mrf_url=_nullable_string(data, "mrf_url"),
        contact_name=_nullable_string(data, "contact_name"),
        contact_email=_nullable_string(data, "contact_email"),
        extra_fields=_extra_fields(data.get("extra_fields")),
        problems=_string_tuple(data.get("problems"), "discovery entry problems"),
    )


def _legacy_discovery(data: Mapping[str, object]) -> Discovery:
    entry = DiscoveryEntry(
        location_name=_nullable_string(data, "location_name"),
        source_page_url=_nullable_string(data, "source_page_url"),
        mrf_url=_nullable_string(data, "mrf_url"),
        extra_fields=_extra_fields(data.get("extra_fields")),
        problems=_string_tuple(data.get("problems"), "discovery problems"),
    )
    return Discovery(domain=_string(data, "domain"), entries=(entry,))


def _discovery_from_object(value: object, *, version: int) -> Discovery | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("'discovery' must be an object or null")
    data = cast(dict[str, object], value)
    if version == 1:
        return _legacy_discovery(data)
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("'entries' must be an array")
    return Discovery(
        domain=_string(data, "domain"),
        entries=tuple(_entry_from_object(entry) for entry in raw_entries),
        document_problems=_string_tuple(data.get("problems"), "discovery problems"),
    )


class Registry:
    """A single-writer append-only JSONL log; iteration verifies persisted records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: RegistryRecord) -> None:
        line = json.dumps(
            record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise RegistryError(f"could not append registry record: {exc}") from exc

    def records(self) -> tuple[RegistryRecord, ...]:
        return tuple(self)

    def __iter__(self) -> Iterator[RegistryRecord]:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        raw: object = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("record must be a JSON object")
                        record = RegistryRecord.from_dict(cast(dict[str, object], raw))
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise RegistryError(
                            f"invalid registry record at line {line_number}: {exc}"
                        ) from exc
                    yield record
        except (OSError, UnicodeError) as exc:
            raise RegistryError(f"could not read registry: {exc}") from exc


def fetch_and_record(
    domain: str,
    url: str,
    *,
    registry: Registry,
    cache_dir: str | Path,
    policy: FetchPolicy,
    opener: Opener | None = None,
    sleep: Sleeper = time.sleep,
    backoff: Backoff | None = None,
    clock: Clock | None = None,
) -> RegistryRecord:
    """Fetch an MRF and durably record the success or named failure."""
    outcome = fetch_url(
        url,
        cache_dir,
        policy=policy,
        opener=opener,
        sleep=sleep,
        backoff=backoff,
        clock=clock,
    )
    record = RegistryRecord(
        domain=domain,
        kind=AttemptKind.FETCH,
        url=url,
        attempted_at=outcome.attempted_at,
        fetch=outcome,
    )
    registry.append(record)
    return record


def discover_domain(
    domain: str,
    *,
    registry: Registry,
    cache_dir: str | Path,
    policy: FetchPolicy,
    opener: Opener | None = None,
    sleep: Sleeper = time.sleep,
    backoff: Backoff | None = None,
    clock: Clock | None = None,
) -> RegistryRecord:
    """Fetch and parse a domain's conventional ``cms-hpt.txt``, then record all evidence."""
    url = cms_hpt_url(domain)
    discovery_policy = replace(policy, max_bytes=min(policy.max_bytes, _DISCOVERY_MAX_BYTES))
    outcome = fetch_url(
        url,
        cache_dir,
        policy=discovery_policy,
        opener=opener,
        sleep=sleep,
        backoff=backoff,
        clock=clock,
    )
    discovery: Discovery | None = None
    problems: tuple[str, ...] = ()
    if outcome.ok and outcome.path is not None:
        try:
            body = outcome.path.read_text(encoding="utf-8-sig")
            discovery = parse_cms_hpt(body, domain=domain)
        except UnicodeDecodeError as exc:
            problems = (f"cms-hpt.txt is not valid UTF-8: {exc}",)
        except OSError as exc:
            problems = (f"could not read cached cms-hpt.txt: {exc}",)
    record = RegistryRecord(
        domain=domain,
        kind=AttemptKind.DISCOVERY,
        url=url,
        attempted_at=outcome.attempted_at,
        fetch=outcome,
        discovery=discovery,
        problems=problems,
    )
    registry.append(record)
    return record
