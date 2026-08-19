"""Behavioral tests for the bounded format probe."""

from __future__ import annotations

from urllib.error import HTTPError
from urllib.request import Request

import pytest
import robots_fixtures

from mrf_honest.fetch import FetchPolicy, ProbeOutcome, probe_url
from mrf_honest.types import ResponseLike

URL = "https://files.example.test/standardcharges"

CSV_HEAD = (
    b"\xef\xbb\xbfhospital_name,last_updated_on,version,location_name,hospital_address,"
    b'license_number|CA,type_2_npi,"attestation...",attester_name\r\n'
)


class _ProbeResponse:
    """A ResponseLike that can honor or ignore a Range request."""

    def __init__(
        self,
        body: bytes,
        *,
        url: str = URL,
        status: int = 206,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers if headers is not None else {}
        self._url = url
        self.reads: list[int] = []
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        self.reads.append(amount)
        if amount < 0:
            return self._body
        return self._body[:amount]

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


class _Opener:
    def __init__(self, response: ResponseLike | Exception) -> None:
        self._response = response
        self.requests: list[Request] = []

    def __call__(self, request: Request, *, timeout: float) -> ResponseLike:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _policy() -> FetchPolicy:
    return FetchPolicy(contact="ops@example.test")


def _probe(response: ResponseLike | Exception, **kwargs: object) -> ProbeOutcome:
    opener = _Opener(response)
    outcome = probe_url(
        URL,
        policy=_policy(),
        politeness=robots_fixtures.politeness(),
        opener=opener,
        **kwargs,  # type: ignore[arg-type]
    )
    return outcome


def test_a_ranged_sample_is_requested_and_classified() -> None:
    response = _ProbeResponse(
        CSV_HEAD,
        status=206,
        headers={"Content-Type": "text/csv", "Content-Range": "bytes 0-4095/34786529"},
    )
    opener = _Opener(response)
    outcome = probe_url(
        URL, policy=_policy(), politeness=robots_fixtures.politeness(), opener=opener
    )
    assert outcome.status == "probed"
    assert outcome.range_honored is True
    assert outcome.sniffed == "text"
    assert outcome.starts_with_csv_general_header is True
    assert outcome.declared_size == 34786529
    assert outcome.content_type == "text/csv"
    request = opener.requests[0]
    assert request.get_header("Range") == "bytes=0-4095"
    assert request.get_header("Accept-encoding") == "identity"
    assert response.closed is True


def test_a_server_that_ignores_range_is_read_up_to_the_bound_only() -> None:
    body = b"{" + b" " * 100_000
    response = _ProbeResponse(body, status=200, headers={"Content-Length": str(len(body))})
    outcome = _probe(response, sample_bytes=1_024)
    assert outcome.status == "probed"
    assert outcome.range_honored is False
    assert outcome.bytes_sampled == 1_024
    assert outcome.sniffed == "json"
    assert outcome.declared_size == len(body)
    assert response.reads == [1_024]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"PK\x03\x04rest-of-zip", "zip"),
        (b"\x1f\x8b\x08gzip-members", "gzip"),
        (b'  {"hospital_name": "x"}', "json"),
        (b"<!DOCTYPE html><html>", "html"),
        (b"plain,comma,text\r\n", "text"),
        (b"\x00\x01\x02\x03", "binary"),
        (b"", "empty"),
    ],
)
def test_leading_bytes_decide_the_classification(body: bytes, expected: str) -> None:
    response = _ProbeResponse(body, status=206)
    outcome = _probe(response)
    assert outcome.sniffed == expected


def test_a_bom_is_stripped_before_classification() -> None:
    outcome = _probe(_ProbeResponse(b"\xef\xbb\xbf[1]", status=206))
    assert outcome.sniffed == "json"


def test_robots_disallow_stops_the_probe_before_any_request() -> None:
    opener = _Opener(_ProbeResponse(b"{}"))
    outcome = probe_url(
        URL,
        policy=_policy(),
        politeness=robots_fixtures.politeness("User-agent: *\nDisallow: /\n"),
        opener=opener,
    )
    assert outcome.status == "robots_disallowed"
    assert opener.requests == []


def test_an_http_error_is_recorded_not_raised() -> None:
    import email.message
    import io

    headers = email.message.Message()
    headers["Content-Type"] = "text/html"
    error = HTTPError(URL, 403, "Forbidden", headers, io.BytesIO(b""))
    outcome = _probe(error)
    assert outcome.status == "http_error"
    assert outcome.http_status == 403
    assert outcome.content_type == "text/html"


def test_an_invalid_url_never_reaches_the_network() -> None:
    opener = _Opener(_ProbeResponse(b"{}"))
    outcome = probe_url(
        "http://insecure.example.test/prices.csv",
        policy=_policy(),
        politeness=robots_fixtures.politeness(),
        opener=opener,
    )
    assert outcome.status == "invalid_url"
    assert opener.requests == []


def test_the_outcome_serializes_to_json_safe_values() -> None:
    outcome = _probe(_ProbeResponse(CSV_HEAD, status=206))
    payload = outcome.to_dict()
    assert payload["status"] == "probed"
    assert isinstance(payload["sample_sha256"], str)
    assert payload["starts_with_csv_general_header"] is True
