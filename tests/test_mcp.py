"""The read-only MCP server, and the questions it refuses to answer.

The refusals carry the weight here. An assistant that can ask "how many A grades are there"
across cohorts gets the pooled number `docs/how-we-compare.md` forbids, and it gets it in a
sentence, without ever seeing the page that explains why the number is meaningless. Each refusal
below is a guard; neutering it in `mrf_honest.mcp` turns its test red.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, cast

import pytest

from mrf_honest.mcp import (
    LIMITS,
    NOT_COMPARABLE_ACROSS,
    TOOLS,
    DatasetUnavailable,
    call_tool,
    handle,
    serve,
)
from mrf_honest.site import render_site

ROOT = Path(__file__).resolve().parent.parent
COHORTS = ROOT / "data" / "cohorts"


def _published_by_profile() -> list[dict[str, object]]:
    """The newest committed cohort of each profile, which is what the site publishes."""

    newest: dict[str, tuple[str, dict[str, object]]] = {}
    for path in sorted(COHORTS.glob("*.comparison.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        cohort = cast(dict[str, object], document["cohort"])
        profile = str(cast(dict[str, object], cohort["comparison_scope"])["profile"])
        key = str(cohort["as_of"])
        if profile not in newest or key > newest[profile][0]:
            newest[profile] = (key, document)
    return [document for _, document in newest.values()]


@pytest.fixture
def site(tmp_path: Path) -> Path:
    render_site(_published_by_profile(), tmp_path / "site", origin="https://example.test")
    return tmp_path / "site"


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(result["content"][0]["text"]))


def _call(site_dir: Path, name: str, **arguments: Any) -> dict[str, Any]:
    return _payload(call_tool(site_dir, name, arguments))


class TestRefusals:
    def test_a_grade_filter_without_a_cohort_is_refused(self, site: Path) -> None:
        """The one question that would undo the whole boundary in a single sentence."""

        answer = _call(site, "list_files", grade="A")
        assert answer["outcome"] == "refused"
        assert "pools rows" in answer["reason"]
        assert answer["available_cohorts"]

    def test_a_grade_filter_with_a_cohort_is_answered(self, site: Path) -> None:
        cohort_id = _call(site, "list_cohorts")["cohorts"][0]["cohort_id"]
        answer = _call(site, "list_files", cohort_id=cohort_id, grade="A")
        assert "outcome" not in answer
        assert answer["cohort_id"] == cohort_id
        assert all(entry["grade"] == "A" for entry in answer["files"])

    def test_an_unknown_cohort_is_refused_with_the_ones_that_exist(self, site: Path) -> None:
        answer = _call(site, "list_files", cohort_id="no-such-cohort")
        assert answer["outcome"] == "refused"
        assert answer["available_cohorts"]

    def test_an_unknown_tool_is_refused_rather_than_answered_approximately(
        self, site: Path
    ) -> None:
        answer = _call(site, "invent_a_comparison")
        assert answer["outcome"] == "refused"
        assert "list_files" in answer["available_tools"]

    def test_a_slug_in_two_cohorts_is_refused_until_one_is_named(self, tmp_path: Path) -> None:
        """The same file graded under two profiles is two claims, not one."""

        both = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(COHORTS.glob("2026-08-1*.comparison.json"))
        ]
        json_cohorts = [
            document
            for document in both
            if cast(dict[str, Any], document["cohort"])["comparison_scope"]["profile"]
            == "cms-hospital-json-v3"
        ]
        assert len(json_cohorts) >= 2, "the fixture needs two cohorts of one profile"
        site_dir = tmp_path / "site"
        (site_dir / "api" / "cohorts").mkdir(parents=True)
        shared = str(cast(list[dict[str, Any]], json_cohorts[0]["files"])[0]["slug"])
        entries = []
        for document in json_cohorts:
            cohort_id = str(cast(dict[str, Any], document["cohort"])["cohort_id"])
            entries.append({"cohort_id": cohort_id})
            (site_dir / "api" / "cohorts" / f"{cohort_id}.json").write_text(
                json.dumps({"files": document["files"]}), encoding="utf-8"
            )
        (site_dir / "api" / "index.json").write_text(
            json.dumps({"cohorts": entries}), encoding="utf-8"
        )
        answer = _call(site_dir, "get_file", slug=shared)
        assert answer["outcome"] == "refused"
        assert len(answer["cohorts"]) == 2

    def test_a_missing_slug_is_refused(self, site: Path) -> None:
        answer = _call(site, "get_file", slug="nobody/nowhere")
        assert answer["outcome"] == "refused"

    def test_an_empty_slug_is_refused(self, site: Path) -> None:
        assert _call(site, "get_file", slug="")["outcome"] == "refused"

    def test_statistics_for_an_unknown_cohort_is_refused(self, site: Path) -> None:
        answer = _call(site, "cohort_statistics", cohort_id="nope")
        assert answer["outcome"] == "refused"

    def test_a_cohort_without_a_statistics_block_is_refused_not_reported_as_empty(
        self, tmp_path: Path
    ) -> None:
        site_dir = tmp_path / "site"
        (site_dir / "api" / "cohorts").mkdir(parents=True)
        (site_dir / "api" / "index.json").write_text(
            json.dumps({"cohorts": [{"cohort_id": "legacy"}]}), encoding="utf-8"
        )
        (site_dir / "api" / "cohorts" / "legacy.json").write_text(
            json.dumps({"files": []}), encoding="utf-8"
        )
        answer = _call(site_dir, "cohort_statistics", cohort_id="legacy")
        assert answer["outcome"] == "refused"
        assert "before the statistics layer" in answer["reason"]

    def test_a_missing_published_api_is_named_not_answered_as_empty(self, tmp_path: Path) -> None:
        with pytest.raises(DatasetUnavailable) as raised:
            call_tool(tmp_path, "list_cohorts", {})
        assert "run 'mrf-honest site'" in str(raised.value)


#: Strings that appear only in a document this project never published. The slug is kept
#: separate from the marker because a refusal quotes back the slug it was asked for, and a
#: quoted argument is not a leak.
UNPUBLISHED_MARKER = "this-document-was-never-published"
UNPUBLISHED_SLUG = "unpublished-hospital"

#: Every hostile spelling of a cohort id, exercised as a set rather than one at a time. A
#: guard checked case by case is how a boundary ends up holding in four call sites and open in
#: the fifth; the traversal this sweep exists for was reachable from three tools and was
#: noticed in one. The absolute-path case is appended by the fixture, which alone knows where
#: the planted document lives.
HOSTILE_COHORT_IDS: tuple[tuple[str, str], ...] = (
    ("relative traversal", "../../../secretplace/notacohort"),
    (
        "traversal behind a real prefix",
        "hospital-json-v3-2026-08-19/../../../secretplace/notacohort",
    ),
    ("one step out, onto the index itself", "../index"),
    ("dot segment first", "./../../secretplace/notacohort"),
    ("bare dot", "."),
    ("bare dot dot", ".."),
    ("trailing separator", "../../../secretplace/notacohort/"),
    ("backslash separators", "..\\..\\..\\secretplace\\notacohort"),
    ("url-encoded traversal", "%2e%2e%2f%2e%2e%2f%2e%2e%2fsecretplace%2fnotacohort"),
    ("double-encoded traversal", "%252e%252e%252fsecretplace%252fnotacohort"),
    ("null byte after a real id", "hospital-json-v3-2026-08-19\x00/../../secretplace/notacohort"),
    ("null byte alone", "notacohort\x00"),
    ("symlink planted inside the cohorts directory", "escape-hatch"),
    ("a real file in the cohorts directory that is not published", "unlisted"),
)

#: The tools that take a cohort id, and how each is asked. Every one of them reached
#: `_load_cohort` with unchecked text.
COHORT_ID_CALLS: tuple[tuple[str, str], ...] = (
    ("list_files", "cohort_id"),
    ("cohort_statistics", "cohort_id"),
    ("get_file", "cohort_id"),
)


def _answered(site_dir: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The tool's answer, or None if the call raised rather than answering."""

    try:
        return _call(site_dir, tool, **arguments)
    except Exception:
        return None


def _secret_document() -> dict[str, Any]:
    """Shaped like a cohort document, published nowhere, and carrying no comparison scope.

    `comparison_scope: null` is the tell. `docs/how-we-compare.md` refuses to produce a
    comparison at all unless every row shares one attested scope, so a document that serves
    grades without one is a claim this project has undertaken never to make.
    """

    return {
        "comparison_scope": None,
        "statistics": {"note": UNPUBLISHED_MARKER},
        "files": [
            {
                "slug": UNPUBLISHED_SLUG,
                "publisher_name": UNPUBLISHED_MARKER,
                "grade": {"grade": "A", "reason": UNPUBLISHED_MARKER},
            }
        ],
    }


@pytest.fixture
def planted(site: Path, tmp_path: Path) -> tuple[Path, tuple[tuple[str, str], ...]]:
    """A published site with the unpublished document planted wherever a hostile id would land.

    Planting is what makes this a test rather than a spelling exercise. Each id is resolved
    exactly the way the unguarded code resolved it -- `site/api/cohorts/{cohort_id}.json` --
    and the secret document is written there when the filesystem allows, so a case that can
    escape actually escapes. A sweep of invented names that happen not to exist would pass
    against a server with no guard at all.
    """

    secret = tmp_path / "secretplace" / "notacohort.json"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(json.dumps(_secret_document()), encoding="utf-8")

    cases = (*HOSTILE_COHORT_IDS, ("absolute path", str(secret.with_suffix(""))))
    cohorts = site / "api" / "cohorts"
    (cohorts / "escape-hatch.json").symlink_to(secret)
    (cohorts / "unlisted.json").write_text(json.dumps(_secret_document()), encoding="utf-8")
    for _, cohort_id in cases:
        try:
            target = cohorts / f"{cohort_id}.json"
            if target.exists():
                continue  # never clobber the real render, and never re-plant
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(_secret_document()), encoding="utf-8")
        except (OSError, ValueError):
            continue  # a null byte cannot be written to a path; the guard must still refuse it
    return site, cases


class TestACohortIdIsNeverAPath:
    """A cohort id is tool input, so it is checked against the published set, not sanitized.

    The defect these cover served an unpublished document, `comparison_scope` and all, to
    anything that could name a path: the one server whose stated purpose is to refuse what the
    site refuses handed over grades with no scope.
    """

    def test_no_hostile_cohort_id_reaches_a_document(
        self, planted: tuple[Path, tuple[tuple[str, str], ...]]
    ) -> None:
        site_dir, cases = planted
        served: list[str] = []
        for label, cohort_id in cases:
            for tool, key in COHORT_ID_CALLS:
                arguments: dict[str, Any] = {key: cohort_id}
                if tool == "get_file":
                    arguments["slug"] = UNPUBLISHED_SLUG
                try:
                    answer = _call(site_dir, tool, **arguments)
                except Exception as exc:  # a crash is not a refusal either
                    served.append(f"{label} via {tool}: raised {type(exc).__name__}: {exc}")
                    continue
                if answer.get("outcome") != "refused":
                    served.append(f"{label} via {tool}: answered {json.dumps(answer)[:200]}")
                elif UNPUBLISHED_MARKER in json.dumps(answer):
                    served.append(f"{label} via {tool}: refusal leaked the document")
        assert not served, "an unpublished document was reachable:\n" + "\n".join(served)

    def test_a_grade_filter_cannot_ride_a_hostile_cohort_id(
        self, planted: tuple[Path, tuple[tuple[str, str], ...]]
    ) -> None:
        """`list_files` reads the cohort only once a grade filter has a cohort_id to sit on."""

        site_dir, cases = planted
        for label, cohort_id in cases:
            try:
                answer = _call(site_dir, "list_files", cohort_id=cohort_id, grade="A")
            except Exception as exc:
                raise AssertionError(f"{label}: raised {type(exc).__name__}: {exc}") from exc
            assert answer.get("outcome") == "refused", label
            assert UNPUBLISHED_MARKER not in json.dumps(answer), label

    def test_every_refusal_names_the_cohorts_that_do_exist(
        self, planted: tuple[Path, tuple[tuple[str, str], ...]]
    ) -> None:
        """Loud, not silent: the refusal says what is published instead of returning nothing."""

        site_dir, cases = planted
        published = _call(site_dir, "list_cohorts")["cohorts"]
        expected = sorted(entry["cohort_id"] for entry in published)
        for label, cohort_id in cases:
            for tool in ("list_files", "cohort_statistics"):
                answer = _call(site_dir, tool, cohort_id=cohort_id)
                assert answer["outcome"] == "refused", label
                assert sorted(answer["available_cohorts"]) == expected, f"{label} via {tool}"

    def test_the_published_cohorts_still_answer(
        self, planted: tuple[Path, tuple[tuple[str, str], ...]]
    ) -> None:
        """The guard is membership, so it must not have narrowed the published set."""

        site_dir, _ = planted
        for entry in _call(site_dir, "list_cohorts")["cohorts"]:
            answer = _call(site_dir, "list_files", cohort_id=entry["cohort_id"])
            assert answer.get("outcome") != "refused"
            assert answer["comparison_scope"], "a served cohort always carries its scope"

    def test_no_tool_ever_serves_a_document_without_a_comparison_scope(
        self, planted: tuple[Path, tuple[tuple[str, str], ...]]
    ) -> None:
        """The invariant the traversal broke, asserted directly rather than through the path."""

        site_dir, cases = planted
        for label, cohort_id in cases:
            for tool, key in COHORT_ID_CALLS:
                arguments: dict[str, Any] = {key: cohort_id}
                if tool == "get_file":
                    arguments["slug"] = UNPUBLISHED_SLUG
                answer = _answered(site_dir, tool, arguments)
                if answer is None:
                    continue  # it raised; the sweep above is where that is reported
                assert "comparison_scope" not in answer or answer["comparison_scope"], label

    def test_an_index_that_names_an_escaping_cohort_fails_loudly(self, tmp_path: Path) -> None:
        """Defence in depth: membership cannot help if the published index is itself wrong."""

        site_dir = tmp_path / "site"
        (site_dir / "api" / "cohorts").mkdir(parents=True)
        secret = tmp_path / "secretplace" / "notacohort.json"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text(json.dumps(_secret_document()), encoding="utf-8")
        escaping = "../../../secretplace/notacohort"
        (site_dir / "api" / "index.json").write_text(
            json.dumps({"cohorts": [{"cohort_id": escaping}]}), encoding="utf-8"
        )
        with pytest.raises(DatasetUnavailable) as raised:
            call_tool(site_dir, "cohort_statistics", {"cohort_id": escaping})
        assert "outside the published cohorts directory" in str(raised.value)


class TestAnswers:
    def test_every_answer_carries_the_scope_boundary(self, site: Path) -> None:
        for name, arguments in (
            ("list_cohorts", {}),
            ("list_files", {}),
            ("grading_method", {}),
        ):
            assert _payload(call_tool(site, name, arguments))["not_comparable_across"] == list(
                NOT_COMPARABLE_ACROSS
            )

    def test_a_statistics_refusal_is_served_as_the_answer(self, site: Path) -> None:
        refused = [
            entry["cohort_id"]
            for entry in _call(site, "list_cohorts")["cohorts"]
            if entry["statistics"]["refusal"]
        ]
        assert refused, "a committed cohort should refuse"
        answer = _call(site, "cohort_statistics", cohort_id=refused[0])
        assert answer["statistics"]["refusal"]["reason"].strip()
        assert answer["statistics"]["estimates"] == []

    def test_a_statistics_estimate_is_served_with_its_interval(self, site: Path) -> None:
        estimated = [
            entry["cohort_id"]
            for entry in _call(site, "list_cohorts")["cohorts"]
            if entry["statistics"]["estimates"]
        ]
        assert estimated
        answer = _call(site, "cohort_statistics", cohort_id=estimated[0])
        for estimate in answer["statistics"]["estimates"]:
            assert estimate["interval_low"] <= estimate["point"] <= estimate["interval_high"]
            assert estimate["denominator"]

    def test_grading_method_reads_the_policy_the_grades_were_minted_under(self, site: Path) -> None:
        """A hand-written summary could drift from the rule table; this cannot."""

        answer = _call(site, "grading_method")
        published = {
            str(
                cast(dict[str, Any], cast(dict[str, Any], document["cohort"])["grade_policy"])[
                    "version"
                ]
            )
            for document in _published_by_profile()
        }
        assert set(answer["policies"]) == published
        for policy in answer["policies"].values():
            assert policy["fingerprint"]
            assert policy["rules"]

    def test_grading_method_states_the_limits(self, site: Path) -> None:
        answer = _call(site, "grading_method")
        assert answer["limits"] == LIMITS
        assert any("does not rank" in limit for limit in answer["limits"])
        assert any("No price comparison" in limit for limit in answer["limits"])

    def test_get_file_returns_the_row_with_its_policy(self, site: Path) -> None:
        cohorts = _call(site, "list_files")["cohorts"]
        cohort_id, slugs = next(iter(cohorts.items()))
        answer = _call(site, "get_file", slug=slugs[0], cohort_id=cohort_id)
        assert answer["file"]["slug"] == slugs[0]
        assert answer["grade_policy"]["fingerprint"]
        assert answer["comparison_scope"]["profile"]


class TestProtocol:
    def test_initialize_names_the_server_and_protocol(self, site: Path) -> None:
        response = handle(site, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert response is not None
        assert response["result"]["serverInfo"]["name"] == "io.github.chelseakr/mrf-honest"

    def test_tools_list_matches_the_declared_tools(self, site: Path) -> None:
        response = handle(site, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert response is not None
        assert [tool["name"] for tool in response["result"]["tools"]] == [
            tool["name"] for tool in TOOLS
        ]

    def test_every_tool_declares_a_description_and_a_schema(self) -> None:
        for tool in TOOLS:
            assert str(tool["description"]).strip()
            assert cast(dict[str, Any], tool["inputSchema"])["type"] == "object"

    def test_an_unknown_method_is_a_json_rpc_error(self, site: Path) -> None:
        response = handle(site, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        assert response is not None
        assert response["error"]["code"] == -32601

    def test_a_notification_gets_no_response(self, site: Path) -> None:
        assert handle(site, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_malformed_input_does_not_end_the_session(self, site: Path) -> None:
        stdin = io.StringIO('not json\n\n{"jsonrpc":"2.0","id":9,"method":"initialize"}\n')
        stdout = io.StringIO()
        assert serve(site, stdin, stdout) == 0
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert responses[0]["error"]["code"] == -32700
        assert responses[1]["id"] == 9

    def test_a_failing_tool_call_becomes_an_error_not_a_crash(self, tmp_path: Path) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "list_cohorts", "arguments": {}},
        }
        stdout = io.StringIO()
        assert serve(tmp_path, io.StringIO(json.dumps(request) + "\n"), stdout) == 0
        response = json.loads(stdout.getvalue())
        assert response["error"]["code"] == -32603
        assert "DatasetUnavailable" in response["error"]["message"]


def test_the_server_opens_no_network_module() -> None:
    """The whole point of a read-only server is that it cannot reach out."""

    source = (ROOT / "src" / "mrf_honest" / "mcp.py").read_text(encoding="utf-8")
    for forbidden in ("urllib", "http.client", "socket", "requests", "mrf_honest.fetch"):
        assert forbidden not in source
