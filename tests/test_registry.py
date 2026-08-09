from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

import pytest

from mrf_honest.fetch import FetchPolicy, FetchStatus, ResponseLike
from mrf_honest.registry import (
    AttemptKind,
    Registry,
    RegistryError,
    discover_domain,
    fetch_and_record,
)

NOW = datetime(2026, 8, 9, tzinfo=UTC)


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "https://hospital.test/cms-hpt.txt",
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.url = url
        self.position = 0
        self.read_calls = 0

    def read(self, amount: int = -1) -> bytes:
        self.read_calls += 1
        if amount < 0:
            amount = len(self.body) - self.position
        chunk = self.body[self.position : self.position + amount]
        self.position += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        pass


class OneResponse:
    def __init__(self, response: ResponseLike) -> None:
        self.response = response
        self.request: Request | None = None

    def __call__(self, request: Request, *, timeout: float) -> ResponseLike:
        self.request = request
        return self.response


def policy(*, max_bytes: int = 1 << 30) -> FetchPolicy:
    return FetchPolicy(contact="owner@example.test", retries=0, max_bytes=max_bytes)


def clock() -> datetime:
    return NOW


def test_discover_domain_composes_fetch_parser_and_append_only_log(tmp_path: Path) -> None:
    text = b"\n".join(
        [
            b"location-name: Hospital",
            b"source-page-url: https://hospital.test/prices",
            b"mrf-url: https://hospital.test/123456789_hospital_standardcharges.json",
        ]
    )
    opener = OneResponse(Response(text))
    registry = Registry(tmp_path / "registry.jsonl")

    record = discover_domain(
        "hospital.test",
        registry=registry,
        cache_dir=tmp_path / "cache",
        policy=policy(),
        opener=opener,
        clock=clock,
    )

    assert record.kind is AttemptKind.DISCOVERY
    assert record.url == "https://hospital.test/cms-hpt.txt"
    assert record.ok and record.discovery is not None
    assert record.discovery.location_name == "Hospital"
    assert record.discovery.mrf_url is not None
    assert opener.request is not None
    assert tuple(registry) == (record,)
    assert registry.path.read_text(encoding="utf-8").count("\n") == 1


def test_failed_fetch_is_still_dated_and_recorded(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.jsonl")
    record = fetch_and_record(
        "hospital.test",
        "https://hospital.test/missing.json",
        registry=registry,
        cache_dir=tmp_path / "cache",
        policy=policy(),
        opener=OneResponse(
            Response(b"missing", status=404, url="https://hospital.test/missing.json")
        ),
        clock=clock,
    )
    assert record.kind is AttemptKind.FETCH
    assert record.fetch.status is FetchStatus.HTTP_ERROR
    assert not record.ok
    assert record.attempted_at == "2026-08-09T00:00:00Z"
    assert registry.records() == (record,)


def test_discovery_has_a_small_independent_download_ceiling(tmp_path: Path) -> None:
    response = Response(b"unused", headers={"Content-Length": str((1 << 20) + 1)})
    record = discover_domain(
        "hospital.test",
        registry=Registry(tmp_path / "registry.jsonl"),
        cache_dir=tmp_path / "cache",
        policy=policy(max_bytes=1 << 30),
        opener=OneResponse(response),
        clock=clock,
    )
    assert record.fetch.status is FetchStatus.TOO_LARGE
    assert response.read_calls == 0
    assert record.discovery is None


def test_invalid_utf8_discovery_is_recorded_as_a_parse_problem(tmp_path: Path) -> None:
    record = discover_domain(
        "hospital.test",
        registry=Registry(tmp_path / "registry.jsonl"),
        cache_dir=tmp_path / "cache",
        policy=policy(),
        opener=OneResponse(Response(b"\xff\xfe")),
        clock=clock,
    )
    assert record.fetch.ok
    assert record.discovery is None
    assert "UTF-8" in record.problems[0]


def test_empty_and_malformed_registries_are_distinguished(tmp_path: Path) -> None:
    path = tmp_path / "registry.jsonl"
    registry = Registry(path)
    assert registry.records() == ()
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="line 1"):
        registry.records()


def test_invalid_utf8_registry_is_a_named_read_error(tmp_path: Path) -> None:
    path = tmp_path / "registry.jsonl"
    path.write_bytes(b"\xff\n")

    with pytest.raises(RegistryError, match="could not read registry"):
        Registry(path).records()


def test_registry_rejects_nested_identity_mismatch(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.jsonl")
    record = fetch_and_record(
        "hospital.test",
        "https://hospital.test/prices.json",
        registry=registry,
        cache_dir=tmp_path / "cache",
        policy=policy(),
        opener=OneResponse(Response(b"{}", url="https://hospital.test/prices.json")),
        clock=clock,
    )
    data = record.to_dict()
    data["url"] = "https://different.test/prices.json"
    registry.path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(RegistryError, match="identity do not match"):
        registry.records()
