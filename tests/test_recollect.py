"""Scheduled re-collection, driven against a fake network and held to its promises.

The network is replaced at ``mrf_honest.fetch.default_open``, the one place every retrieval in
this package goes through, rather than by injecting an opener into ``recollect``. An injected
opener makes the assessment's execution strategy ``custom``, and the comparison layer refuses to
build a comparison from custom rows, so injecting would have left the built-comparison path
untested. Robots.txt is served by the same stub the rest of the suite uses, through the real gate.

The promises under test: a failure is recorded as no evidence and never carries a letter; a run
is idempotent for its UTC date; an interrupted run resumes without refetching; a different
watchlist cannot be merged into a completed date; the per-publisher cap is stated and not silent;
and robots.txt is honored for every publisher.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request

import pytest
import robots_fixtures

import mrf_honest.fetch as fetch_module
from mrf_honest.cohort import build_comparison
from mrf_honest.discover import DiscoveryEntry, parse_cms_hpt
from mrf_honest.fetch import FetchPolicy
from mrf_honest.recollect import (
    DEFAULT_MAX_FILES_PER_PUBLISHER,
    OUTCOME_ASSESSED,
    OUTCOME_NO_EVIDENCE,
    OUTCOME_NOT_ATTEMPTED,
    RECOLLECTION_SCHEMA_VERSION,
    RecollectionError,
    load_watchlist,
    plan_files,
    profile_for_url,
    recollect,
    slugify,
)
from mrf_honest.scorecard import CSV_PROFILE, JSON_PROFILE, AssessmentRegistry

ROOT = Path(__file__).resolve().parent.parent
COMMITTED_WATCHLIST = ROOT / "data" / "recollections" / "watchlist.json"
COHORTS = ROOT / "data" / "cohorts"


def _document() -> dict[str, object]:
    return {
        "hospital_name": "Example Hospital",
        "last_updated_on": "2026-04-01",
        "version": "3.0.0",
        "location_name": ["Example Main"],
        "hospital_address": ["1 Main St, Testville, CA 90000"],
        "type_2_npi": ["1234567890"],
        "license_information": {"state": "CA", "license_number": "A1"},
        "attestation": {
            "attestation": "CMS-required statement represented in the fixture",
            "confirm_attestation": True,
            "attester_name": "A. Executive",
        },
        "standard_charge_information": [
            {
                "description": "Example service",
                "code_information": [{"code": "12345", "type": "CPT"}],
                "standard_charges": [
                    {
                        "gross_charge": 120,
                        "discounted_cash": 90,
                        "minimum": 70,
                        "maximum": 100,
                        "setting": "outpatient",
                        "payers_information": [
                            {
                                "payer_name": "Alpha",
                                "plan_name": "PPO",
                                "methodology": "fee schedule",
                                "standard_charge_dollar": 80,
                            }
                        ],
                    }
                ],
            }
        ],
    }


GOOD_FILE = json.dumps(_document()).encode()


def _hpt(*entries: tuple[str, str]) -> bytes:
    blocks = [
        "\n".join(
            [
                f"location-name: {name}",
                "source-page-url: https://alpha.test/prices",
                f"mrf-url: {url}",
                "contact-name: MRF Team",
                "contact-email: mrf@alpha.test",
            ]
        )
        for name, url in entries
    ]
    return ("\n\n".join(blocks) + "\n").encode()


class Body:
    def __init__(self, body: bytes, url: str) -> None:
        self.body = body
        self.status = 200
        self.headers: dict[str, str] = {}
        self._url = url
        self._position = 0

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self.body) - self._position
        chunk = self.body[self._position : self._position + amount]
        self._position += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        pass


class Network:
    """Serves bodies by exact URL, answers anything else 404, and records every request."""

    def __init__(self, routes: Mapping[str, bytes | int]) -> None:
        self.routes = dict(routes)
        self.requests: list[str] = []

    def __call__(self, request: Request, *, timeout: float) -> Body:
        url = request.full_url
        self.requests.append(url)
        served = self.routes.get(url, 404)
        if isinstance(served, int):
            raise HTTPError(url, served, "refused", Message(), io.BytesIO())
        return Body(served, url)

    def hits(self, fragment: str) -> int:
        return sum(1 for url in self.requests if fragment in url)


ALPHA_A1 = "https://files.alpha.test/a1_standardcharges.json"
ALPHA_A4 = "https://files.alpha.test/a4_standardcharges.json"
ALPHA_EXTENSIONLESS = "https://files.alpha.test/download?id=3"
GAMMA_FILE = "https://files.gamma.test/g1_standardcharges.json"


def _routes() -> dict[str, bytes | int]:
    return {
        "https://alpha.test/cms-hpt.txt": _hpt(
            ("Alpha East", ALPHA_A1),
            ("Alpha West", ALPHA_A1),
            ("Alpha Annex", ALPHA_EXTENSIONLESS),
            ("Alpha North", ALPHA_A4),
        ),
        ALPHA_A1: GOOD_FILE,
        ALPHA_A4: GOOD_FILE,
        "https://beta.test/cms-hpt.txt": 404,
        "https://gamma.test/cms-hpt.txt": _hpt(("Gamma Main", GAMMA_FILE)),
        GAMMA_FILE: 503,
    }


def _write_watchlist(path: Path, *publishers: tuple[str, str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "publishers": [
                    {"publisher_id": identifier, "name": identifier.title(), "domain": domain}
                    for identifier, domain in publishers
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def watchlist_path(tmp_path: Path) -> Path:
    return _write_watchlist(
        tmp_path / "watchlist.json",
        ("alpha-health", "alpha.test"),
        ("beta-health", "beta.test"),
        ("gamma-health", "gamma.test"),
    )


def _run(
    tmp_path: Path,
    watchlist_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    network: Network,
    *,
    robots: str = robots_fixtures.ALLOW_ALL,
    **options: int,
) -> object:
    monkeypatch.setattr(fetch_module, "default_open", network)
    return recollect(
        load_watchlist(watchlist_path),
        out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        policy=FetchPolicy(contact="owner@example.test", retries=0),
        politeness=robots_fixtures.politeness(robots),
        **options,
    )


def _publisher(document: Mapping[str, object], identifier: str) -> dict[str, object]:
    return next(
        item
        for item in cast(list[dict[str, object]], document["publishers"])
        if item["publisher_id"] == identifier
    )


# --- the watchlist --------------------------------------------------------------------------


def test_the_committed_watchlist_loads_and_every_entry_is_tied_to_committed_evidence() -> None:
    """Each domain must already appear as a discovered origin in a committed manifest, and each
    publisher id must already be a graded publisher there. A watchlist entry nobody can trace
    is a URL that was guessed, which this project does not do."""
    watchlist = load_watchlist(COMMITTED_WATCHLIST)
    domains: set[str] = set()
    for manifest in COHORTS.glob("*.json"):
        if manifest.name.endswith(".comparison.json"):
            continue
        document = json.loads(manifest.read_text(encoding="utf-8"))
        discovery = document.get("discovery") if isinstance(document, dict) else None
        if isinstance(discovery, dict):
            domains |= {item["domain"] for item in discovery.get("domains", [])}
    graded: set[str] = set()
    for comparison in COHORTS.glob("*.comparison.json"):
        document = json.loads(comparison.read_text(encoding="utf-8"))
        graded |= {row["publisher_id"] for row in document["files"]}
    assert watchlist.publishers
    for publisher in watchlist.publishers:
        assert publisher.domain in domains, publisher
        assert publisher.publisher_id in graded, publisher


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"publishers": []}, "at least one publisher"),
        ({"publishers": [{"publisher_id": "A", "name": "x", "domain": "a.test"}]}, "slug"),
        (
            {"publishers": [{"publisher_id": "a", "name": "x", "domain": "https://a.test"}]},
            "bare lowercase host",
        ),
        (
            {"publishers": [{"publisher_id": "a", "name": "x", "domain": "a.test/path"}]},
            "bare lowercase host",
        ),
        ({"publishers": [{"publisher_id": "a", "name": " ", "domain": "a.test"}]}, "non-empty"),
        (
            {
                "publishers": [
                    {"publisher_id": "a", "name": "x", "domain": "a.test"},
                    {"publisher_id": "a", "name": "y", "domain": "b.test"},
                ]
            },
            "repeats",
        ),
        (
            {
                "publishers": [
                    {"publisher_id": "a", "name": "x", "domain": "a.test"},
                    {"publisher_id": "b", "name": "y", "domain": "a.test"},
                ]
            },
            "repeats",
        ),
    ],
)
def test_a_malformed_watchlist_is_refused_with_the_reason(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    base: dict[str, object] = {
        "schema_version": 1,
        "publishers": [{"publisher_id": "a", "name": "x", "domain": "a.test"}],
    }
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps({**base, **mutation}), encoding="utf-8")
    with pytest.raises(RecollectionError, match=message):
        load_watchlist(path)


def test_an_unreadable_or_non_object_watchlist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RecollectionError, match="could not read"):
        load_watchlist(tmp_path / "missing.json")
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RecollectionError, match="JSON object"):
        load_watchlist(path)
    path.write_text('{"schema_version": 1, "publishers": [3]}', encoding="utf-8")
    with pytest.raises(RecollectionError, match="must be an object"):
        load_watchlist(path)


# --- planning -------------------------------------------------------------------------------


def _entries(text: bytes) -> list[DiscoveryEntry]:
    return list(parse_cms_hpt(text.decode(), domain="alpha.test").entries)


def test_one_file_listed_by_two_locations_is_planned_once() -> None:
    planned, skipped = plan_files(
        _entries(_routes()["https://alpha.test/cms-hpt.txt"]), max_files=9
    )  # type: ignore[arg-type]

    assert [item.url for item in planned] == [ALPHA_A1, ALPHA_A4]
    assert planned[0].location_names == ("Alpha East", "Alpha West")
    assert planned[0].location_id == "alpha-east"
    assert [item["code"] for item in skipped] == ["format_not_named_by_url"]


def test_the_cap_keeps_the_first_files_and_states_what_it_left_out() -> None:
    planned, skipped = plan_files(
        _entries(_routes()["https://alpha.test/cms-hpt.txt"]), max_files=1
    )  # type: ignore[arg-type]

    assert [item.url for item in planned] == [ALPHA_A1]
    assert {item["code"] for item in skipped} == {
        "format_not_named_by_url",
        "over_per_publisher_cap",
    }


def test_colliding_location_slugs_are_numbered_in_document_order() -> None:
    text = _hpt(("Same Name", "https://f.test/1.json"), ("Same Name", "https://f.test/2.json"))
    planned, _ = plan_files(_entries(text), max_files=5)
    assert [item.location_id for item in planned] == ["same-name", "same-name-2"]


def test_a_nameless_location_falls_back_to_the_file_name() -> None:
    entry = DiscoveryEntry(mrf_url="https://f.test/path/123_Big-Hospital_standardcharges.json")
    [planned], _ = plan_files([entry], max_files=5)
    assert planned.location_id == "123-big-hospital-standardcharges"


def test_an_entry_without_a_usable_url_is_skipped_with_its_problems() -> None:
    entry = DiscoveryEntry(location_name="Broken", problems=("mrf-url is not https: 'http://x'",))
    planned, [skip] = plan_files([entry], max_files=5)
    assert planned == []
    assert skip["code"] == "no_usable_mrf_url"
    assert "not https" in str(skip["reason"])


def test_format_is_read_from_the_url_and_never_guessed() -> None:
    assert profile_for_url("https://f.test/a.JSON") is JSON_PROFILE
    assert profile_for_url("https://f.test/a.csv?sig=1") is CSV_PROFILE
    assert profile_for_url("https://f.test/a.zip") is None
    assert profile_for_url("https://f.test/download") is None


def test_slugify_is_bounded_and_ascii() -> None:
    assert slugify("St. Mary's  Hospital (East)") == "st-mary-s-hospital-east"
    assert len(slugify("x" * 500)) <= 120
    assert slugify("---") == ""


# --- a run ----------------------------------------------------------------------------------


def test_a_run_records_what_was_read_and_what_was_not(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    result = _run(tmp_path, watchlist_path, monkeypatch, network)
    document = result.document  # type: ignore[attr-defined]

    assert document["schema_version"] == RECOLLECTION_SCHEMA_VERSION
    assert "No evidence is not a pass" in str(document["notice"])

    alpha = _publisher(document, "alpha-health")
    files = {str(item["url"]): item for item in cast(list[dict[str, object]], alpha["files"])}
    assert files[ALPHA_A1]["outcome"] == OUTCOME_ASSESSED
    assert files[ALPHA_A1]["grade"] in {"A", "B", "C", "D", "F"}
    assert files[ALPHA_A1]["locations"] == ["Alpha East", "Alpha West"]
    assert files[ALPHA_A4]["outcome"] == OUTCOME_ASSESSED
    # A published URL has its query redacted; the digest, not the string, identifies it.
    extensionless = files["https://files.alpha.test/download"]
    assert extensionless["outcome"] == OUTCOME_NOT_ATTEMPTED
    assert extensionless["grade"] is None
    assert network.hits("/download") == 0
    # One file listed by two locations is one request, not two.
    assert network.hits("a1_standardcharges.json") == 1

    beta = _publisher(document, "beta-health")
    beta_discovery = cast(dict[str, object], beta["discovery"])
    assert beta_discovery["outcome"] == OUTCOME_NO_EVIDENCE
    assert "HTTP 404" in str(beta_discovery["reason"])
    assert beta["files"] == []

    summary = cast(dict[str, object], document["summary"])
    assert summary["publishers_no_evidence"] == 1
    assert summary["files_assessed"] == 2
    assert summary["infrastructure_failures"] == 0


def test_an_unreachable_file_has_no_letter_and_says_why(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(tmp_path, watchlist_path, monkeypatch, Network(_routes()))
    [file] = cast(
        list[dict[str, object]],
        _publisher(result.document, "gamma-health")["files"],  # type: ignore[attr-defined]
    )

    assert file["outcome"] == OUTCOME_NO_EVIDENCE
    assert file["grade"] is None
    assert file["content_sha256"] is None
    assert "503" in str(file["reason"]) or "HTTP" in str(file["reason"])


def test_no_row_in_the_result_document_pairs_a_failure_with_a_letter(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole-document form of the promise, so a new code path cannot slip past the two
    cases above."""
    result = _run(tmp_path, watchlist_path, monkeypatch, Network(_routes()))
    for publisher in cast(list[dict[str, object]], result.document["publishers"]):  # type: ignore[attr-defined]
        for file in cast(list[dict[str, object]], publisher["files"]):
            if file["outcome"] != OUTCOME_ASSESSED:
                assert file["grade"] is None, file
            else:
                assert file["content_sha256"] is not None, file


def test_the_comparison_is_built_from_the_assessments_and_can_be_re_derived(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(tmp_path, watchlist_path, monkeypatch, Network(_routes()))
    document = result.document  # type: ignore[attr-defined]
    collected_on = str(document["collected_on"])
    out = tmp_path / "out"

    state = cast(
        dict[str, object], cast(dict[str, object], document["comparisons"])[JSON_PROFILE.name]
    )
    assert state == {"status": "built", "comparison": f"{collected_on}.comparison.json"}

    manifest = json.loads((out / f"{collected_on}.json").read_text(encoding="utf-8"))
    comparison_text = (out / f"{collected_on}.comparison.json").read_text(encoding="utf-8")
    rows = AssessmentRegistry(out / f"{collected_on}.assessments.jsonl").records()
    rederived = build_comparison(rows, manifest, generated_at=str(document["started_at"]))
    assert comparison_text == (
        json.dumps(rederived, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    )
    files = cast(list[dict[str, object]], rederived["files"])
    # The failed file is in the comparison with the cohort policy's own treatment of it, which
    # is why the alert layer, and not this one, decides it is no evidence.
    assert {row["slug"] for row in files} == {
        "alpha-health/alpha-east",
        "alpha-health/alpha-north",
        "gamma-health/gamma-main",
    }
    assert manifest["collection"]["operator_controlled_single_run"] is True
    assert cast(dict[str, object], rederived["statistics"])["refusal"] is not None


def test_a_single_row_cannot_make_a_comparison_and_the_result_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watchlist = _write_watchlist(tmp_path / "w.json", ("gamma-health", "gamma.test"))
    result = _run(tmp_path, watchlist, monkeypatch, Network(_routes()))
    state = cast(dict[str, object], result.document["comparisons"])[JSON_PROFILE.name]  # type: ignore[attr-defined]
    assert cast(dict[str, object], state)["status"] == "not_built"
    assert "at least two" in str(cast(dict[str, object], state)["reason"])


def test_robots_txt_that_says_no_means_no_evidence_and_no_request_for_any_file(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    result = _run(
        tmp_path, watchlist_path, monkeypatch, network, robots="User-agent: *\nDisallow: /\n"
    )
    document = result.document  # type: ignore[attr-defined]

    assert network.requests == []
    for publisher in cast(list[dict[str, object]], document["publishers"]):
        discovery = cast(dict[str, object], publisher["discovery"])
        assert discovery["outcome"] == OUTCOME_NO_EVIDENCE
        assert "robots_disallowed" in str(discovery["reason"])
        assert publisher["files"] == []
    assert document["comparisons"] == {}


# --- cap, idempotency, resumption -----------------------------------------------------------


def test_the_cap_is_applied_and_published(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    result = _run(tmp_path, watchlist_path, monkeypatch, network, max_files_per_publisher=1)
    document = result.document  # type: ignore[attr-defined]

    alpha = _publisher(document, "alpha-health")
    capped = [item for item in cast(list[dict[str, object]], alpha["files"]) if item.get("code")]
    assert {item["code"] for item in capped} == {
        "format_not_named_by_url",
        "over_per_publisher_cap",
    }
    assert network.hits("a4_standardcharges.json") == 0
    assert cast(dict[str, object], document["limits"])["max_files_per_publisher"] == 1
    assert DEFAULT_MAX_FILES_PER_PUBLISHER >= 1


def test_running_again_on_the_same_date_fetches_nothing_and_changes_nothing(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    first = _run(tmp_path, watchlist_path, monkeypatch, network)
    assert not first.already_complete  # type: ignore[attr-defined]
    before = {path.name: path.read_bytes() for path in sorted((tmp_path / "out").iterdir())}
    requests = len(network.requests)

    second = _run(tmp_path, watchlist_path, monkeypatch, network)

    assert second.already_complete  # type: ignore[attr-defined]
    assert second.document == first.document  # type: ignore[attr-defined]
    assert len(network.requests) == requests
    assert before == {path.name: path.read_bytes() for path in sorted((tmp_path / "out").iterdir())}
    assert [path.name for path in second.comparison_paths] == [  # type: ignore[attr-defined]
        f"{first.document['collected_on']}.comparison.json"  # type: ignore[attr-defined]
    ]


def test_a_different_watchlist_cannot_be_merged_into_a_completed_date(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    _run(tmp_path, watchlist_path, monkeypatch, network)
    other = _write_watchlist(tmp_path / "other.json", ("alpha-health", "alpha.test"))

    with pytest.raises(RecollectionError, match="different watchlist"):
        _run(tmp_path, other, monkeypatch, network)


def test_an_interrupted_run_resumes_without_refetching_what_it_already_assessed(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    first = _run(tmp_path, watchlist_path, monkeypatch, network)
    # Simulate a kill after the assessments were written but before the completion marker.
    first.path.unlink()  # type: ignore[attr-defined]
    file_requests = network.hits("_standardcharges.json")
    assert file_requests == 3

    second = _run(tmp_path, watchlist_path, monkeypatch, network)

    assert not second.already_complete  # type: ignore[attr-defined]
    # The two assessed rows are reused; the 503 file left no assessment row to reuse, no: it did
    # leave one, so nothing is refetched at all.
    assert network.hits("_standardcharges.json") == file_requests
    assert second.document["summary"] == first.document["summary"]  # type: ignore[attr-defined]
    rows = AssessmentRegistry(
        tmp_path / "out" / f"{first.document['collected_on']}.assessments.jsonl"  # type: ignore[attr-defined]
    ).records()
    assert len(rows) == 3, "a resumed run must not append a second row for the same file"


def test_a_stale_or_foreign_result_file_is_refused(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network(_routes())
    first = _run(tmp_path, watchlist_path, monkeypatch, network)
    path: Path = first.path  # type: ignore[attr-defined]
    document = json.loads(path.read_text(encoding="utf-8"))

    document["schema_version"] = 99
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RecollectionError, match="schema_version"):
        _run(tmp_path, watchlist_path, monkeypatch, network)

    path.write_text("not json", encoding="utf-8")
    with pytest.raises(RecollectionError, match="cannot be read"):
        _run(tmp_path, watchlist_path, monkeypatch, network)

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RecollectionError, match="not a result document"):
        _run(tmp_path, watchlist_path, monkeypatch, network)


def test_the_cap_must_be_at_least_one(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RecollectionError, match="at least 1"):
        _run(tmp_path, watchlist_path, monkeypatch, Network({}), max_files_per_publisher=0)


# --- the command ----------------------------------------------------------------------------


def _network_with_robots() -> Network:
    routes = _routes()
    for host in ("alpha.test", "beta.test", "gamma.test", "files.alpha.test", "files.gamma.test"):
        routes[f"https://{host}/robots.txt"] = robots_fixtures.ALLOW_ALL.encode()
    return Network(routes)


def test_the_command_writes_the_dated_files_and_exits_zero_on_publisher_failures(
    tmp_path: Path,
    watchlist_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import mrf_honest.cli as cli

    network = _network_with_robots()
    monkeypatch.setattr(fetch_module, "default_open", network)
    monkeypatch.setattr(cli, "default_open", network)
    argv = [
        "recollect",
        "--watchlist",
        str(watchlist_path),
        "--out-dir",
        str(tmp_path / "out"),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--contact",
        "owner@example.test",
    ]

    # A 404 cms-hpt.txt and a 503 file are observations about publishers, so the run succeeds.
    assert cli.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["already_complete"] is False
    assert first["summary"]["publishers_no_evidence"] == 1
    assert Path(first["result"]).is_file()

    requests = len(network.requests)
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["already_complete"] is True
    assert len(network.requests) == requests


def test_the_command_refuses_a_bad_watchlist_with_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import mrf_honest.cli as cli

    bad = tmp_path / "w.json"
    bad.write_text('{"schema_version": 1, "publishers": []}', encoding="utf-8")
    status = cli.main(
        [
            "recollect",
            "--watchlist",
            str(bad),
            "--out-dir",
            str(tmp_path / "out"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--contact",
            "owner@example.test",
        ]
    )
    assert status == 1
    assert "at least one publisher" in capsys.readouterr().err


def test_a_recollection_comparison_is_something_the_alert_layer_can_read(
    tmp_path: Path, watchlist_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipeline's two halves meet at a document. Here the unreachable file is the interesting
    row: the cohort policy files it under the comparison with a grade, and the alert must report
    it as no evidence when the earlier collection had read it."""
    import copy

    from mrf_honest.alert import build_alert

    result = _run(tmp_path, watchlist_path, monkeypatch, Network(_routes()))
    path = tmp_path / "out" / f"{result.document['collected_on']}.comparison.json"  # type: ignore[attr-defined]
    after = json.loads(path.read_text(encoding="utf-8"))
    before = copy.deepcopy(after)
    cohort = before["cohort"]
    cohort["cohort_id"] = "recollection-json-v3-2000-01-01"
    cohort["as_of"] = "2000-01-01"
    cohort["comparison_scope"]["as_of"] = "2000-01-01"
    for row in before["files"]:
        row["as_of"] = "2000-01-01"
        if row["slug"] == "gamma-health/gamma-main":
            # Pretend the earlier collection did read this file.
            row["coverage"]["verified_body_available"] = True
            row["grade"]["grade"] = "A"

    alert = build_alert(before, after)

    assert alert["regressions"] == []
    [missing] = alert["no_evidence"]
    assert missing["slug"] == "gamma-health/gamma-main"
    assert alert["summary"]["only_in_before"] == 0
