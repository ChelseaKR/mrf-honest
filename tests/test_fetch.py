from __future__ import annotations

import gzip
import hashlib
import io
import json
import ssl
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
import robots_fixtures

from mrf_honest import fetch as fetch_module
from mrf_honest.fetch import (
    CacheMetadata,
    FetchOutcome,
    FetchPolicy,
    FetchStatus,
    ResponseLike,
    cache_metadata_path,
    fetch_url,
)

NOW = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        final_url: str = "https://example.test/prices.json",
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.final_url = final_url
        self.position = 0
        self.read_calls = 0
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        self.read_calls += 1
        if amount < 0:
            amount = len(self.body) - self.position
        result = self.body[self.position : self.position + amount]
        self.position += len(result)
        return result

    def geturl(self) -> str:
        return self.final_url

    def close(self) -> None:
        self.closed = True


class BrokenResponse(FakeResponse):
    def read(self, amount: int = -1) -> bytes:
        raise OSError("connection reset")


class ShortResponse(FakeResponse):
    """A length-delimited response that stops early without raising.

    This is not a contrived double. CPython's ``http.client.HTTPResponse.read(amt)`` returns
    ``b""`` and closes the connection when a length-delimited body ends early rather than
    raising ``IncompleteRead``, so a truncated download really does look like this from above.
    """

    def __init__(
        self,
        body: bytes,
        *,
        delivered: int,
        declared: int | None = None,
        extra_headers: dict[str, str] | None = None,
        final_url: str = "https://example.test/prices.json",
    ) -> None:
        headers = {"Content-Length": str(len(body) if declared is None else declared)}
        headers.update(extra_headers or {})
        super().__init__(body, headers=headers, final_url=final_url)
        self.delivered = delivered

    def read(self, amount: int = -1) -> bytes:
        self.read_calls += 1
        if amount < 0:
            amount = self.delivered - self.position
        end = min(self.position + amount, self.delivered)
        result = self.body[self.position : end]
        self.position += len(result)
        return result


class FakeOpener:
    def __init__(self, *results: ResponseLike | Exception) -> None:
        self.results = list(results)
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, *, timeout: float) -> ResponseLike:
        self.requests.append(request)
        self.timeouts.append(timeout)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def policy(**changes: object) -> FetchPolicy:
    values: dict[str, object] = {
        "contact": "maintainer@example.test",
        "retries": 0,
        "max_bytes": 1024,
        "chunk_size": 3,
    }
    values.update(changes)
    return FetchPolicy(**values)  # type: ignore[arg-type]


def clock() -> datetime:
    return NOW


def test_fetch_streams_to_content_addressed_cache_with_identifying_headers(
    tmp_path: Path,
) -> None:
    body = b'{"price":123}'
    response = FakeResponse(
        body,
        headers={
            "ETag": '"v1"',
            "Last-Modified": "Sat, 08 Aug 2026 12:00:00 GMT",
        },
    )
    opener = FakeOpener(response)

    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=opener,
        clock=clock,
    )

    digest = hashlib.sha256(body).hexdigest()
    assert outcome.status is FetchStatus.FETCHED
    assert outcome.ok and outcome.path is not None
    assert outcome.path.name == digest
    assert outcome.path.read_bytes() == body
    assert outcome.size_bytes == len(body)
    assert outcome.wire_size_bytes == len(body)
    assert outcome.attempted_at == "2026-08-09T12:30:00Z"
    assert response.read_calls > 2 and response.closed
    assert "maintainer@example.test" in opener.requests[0].get_header("User-agent")
    assert opener.requests[0].get_header("Accept-encoding") == "gzip"
    metadata = json.loads(cache_metadata_path(tmp_path, outcome.url).read_text(encoding="utf-8"))
    assert metadata["content_sha256"] == digest
    assert metadata["etag"] == '"v1"'


def test_conditional_get_reuses_a_verified_cached_body(tmp_path: Path) -> None:
    url = "https://example.test/prices.json"
    first = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(
            FakeResponse(
                b"content",
                headers={"ETag": '"abc"', "Last-Modified": "yesterday"},
            )
        ),
        clock=clock,
    )
    second_opener = FakeOpener(FakeResponse(status=304, headers={"ETag": '"abc"'}))

    second = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=second_opener,
        clock=clock,
    )

    assert second.status is FetchStatus.NOT_MODIFIED
    assert second.from_cache and second.path == first.path
    request = second_opener.requests[0]
    assert request.get_header("If-none-match") == '"abc"'
    assert request.get_header("If-modified-since") == "yesterday"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/a.json",
        "file:///tmp/a.json",
        "https://user:secret@example.test/a.json",
    ],
)
def test_only_safe_https_urls_are_attempted(tmp_path: Path, url: str) -> None:
    opener = FakeOpener(FakeResponse())
    outcome = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=opener,
        clock=clock,
    )
    assert outcome.status is FetchStatus.INVALID_URL
    assert outcome.attempts == 0
    assert not opener.requests


def test_https_redirect_to_plaintext_is_refused(tmp_path: Path) -> None:
    response = FakeResponse(final_url="http://example.test/prices.json")
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(response),
        clock=clock,
    )
    assert outcome.status is FetchStatus.INVALID_URL
    assert "redirect" in (outcome.error or "")
    assert response.read_calls == 0


def test_declared_and_streamed_size_overruns_are_named(tmp_path: Path) -> None:
    declared = FakeResponse(b"not read", headers={"Content-Length": "20"})
    declared_outcome = fetch_url(
        "https://example.test/declared.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=10),
        opener=FakeOpener(declared),
        clock=clock,
    )
    streamed_outcome = fetch_url(
        "https://example.test/streamed.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=5),
        opener=FakeOpener(FakeResponse(b"123456")),
        clock=clock,
    )
    assert declared_outcome.status is FetchStatus.TOO_LARGE
    assert declared.read_calls == 0
    assert streamed_outcome.status is FetchStatus.TOO_LARGE
    assert not list((tmp_path / ".tmp").iterdir())


def test_a_body_shorter_than_its_declared_length_is_not_a_fetched_file(tmp_path: Path) -> None:
    body = b'{"standard_charge_information":[{"description":"Example"}]}'
    response = ShortResponse(body, delivered=20)
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=1024),
        opener=FakeOpener(response),
        clock=clock,
    )
    assert outcome.status is FetchStatus.NETWORK_ERROR
    assert not outcome.ok
    assert outcome.path is None
    # Both numbers are in the reason, because this sentence is published verbatim as the
    # retrievability finding on a page that names a hospital.
    assert outcome.error == (
        f"the response body ended after 20 of the {len(body)} bytes the server declared in "
        "Content-Length"
    )
    # The partial bytes must not survive anywhere: a cached truncated blob plus the server's
    # ETag would let a later 304 revalidate the truncation into a permanent record.
    assert not cache_metadata_path(tmp_path, "https://example.test/prices.json").exists()
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_a_transfer_that_ends_early_is_retried_before_it_is_recorded(tmp_path: Path) -> None:
    body = b"0123456789"
    opener = FakeOpener(ShortResponse(body, delivered=4), FakeResponse(body))
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=1, backoff_seconds=0.0),
        opener=opener,
        sleep=lambda _seconds: None,
        clock=clock,
    )
    assert outcome.status is FetchStatus.FETCHED
    assert outcome.attempts == 2
    assert outcome.path is not None and outcome.path.read_bytes() == body


def test_a_body_longer_than_its_declared_length_is_not_a_fetched_file(tmp_path: Path) -> None:
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=0),
        opener=FakeOpener(ShortResponse(b"0123456789", delivered=10, declared=4)),
        clock=clock,
    )
    assert outcome.status is FetchStatus.NETWORK_ERROR
    assert outcome.error == (
        "the response body carried 10 bytes against the 4 the server declared in Content-Length"
    )


def test_a_declared_length_is_ignored_when_the_transfer_is_chunked(tmp_path: Path) -> None:
    # RFC 9112 section 6.1: Transfer-Encoding overrides Content-Length, and http.client drops
    # the declared length outright. Trusting it here would do two wrong things to a chunked
    # file: refuse it as oversized before reading a byte (the declared 99999 is above this
    # policy's 1024 ceiling), and then call the body that did arrive truncated.
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(
            FakeResponse(
                b"0123456789",
                headers={"Content-Length": "99999", "Transfer-Encoding": "chunked"},
            )
        ),
        clock=clock,
    )
    assert outcome.status is FetchStatus.FETCHED
    assert outcome.path is not None and outcome.path.read_bytes() == b"0123456789"


def test_a_declared_length_is_compared_against_the_wire_body_not_the_decoded_one(
    tmp_path: Path,
) -> None:
    body = b"abcdefghij" * 8
    encoded = gzip.compress(body)
    assert len(encoded) != len(body)
    complete = fetch_url(
        "https://example.test/prices.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=1024),
        opener=FakeOpener(
            FakeResponse(
                encoded,
                headers={"Content-Length": str(len(encoded))},
                final_url="https://example.test/prices.json.gz",
            )
        ),
        clock=clock,
    )
    cut = fetch_url(
        "https://example.test/cut.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=1024, retries=0),
        opener=FakeOpener(
            ShortResponse(
                encoded,
                delivered=len(encoded) - 5,
                final_url="https://example.test/cut.json.gz",
            )
        ),
        clock=clock,
    )
    assert complete.status is FetchStatus.FETCHED
    assert complete.path is not None and complete.path.read_bytes() == body
    # The gzip trailer check would also catch this one; the point is which cause is reported,
    # because "the download stopped early" and "the file is not valid gzip" are different
    # statements about a publisher.
    assert cut.status is FetchStatus.NETWORK_ERROR
    assert "Content-Length" in (cut.error or "")


def test_gzip_is_decoded_incrementally_and_bounded_after_decoding(tmp_path: Path) -> None:
    body = b"abcdefghij"
    encoded = gzip.compress(body)
    outcome = fetch_url(
        "https://example.test/prices.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=100),
        opener=FakeOpener(FakeResponse(encoded, final_url="https://example.test/prices.json.gz")),
        clock=clock,
    )
    too_large = fetch_url(
        "https://example.test/large.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=9),
        opener=FakeOpener(FakeResponse(encoded, final_url="https://example.test/large.json.gz")),
        clock=clock,
    )
    assert outcome.ok and outcome.path is not None
    assert outcome.decoded_gzip and outcome.path.read_bytes() == body
    assert too_large.status is FetchStatus.TOO_LARGE


def test_concatenated_gzip_members_work_and_trailing_junk_does_not(tmp_path: Path) -> None:
    joined = gzip.compress(b"first") + gzip.compress(b"second")
    good = fetch_url(
        "https://example.test/joined.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=100),
        opener=FakeOpener(FakeResponse(joined, final_url="https://example.test/joined.json.gz")),
        clock=clock,
    )
    bad = fetch_url(
        "https://example.test/junk.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=100),
        opener=FakeOpener(
            FakeResponse(
                gzip.compress(b"ok") + b"junk", final_url="https://example.test/junk.json.gz"
            )
        ),
        clock=clock,
    )
    truncated = fetch_url(
        "https://example.test/truncated.json.gz",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(max_bytes=100),
        opener=FakeOpener(
            FakeResponse(
                gzip.compress(b"ok")[:-3], final_url="https://example.test/truncated.json.gz"
            )
        ),
        clock=clock,
    )
    assert good.path is not None and good.path.read_bytes() == b"firstsecond"
    assert bad.status is FetchStatus.CONTENT_ERROR
    assert truncated.status is FetchStatus.CONTENT_ERROR


def test_network_and_retryable_http_errors_have_injectable_backoff(tmp_path: Path) -> None:
    delays: list[float] = []
    opener = FakeOpener(URLError("dns failure"), FakeResponse(b"ok"))
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=1),
        opener=opener,
        sleep=delays.append,
        backoff=lambda attempt: float(attempt + 4),
        clock=clock,
    )
    http = fetch_url(
        "https://example.test/missing.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(FakeResponse(status=404)),
        clock=clock,
    )
    assert outcome.ok and outcome.attempts == 2
    assert delays == [5.0]
    assert http.status is FetchStatus.HTTP_ERROR
    assert http.http_status == 404 and "404" in (http.error or "")


@pytest.mark.parametrize("status", [202, 204, 206])
def test_only_complete_200_responses_are_cached(tmp_path: Path, status: int) -> None:
    response = FakeResponse(b"partial or asynchronous", status=status)

    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(response),
        clock=clock,
    )

    assert outcome.status is FetchStatus.HTTP_ERROR
    assert outcome.http_status == status
    assert response.read_calls == 0
    assert not list((tmp_path / "blobs").rglob("*"))


def test_body_read_error_and_retryable_status_use_named_retry_path(tmp_path: Path) -> None:
    delays: list[float] = []
    read_failure = fetch_url(
        "https://example.test/read.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(BrokenResponse()),
        clock=clock,
    )
    retried = fetch_url(
        "https://example.test/retry.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=1, backoff_seconds=0.25),
        opener=FakeOpener(FakeResponse(status=503), FakeResponse(b"recovered")),
        sleep=delays.append,
        clock=clock,
    )
    assert read_failure.status is FetchStatus.NETWORK_ERROR
    assert retried.ok and retried.attempts == 2
    assert delays == [0.25]


def test_http_error_objects_and_bare_304_are_structured(tmp_path: Path) -> None:
    url = "https://example.test/error.json"
    headers = Message()
    http_error = HTTPError(url, 429, "slow down", headers, io.BytesIO())
    errored = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(http_error),
        clock=clock,
    )
    no_cache = fetch_url(
        "https://example.test/no-cache.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(FakeResponse(status=304)),
        clock=clock,
    )
    assert errored.status is FetchStatus.HTTP_ERROR and errored.http_status == 429
    assert no_cache.status is FetchStatus.CACHE_MISS


def test_http_error_at_an_unsafe_final_url_is_rejected(tmp_path: Path) -> None:
    unsafe = HTTPError(
        "http://example.test/downgraded.json",
        404,
        "missing",
        Message(),
        io.BytesIO(),
    )

    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(unsafe),
        clock=clock,
    )

    assert outcome.status is FetchStatus.INVALID_URL
    assert outcome.final_url == "http://example.test/downgraded.json"


def test_corrupt_cache_is_repaired_with_an_unconditional_request(tmp_path: Path) -> None:
    url = "https://example.test/prices.json"
    first = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(FakeResponse(b"original", headers={"ETag": '"old"'})),
        clock=clock,
    )
    assert first.path is not None
    first.path.write_bytes(b"corrupt!")
    repair_opener = FakeOpener(FakeResponse(b"repaired", headers={"ETag": '"new"'}))
    repaired = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=repair_opener,
        clock=clock,
    )
    assert repaired.path is not None and repaired.path.read_bytes() == b"repaired"
    assert repair_opener.requests[0].get_header("If-none-match") is None


def test_default_redirect_handler_refuses_downgrade_before_following() -> None:
    headers = Message()
    handler = fetch_module._HTTPSOnlyRedirectHandler()
    request = Request("https://example.test/start")
    with pytest.raises(HTTPError, match="unsafe redirect"):
        handler.redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            headers,
            "http://example.test/target",
        )
    redirected = handler.redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        headers,
        "https://example.test/target",
    )
    assert redirected is not None and redirected.full_url.endswith("/target")


def test_fetch_outcome_round_trips_for_registry_storage(tmp_path: Path) -> None:
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(FakeResponse(b"ok")),
        clock=clock,
    )
    assert FetchOutcome.from_dict(outcome.to_dict()) == outcome


def test_policy_requires_a_real_contact() -> None:
    with pytest.raises(ValueError, match="contact"):
        FetchPolicy(contact="anonymous")


@pytest.mark.parametrize(
    "changes",
    [
        {"user_agent": ""},
        {"max_bytes": 0},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"retries": -1},
        {"backoff_seconds": -1},
        {"backoff_seconds": float("nan")},
        {"backoff_seconds": float("inf")},
        {"chunk_size": 0},
    ],
)
def test_policy_rejects_invalid_limits(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        policy(**changes)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/a b",
        "https:///missing-host",
        "https://example.test:/empty-port",
        "https://example.test:0/zero-port",
        "https://example.test:99999/bad-port",
        "https://example.test/\x7f.json",
    ],
)
def test_malformed_https_urls_are_structured(tmp_path: Path, url: str) -> None:
    outcome = fetch_url(
        url,
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=FakeOpener(),
        clock=clock,
    )
    assert outcome.status is FetchStatus.INVALID_URL
    assert outcome.attempts == 0


def test_serializers_reject_wrong_types_and_cache_versions() -> None:
    with pytest.raises(ValueError):
        FetchOutcome.from_dict({"url": 1})
    with pytest.raises(ValueError, match="version"):
        CacheMetadata.from_dict({"version": 2})
    with pytest.raises(ValueError, match="digest"):
        CacheMetadata.from_dict({"version": 1, "content_sha256": "not-a-digest"})


def test_cache_metadata_io_failure_stops_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = FakeOpener(FakeResponse(b"must not be read"))

    def fail_metadata(_cache: Path, _url: str) -> CacheMetadata | None:
        raise fetch_module._CacheMetadataError("could not read cache metadata: denied")

    monkeypatch.setattr(fetch_module, "_load_metadata", fail_metadata)
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=opener,
        clock=clock,
    )

    assert outcome.status is FetchStatus.CACHE_ERROR
    assert outcome.attempts == 0
    assert not opener.requests


def test_a_certificate_failure_is_not_recorded_as_a_publisher_failure(tmp_path: Path) -> None:
    """The defect this status exists for.

    On 2026-08-15 two hosts recorded as ``txt_fetch_failed`` in the 2026-08-14 cohort were
    re-probed: both returned HTTP 200 with ``ssl_verify=0`` to curl on the same machine at the
    same minute, while Python raised ``CERTIFICATE_VERIFY_FAILED`` because its OpenSSL bundle
    lacked the roots. Classified as ``network_error`` that becomes an ERROR-severity
    ``MRF_DIRECT_DOWNLOAD_FAILED`` citing 45 CFR 180.50 -- a published claim about a hospital,
    caused by the operator's laptop.
    """
    real = ssl.SSLCertVerificationError(
        1,
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed "
        "certificate in certificate chain (_ssl.c:1081)",
    )
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=0),
        opener=FakeOpener(URLError(real)),
        clock=clock,
    )
    assert outcome.status is FetchStatus.TLS_VERIFICATION_FAILED
    assert outcome.status is not FetchStatus.NETWORK_ERROR
    assert "certificate verification failed" in (outcome.error or "")


def test_a_certificate_failure_is_not_retried(tmp_path: Path) -> None:
    """Three attempts will not grow a missing root. Retrying is noise the host pays for."""
    slept: list[float] = []
    opener = FakeOpener(
        URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] nope")),
        FakeResponse(b'{"never":"reached"}'),
    )
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=3),
        opener=opener,
        sleep=slept.append,
        clock=clock,
    )
    assert outcome.status is FetchStatus.TLS_VERIFICATION_FAILED
    assert outcome.attempts == 1
    assert len(opener.requests) == 1
    assert slept == []


def test_an_ordinary_network_error_is_still_a_network_error(tmp_path: Path) -> None:
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=0),
        opener=FakeOpener(URLError("dns failure")),
        clock=clock,
    )
    assert outcome.status is FetchStatus.NETWORK_ERROR


def test_a_rewrapped_certificate_error_is_still_recognised(tmp_path: Path) -> None:
    """Some layers flatten the cause to a message; the distinction must survive that."""
    outcome = fetch_url(
        "https://example.test/prices.json",
        tmp_path,
        politeness=robots_fixtures.politeness(),
        policy=policy(retries=0),
        opener=FakeOpener(URLError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer")),
        clock=clock,
    )
    assert outcome.status is FetchStatus.TLS_VERIFICATION_FAILED
