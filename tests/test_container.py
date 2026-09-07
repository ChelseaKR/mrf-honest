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

    def test_a_member_nobody_opened_is_not_reported_as_one_without_a_profile(
        self, tmp_path: Path
    ) -> None:
        """The refusal said "no member is a document this project has a profile for".

        It said that of members it had never opened. ``_choose`` skips any member whose name does
        not end in ``.json`` or ``.csv`` *before* sniffing, so a CMS-shaped CSV stored under a
        name with no extension was refused with a sentence about its contents while its bytes
        went unread -- and this project's own committed draw contains four extensionless CSV
        endpoints, so it is an observed shape. This asserts the stated reason distinguishes what
        was examined from what was not.

        Acceptance is unchanged: the name still decides what gets opened, because the sniffer
        classifies a CSV and a README alike as ``text`` and dropping the name would make a
        document-beside-a-readme archive ambiguous.
        """

        archive = _zip(tmp_path / "a.zip", {"standardcharges": CMS_CSV})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NO_GRADEABLE_MEMBER
        assert "not examined" in outcome.detail
        assert "standardcharges" in outcome.detail
        # The old sentence was a claim about contents. It must not be what comes back.
        assert "no member is a document this project has a profile for" not in outcome.detail

    def test_a_member_that_was_opened_is_reported_as_examined(self, tmp_path: Path) -> None:
        """The other half, so the two cases cannot collapse into one wording.

        The fixture's precondition is asserted rather than assumed: the body has to be one the
        sniffer classifies as neither ``json`` nor ``text``, or the member would be *selected*
        and this test would pass while asserting nothing about a refusal. An earlier draft of it
        used an eight-byte PNG header, which this sniffer calls ``text``, and the assertions sat
        behind a branch that never ran.
        """

        from mrf_honest.fetch import _sniff_sample

        body = b"\x89PNG\r\n\x1a\n\x00"
        assert _sniff_sample(body)[0] not in {"json", "text"}, "the fixture would be selected"

        archive = _zip(tmp_path / "a.zip", {"standardcharges.json": body})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert outcome.reason is ArchiveRefusal.NO_GRADEABLE_MEMBER
        assert "whose name ends in .json or .csv" in outcome.detail
        assert "not examined" not in outcome.detail

    def test_both_halves_are_named_when_an_archive_holds_each(self, tmp_path: Path) -> None:
        """One member opened and rejected, one never opened. The reason has to say both."""

        from mrf_honest.fetch import _sniff_sample

        body = b"\x89PNG\r\n\x1a\n\x00"
        assert _sniff_sample(body)[0] not in {"json", "text"}

        archive = _zip(tmp_path / "a.zip", {"notes.csv": body, "standardcharges": CMS_CSV})
        outcome = select_member(archive)
        assert isinstance(outcome, ArchiveRefused)
        assert "not opened at all" in outcome.detail
        assert "'standardcharges'" in outcome.detail

    def test_the_member_cap_is_the_documented_number_and_not_whatever_it_says(
        self, tmp_path: Path
    ) -> None:
        """64, against a literal, on both sides of the boundary.

        Measured on this module before this test existed: ``MAX_MEMBERS = 4`` and
        ``MAX_MEMBERS = 10000`` each left all 21 tests passing. The only test of the cap builds
        ``range(MAX_MEMBERS + 1)`` -- a fixture that is always exactly one past whatever the
        bound happens to be, so it pins the *relationship* and nothing about the number. A reader
        of ``container.py`` is told 64 is a stated cap, and until now nothing checked that it was.

        The two behavioural halves matter in opposite directions. A silently *lowered* cap starts
        refusing real publications; a silently *raised* one is an unbounded enumeration the
        comment says this reader will not do.
        """

        assert MAX_MEMBERS == 64

        at_cap = {f"file-{index}.txt": b"x" for index in range(63)}
        at_cap["standardcharges.json"] = CMS_JSON
        assert len(at_cap) == 64
        outcome = select_member(_zip(tmp_path / "at-cap.zip", at_cap))
        assert isinstance(outcome, ArchiveMember), (
            "an archive of exactly 64 members was refused; the documented cap is 64"
        )
        assert outcome.name == "standardcharges.json"

        over_cap = {f"file-{index}.txt": b"x" for index in range(64)}
        over_cap["standardcharges.json"] = CMS_JSON
        assert len(over_cap) == 65
        refused = select_member(_zip(tmp_path / "over-cap.zip", over_cap))
        assert isinstance(refused, ArchiveRefused)
        assert refused.reason is ArchiveRefusal.TOO_MANY_MEMBERS

    def test_the_ratio_bound_is_not_quietly_tightened_onto_a_real_publication(
        self, tmp_path: Path
    ) -> None:
        """The other direction of the same bound, which its existing test cannot see.

        ``test_the_ratio_bound_is_the_one_that_is_documented`` builds a 2 MiB run of zeros --
        about 1,000 to 1 -- and asserts it exceeds the constant, so raising the bound to a
        million is caught. Measured: *lowering* it to ``4.0`` left all 21 tests passing. That is
        the dangerous direction: real CMS CSV compresses far better than 4 to 1, so a quietly
        tightened bound refuses ordinary publications as archive bombs, and the refusal reads as
        the publisher's fault.

        The fixture is an ordinary repetitive CSV, which lands near 74 to 1 -- inside the stated
        bound with room either side, and well clear of any plausible tightening.
        """

        assert MAX_MEMBER_EXPANSION_RATIO == 200.0

        row = b"Example Hospital,2026-08-01,3.0.0,inpatient,DRG,470,12345.67\n"
        body = b"hospital_name,last_updated_on,version,setting,code_type,code,amount\n" + row * 200
        path = _zip(tmp_path / "ordinary.zip", {"standardcharges.csv": body})
        with zipfile.ZipFile(path) as archive:
            info = archive.infolist()[0]
        observed = info.file_size / info.compress_size
        assert 4.0 < observed < 200.0, (
            f"the fixture's ratio is {observed:.1f}; it has to sit inside the stated bound with "
            "room either side for this test to mean anything"
        )
        outcome = select_member(path)
        assert isinstance(outcome, ArchiveMember), (
            "an ordinary repetitive CSV was refused as an archive bomb"
        )

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
