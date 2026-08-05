"""Stream large price-transparency files without loading them into memory.

Phase 0 measured naive ``json.load`` on a 65 MB hospital file at 506 MB resident, 7.8x the file.
Stanford's 155 MB file implies roughly 1.2 GB by the same ratio, and payer files run one to three
orders of magnitude larger again. Streaming is not an optimization here, it is the difference
between the project working and not.

The approach is a hand-written incremental parser over the one shape that matters: a large array
nested inside an otherwise small object. Everything outside that array (hospital name, address,
attestation, version) is small and is captured whole; the array is yielded item by item and never
accumulated.

Standard library only, deliberately. A dependency that hides the memory behaviour would defeat the
point of measuring it.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, BinaryIO

BOM = b"\xef\xbb\xbf"
_WHITESPACE = b" \t\r\n"
_CHUNK = 1 << 20  # 1 MiB reads; large enough to amortize syscalls, small enough to stay flat


@dataclass
class StreamStats:
    """What the reader observed. Reported rather than inferred, so claims stay measurable."""

    bytes_read: int = 0
    items_yielded: int = 0
    had_bom: bool = False
    problems: list[str] = field(default_factory=list)


class StreamError(Exception):
    """Raised only when the document cannot be interpreted at all."""


def _skip_ws(buf: bytes, i: int) -> int:
    while i < len(buf) and buf[i] in _WHITESPACE:
        i += 1
    return i


class _Reader:
    """Buffered byte reader that can grow its window on demand."""

    def __init__(self, fh: BinaryIO, stats: StreamStats) -> None:
        self._fh = fh
        self._buf = b""
        self._pos = 0
        # While pinned, refills must not compact. A value scan captures absolute start and end
        # indices, and compacting between them silently corrupted one item per refill.
        self._pinned = False
        self.stats = stats

    def fill(self, need: int = 1) -> bool:
        """Ensure at least ``need`` unconsumed bytes are buffered. False at end of input."""
        while len(self._buf) - self._pos < need:
            chunk = self._fh.read(_CHUNK)
            if not chunk:
                return len(self._buf) - self._pos > 0
            self.stats.bytes_read += len(chunk)
            # Drop the consumed prefix so the buffer does not grow with the file, except while a
            # value scan holds indices into it. Growth is then bounded by one item, which is the
            # memory guarantee this module documents.
            if self._pos and not self._pinned:
                self._buf = self._buf[self._pos :]
                self._pos = 0
            self._buf += chunk
        return True

    @property
    def buf(self) -> bytes:
        return self._buf

    @property
    def pos(self) -> int:
        return self._pos

    @pos.setter
    def pos(self, value: int) -> None:
        self._pos = value

    def pin(self) -> None:
        self._pinned = True

    def unpin(self) -> None:
        """Release the pin and compact away everything already consumed."""
        self._pinned = False
        if self._pos:
            self._buf = self._buf[self._pos :]
            self._pos = 0

    def ensure(self, i: int) -> int:
        """Make index ``i`` readable, returning it rebased for any buffer compaction.

        fill() drops the consumed prefix and resets _pos, so an absolute index captured before a
        refill points at the wrong byte afterwards. Scanners must go through this. Returns -1 at
        end of input.
        """
        if i < len(self._buf):
            return i
        offset = i - self._pos
        if not self.fill(offset + 1):
            return -1
        return self._pos + offset

    def peek(self) -> int | None:
        if not self.fill(1):
            return None
        self._pos = _skip_ws(self._buf, self._pos)
        if self._pos >= len(self._buf):
            return None if not self.fill(1) else self.peek()
        return self._buf[self._pos]


def _scan_string(reader: _Reader, i: int) -> int:
    """Return the index just past a string that starts at ``i``. Grows the buffer as needed."""
    i += 1  # opening quote
    escaped = False
    while True:
        i = reader.ensure(i)
        if i < 0:
            raise StreamError("unexpected end of input inside a string")
        byte = reader.buf[i]
        if escaped:
            escaped = False
        elif byte == 0x5C:  # backslash
            escaped = True
        elif byte == 0x22:  # closing quote
            return i + 1
        i += 1


def _scan_container(reader: _Reader, i: int) -> int:
    """Return the index just past the object or array starting at ``i``.

    Strings are skipped wholesale so a brace inside a description field cannot change the depth
    count, which is the single most error-prone part of hand-rolling this.
    """
    depth = 0
    while True:
        i = reader.ensure(i)
        if i < 0:
            raise StreamError("unexpected end of input inside a container")
        byte = reader.buf[i]
        if byte == 0x22:  # a string: skip it entirely
            i = _scan_string(reader, i)
            continue
        if byte in (0x7B, 0x5B):  # { [
            depth += 1
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1


def _scan_scalar(reader: _Reader, i: int) -> int:
    """Return the index just past a bare scalar (number, true, false, null)."""
    while True:
        nxt = reader.ensure(i)
        if nxt < 0:
            return i  # end of input terminates the scalar
        i = nxt
        if reader.buf[i] in b",]}: \t\r\n":
            return i
        i += 1


def _scan_value(reader: _Reader) -> bytes:
    """Consume the next complete JSON value and return its raw bytes, advancing the reader.

    Returns bytes rather than indices deliberately. An earlier version returned a span, and a
    refill between capturing ``start`` and capturing ``end`` compacted the buffer underneath them,
    corrupting exactly one item per refill. Holding the pin inside this function and handing back
    a copy makes that class of bug unrepresentable.

    Dispatches on the first byte: a string ends at its closing quote, a container at depth zero,
    and a bare scalar at the next delimiter.
    """
    reader.fill(1)
    reader.pos = _skip_ws(reader.buf, reader.pos)
    if reader.pos >= len(reader.buf) and not reader.fill(1):
        raise StreamError("unexpected end of input while scanning a value")
    reader.pos = _skip_ws(reader.buf, reader.pos)
    reader.pin()
    try:
        start = reader.pos
        first = reader.buf[start]
        if first == 0x22:
            end = _scan_string(reader, start)
        elif first in (0x7B, 0x5B):
            end = _scan_container(reader, start)
        else:
            end = _scan_scalar(reader, start)
            if end == start:
                raise StreamError(
                    f"unexpected byte {bytes([first])!r} while scanning a value")
        raw = bytes(reader.buf[start:end])
        reader.pos = end
        return raw
    finally:
        reader.unpin()


def _open_object(fh: BinaryIO, st: StreamStats) -> _Reader:
    """Position a reader just inside the top-level object, tolerating a BOM."""
    reader = _Reader(fh, st)
    if not reader.fill(3):
        raise StreamError("file is empty")
    if reader.buf[reader.pos : reader.pos + 3] == BOM:
        # Seen on the first real file touched in phase 0: a BOM makes json.load fail outright.
        st.had_bom = True
        reader.pos += 3
    if reader.peek() != 0x7B:  # {
        raise StreamError("top level is not a JSON object")
    reader.pos += 1
    return reader


def stream_array_items(
    fh: BinaryIO, array_key: str, *, stats: StreamStats | None = None
) -> Iterator[dict[str, Any]]:
    """Yield items from the top-level array at ``array_key`` one at a time.

    Sibling keys are skipped without being materialized. The caller decides what to keep, so peak
    memory is bounded by the largest single item rather than by the file.
    """
    st = stats if stats is not None else StreamStats()
    reader = _open_object(fh, st)
    while True:
        nxt = reader.peek()
        if nxt is None:
            raise StreamError(f"no {array_key!r} array found before end of input")
        if nxt == 0x7D:  # }
            raise StreamError(f"no {array_key!r} array found in object")
        if nxt == 0x2C:  # ,
            reader.pos += 1
            continue
        key = json.loads(_scan_value(reader).decode("utf-8", "replace"))
        if reader.peek() != 0x3A:  # :
            raise StreamError(f"expected ':' after key {key!r}")
        reader.pos += 1

        if key != array_key:
            _scan_value(reader)  # skipped without being materialized
            continue

        if reader.peek() != 0x5B:  # [
            raise StreamError(f"{array_key!r} is not an array")
        reader.pos += 1
        yield from _iter_array(reader, st, array_key)
        return


def _iter_array(reader: _Reader, st: StreamStats, array_key: str) -> Iterator[dict[str, Any]]:
    """Yield each object in the already-opened array, recording items it cannot use."""
    while True:
        nxt = reader.peek()
        if nxt is None:
            raise StreamError(f"unterminated {array_key!r} array")
        if nxt == 0x5D:  # ]
            return
        if nxt == 0x2C:  # ,
            reader.pos += 1
            continue
        raw = _scan_value(reader)
        try:
            item = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            st.problems.append(f"item {st.items_yielded}: {exc}")
            continue
        if isinstance(item, dict):
            st.items_yielded += 1
            yield item
        else:
            st.problems.append(f"item {st.items_yielded}: not an object")


def read_envelope(fh: BinaryIO, array_key: str, *, max_bytes: int = 1 << 20) -> dict[str, Any]:
    """Read the small scalar fields around the big array (hospital name, version, dates).

    Only the first ``max_bytes`` are inspected, because the envelope is small by construction and
    reading further would reintroduce the memory problem this module exists to avoid.
    """
    head = fh.read(max_bytes)
    if head.startswith(BOM):
        head = head[3:]
    envelope: dict[str, Any] = {}
    reader = _Reader(io.BytesIO(head), StreamStats())
    if not reader.fill(1) or reader.peek() != 0x7B:
        return envelope
    reader.pos += 1
    while True:
        nxt = reader.peek()
        if nxt is None or nxt == 0x7D:
            return envelope
        if nxt == 0x2C:
            reader.pos += 1
            continue
        try:
            key = json.loads(_scan_value(reader).decode("utf-8", "replace"))
            if reader.peek() != 0x3A:
                return envelope
            reader.pos += 1
            raw = _scan_value(reader)
        except (StreamError, ValueError):
            return envelope
        if key == array_key:
            continue  # the big one; never materialized here
        try:
            envelope[str(key)] = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
