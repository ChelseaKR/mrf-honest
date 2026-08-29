"""Bounded reading of ZIP publications, and every bound refusing on purpose.

Seven publications in the committed draw are ZIP archives. Opening a container that a publisher
controls is the point at which an unbounded read, a path escape, or a decompression bomb walks
in, so each bound below has a test that fires it.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from mrf_honest.container import (
    MAX_MEMBER_EXPANSION_RATIO,
    MAX_MEMBERS,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    ArchiveMember,
    ArchiveRefusal,
    ArchiveRefused,
    looks_like_archive,
    open_member,
    select_member,
)

CMS_JSON = json.dumps(
    {
        "hospital_name": "Example Hospital",
        "version": "3.0.0",
        "last_updated_on": "2026-08-01",
        "standard_charge_information": [],
    }
).encode()

CMS_CSV = b"hospital_name,last_updated_on,version\nExample Hospital,2026-08-01,3.0.0\n"


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


class TestSelection:
    def test_one_json_member_is_selected(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path / "a.zip", {"standardcharges.json": CMS_JSON})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveMember)
        assert outcome.name == "standardcharges.json"
        assert outcome.sniffed == "json"
        assert outcome.uncompressed_size == len(CMS_JSON)

    def test_one_csv_member_beside_a_readme_is_still_one_candidate(self, tmp_path: Path) -> None:
        """A readme is not a publication, and its presence must not make the archive ambiguous."""

        archive = _zip(
            tmp_path / "a.zip",
            {"standardcharges.csv": CMS_CSV, "README.txt": b"how to read this"},
        )
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveMember)
        assert outcome.name == "standardcharges.csv"

    def test_the_member_is_classified_by_its_bytes_not_its_name(self, tmp_path: Path) -> None:
        """The sampling frame's format rule: the document decides, never the label on it."""

        archive = _zip(tmp_path / "a.zip", {"charges.json": b"<!doctype html><html>nope"})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NO_GRADEABLE_MEMBER

    def test_a_selected_member_streams_without_reading_the_whole_archive(
        self, tmp_path: Path
    ) -> None:
        archive = _zip(tmp_path / "a.zip", {"standardcharges.json": CMS_JSON})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveMember)
        with open_member(archive, outcome) as handle:
            first = handle.read(16)
            rest = handle.read()
        assert first + rest == CMS_JSON


class TestRefusals:
    def test_two_gradeable_members_are_refused_not_chosen_between(self, tmp_path: Path) -> None:
        """Choosing would be this project deciding which file a hospital meant to publish."""

        archive = _zip(
            tmp_path / "a.zip",
            {"standardcharges.json": CMS_JSON, "standardcharges-2.json": CMS_JSON},
        )
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.AMBIGUOUS_MEMBERS
        assert len(outcome.candidates) == 2

    def test_an_archive_with_nothing_gradeable_is_refused_with_what_it_held(
        self, tmp_path: Path
    ) -> None:
        archive = _zip(tmp_path / "a.zip", {"notes.txt": b"nothing to grade"})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NO_GRADEABLE_MEMBER
        assert outcome.candidates == ("notes.txt",)

    def test_a_traversal_member_name_is_refused_unread(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path / "a.zip", {"../escape.json": CMS_JSON})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.UNSAFE_MEMBER_NAME

    def test_an_absolute_member_name_is_refused_unread(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path / "a.zip", {"/etc/passwd.json": CMS_JSON})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.UNSAFE_MEMBER_NAME

    def test_a_nested_archive_is_refused_rather_than_descended(self, tmp_path: Path) -> None:
        inner = _zip(tmp_path / "inner.zip", {"standardcharges.json": CMS_JSON})
        archive = _zip(
            tmp_path / "outer.zip",
            {"standardcharges.json": CMS_JSON, "more.zip": inner.read_bytes()},
        )
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NESTED_ARCHIVE

    def test_a_decompression_bomb_is_refused_before_a_byte_is_expanded(
        self, tmp_path: Path
    ) -> None:
        """The declared ratio is read from the central directory, so nothing is decompressed."""

        path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("standardcharges.json", b"\0" * (2 * 1024 * 1024))
        outcome = select_member(path)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.EXPANSION_RATIO
        assert "to 1" in outcome.detail

    def test_the_ratio_bound_is_the_one_that_is_documented(self, tmp_path: Path) -> None:
        path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("standardcharges.json", b"\0" * (2 * 1024 * 1024))
        with zipfile.ZipFile(path) as archive:
            info = archive.infolist()[0]
        assert info.file_size / info.compress_size > MAX_MEMBER_EXPANSION_RATIO

    def test_too_many_members_are_refused_rather_than_enumerated(self, tmp_path: Path) -> None:
        members = {f"file-{index}.txt": b"x" for index in range(MAX_MEMBERS + 1)}
        archive = _zip(tmp_path / "a.zip", members)
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.TOO_MANY_MEMBERS

    def test_an_oversized_archive_is_refused_from_the_central_directory(
        self, tmp_path: Path
    ) -> None:
        """The ceiling is checked against declared sizes, so an archive that would expand past
        the pipeline's limit never gets read at all."""

        import mrf_honest.container as container

        archive = _zip(tmp_path / "a.zip", {"standardcharges.json": CMS_JSON})
        original = container.MAX_TOTAL_UNCOMPRESSED_BYTES
        try:
            container.MAX_TOTAL_UNCOMPRESSED_BYTES = 1
            outcome = select_member(archive)
        finally:
            container.MAX_TOTAL_UNCOMPRESSED_BYTES = original
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.TOO_LARGE_UNCOMPRESSED
        assert MAX_TOTAL_UNCOMPRESSED_BYTES == 1024 * 1024 * 1024

    def test_an_encrypted_member_is_refused(self, tmp_path: Path) -> None:
        """This project holds no credentials, and guessing one is not a retrieval strategy."""

        path = tmp_path / "a.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("standardcharges.json", CMS_JSON)
        raw = bytearray(path.read_bytes())
        # Set the encryption bit in both the local header and the central directory entry.
        for signature in (b"PK\x03\x04", b"PK\x01\x02"):
            start = raw.find(signature)
            assert start >= 0
            offset = start + (6 if signature == b"PK\x03\x04" else 8)
            raw[offset] |= 0x01
        path.write_bytes(bytes(raw))
        outcome = select_member(path)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.ENCRYPTED_MEMBER

    def test_a_file_that_is_not_an_archive_is_refused_by_its_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "charges.zip"
        path.write_bytes(CMS_JSON)
        outcome = select_member(path)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NOT_AN_ARCHIVE

    def test_a_corrupt_archive_is_refused_with_the_cause_named(self, tmp_path: Path) -> None:
        path = tmp_path / "a.zip"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
        outcome = select_member(path)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.UNREADABLE
        assert outcome.detail

    def test_a_missing_file_is_refused_not_raised(self, tmp_path: Path) -> None:
        outcome = select_member(tmp_path / "absent.zip")
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NOT_AN_ARCHIVE


class TestSerialisation:
    def test_a_selection_serialises_with_what_it_selected(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path / "a.zip", {"standardcharges.json": CMS_JSON})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveMember)
        payload = outcome.as_dict()
        assert payload["outcome"] == "selected"
        assert json.dumps(payload)

    def test_a_refusal_serialises_with_its_reason_and_candidates(self, tmp_path: Path) -> None:
        archive = _zip(
            tmp_path / "a.zip",
            {"standardcharges.json": CMS_JSON, "other.json": CMS_JSON},
        )
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        payload = outcome.as_dict()
        assert payload["outcome"] == "refused"
        assert payload["reason"] == "ambiguous_members"
        assert len(payload["candidates"]) == 2  # type: ignore[arg-type]
        assert json.dumps(payload)

    def test_every_refusal_reason_is_a_stable_string(self) -> None:
        for reason in ArchiveRefusal:
            assert str(reason) == reason.value
            assert reason.value.islower()


def test_looks_like_archive_reads_bytes_not_names(tmp_path: Path) -> None:
    named = tmp_path / "charges.json"
    _zip(named, {"standardcharges.json": CMS_JSON})
    assert looks_like_archive(named)
    plain = tmp_path / "charges.zip"
    plain.write_bytes(CMS_JSON)
    assert not looks_like_archive(plain)
