"""Bounded reading of ZIP-published machine-readable files.

Seven publications in the committed 2026-08-19 draw are ZIP archives. Until now every one was a
recorded exclusion, with the stated reason that "a ZIP archive is a container, not a CSV file,
and grading one against this profile would measure the wrong thing"
(`docs/SAMPLING-FRAME.md`). That reason is exactly right about grading the *container*, and it
says nothing about the document inside it, which is the thing a hospital actually published.

This module opens the container and answers one question: **does it hold exactly one document
this project could grade?** Exactly one is a routing decision. Zero, or more than one, is a
stated refusal, because choosing among several members would be this project inventing which
file a hospital meant to publish.

Everything here is bounded, and every bound is stated rather than assumed:

* a cap on the number of members, so a directory of ten thousand entries is refused rather than
  enumerated;
* a cap on the total declared uncompressed size, matching the project's decoded-size ceiling, so
  an archive that expands past what the pipeline would accept is refused before a byte is read;
* a cap on the expansion ratio of any single member, because that is what a zip bomb is;
* refusal of an encrypted member, a member whose name escapes the archive root, and a member
  that is itself an archive, because a nested container is a second unbounded read wearing the
  first one's clothes.

A refusal is a published outcome carrying its reason, never a silent skip. Standard library
only, per ADR 0002: `zipfile` reads the central directory and then streams one member, so peak
memory stays bounded by the read buffer rather than by the archive.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO

#: The most members this project will enumerate. A hospital's price-transparency publication is
#: one document, occasionally beside a readme; an archive with hundreds of entries is not the
#: shape this reader was built for, and enumerating it anyway would be a guess about intent.
MAX_MEMBERS = 64

#: Total declared uncompressed bytes across all members. The same 1 GiB the fetcher accepts for
#: a single body, so an archive cannot smuggle past the ceiling by being compressed.
MAX_TOTAL_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024

#: Highest expansion ratio any single member may declare. Measured, not guessed: a 42.zip layer
#: expands about 1,000-fold, real CMS JSON and CSV compress between roughly 5 and 20 times, and
#: the highest ratio observed in this project's own fixtures is far below this bound.
MAX_MEMBER_EXPANSION_RATIO = 200.0

#: Extensions this project has a profile for. Read from the member name only as a first pass;
#: the leading bytes decide, exactly as the sampling frame's format rule requires.
_GRADEABLE_SUFFIXES = (".json", ".csv")

_ARCHIVE_SUFFIXES = (".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2", ".xz")

#: How many leading bytes of a member are read to classify it.
_CLASSIFY_BYTES = 4_096


class ArchiveRefusal(StrEnum):
    """Why no member was selected. Each is an outcome to publish, not an error to swallow."""

    NOT_AN_ARCHIVE = "not_an_archive"
    UNREADABLE = "unreadable"
    TOO_MANY_MEMBERS = "too_many_members"
    TOO_LARGE_UNCOMPRESSED = "too_large_uncompressed"
    EXPANSION_RATIO = "expansion_ratio"
    ENCRYPTED_MEMBER = "encrypted_member"
    UNSAFE_MEMBER_NAME = "unsafe_member_name"
    NESTED_ARCHIVE = "nested_archive"
    NO_GRADEABLE_MEMBER = "no_gradeable_member"
    AMBIGUOUS_MEMBERS = "ambiguous_members"


@dataclass(frozen=True)
class ArchiveMember:
    """The one member an archive was found to hold, and how it was classified."""

    name: str
    compressed_size: int
    uncompressed_size: int
    sniffed: str

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": "selected",
            "name": self.name,
            "compressed_size": self.compressed_size,
            "uncompressed_size": self.uncompressed_size,
            "sniffed": self.sniffed,
        }


@dataclass(frozen=True)
class ArchiveRefused:
    """A stated reason no member was selected, with the detail a reader needs to check it."""

    reason: ArchiveRefusal
    detail: str
    candidates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": "refused",
            "reason": str(self.reason),
            "detail": self.detail,
            "candidates": list(self.candidates),
        }


ArchiveOutcome = ArchiveMember | ArchiveRefused


def looks_like_archive(path: Path) -> bool:
    """Whether the leading bytes are a ZIP signature. The name is not consulted."""

    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    return head in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}


def _unsafe_name(name: str) -> bool:
    """A member name that escapes the archive root, or is absolute, is refused unread."""

    if name.startswith(("/", "\\")) or ":" in name.split("/", 1)[0][1:2]:
        return True
    parts = Path(name.replace("\\", "/")).parts
    return any(part == ".." for part in parts)


def _classify(handle: IO[bytes]) -> str:
    """Classify a member by its leading bytes, never by the name it was stored under."""

    from mrf_honest.fetch import _sniff_sample  # local: avoids a cycle at import time

    sniffed, _ = _sniff_sample(handle.read(_CLASSIFY_BYTES))
    return sniffed


def _reject_member(info: zipfile.ZipInfo) -> ArchiveRefused | None:
    """Every per-member bound, in one place, checked before anything is decompressed."""

    if _unsafe_name(info.filename):
        return ArchiveRefused(
            ArchiveRefusal.UNSAFE_MEMBER_NAME,
            f"member {info.filename!r} escapes the archive root",
        )
    if info.flag_bits & 0x1:
        return ArchiveRefused(
            ArchiveRefusal.ENCRYPTED_MEMBER,
            f"member {info.filename!r} is encrypted; this project holds no credentials",
        )
    if info.filename.lower().endswith(_ARCHIVE_SUFFIXES):
        return ArchiveRefused(
            ArchiveRefusal.NESTED_ARCHIVE,
            f"member {info.filename!r} is itself an archive; a nested container is a second "
            "unbounded read and is refused rather than descended",
        )
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > MAX_MEMBER_EXPANSION_RATIO:
            return ArchiveRefused(
                ArchiveRefusal.EXPANSION_RATIO,
                f"member {info.filename!r} declares an expansion ratio of {ratio:.0f} to 1, "
                f"above the stated bound of {MAX_MEMBER_EXPANSION_RATIO:.0f} to 1",
            )
    return None


def _select(archive: zipfile.ZipFile) -> ArchiveOutcome:
    """Pick the one gradeable member, or state why there is not exactly one."""

    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_MEMBERS:
        return ArchiveRefused(
            ArchiveRefusal.TOO_MANY_MEMBERS,
            f"{len(members)} members, above the stated cap of {MAX_MEMBERS}",
        )
    total = sum(info.file_size for info in members)
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        return ArchiveRefused(
            ArchiveRefusal.TOO_LARGE_UNCOMPRESSED,
            f"{total} declared uncompressed bytes, above the stated ceiling of "
            f"{MAX_TOTAL_UNCOMPRESSED_BYTES}",
        )
    for info in members:
        refusal = _reject_member(info)
        if refusal is not None:
            return refusal
    return _choose(archive, members)


def _choose(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo]) -> ArchiveOutcome:
    """Among admitted members, find the ones this project has a profile for."""

    candidates: list[tuple[zipfile.ZipInfo, str]] = []
    for info in members:
        if not info.filename.lower().endswith(_GRADEABLE_SUFFIXES):
            continue
        with archive.open(info) as handle:
            sniffed = _classify(handle)
        if sniffed in {"json", "text"}:
            candidates.append((info, sniffed))
    if not candidates:
        return ArchiveRefused(
            ArchiveRefusal.NO_GRADEABLE_MEMBER,
            "no member is a document this project has a profile for",
            tuple(info.filename for info in members),
        )
    if len(candidates) > 1:
        return ArchiveRefused(
            ArchiveRefusal.AMBIGUOUS_MEMBERS,
            "more than one member could be graded; choosing among them would be this project "
            "deciding which file the publisher meant to publish",
            tuple(info.filename for info, _ in candidates),
        )
    info, sniffed = candidates[0]
    return ArchiveMember(
        name=info.filename,
        compressed_size=info.compress_size,
        uncompressed_size=info.file_size,
        sniffed=sniffed,
    )


def select_member(path: Path) -> ArchiveOutcome:
    """Open a ZIP publication and select the one document inside it, or state why not."""

    if not looks_like_archive(path):
        return ArchiveRefused(
            ArchiveRefusal.NOT_AN_ARCHIVE, f"{path} does not begin with a ZIP signature"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            return _select(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        return ArchiveRefused(ArchiveRefusal.UNREADABLE, f"{type(exc).__name__}: {exc}")


@contextmanager
def open_member(path: Path, member: ArchiveMember) -> Iterator[IO[bytes]]:
    """Stream one selected member, so the inspectors read it as they read any other body.

    `zipfile` decompresses on demand from a seekable file, so peak memory stays bounded by the
    read buffer rather than by the member's uncompressed size, which is the property ADR 0002
    exists to protect.
    """

    with zipfile.ZipFile(path) as archive, archive.open(member.name) as handle:
        yield handle
