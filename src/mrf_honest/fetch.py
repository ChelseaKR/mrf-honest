"""Polite, bounded HTTPS retrieval with a content-addressed cache.

Price-transparency files are both large and public infrastructure. Fetching them should therefore
be boring and considerate: identify the client, validate cached copies instead of downloading them
again, bound every byte written, and turn expected remote failures into data a registry can retain.
This module deliberately uses only the standard library.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import ssl
import tempfile
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from http.client import HTTPException, HTTPMessage, InvalidURL
from pathlib import Path
from typing import IO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from mrf_honest.politeness import Politeness
from mrf_honest.types import Backoff, Clock, Opener, ResponseLike, Sleeper

__all__ = [
    "PROBE_SAMPLE_BYTES",
    "Backoff",
    "CacheMetadata",
    "Clock",
    "FetchOutcome",
    "FetchPolicy",
    "FetchStatus",
    "Opener",
    "ProbeOutcome",
    "ResponseLike",
    "Sleeper",
    "cache_metadata_path",
    "default_open",
    "fetch_url",
    "probe_url",
]

_DEFAULT_CHUNK_SIZE = 64 * 1024
_CACHE_VERSION = 1
_CONTENT_TYPE_TEXT_LIMIT = 200
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class FetchStatus(StrEnum):
    """Cause-specific terminal states for one logical retrieval."""

    FETCHED = "fetched"
    NOT_MODIFIED = "not_modified"
    INVALID_URL = "invalid_url"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    TOO_LARGE = "too_large"
    CONTENT_ERROR = "content_error"
    CACHE_MISS = "cache_miss"
    CACHE_ERROR = "cache_error"
    ROBOTS_DISALLOWED = "robots_disallowed"
    TLS_VERIFICATION_FAILED = "tls_verification_failed"


@dataclass(frozen=True)
class FetchPolicy:
    """Operator-controlled limits and identity for retrieval.

    ``contact`` is intentionally mandatory. A crawler that cannot be traced to a person or
    project is not polite merely because its request rate is low.
    """

    contact: str
    user_agent: str = "mrf-honest/0.1"
    max_bytes: int = 1 << 30
    timeout_seconds: float = 60.0
    retries: int = 2
    backoff_seconds: float = 1.0
    chunk_size: int = _DEFAULT_CHUNK_SIZE

    def __post_init__(self) -> None:
        contact = self.contact.strip()
        contact_url = urlsplit(contact)
        is_email = "@" in contact and not any(char.isspace() for char in contact)
        is_url = contact_url.scheme == "https" and bool(contact_url.netloc)
        is_mailto = contact.lower().startswith("mailto:") and "@" in contact[7:]
        if not contact or not (is_email or is_url or is_mailto):
            raise ValueError("contact must be an email address, mailto URI, or https URL")
        if not self.user_agent.strip() or "\n" in self.user_agent or "\r" in self.user_agent:
            raise ValueError("user_agent must be a non-empty single-line identifier")
        if "\n" in contact or "\r" in contact:
            raise ValueError("contact must be a single line")
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.retries < 0:
            raise ValueError("retries cannot be negative")
        if not math.isfinite(self.backoff_seconds) or self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

    @property
    def identifying_user_agent(self) -> str:
        """A User-Agent containing both project identity and a usable contact."""
        contact = self.contact.strip()
        if "@" in contact and ":" not in contact:
            contact = f"mailto:{contact}"
        return f"{self.user_agent.strip()} ({contact})"


@dataclass(frozen=True)
class FetchOutcome:
    """The complete, serializable result of a retrieval attempt."""

    url: str
    status: FetchStatus
    attempted_at: str
    attempts: int
    path: Path | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    wire_size_bytes: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    http_status: int | None = None
    final_url: str | None = None
    error: str | None = None
    decoded_gzip: bool = False
    content_type: str | None = None
    """The ``Content-Type`` the server declared, verbatim, or ``None`` if it declared none.

    This is recorded and never acted on here. A fetch that succeeded is not evidence that the
    requested document arrived, and this header is the only thing the server ever said about
    *what* it was sending -- so a later stage can distinguish "this URL served a web page" from
    "this file could not be read" instead of publishing the second sentence for both. It is
    deliberately not a retrieval gate: a conforming MRF served as ``text/html`` is a conforming
    MRF, and refusing it on a header would fail a publisher for something this tool cannot judge.
    """

    @property
    def ok(self) -> bool:
        """Whether a verified local body is available."""
        return self.status in {FetchStatus.FETCHED, FetchStatus.NOT_MODIFIED}

    @property
    def from_cache(self) -> bool:
        return self.status is FetchStatus.NOT_MODIFIED

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe data for durable registry records."""
        return {
            "url": self.url,
            "status": self.status.value,
            "attempted_at": self.attempted_at,
            "attempts": self.attempts,
            "path": str(self.path) if self.path is not None else None,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "wire_size_bytes": self.wire_size_bytes,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "error": self.error,
            "decoded_gzip": self.decoded_gzip,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> FetchOutcome:
        """Rehydrate a registry value, rejecting malformed required fields."""
        raw_path = _optional_str(data, "path")
        return cls(
            url=_required_str(data, "url"),
            status=FetchStatus(_required_str(data, "status")),
            attempted_at=_required_str(data, "attempted_at"),
            attempts=_required_int(data, "attempts"),
            path=Path(raw_path) if raw_path is not None else None,
            content_sha256=_optional_str(data, "content_sha256"),
            size_bytes=_optional_int(data, "size_bytes"),
            wire_size_bytes=_optional_int(data, "wire_size_bytes"),
            etag=_optional_str(data, "etag"),
            last_modified=_optional_str(data, "last_modified"),
            http_status=_optional_int(data, "http_status"),
            final_url=_optional_str(data, "final_url"),
            error=_optional_str(data, "error"),
            decoded_gzip=_optional_bool(data, "decoded_gzip", default=False),
            # Absent in records written before this was recorded. An unrecorded declaration and
            # a server that declared nothing are both ``None`` here, and neither may be read as
            # a statement about what the server served.
            content_type=_optional_str(data, "content_type"),
        )


@dataclass(frozen=True)
class CacheMetadata:
    """Metadata kept separately from immutable, content-addressed bodies."""

    url: str
    content_sha256: str
    size_bytes: int
    wire_size_bytes: int
    etag: str | None
    last_modified: str | None
    fetched_at: str
    validated_at: str
    final_url: str
    decoded_gzip: bool
    content_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "version": _CACHE_VERSION,
            "url": self.url,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "wire_size_bytes": self.wire_size_bytes,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at,
            "validated_at": self.validated_at,
            "final_url": self.final_url,
            "decoded_gzip": self.decoded_gzip,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CacheMetadata:
        if _required_int(data, "version") != _CACHE_VERSION:
            raise ValueError("unsupported cache metadata version")
        sha256 = _required_str(data, "content_sha256")
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise ValueError("invalid cached content digest")
        size_bytes = _required_int(data, "size_bytes")
        wire_size_bytes = _required_int(data, "wire_size_bytes")
        if size_bytes < 0 or wire_size_bytes < 0:
            raise ValueError("cached body sizes cannot be negative")
        return cls(
            url=_required_str(data, "url"),
            content_sha256=sha256,
            size_bytes=size_bytes,
            wire_size_bytes=wire_size_bytes,
            etag=_optional_str(data, "etag"),
            last_modified=_optional_str(data, "last_modified"),
            fetched_at=_required_str(data, "fetched_at"),
            validated_at=_required_str(data, "validated_at"),
            final_url=_required_str(data, "final_url"),
            decoded_gzip=_optional_bool(data, "decoded_gzip", default=False),
            content_type=_optional_str(data, "content_type"),
        )


class _TooLargeError(Exception):
    pass


class _ContentError(Exception):
    """A body that could not be decoded, carrying how many wire bytes had arrived.

    The count is what lets a caller ask whether the decoding failed because the transfer was
    cut short, which is a different statement about a publisher than an invalid encoding.
    """

    def __init__(self, message: str, *, wire_size: int = 0) -> None:
        super().__init__(message)
        self.wire_size = wire_size


class _NetworkReadError(Exception):
    pass


class _IncompleteTransferError(Exception):
    """The body that arrived disagrees with the length the server declared for it."""


class _CacheMetadataError(Exception):
    pass


class _UnsafeRedirectError(HTTPError):
    def __init__(
        self,
        url: str,
        code: int,
        message: str,
        headers: HTTPMessage,
        body: IO[bytes],
        target: str,
    ) -> None:
        super().__init__(url, code, message, headers, body)
        self.target = target


class _HTTPSOnlyRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        body: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
        new_url: str,
    ) -> Request | None:
        problem = _url_problem(new_url)
        if problem is not None:
            raise _UnsafeRedirectError(
                request.full_url,
                code,
                f"unsafe redirect target: {problem}",
                headers,
                body,
                new_url,
            )
        return super().redirect_request(request, body, code, message, headers, new_url)


def _required_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key!r} must be a string")
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{key!r} must be a string or null")


def _required_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key!r} must be an integer")
    return value


def _optional_int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key!r} must be an integer or null")
    return value


def _optional_bool(data: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key!r} must be a boolean")
    return value


def default_open(request: Request, *, timeout: float) -> ResponseLike:
    """The project's standard opener: HTTPS-only, redirect targets validated."""
    opener = build_opener(_HTTPSOnlyRedirectHandler())
    return cast(ResponseLike, opener.open(request, timeout=timeout))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(clock: Clock) -> str:
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _url_problem(url: str) -> str | None:
    if not url or any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        return "URL is empty or contains whitespace/control characters"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        return f"invalid URL: {exc}"
    if parsed.scheme.lower() != "https":
        return "refused non-HTTPS URL"
    if not hostname:
        return "HTTPS URL has no hostname"
    if port == 0:
        return "HTTPS URL port must be between 1 and 65535"
    if parsed.netloc.endswith(":"):
        return "HTTPS URL has an empty port"
    if parsed.username is not None or parsed.password is not None:
        return "refused URL containing credentials"
    return None


def cache_metadata_path(cache_dir: str | Path, url: str) -> Path:
    """Stable metadata location for a URL, without exposing URL text as a filename."""
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / "metadata" / f"{key}.json"


def _blob_path(cache_dir: Path, sha256: str) -> Path:
    return cache_dir / "blobs" / sha256[:2] / sha256


def _prepare_cache(cache_dir: Path) -> None:
    (cache_dir / "metadata").mkdir(parents=True, exist_ok=True)
    (cache_dir / "blobs").mkdir(parents=True, exist_ok=True)
    (cache_dir / ".tmp").mkdir(parents=True, exist_ok=True)


def _load_metadata(cache_dir: Path, url: str) -> CacheMetadata | None:
    path = cache_metadata_path(cache_dir, url)
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        data = cast(dict[str, object], raw)
        metadata = CacheMetadata.from_dict(data)
        if metadata.url != url:
            return None
        return metadata
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _CacheMetadataError(f"could not read cache metadata: {exc}") from exc
    except (ValueError, json.JSONDecodeError):
        # A malformed entry is not trusted for a conditional request. A fresh 200 can repair it.
        return None


def _atomic_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                data,
                handle,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _verify_blob(cache_dir: Path, metadata: CacheMetadata) -> Path | None:
    path = _blob_path(cache_dir, metadata.content_sha256)
    try:
        if path.stat().st_size != metadata.size_bytes:
            return None
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(_DEFAULT_CHUNK_SIZE):
                size += len(chunk)
                digest.update(chunk)
        if size != metadata.size_bytes or digest.hexdigest() != metadata.content_sha256:
            return None
    except OSError:
        return None
    return path


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        # Plain dict fakes are often case-sensitive, unlike HTTPMessage.
        lower_name = name.lower()
        for key, candidate in headers.items():
            if key.lower() == lower_name:
                return candidate
    return value


def _content_length(headers: Mapping[str, str]) -> int | None:
    raw = _header(headers, "Content-Length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _declared_content_type(headers: Mapping[str, str]) -> str | None:
    """The server's ``Content-Type`` declaration, verbatim, bounded and on one line.

    Kept exactly as sent, parameters and all, because this is evidence about a response rather
    than a value this module interprets. Normalizing to a bare media type here would quietly
    discard the ``charset`` a later reader may need, and deciding what the declaration *means*
    is a question for the stage that also knows whether the document parsed.
    """
    raw = _header(headers, "Content-Type")
    if raw is None:
        return None
    single_line = " ".join(raw.splitlines()).strip()
    return single_line[:_CONTENT_TYPE_TEXT_LIMIT] or None


def _declared_wire_size(headers: Mapping[str, str]) -> int | None:
    """The wire length the server declared, when the message framing makes it mean anything.

    Per RFC 9112 section 6.1 a ``Transfer-Encoding`` header overrides ``Content-Length``, and
    ``http.client`` discards the declared length outright in that case. A declared length that
    arrives beside one therefore says nothing about the body, and must not be used either to
    refuse a file as oversized before reading it or to judge the body that arrives.
    """
    if _header(headers, "Transfer-Encoding") is not None:
        return None
    return _content_length(headers)


def _short_transfer_reason(declared_size: int | None, wire_size: int) -> str | None:
    """Say how a delivered body disagrees with the length its server declared, or ``None``.

    CPython's ``http.client`` does not raise ``IncompleteRead`` when a length-delimited response
    ends early: ``HTTPResponse.read(amt)`` returns ``b""`` and closes the connection, with a
    source comment saying that raising there "might break compatibility". A file cut off
    mid-transfer therefore arrives looking exactly like a complete, smaller file, and at the size
    these files run to, the connection dropping is the likeliest failure there is. The only
    surviving evidence of the truncation is the ``Content-Length`` the server already sent, and
    checking it is the difference between a dated statement that this download did not finish and
    a published claim that a named hospital's document could not be read.

    ``Content-Length`` counts the bytes on the wire, so a gzip-encoded body is compared before it
    is decoded.
    """
    if declared_size is None or wire_size == declared_size:
        return None
    if wire_size < declared_size:
        return (
            f"the response body ended after {wire_size} of the {declared_size} bytes the server "
            f"declared in Content-Length"
        )
    return (
        f"the response body carried {wire_size} bytes against the {declared_size} the server "
        f"declared in Content-Length"
    )


def _is_gzip(headers: Mapping[str, str], final_url: str) -> bool:
    encoding = (_header(headers, "Content-Encoding") or "").lower()
    path = urlsplit(final_url).path.lower()
    return "gzip" in {part.strip() for part in encoding.split(",")} or path.endswith(".gz")


def _write_decoded(
    output: IO[bytes],
    update_digest: Callable[[bytes], None],
    data: bytes,
    *,
    size: int,
    max_bytes: int,
) -> int:
    if size + len(data) > max_bytes:
        raise _TooLargeError(f"decoded body exceeds {max_bytes} bytes")
    output.write(data)
    update_digest(data)
    return size + len(data)


def _stream_body(  # noqa: C901 - bounds, gzip members, and network causes share one read loop
    response: ResponseLike,
    output: IO[bytes],
    *,
    max_bytes: int,
    chunk_size: int,
    decode_gzip: bool,
) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    decoded_size = 0
    wire_size = 0
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if decode_gzip else None
    try:
        while chunk := response.read(chunk_size):
            wire_size += len(chunk)
            if wire_size > max_bytes:
                raise _TooLargeError(f"wire body exceeds {max_bytes} bytes")
            if decoder is None:
                decoded_size = _write_decoded(
                    output,
                    digest.update,
                    chunk,
                    size=decoded_size,
                    max_bytes=max_bytes,
                )
                continue
            pending = chunk
            while pending:
                if decoder.eof:
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                remaining = max_bytes - decoded_size
                decoded = decoder.decompress(pending, remaining + 1)
                decoded_size = _write_decoded(
                    output,
                    digest.update,
                    decoded,
                    size=decoded_size,
                    max_bytes=max_bytes,
                )
                pending = decoder.unconsumed_tail or decoder.unused_data
                if not pending:
                    break
        if decoder is not None:
            remaining = max_bytes - decoded_size
            tail = decoder.flush(remaining + 1)
            decoded_size = _write_decoded(
                output,
                digest.update,
                tail,
                size=decoded_size,
                max_bytes=max_bytes,
            )
            if not decoder.eof:
                raise _ContentError("gzip response ended before its trailer", wire_size=wire_size)
    except (HTTPException, OSError, URLError) as exc:
        raise _NetworkReadError(str(exc)) from exc
    except zlib.error as exc:
        raise _ContentError(f"invalid gzip body: {exc}", wire_size=wire_size) from exc
    return digest.hexdigest(), decoded_size, wire_size


def _body_failure(exc: Exception, declared_size: int | None) -> tuple[FetchStatus, str]:
    """Name the cause of a body that could not be stored, and therefore who it is about.

    A decoding failure is re-examined against the declared length before it is reported. A gzip
    stream that stops before its trailer is the same event as any other transfer that ended
    early, and calling it an encoding fault would make it a ``content_error``: permanent, never
    retried, and published as a claim that a publisher's file is corrupt.
    """
    if isinstance(exc, _TooLargeError):
        return FetchStatus.TOO_LARGE, str(exc)
    if isinstance(exc, _IncompleteTransferError):
        return FetchStatus.NETWORK_ERROR, str(exc)
    if isinstance(exc, _ContentError):
        short = _short_transfer_reason(declared_size, exc.wire_size)
        if short is not None:
            return FetchStatus.NETWORK_ERROR, short
        return FetchStatus.CONTENT_ERROR, str(exc)
    if isinstance(exc, _NetworkReadError):
        return FetchStatus.NETWORK_ERROR, f"network error while reading body: {exc}"
    return FetchStatus.CACHE_ERROR, f"could not write cache: {exc}"


def _install_blob(cache_dir: Path, tmp: Path, sha256: str) -> Path:
    destination = _blob_path(cache_dir, sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Replacing also repairs a truncated prior blob with the same content-addressed name.
    os.replace(tmp, destination)
    return destination


def _error_outcome(
    url: str,
    status: FetchStatus,
    attempted_at: str,
    attempts: int,
    error: str,
    *,
    http_status: int | None = None,
    final_url: str | None = None,
    content_type: str | None = None,
) -> FetchOutcome:
    return FetchOutcome(
        url=url,
        status=status,
        attempted_at=attempted_at,
        attempts=attempts,
        http_status=http_status,
        final_url=final_url,
        error=error,
        content_type=content_type,
    )


def _is_certificate_failure(reason: object) -> bool:
    """Whether a transport error was the certificate rather than the connection.

    Checked structurally first. The string fallback exists because some layers re-wrap the
    original exception into a plain ``URLError`` whose reason is only a message, and losing
    the distinction there would put the misattribution straight back.
    """
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    if isinstance(reason, ssl.SSLError):
        return "CERTIFICATE_VERIFY_FAILED" in " ".join(str(arg) for arg in reason.args)
    return "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _should_retry(outcome: FetchOutcome) -> bool:
    if outcome.status is FetchStatus.NETWORK_ERROR:
        return True
    return (
        outcome.status is FetchStatus.HTTP_ERROR and outcome.http_status in _RETRYABLE_HTTP_STATUSES
    )


def _conditional_headers(metadata: CacheMetadata | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if metadata is not None and metadata.etag:
        headers["If-None-Match"] = metadata.etag
    if metadata is not None and metadata.last_modified:
        headers["If-Modified-Since"] = metadata.last_modified
    return headers


def _not_modified(
    *,
    url: str,
    cache_dir: Path,
    metadata: CacheMetadata | None,
    headers: Mapping[str, str],
    attempted_at: str,
    attempts: int,
) -> FetchOutcome:
    if metadata is None:
        return _error_outcome(
            url,
            FetchStatus.CACHE_MISS,
            attempted_at,
            attempts,
            "server returned 304 but no cache metadata is available",
            http_status=304,
        )
    path = _verify_blob(cache_dir, metadata)
    if path is None:
        return _error_outcome(
            url,
            FetchStatus.CACHE_MISS,
            attempted_at,
            attempts,
            "server returned 304 but the cached body is missing or corrupt",
            http_status=304,
        )
    refreshed = CacheMetadata(
        url=metadata.url,
        content_sha256=metadata.content_sha256,
        size_bytes=metadata.size_bytes,
        wire_size_bytes=metadata.wire_size_bytes,
        etag=_header(headers, "ETag") or metadata.etag,
        last_modified=_header(headers, "Last-Modified") or metadata.last_modified,
        fetched_at=metadata.fetched_at,
        validated_at=attempted_at,
        final_url=metadata.final_url,
        decoded_gzip=metadata.decoded_gzip,
        # A 304 carries no body and commonly no Content-Type. The declaration that describes
        # the bytes actually being revalidated is the one made when they were downloaded, so a
        # 304 that repeats it may refresh it and a 304 that omits it must not erase it.
        content_type=_declared_content_type(headers) or metadata.content_type,
    )
    try:
        _atomic_json(cache_metadata_path(cache_dir, url), refreshed.to_dict())
    except OSError as exc:
        return _error_outcome(
            url,
            FetchStatus.CACHE_ERROR,
            attempted_at,
            attempts,
            f"could not update cache metadata after 304: {exc}",
            http_status=304,
            final_url=metadata.final_url,
        )
    return FetchOutcome(
        url=url,
        status=FetchStatus.NOT_MODIFIED,
        attempted_at=attempted_at,
        attempts=attempts,
        path=path,
        content_sha256=refreshed.content_sha256,
        size_bytes=refreshed.size_bytes,
        wire_size_bytes=0,
        etag=refreshed.etag,
        last_modified=refreshed.last_modified,
        http_status=304,
        final_url=refreshed.final_url,
        decoded_gzip=refreshed.decoded_gzip,
        content_type=refreshed.content_type,
    )


def _consume_response(
    response: ResponseLike,
    *,
    url: str,
    cache_dir: Path,
    attempted_at: str,
    attempts: int,
    policy: FetchPolicy,
) -> FetchOutcome:
    status = response.status
    final_url = response.geturl()
    # Read before any branch returns: the declaration is evidence about the response, and it is
    # most needed on exactly the paths where no usable body survives.
    content_type = _declared_content_type(response.headers)
    problem = _url_problem(final_url)
    if problem is not None:
        return _error_outcome(
            url,
            FetchStatus.INVALID_URL,
            attempted_at,
            attempts,
            f"unsafe redirect target: {problem}",
            http_status=status,
            final_url=final_url,
            content_type=content_type,
        )
    if status != 200:
        return _error_outcome(
            url,
            FetchStatus.HTTP_ERROR,
            attempted_at,
            attempts,
            f"unexpected HTTP {status}; expected 200",
            http_status=status,
            final_url=final_url,
            content_type=content_type,
        )
    declared_size = _declared_wire_size(response.headers)
    if declared_size is not None and declared_size > policy.max_bytes:
        return _error_outcome(
            url,
            FetchStatus.TOO_LARGE,
            attempted_at,
            attempts,
            f"Content-Length {declared_size} exceeds limit {policy.max_bytes}",
            http_status=status,
            final_url=final_url,
            content_type=content_type,
        )

    try:
        descriptor, raw_tmp = tempfile.mkstemp(prefix="body.", dir=cache_dir / ".tmp")
    except OSError as exc:
        return _error_outcome(
            url,
            FetchStatus.CACHE_ERROR,
            attempted_at,
            attempts,
            f"could not create cache temporary file: {exc}",
            http_status=status,
            final_url=final_url,
            content_type=content_type,
        )
    tmp = Path(raw_tmp)
    decoded_gzip = _is_gzip(response.headers, final_url)
    try:
        with os.fdopen(descriptor, "wb") as output:
            sha256, size, wire_size = _stream_body(
                response,
                output,
                max_bytes=policy.max_bytes,
                chunk_size=policy.chunk_size,
                decode_gzip=decoded_gzip,
            )
            short = _short_transfer_reason(declared_size, wire_size)
            if short is not None:
                raise _IncompleteTransferError(short)
            output.flush()
            os.fsync(output.fileno())
        path = _install_blob(cache_dir, tmp, sha256)
        metadata = CacheMetadata(
            url=url,
            content_sha256=sha256,
            size_bytes=size,
            wire_size_bytes=wire_size,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            fetched_at=attempted_at,
            validated_at=attempted_at,
            final_url=final_url,
            decoded_gzip=decoded_gzip,
            content_type=content_type,
        )
        _atomic_json(cache_metadata_path(cache_dir, url), metadata.to_dict())
    except (
        _TooLargeError,
        _ContentError,
        _NetworkReadError,
        _IncompleteTransferError,
        OSError,
    ) as exc:
        # Nothing partial survives this branch. A truncated body installed in the cache would
        # carry the server's validators with it, so the next conditional request would 304 and
        # revalidate the truncation rather than re-fetch the file.
        tmp.unlink(missing_ok=True)
        failure, message = _body_failure(exc, declared_size)
        return _error_outcome(
            url,
            failure,
            attempted_at,
            attempts,
            message,
            http_status=status,
            final_url=final_url,
            content_type=content_type,
        )
    return FetchOutcome(
        url=url,
        status=FetchStatus.FETCHED,
        attempted_at=attempted_at,
        attempts=attempts,
        path=path,
        content_sha256=sha256,
        size_bytes=size,
        wire_size_bytes=wire_size,
        etag=metadata.etag,
        last_modified=metadata.last_modified,
        http_status=status,
        final_url=final_url,
        decoded_gzip=decoded_gzip,
        content_type=content_type,
    )


def _http_error_outcome(
    exc: HTTPError,
    *,
    url: str,
    cache_dir: Path,
    metadata: CacheMetadata | None,
    attempted_at: str,
    attempts: int,
) -> FetchOutcome:
    final_url = exc.geturl()
    headers = cast(Mapping[str, str], exc.headers)
    content_type = _declared_content_type(headers)
    problem = _url_problem(final_url)
    if problem is not None:
        return _error_outcome(
            url,
            FetchStatus.INVALID_URL,
            attempted_at,
            attempts,
            f"unsafe redirect target: {problem}",
            http_status=exc.code,
            final_url=final_url,
            content_type=content_type,
        )
    if exc.code == 304:
        return _not_modified(
            url=url,
            cache_dir=cache_dir,
            metadata=metadata,
            headers=headers,
            attempted_at=attempted_at,
            attempts=attempts,
        )
    return _error_outcome(
        url,
        FetchStatus.HTTP_ERROR,
        attempted_at,
        attempts,
        f"HTTP {exc.code}: {exc.reason}",
        http_status=exc.code,
        final_url=final_url,
        content_type=content_type,
    )


def fetch_url(  # noqa: C901 - each branch is a distinct structured terminal outcome
    url: str,
    cache_dir: str | Path,
    *,
    policy: FetchPolicy,
    politeness: Politeness,
    opener: Opener | None = None,
    sleep: Sleeper = time.sleep,
    backoff: Backoff | None = None,
    clock: Clock | None = None,
) -> FetchOutcome:
    """Retrieve ``url`` into a content-addressed cache and return a structured outcome.

    Network, HTTP, cache, content, and size failures are normal return values. Invalid policy
    values remain exceptions because those are programmer errors rather than observations about a
    publisher.

    ``politeness`` is required, not optional. robots.txt is consulted before the first request
    and a disallow is terminal; there is no flag that skips it, because a flag to ignore
    robots.txt is the whole of the harm. The same object holds the per-host interval and the
    ``Retry-After`` state, so pacing applies across a whole run rather than per call.
    """
    active_clock = clock or _utc_now
    attempted_at = _timestamp(active_clock)
    problem = _url_problem(url)
    if problem is not None:
        return _error_outcome(url, FetchStatus.INVALID_URL, attempted_at, 0, problem)

    root = Path(cache_dir)
    try:
        _prepare_cache(root)
    except OSError as exc:
        return _error_outcome(
            url, FetchStatus.CACHE_ERROR, attempted_at, 0, f"could not prepare cache: {exc}"
        )
    try:
        metadata = _load_metadata(root, url)
    except _CacheMetadataError as exc:
        return _error_outcome(url, FetchStatus.CACHE_ERROR, attempted_at, 0, str(exc))
    if metadata is not None and _verify_blob(root, metadata) is None:
        # Never send validators for a body we cannot actually reuse. An unconditional 200 repairs
        # a missing or corrupt blob instead of ending in an avoidable 304 cache miss.
        metadata = None
    active_opener = opener or default_open

    # robots.txt first, always, before any request for the target itself.
    decision = politeness.clear_to_fetch(url)
    if not decision.allowed:
        return _error_outcome(
            url,
            FetchStatus.ROBOTS_DISALLOWED,
            attempted_at,
            0,
            f"{decision.status.value}: {decision.reason} ({decision.robots_url})",
            http_status=decision.http_status,
        )

    headers = {
        "User-Agent": policy.identifying_user_agent,
        "Accept-Encoding": "gzip",
        **_conditional_headers(metadata),
    }

    last_outcome: FetchOutcome | None = None
    for attempt in range(1, policy.retries + 2):
        response: ResponseLike | None = None
        retry_headers: Mapping[str, str] = {}
        politeness.wait_turn(url, crawl_delay_seconds=decision.crawl_delay_seconds)
        try:
            # S310 is addressed by _url_problem immediately above; only HTTPS reaches this point.
            request = Request(url, headers=headers, method="GET")  # noqa: S310
            response = active_opener(request, timeout=policy.timeout_seconds)
            retry_headers = response.headers
            final_problem = _url_problem(response.geturl())
            if final_problem is not None:
                outcome = _error_outcome(
                    url,
                    FetchStatus.INVALID_URL,
                    attempted_at,
                    attempt,
                    f"unsafe redirect target: {final_problem}",
                    http_status=response.status,
                    final_url=response.geturl(),
                    content_type=_declared_content_type(response.headers),
                )
            elif response.status == 304:
                outcome = _not_modified(
                    url=url,
                    cache_dir=root,
                    metadata=metadata,
                    headers=response.headers,
                    attempted_at=attempted_at,
                    attempts=attempt,
                )
            else:
                outcome = _consume_response(
                    response,
                    url=url,
                    cache_dir=root,
                    attempted_at=attempted_at,
                    attempts=attempt,
                    policy=policy,
                )
        except _UnsafeRedirectError as exc:
            try:
                outcome = _error_outcome(
                    url,
                    FetchStatus.INVALID_URL,
                    attempted_at,
                    attempt,
                    str(exc.reason),
                    http_status=exc.code,
                    final_url=exc.target,
                )
            finally:
                exc.close()
        except (UnicodeError, ValueError) as exc:
            outcome = _error_outcome(
                url,
                FetchStatus.INVALID_URL,
                attempted_at,
                0,
                f"invalid URL: {exc}",
            )
        except HTTPError as exc:
            try:
                retry_headers = cast(Mapping[str, str], exc.headers)
                outcome = _http_error_outcome(
                    exc,
                    url=url,
                    cache_dir=root,
                    metadata=metadata,
                    attempted_at=attempted_at,
                    attempts=attempt,
                )
            finally:
                exc.close()
        except InvalidURL as exc:
            outcome = _error_outcome(
                url,
                FetchStatus.INVALID_URL,
                attempted_at,
                0,
                f"invalid URL: {exc}",
            )
        except (HTTPException, URLError, TimeoutError, OSError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            # A certificate that will not verify is NOT a publisher failure, because from one
            # attempt this client cannot tell the two causes apart: the server's chain may be
            # broken, or this machine's trust store may be missing a root. Both were observed
            # on 2026-08-15, when two hosts recorded as unreachable in the 2026-08-14 cohort
            # turned out to serve HTTP 200 with a clean chain to curl on the same machine at
            # the same minute -- the Python build was verifying against an OpenSSL bundle that
            # lacked the roots. Calling that a publisher failure publishes an ERROR finding
            # citing 45 CFR 180.50 against a hospital whose file is fine.
            if _is_certificate_failure(reason):
                outcome = _error_outcome(
                    url,
                    FetchStatus.TLS_VERIFICATION_FAILED,
                    attempted_at,
                    attempt,
                    f"TLS certificate verification failed: {reason}",
                )
            else:
                outcome = _error_outcome(
                    url,
                    FetchStatus.NETWORK_ERROR,
                    attempted_at,
                    attempt,
                    f"network error: {reason}",
                )
        finally:
            if response is not None:
                response.close()

        last_outcome = outcome
        if not _should_retry(outcome) or attempt > policy.retries:
            return outcome
        # A server's Retry-After outranks this tool's own backoff curve: backoff is a policy
        # we picked, Retry-After is the instruction we were given. The deferral is recorded on
        # the pacer so it also holds for any later request to the same host in this run.
        asked = politeness.observe_retry_after(url, outcome.http_status or 0, retry_headers)
        if asked is not None:
            sleep(asked)
            continue
        delay = (
            backoff(attempt) if backoff is not None else policy.backoff_seconds * 2 ** (attempt - 1)
        )
        sleep(delay)

    if last_outcome is None:  # pragma: no cover - policy validation guarantees at least one pass
        raise RuntimeError("fetch attempt loop did not run")
    return last_outcome


# --- the format probe -------------------------------------------------------------------------


#: How many leading bytes one probe asks for. Enough to see a BOM, a JSON opener, a ZIP local
#: file header, an HTML doctype, or the CMS CSV general-element header row.
PROBE_SAMPLE_BYTES = 4_096

_HTML_MARKERS = (b"<!doctype", b"<html")
_CSV_GENERAL_HEADER_PREFIX = b"hospital_name"


@dataclass(frozen=True)
class ProbeOutcome:
    """The complete, serializable result of one bounded format probe.

    A probe answers one narrow question in kilobytes: *what kind of document does this URL
    serve?* It exists because the 2026-08-19 cohort had to download 669,479,338 bytes to learn
    that four extensionless targets were CSV (docs/ROADMAP.md). It is classification evidence
    for routing a target to an assessment profile; it is never a grading input, it never touches
    the cache, and a probe that fails says nothing about the publisher.
    """

    url: str
    attempted_at: str
    status: str
    http_status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    declared_size: int | None = None
    bytes_sampled: int = 0
    range_honored: bool | None = None
    sniffed: str | None = None
    starts_with_csv_general_header: bool | None = None
    sample_sha256: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "attempted_at": self.attempted_at,
            "status": self.status,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "content_type": self.content_type,
            "declared_size": self.declared_size,
            "bytes_sampled": self.bytes_sampled,
            "range_honored": self.range_honored,
            "sniffed": self.sniffed,
            "starts_with_csv_general_header": self.starts_with_csv_general_header,
            "sample_sha256": self.sample_sha256,
            "error": self.error,
        }


def _sniff_sample(sample: bytes) -> tuple[str, bool]:
    """Classify leading bytes by what they are, not by what a header claims they are."""
    body = sample.removeprefix(b"\xef\xbb\xbf")
    if not body.strip():
        return "empty", False
    if body.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "zip", False
    if body.startswith(b"\x1f\x8b"):
        return "gzip", False
    stripped = body.lstrip()
    if stripped.startswith((b"{", b"[")):
        return "json", False
    if stripped[:64].lower().startswith(_HTML_MARKERS):
        return "html", False
    if b"\x00" in body:
        return "binary", False
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("latin-1")
    header_like = (
        text.lstrip().lstrip('"').lower().startswith(_CSV_GENERAL_HEADER_PREFIX.decode("ascii"))
    )
    return "text", header_like


def _declared_total_size(headers: Mapping[str, str], http_status: int) -> int | None:
    content_range = _header(headers, "Content-Range")
    if content_range is not None:
        _, _, total = content_range.partition("/")
        total = total.strip()
        if total.isdigit():
            return int(total)
        return None
    if http_status == 200:
        return _content_length(headers)
    return None


def _consume_probe_response(
    url: str, attempted_at: str, response: ResponseLike, sample_bytes: int
) -> ProbeOutcome:
    final_problem = _url_problem(response.geturl())
    if final_problem is not None:
        return ProbeOutcome(
            url=url,
            attempted_at=attempted_at,
            status="invalid_url",
            http_status=response.status,
            final_url=response.geturl(),
            content_type=_declared_content_type(response.headers),
            error=f"unsafe redirect target: {final_problem}",
        )
    sample = response.read(sample_bytes)
    sniffed, header_like = _sniff_sample(sample)
    return ProbeOutcome(
        url=url,
        attempted_at=attempted_at,
        status="probed",
        http_status=response.status,
        final_url=response.geturl(),
        content_type=_declared_content_type(response.headers),
        declared_size=_declared_total_size(response.headers, response.status),
        bytes_sampled=len(sample),
        range_honored=response.status == 206,
        sniffed=sniffed,
        starts_with_csv_general_header=header_like,
        sample_sha256=hashlib.sha256(sample).hexdigest(),
    )


def probe_url(
    url: str,
    *,
    policy: FetchPolicy,
    politeness: Politeness,
    opener: Opener | None = None,
    clock: Clock | None = None,
    sample_bytes: int = PROBE_SAMPLE_BYTES,
) -> ProbeOutcome:
    """Classify what one URL serves with a single bounded ranged request.

    The request asks for the first ``sample_bytes`` bytes and identity encoding. A server that
    ignores the Range header is read up to the same bound and the connection is closed, so the
    load stays a sample either way; ``range_honored`` records which happened. robots.txt is
    consulted first exactly as for a full retrieval, with no override.
    """
    if sample_bytes <= 0:
        raise ValueError("sample_bytes must be positive")
    attempted_at = _timestamp(clock or _utc_now)
    problem = _url_problem(url)
    if problem is not None:
        return ProbeOutcome(url=url, attempted_at=attempted_at, status="invalid_url", error=problem)

    decision = politeness.clear_to_fetch(url)
    if not decision.allowed:
        return ProbeOutcome(
            url=url,
            attempted_at=attempted_at,
            status="robots_disallowed",
            http_status=decision.http_status,
            error=f"{decision.status.value}: {decision.reason} ({decision.robots_url})",
        )

    headers = {
        "User-Agent": policy.identifying_user_agent,
        "Accept-Encoding": "identity",
        "Range": f"bytes=0-{sample_bytes - 1}",
    }
    politeness.wait_turn(url, crawl_delay_seconds=decision.crawl_delay_seconds)
    response: ResponseLike | None = None
    try:
        # S310 is addressed by _url_problem immediately above; only HTTPS reaches this point.
        request = Request(url, headers=headers, method="GET")  # noqa: S310
        response = (opener or default_open)(request, timeout=policy.timeout_seconds)
        return _consume_probe_response(url, attempted_at, response, sample_bytes)
    except _UnsafeRedirectError as exc:
        try:
            return ProbeOutcome(
                url=url,
                attempted_at=attempted_at,
                status="invalid_url",
                http_status=exc.code,
                final_url=exc.target,
                error=str(exc.reason),
            )
        finally:
            exc.close()
    except HTTPError as exc:
        try:
            return ProbeOutcome(
                url=url,
                attempted_at=attempted_at,
                status="http_error",
                http_status=exc.code,
                content_type=_declared_content_type(dict(exc.headers.items())),
                error=f"HTTP {exc.code}: {exc.reason}",
            )
        finally:
            exc.close()
    except (TimeoutError, HTTPException, OSError, UnicodeError, ValueError) as exc:
        cause = exc.reason if isinstance(exc, URLError) else exc
        if _is_certificate_failure(cause):
            return ProbeOutcome(
                url=url,
                attempted_at=attempted_at,
                status="tls_verification_failed",
                error=f"certificate verification failed: {cause}",
            )
        return ProbeOutcome(
            url=url, attempted_at=attempted_at, status="network_error", error=str(cause)
        )
    finally:
        if response is not None:
            response.close()
