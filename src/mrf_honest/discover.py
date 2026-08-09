"""Discover hospital price-transparency files via the CMS-required `cms-hpt.txt` convention.

CMS requires hospitals to publish a `cms-hpt.txt` at the root of their public website pointing at
their machine-readable file. That convention is what makes this project's registry buildable
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

# The three fields CMS specifies. Real files in the wild also carry extras; those are preserved
# rather than dropped, because an unexpected field is evidence about the publisher.
_KNOWN_FIELDS = ("location-name", "source-page-url", "mrf-url")

# {EIN}_{hospital-name}_standardcharges.{json|csv|xlsx}, per the CMS naming convention. The EIN
# identifies the filer, which is a second structured signal beyond the URL itself.
_FILENAME_RE = re.compile(
    r"^(?P<ein>\d{9})_(?P<name>.+?)_standardcharges\.(?P<ext>json|csv|xlsx)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Discovery:
    """What a domain's cms-hpt.txt claims. `problems` is empty only when everything parsed."""

    domain: str
    location_name: str | None = None
    source_page_url: str | None = None
    mrf_url: str | None = None
    extra_fields: tuple[tuple[str, str], ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """True when an https MRF URL was found. Everything else is nice to have."""
        return bool(self.mrf_url)


@dataclass(frozen=True)
class FilenameFacts:
    ein: str | None
    hospital_name: str | None
    extension: str | None
    follows_convention: bool


def _split_fields(text: str) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
    """Split a cms-hpt body into known fields, unrecognized fields, and parse problems."""
    fields: dict[str, str] = {}
    extra: list[tuple[str, str]] = []
    problems: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip(BOM)
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            problems.append(f"unparseable line: {line[:60]!r}")
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace("_", "-")
        value = value.strip()
        if not value:
            problems.append(f"field {key!r} present but empty")
        elif key not in _KNOWN_FIELDS:
            extra.append((key, value))
        elif key in fields:
            # First declaration wins; the duplication is recorded rather than silently resolved.
            problems.append(f"field {key!r} declared more than once")
        else:
            fields[key] = value
    return fields, extra, problems


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
    if not parsed.netloc or not hostname or port == 0:
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
            domain=domain, problems=("served HTML rather than a cms-hpt.txt document",)
        )
    if not stripped:
        return Discovery(domain=domain, problems=("cms-hpt.txt is empty",))

    fields, extra, problems = _split_fields(stripped)
    mrf_url = _check_mrf_url(fields.get("mrf-url"), problems)
    for optional in ("location-name", "source-page-url"):
        if optional not in fields:
            problems.append(f"no {optional} field")

    return Discovery(
        domain=domain,
        location_name=fields.get("location-name"),
        source_page_url=fields.get("source-page-url"),
        mrf_url=mrf_url,
        extra_fields=tuple(extra),
        problems=tuple(problems),
    )


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
