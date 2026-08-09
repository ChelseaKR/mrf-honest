from __future__ import annotations

import json
from pathlib import Path

import pytest

import mrf_honest.cli as cli
from mrf_honest.fetch import FetchOutcome, FetchStatus


class _Result:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return self.payload


def test_inspect_json_passes_explicit_context_and_findings_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    def fake_inspect(path: Path, publisher: object, *, as_of: object) -> _Result:
        observed.update(path=path, publisher=publisher, as_of=as_of)
        return _Result(
            {
                "findings": [{"code": "CMS_V3_EXAMPLE", "severity": "ERROR"}],
                "source_path": str(path),
            }
        )

    monkeypatch.setattr(cli, "inspect_hospital_file", fake_inspect)
    status = cli.main(
        [
            "inspect",
            "prices.json",
            "--publisher-id",
            "example-health",
            "--as-of",
            "2026-08-09",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert json.loads(captured.out)["findings"][0]["severity"] == "ERROR"
    assert captured.err == ""
    assert observed["path"] == Path("prices.json")
    assert str(observed["as_of"]) == "2026-08-09"
    publisher = observed["publisher"]
    assert publisher.identifier == "example-health"  # type: ignore[attr-defined]


def test_inspect_human_is_readable_and_keeps_progress_on_stderr(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    source = tmp_path / "prices.json"
    source.write_text('{"standard_charge_information": []}', encoding="utf-8")

    status = cli.main(
        [
            "inspect",
            str(source),
            "--as-of",
            "2026-08-09",
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "conformance: FINDINGS" in captured.out
    assert "[ERROR] CMS_V3_ENVELOPE_HOSPITAL_NAME_MISSING" in captured.out
    assert "Inspecting" in captured.err


def test_ingest_json_is_stable_and_passes_operator_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def fake_ingest(
        source: Path,
        warehouse: Path,
        *,
        publisher: object,
        memory_limit: str,
        threads: int,
        as_of: object,
    ) -> _Result:
        observed.update(
            source=source,
            warehouse=warehouse,
            publisher=publisher,
            memory_limit=memory_limit,
            threads=threads,
            as_of=as_of,
        )
        return _Result({"status": "success", "run_id": "run-1", "reused": False})

    monkeypatch.setattr(cli, "ingest_hospital_file", fake_ingest)
    status = cli.main(
        [
            "ingest",
            "prices.json",
            "--publisher-id",
            "example-health",
            "--publisher-name",
            "Example Health",
            "--source-url",
            "https://example.test/prices.json",
            "--warehouse",
            str(tmp_path / "warehouse"),
            "--memory-limit",
            "512MB",
            "--threads",
            "3",
            "--as-of",
            "2026-08-09",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == '{"reused":false,"run_id":"run-1","status":"success"}\n'
    assert captured.err == ""
    assert observed["source"] == Path("prices.json")
    assert observed["warehouse"] == tmp_path / "warehouse"
    assert observed["memory_limit"] == "512MB"
    assert observed["threads"] == 3
    assert str(observed["as_of"]) == "2026-08-09"
    publisher = observed["publisher"]
    assert publisher.identifier == "example-health"  # type: ignore[attr-defined]
    assert publisher.name == "Example Health"  # type: ignore[attr-defined]
    assert publisher.source_url == "https://example.test/prices.json"  # type: ignore[attr-defined]


def test_ingest_human_progress_does_not_pollute_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "ingest_hospital_file",
        lambda *args, **kwargs: _Result({"status": "success", "counts": {"items": 2}}),
    )

    assert (
        cli.main(
            [
                "ingest",
                "prices.json",
                "--publisher-id",
                "example",
                "--warehouse",
                str(tmp_path),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == 'status: success\ncounts: {"items": 2}\n'
    assert captured.err == "Ingesting prices.json ...\n"


def test_profile_outputs_the_order_returned_by_the_lakehouse(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        {"methodology": "fee schedule", "observation_count": 4},
        {"methodology": "other", "observation_count": 1},
    ]
    monkeypatch.setattr(cli, "query_file_profile", lambda warehouse, run_id: rows)

    assert cli.main(["profile", "warehouse", "run-1"]) == 0
    assert json.loads(capsys.readouterr().out) == rows


@pytest.mark.parametrize(
    ("fetch_status", "expected_status"),
    [(FetchStatus.FETCHED, 0), (FetchStatus.NETWORK_ERROR, 1)],
)
def test_fetch_uses_a_bounded_identified_policy_without_real_network(
    fetch_status: FetchStatus,
    expected_status: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def fake_fetch(url: str, cache_dir: Path, *, policy: object) -> FetchOutcome:
        observed.update(url=url, cache_dir=cache_dir, policy=policy)
        return FetchOutcome(
            url=url,
            status=fetch_status,
            attempted_at="2026-08-09T00:00:00Z",
            attempts=1,
            error="offline" if fetch_status is FetchStatus.NETWORK_ERROR else None,
        )

    monkeypatch.setattr(cli, "fetch_url", fake_fetch)
    status = cli.main(
        [
            "fetch",
            "https://example.test/prices.json",
            "--cache-dir",
            str(tmp_path),
            "--contact",
            "operator@example.test",
            "--max-bytes",
            "2048",
        ]
    )

    assert status == expected_status
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == fetch_status.value
    assert observed["url"] == "https://example.test/prices.json"
    policy = observed["policy"]
    assert policy.contact == "operator@example.test"  # type: ignore[attr-defined]
    assert policy.max_bytes == 2048  # type: ignore[attr-defined]


def test_discover_records_each_domain_and_treats_findings_as_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, object, Path, object]] = []

    def fake_discover(
        domain: str,
        *,
        registry: object,
        cache_dir: Path,
        policy: object,
    ) -> _Result:
        observed.append((domain, registry, cache_dir, policy))
        return _Result({"domain": domain, "problems": ["no mrf-url"]})

    monkeypatch.setattr(cli, "discover_domain", fake_discover)
    registry_path = tmp_path / "registry.jsonl"
    cache_dir = tmp_path / "cache"
    status = cli.main(
        [
            "discover",
            "one.test",
            "two.test",
            "--registry",
            str(registry_path),
            "--cache-dir",
            str(cache_dir),
            "--contact",
            "operator@example.test",
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert [record["domain"] for record in json.loads(captured.out)] == ["one.test", "two.test"]
    assert captured.err == "Discovering one.test ...\nDiscovering two.test ...\n"
    assert [call[0] for call in observed] == ["one.test", "two.test"]
    assert observed[0][1] is observed[1][1]
    assert observed[0][1].path == registry_path  # type: ignore[attr-defined]
    assert observed[0][2] == cache_dir
    assert observed[0][3].contact == "operator@example.test"  # type: ignore[attr-defined]


def test_explain_emits_the_authoritative_catalog_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["explain", "FRESHNESS_DATE_IN_FUTURE"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "FRESHNESS_DATE_IN_FUTURE"
    assert payload["dimension"] == "freshness"
    assert payload["severity"] == "WARNING"
    assert payload["description"]
    assert payload["citations"]


def test_explain_unknown_code_is_a_clean_usage_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["explain", "NOT_A_FINDING"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: unknown finding code: NOT_A_FINDING\n"


def test_operational_failure_is_concise_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_warehouse: Path, _run_id: str) -> list[dict[str, object]]:
        raise RuntimeError("warehouse is locked")

    monkeypatch.setattr(cli, "query_file_profile", fail)

    assert cli.main(["profile", "warehouse", "run-1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: warehouse is locked\n"


@pytest.mark.parametrize(
    "argv",
    [
        [
            "fetch",
            "https://example.test/a",
            "--cache-dir",
            "cache",
            "--contact",
            "a@b.test",
            "--max-bytes",
            "0",
        ],
        ["ingest", "a.json", "--publisher-id", "x", "--warehouse", "w", "--threads", "-1"],
        ["inspect", "a.json", "--as-of", "09/08/2026"],
    ],
)
def test_invalid_typed_options_are_usage_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2
