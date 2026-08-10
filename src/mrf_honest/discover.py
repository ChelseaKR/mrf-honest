"""Discover hospital price-transparency files via the CMS-required `cms-hpt.txt` convention.

CMS requires the public website selected to host a hospital's machine-readable file to publish a
``cms-hpt.txt`` at its root. That convention is what makes this project's registry buildable
automatically, which is the opposite of the payer FHIR situation where base URLs had to be curated
one developer portal at a time.

Parsing is deliberately forgiving about formatting and strict about substance: a file that says
something unusable produces a finding, never an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

BOM = "﻿"

# CMS specifies five fields for every location entry. Real files in the wild also carry extras;
# those are preserved rather than dropped, because an unexpected field is publisher evidence.
_KNOWN_FIELDS = (
    "location-name",
    "source-page-url",
    "mrf-url",
    "contact-name",
    "contact-email",
)

# {EIN}_{hospital-name}_standardcharges.{json|csv}, per the current CMS naming convention. The EIN
# identifies the filer, which is a second structured signal beyond the URL itself.
_FILENAME_RE = re.compile(
    r"^(?P<ein>\d{9})_(?P<name>.+?)_standardcharges\.(?P<ext>json|csv)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscoveryEntry:
    """One location record claimed by a ``cms-hpt.txt`` document."""

    location_name: str | None = None
    source_page_url: str | None = None
    mrf_url: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    extra_fields: tuple[tuple[str, str], ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """True when an HTTPS MRF URL was found for this entry."""
        return bool(self.mrf_url)


@dataclass(frozen=True)
class Discovery:
    """What a domain's ``cms-hpt.txt`` claims, including every location entry."""

    domain: str
    entries: tuple[DiscoveryEntry, ...] = ()
    document_problems: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """True when at least one location entry has an HTTPS MRF URL."""
        return any(entry.usable for entry in self.entries)

    @property
    def problems(self) -> tuple[str, ...]:
        """All document- and entry-level problems, retained for single-record callers."""
        return self.document_problems + tuple(
            problem for entry in self.entries for problem in entry.problems
        )

    @property
    def all_problems(self) -> tuple[str, ...]:
        """Alias that makes aggregation explicit to multi-entry callers."""
        return self.problems

    @property
    def location_name(self) -> str | None:
        """The first entry's location name, for compatibility with the original API."""
        return self.entries[0].location_name if self.entries else None

    @property
    def source_page_url(self) -> str | None:
        """The first entry's source-page URL, for compatibility with the original API."""
        return self.entries[0].source_page_url if self.entries else None

    @property
    def mrf_url(self) -> str | None:
        """The first entry's MRF URL, for compatibility with the original API."""
        return self.entries[0].mrf_url if self.entries else None

    @property
    def contact_name(self) -> str | None:
        """The first entry's contact name, for compatibility with the original API."""
        return self.entries[0].contact_name if self.entries else None

    @property
    def contact_email(self) -> str | None:
        """The first entry's contact email, for compatibility with the original API."""
        return self.entries[0].contact_email if self.entries else None

    @property
    def extra_fields(self) -> tuple[tuple[str, str], ...]:
        """The first entry's extra fields, for compatibility with the original API."""
        return self.entries[0].extra_fields if self.entries else ()


@dataclass(frozen=True)
class FilenameFacts:
    ein: str | None
    hospital_name: str | None
    extension: str | None
    follows_convention: bool


@dataclass
class _EntryParts:
    fields: dict[str, str]
    extra: list[tuple[str, str]]
    problems: list[str]


def _new_entry_parts() -> _EntryParts:
    return _EntryParts(fields={}, extra=[], problems=[])


def _has_entry_content(parts: _EntryParts) -> bool:
    return bool(parts.fields or parts.extra or parts.problems)


def _field_from_line(line: str, problems: list[str]) -> tuple[str, str] | None:
    if ":" not in line:
        problems.append(f"unparseable line: {line[:60]!r}")
        return None
    key, _, value = line.partition(":")
    return key.strip().lower().replace("_", "-"), value.strip()


def _add_field(parts: _EntryParts, key: str, value: str) -> None:
    if not value:
        parts.problems.append(f"field {key!r} present but empty")
    elif key not in _KNOWN_FIELDS:
        parts.extra.append((key, value))
    elif key in parts.fields:
        # First declaration wins inside one location record. Repeated records are handled by
        # ``_split_entries`` rather than being mistaken for duplicate fields.
        parts.problems.append(f"field {key!r} declared more than once")
    else:
        parts.fields[key] = value


def _split_entries(text: str) -> list[_EntryParts]:
    """Split a CMS TXT body into ordered location records without losing malformed input."""
    entries: list[_EntryParts] = []
    current = _new_entry_parts()

    def finish_entry() -> None:
        nonlocal current
        if _has_entry_content(current):
            entries.append(current)
            current = _new_entry_parts()

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip(BOM)
        if not line:
            finish_entry()
            continue
        if line.startswith("#"):
            continue
        field = _field_from_line(line, current.problems)
        if field is None:
            continue
        key, value = field

        # Accept adjacent complete blocks even when a publisher omitted the generator's blank
        # separator or reordered the next block. A repeat inside an incomplete block remains a
        # duplicate-field problem rather than silently creating a new entry.
        if key in current.fields and all(field in current.fields for field in _KNOWN_FIELDS):
            finish_entry()
        _add_field(current, key, value)

    finish_entry()
    return entries


def _entry_from_parts(parts: _EntryParts) -> DiscoveryEntry:
    problems = parts.problems.copy()
    mrf_url = _check_mrf_url(parts.fields.get("mrf-url"), problems)
    for field_name in _KNOWN_FIELDS:
        if field_name != "mrf-url" and field_name not in parts.fields:
            problems.append(f"no {field_name} field")
    _check_source_page_url(parts.fields.get("source-page-url"), problems)
    _check_contact_email(parts.fields.get("contact-email"), problems)
    return DiscoveryEntry(
        location_name=parts.fields.get("location-name"),
        source_page_url=parts.fields.get("source-page-url"),
        mrf_url=mrf_url,
        contact_name=parts.fields.get("contact-name"),
        contact_email=parts.fields.get("contact-email"),
        extra_fields=tuple(parts.extra),
        problems=tuple(problems),
    )


def _check_source_page_url(raw: str | None, problems: list[str]) -> None:
    if raw is None:
        return
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        problems.append(f"source-page-url is not a resolvable URL: {raw[:80]!r}")
        return
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        problems.append(f"source-page-url is not a resolvable URL: {raw[:80]!r}")
        return
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or port == 0
        or authority.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
    ):
        problems.append(f"source-page-url is not a resolvable URL: {raw[:80]!r}")


def _check_contact_email(raw: str | None, problems: list[str]) -> None:
    if raw is None:
        return
    if raw.count("@") != 1 or any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in raw
    ):
        problems.append(f"contact-email is not a valid email address: {raw[:80]!r}")
        return
    local, domain = raw.rsplit("@", 1)
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        problems.append(f"contact-email is not a valid email address: {raw[:80]!r}")


def _check_mrf_url(raw: str | None, problems: list[str]) -> str | None:
    if raw is None:
        problems.append("no mrf-url field")
        return None
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        problems.append(f"mrf-url contains whitespace or control characters: {raw[:80]!r}")
        return None
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        # urllib deliberately does only light URL validation and raises for a few malformed
        # authorities (notably unmatched IPv6 brackets and invalid ports). Those inputs are
        # findings, not a reason for this never-raises parser to fail.
        problems.append(f"mrf-url is not a resolvable URL: {raw[:80]!r}")
        return None
    if parsed.scheme.lower() != "https":
        # Refused rather than upgraded: a price file fetched over plaintext is not verifiable.
        problems.append(f"mrf-url is not https: {raw[:80]!r}")
        return None
    if parsed.username is not None or parsed.password is not None:
        problems.append(f"mrf-url contains credentials: {raw[:80]!r}")
        return None
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if not parsed.netloc or not hostname or port == 0 or authority.endswith(":"):
        problems.append(f"mrf-url is not a resolvable URL: {raw[:80]!r}")
        return None
    return raw


def parse_cms_hpt(text: str, *, domain: str) -> Discovery:
    """Parse a cms-hpt.txt body. Never raises; unusable input becomes recorded problems."""
    # A server answering 200 with an HTML error page is common enough to name specifically:
    # treating that as "no file" loses the distinction between absent and misconfigured.
    stripped = text.lstrip(BOM).strip()
    if stripped[:1] == "<" or "<html" in stripped[:512].lower():
        return Discovery(
            domain=domain,
            document_problems=("served HTML rather than a cms-hpt.txt document",),
        )
    if not stripped:
        return Discovery(domain=domain, document_problems=("cms-hpt.txt is empty",))

    entries = tuple(_entry_from_parts(parts) for parts in _split_entries(stripped))
    return Discovery(domain=domain, entries=entries)


def parse_mrf_filename(url: str) -> FilenameFacts:
    """Read the CMS filename convention out of an MRF URL.

    A conforming name carries the filer's EIN, which is worth having: it identifies the entity
    independently of whatever name the hospital chose to publish.
    """
    path = urlparse(url).path
    filename = path.rsplit("/", 1)[-1] if path else ""
    match = _FILENAME_RE.match(filename)
    if not match:
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else None
        return FilenameFacts(
            ein=None, hospital_name=None, extension=extension, follows_convention=False
        )
    return FilenameFacts(
        ein=match.group("ein"),
        hospital_name=match.group("name").replace("-", " ").replace("_", " ").strip(),
        extension=match.group("ext").lower(),
        follows_convention=True,
    )


def cms_hpt_url(domain: str) -> str:
    """The conventional location, given a bare domain or a URL."""
    raw = domain.strip()
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = parsed.netloc or raw
    return f"https://{host.strip('/')}/cms-hpt.txt"
