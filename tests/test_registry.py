from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

import pytest
import robots_fixtures

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
            b"contact-name: Hospital MRF Team",
            b"contact-email: mrf@hospital.test",
        ]
    )
    opener = OneResponse(Response(text))
    registry = Registry(tmp_path / "registry.jsonl")

    record = discover_domain(
        "hospital.test",
        registry=registry,
        cache_dir=tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=opener,
        clock=clock,
    )

    assert record.kind is AttemptKind.DISCOVERY
    assert record.url == "https://hospital.test/cms-hpt.txt"
    assert record.ok and record.discovery is not None
    assert record.discovery.location_name == "Hospital"
    assert record.discovery.mrf_url is not None
    assert record.discovery.contact_name == "Hospital MRF Team"
    assert record.discovery.contact_email == "mrf@hospital.test"
    assert opener.request is not None
    assert tuple(registry) == (record,)
    assert registry.path.read_text(encoding="utf-8").count("\n") == 1


def test_registry_round_trips_multiple_discovery_entries_and_extras(tmp_path: Path) -> None:
    text = b"\n".join(
        [
            b"location-name: Hospital East",
            b"source-page-url: https://hospital.test/prices",
            b"mrf-url: https://hospital.test/east.json",
            b"contact-name: East Team",
            b"contact-email: east@hospital.test",
            b"vendor-id: east-1",
            b"",
            b"location-name: Hospital West",
            b"source-page-url: https://hospital.test/prices",
            b"mrf-url: https://hospital.test/west.json",
            b"contact-name: West Team",
            b"contact-email: west@hospital.test",
            b"vendor-id: west-2",
        ]
    )
    registry = Registry(tmp_path / "registry.jsonl")

    record = discover_domain(
        "hospital.test",
        registry=registry,
        cache_dir=tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=OneResponse(Response(text)),
        clock=clock,
    )

    loaded = registry.records()[0]
    assert loaded == record
    assert loaded.discovery is not None
    assert len(loaded.discovery.entries) == 2
    assert loaded.discovery.entries[0].contact_email == "east@hospital.test"
    assert loaded.discovery.entries[0].extra_fields == (("vendor-id", "east-1"),)
    assert loaded.discovery.entries[1].contact_name == "West Team"
    assert loaded.discovery.entries[1].extra_fields == (("vendor-id", "west-2"),)
    assert loaded.to_dict()["version"] == 2


def test_discovery_with_missing_required_contact_fields_is_not_ok(tmp_path: Path) -> None:
    record = discover_domain(
        "hospital.test",
        registry=Registry(tmp_path / "registry.jsonl"),
        cache_dir=tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=OneResponse(
            Response(
                b"\n".join(
                    [
                        b"location-name: Hospital",
                        b"source-page-url: https://hospital.test/prices",
                        b"mrf-url: https://hospital.test/prices.json",
                    ]
                )
            )
        ),
        clock=clock,
    )

    assert record.discovery is not None and record.discovery.usable
    assert "no contact-name field" in record.discovery.problems
    assert "no contact-email field" in record.discovery.problems
    assert not record.ok


def test_registry_reads_legacy_v1_single_discovery_without_inventing_contacts(
    tmp_path: Path,
) -> None:
    registry = Registry(tmp_path / "registry.jsonl")
    record = discover_domain(
        "hospital.test",
        registry=registry,
        cache_dir=tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=OneResponse(
            Response(
                b"\n".join(
                    [
                        b"location-name: Hospital",
                        b"source-page-url: https://hospital.test/prices",
                        b"mrf-url: https://hospital.test/prices.json",
                        b"contact-name: MRF Team",
                        b"contact-email: mrf@hospital.test",
                    ]
                )
            )
        ),
        clock=clock,
    )
    current = record.to_dict()
    current["version"] = 1
    current_discovery = current["discovery"]
    assert isinstance(current_discovery, dict)
    current_entries = current_discovery["entries"]
    assert isinstance(current_entries, list)
    current_entry = current_entries[0]
    assert isinstance(current_entry, dict)
    current["discovery"] = {
        "domain": current_discovery["domain"],
        "location_name": current_entry["location_name"],
        "source_page_url": current_entry["source_page_url"],
        "mrf_url": current_entry["mrf_url"],
        "extra_fields": [["legacy-key", "legacy-value"]],
        "problems": ["legacy parse problem"],
    }
    registry.path.write_text(json.dumps(current) + "\n", encoding="utf-8")

    loaded = registry.records()[0]

    assert loaded.discovery is not None
    assert len(loaded.discovery.entries) == 1
    assert loaded.discovery.contact_name is None
    assert loaded.discovery.contact_email is None
    assert loaded.discovery.extra_fields == (("legacy-key", "legacy-value"),)
    assert loaded.discovery.problems == ("legacy parse problem",)
    assert not loaded.ok


def test_failed_fetch_is_still_dated_and_recorded(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.jsonl")
    record = fetch_and_record(
        "hospital.test",
        "https://hospital.test/missing.json",
        registry=registry,
        cache_dir=tmp_path / "cache",
        politeness=robots_fixtures.politeness(),
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
        politeness=robots_fixtures.politeness(),
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
        politeness=robots_fixtures.politeness(),
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
        politeness=robots_fixtures.politeness(),
        policy=policy(),
        opener=OneResponse(Response(b"{}", url="https://hospital.test/prices.json")),
        clock=clock,
    )
    data = record.to_dict()
    data["url"] = "https://different.test/prices.json"
    registry.path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(RegistryError, match="identity do not match"):
        registry.records()
