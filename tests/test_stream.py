from __future__ import annotations

import io
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mrf_honest.stream import BOM, StreamError, StreamStats, read_envelope, stream_array_items


def _doc(items: list[dict], **envelope: object) -> bytes:
    payload = {"hospital_name": "Example", "version": "2.0", **envelope,
               "standard_charge_information": items}
    return json.dumps(payload).encode()


def _items(raw: bytes, key: str = "standard_charge_information"):
    stats = StreamStats()
    return list(stream_array_items(io.BytesIO(raw), key, stats=stats)), stats


def test_streams_items_in_order() -> None:
    items, stats = _items(_doc([{"i": 0}, {"i": 1}, {"i": 2}]))
    assert [i["i"] for i in items] == [0, 1, 2]
    assert stats.items_yielded == 3
    assert stats.bytes_read > 0


def test_bom_is_handled_and_recorded() -> None:
    """The first real file touched in phase 0 had a BOM and broke json.load outright."""
    items, stats = _items(BOM + _doc([{"i": 1}]))
    assert len(items) == 1
    assert stats.had_bom


def test_sibling_keys_before_and_after_the_array_are_skipped() -> None:
    raw = json.dumps({
        "a": {"deeply": {"nested": [1, 2, {"x": "}"}]}},
        "standard_charge_information": [{"i": 1}],
        "z": "trailing",
    }).encode()
    items, _ = _items(raw)
    assert items == [{"i": 1}]


def test_braces_inside_strings_do_not_confuse_depth() -> None:
    """The one genuinely error-prone part of hand-rolling this."""
    tricky = {"description": 'a "quoted } brace" and [bracket] and \\ backslash'}
    items, _ = _items(_doc([tricky, {"i": 2}]))
    assert items[0] == tricky
    assert len(items) == 2


def test_empty_array() -> None:
    items, stats = _items(_doc([]))
    assert items == [] and stats.items_yielded == 0


def test_non_object_items_are_recorded_not_yielded() -> None:
    raw = json.dumps({"standard_charge_information": [{"i": 1}, 42, "text"]}).encode()
    items, stats = _items(raw)
    assert items == [{"i": 1}]
    assert len(stats.problems) == 2


@pytest.mark.parametrize("raw,message", [
    (b"", "empty"),
    (b"[1,2,3]", "not a JSON object"),
    (b'{"other": 1}', "no 'standard_charge_information' array"),
    (b'{"standard_charge_information": 5}', "not an array"),
    (b'{"standard_charge_information": [{"a": 1}', "unterminated"),
])
def test_unusable_documents_raise_with_a_reason(raw: bytes, message: str) -> None:
    with pytest.raises(StreamError, match=message):
        _items(raw)


def test_missing_colon_is_an_error() -> None:
    with pytest.raises(StreamError, match="expected ':'"):
        _items(b'{"standard_charge_information" [1]}')


def test_large_document_streams_without_accumulating() -> None:
    """Peak memory should track the largest item, not the file. 20k items with a padded body
    produces a document far larger than the buffer ever holds."""
    big = [{"i": n, "pad": "x" * 400} for n in range(20_000)]
    raw = _doc(big)
    assert len(raw) > 8_000_000  # ~8 MB, comfortably past any single chunk
    seen = 0
    stats = StreamStats()
    for item in stream_array_items(io.BytesIO(raw), "standard_charge_information", stats=stats):
        assert item["i"] == seen
        seen += 1
    assert seen == 20_000
    assert stats.bytes_read == len(raw)


def test_envelope_reads_scalars_without_the_array() -> None:
    raw = _doc([{"i": n} for n in range(500)], last_updated_on="2026-08-05")
    env = read_envelope(io.BytesIO(raw), "standard_charge_information")
    assert env["hospital_name"] == "Example"
    assert env["last_updated_on"] == "2026-08-05"
    assert "standard_charge_information" not in env


def test_envelope_tolerates_bom_and_garbage() -> None:
    assert read_envelope(io.BytesIO(BOM + _doc([])), "standard_charge_information")["version"]
    assert read_envelope(io.BytesIO(b"not json"), "x") == {}
    assert read_envelope(io.BytesIO(b"[1,2]"), "x") == {}


@given(items=st.lists(
    st.dictionaries(
        st.text(min_size=1, max_size=8),
        st.one_of(st.integers(), st.text(max_size=30), st.booleans(), st.none()),
        max_size=4),
    max_size=25))
def test_round_trips_arbitrary_item_dicts(items: list[dict]) -> None:
    """Whatever json.dumps can write, the streaming reader must read back identically."""
    parsed, stats = _items(_doc(items))
    assert parsed == items
    assert stats.items_yielded == len(items)


def test_no_item_lost_at_buffer_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a refill mid-scan compacted the buffer underneath the captured start index,
    corrupting exactly one item per refill. Only visible when many refills occur, so force a tiny
    chunk size rather than relying on a large fixture."""
    import mrf_honest.stream as stream_mod

    monkeypatch.setattr(stream_mod, "_CHUNK", 512)
    items = [{"i": n, "pad": "x" * 40} for n in range(400)]
    raw = json.dumps({"a": "head", "standard_charge_information": items, "z": 1}).encode()
    got = [it["i"] for it in stream_mod.stream_array_items(
        io.BytesIO(raw), "standard_charge_information")]
    assert got == list(range(400))


def test_scan_returns_bytes_so_indices_cannot_go_stale() -> None:
    """The API shape is the fix: handing back a copy makes the compaction bug unrepresentable."""
    import inspect

    import mrf_honest.stream as stream_mod

    assert inspect.signature(stream_mod._scan_value).return_annotation == "bytes"


def test_envelope_survives_tiny_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    import mrf_honest.stream as stream_mod

    monkeypatch.setattr(stream_mod, "_CHUNK", 64)
    raw = _doc([{"i": n} for n in range(200)], last_updated_on="2026-08-05")
    env = stream_mod.read_envelope(io.BytesIO(raw), "standard_charge_information")
    assert env["hospital_name"] == "Example"
    assert env["last_updated_on"] == "2026-08-05"
