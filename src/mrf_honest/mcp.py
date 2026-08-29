"""Read-only MCP server over the published dataset.

Exposes the published grades to an assistant without giving it network reach. Every answer is
read from the static JSON API this project's own render wrote (`api/index.json` and
`api/cohorts/*.json`); there is no tool here that retrieves a hospital's file, because a model
deciding to fetch arbitrary URLs is a different and much larger surface than one reading a
document this project already published.

The refusals are the point. `docs/how-we-compare.md` forbids pooling rows assessed under
different profiles, and an assistant asking "how many A grades are there" is asking for exactly
that pooled number. A server that answered would undo, in one sentence, the boundary the site
spends a page establishing, so it refuses and says which cohort to ask about instead.

JSON-RPC 2.0 over stdio, standard library only (ADR 0002).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "io.github.chelseakr/mrf-honest"
SERVER_INFO = {"name": SERVER_NAME, "version": "0.1.0"}

#: The scope keys along which two rows are not comparable. Repeated in every answer, because a
#: consumer that never reads the site should still meet the boundary.
NOT_COMPARABLE_ACROSS = (
    "profile",
    "publisher_type",
    "url_provenance",
    "assessment_policy_fingerprint",
    "retrieval_policy_fingerprint",
    "as_of",
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_cohorts",
        "description": (
            "Every published cohort with its scope, its grade policy, its summary counts and "
            "its statistics outcome. Start here: a grade is only meaningful inside one cohort."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_files",
        "description": (
            "Graded files in one cohort. A cohort_id is required whenever a grade filter is "
            "given, because counting a letter across cohorts pools rows produced under "
            "different profiles and policies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cohort_id": {"type": "string", "description": "Which cohort to read"},
                "grade": {"type": "string", "description": "Filter by letter grade or NOT_GRADED"},
            },
        },
    },
    {
        "name": "get_file",
        "description": (
            "The full published record for one file: its grade with the sentence explaining it, "
            "every dimension, every finding with its citations, and its warehouse outcome."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "cohort_id": {
                    "type": "string",
                    "description": "Required if the slug is graded twice",
                },
            },
            "required": ["slug"],
        },
    },
    {
        "name": "cohort_statistics",
        "description": (
            "The population shares for one cohort, each with its denominator and interval, or "
            "the stated reason no share was published. A refusal here is an answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"cohort_id": {"type": "string"}},
            "required": ["cohort_id"],
        },
    },
    {
        "name": "grading_method",
        "description": (
            "How a grade is computed and what the dataset does not claim. The rule table is "
            "read from the policy the published grades were minted under, not from a summary. "
            "Read this before characterizing any grade."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

#: What the dataset does not say. Kept next to the tool that serves it so it cannot be answered
#: from a paraphrase.
LIMITS = [
    "A grade describes one published file under one stated policy on one date. It does not rank "
    "a hospital, price care, or determine compliance with 45 CFR part 180.",
    "An A means the implemented checks emitted no error or warning findings over the assessed "
    "scope. It is not exhaustive schema validation and it is not the official CMS validator.",
    "Rows are not comparable across " + ", ".join(NOT_COMPARABLE_ACROSS) + ".",
    "A cohort-wide count describes that cohort. Where a cohort's manifest records a probability "
    "stratum, the shares in cohort_statistics describe the drawn sample and carry an interval; "
    "where it does not, that tool states why no share was published.",
    "No price comparison is published anywhere. Rate representations are separated structurally, "
    "but no comparison of amounts is offered.",
]


class DatasetUnavailable(RuntimeError):
    """The published API is not where the server was told to look."""


def _load_index(site_dir: Path) -> dict[str, Any]:
    index_path = site_dir / "api" / "index.json"
    if not index_path.is_file():
        raise DatasetUnavailable(
            f"no published API at {index_path}; run 'mrf-honest site' to write one"
        )
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _cohort_document_path(site_dir: Path, cohort_id: str) -> Path | None:
    """Resolve a cohort id to its published document, or refuse to build a path at all.

    `cohort_id` is tool input: it arrives from whatever is driving the assistant, so it is
    attacker-controlled text and never a filename. Membership in the published index is
    therefore checked *before* the id reaches the filesystem. An id the index does not list
    never becomes a path, so no spelling of a separator, an escape, or an encoding can name a
    document outside the published set: the set is the check, not the string's shape.

    Interpolating the id straight into the path is what this replaces, and it was not a
    theoretical hole. `cohort_id="../../../secretplace/notacohort"` served an unpublished
    document whose `comparison_scope` was `null` -- grades with no scope, which is precisely
    what `docs/how-we-compare.md` establishes may never be published, delivered by the one
    server whose whole purpose is to refuse what the site refuses.

    The containment check below is defence in depth for the case membership cannot cover: an
    index that itself named an id which walks out of the cohorts directory. That would mean
    this project's own render wrote something it must not, so it fails loudly rather than
    reading the file.
    """

    if cohort_id not in _cohort_ids(_load_index(site_dir)):
        return None
    cohorts_dir = (site_dir / "api" / "cohorts").resolve()
    path = (cohorts_dir / f"{cohort_id}.json").resolve()
    if path.parent != cohorts_dir:
        raise DatasetUnavailable(
            f"published index names cohort {cohort_id!r}, whose document resolves to {path}, "
            f"outside the published cohorts directory {cohorts_dir}; refusing to read it"
        )
    return path


def _load_cohort(site_dir: Path, cohort_id: str) -> dict[str, Any] | None:
    path = _cohort_document_path(site_dir, cohort_id)
    if path is None or not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _text(payload: object) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}]}


def _refusal(reason: str, **detail: Any) -> dict[str, Any]:
    """Every refusal is an answer with a reason, never an empty result set."""

    return _text({"outcome": "refused", "reason": reason, **detail})


def _cohort_ids(index: dict[str, Any]) -> list[str]:
    return [
        str(entry.get("cohort_id")) for entry in index.get("cohorts", []) if isinstance(entry, dict)
    ]


def _list_cohorts(site_dir: Path) -> dict[str, Any]:
    index = _load_index(site_dir)
    return _text(
        {
            "not_comparable_across": list(NOT_COMPARABLE_ACROSS),
            "caveat": index.get("caveat"),
            "cohorts": index.get("cohorts", []),
        }
    )


def _list_files(site_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    index = _load_index(site_dir)
    cohort_id = arguments.get("cohort_id")
    grade = arguments.get("grade")
    if grade is not None and not cohort_id:
        return _refusal(
            "a grade filter needs a cohort_id: counting a letter across cohorts pools rows "
            "produced under different profiles and policies, which is the one comparison this "
            "project refuses to make",
            available_cohorts=_cohort_ids(index),
        )
    if not cohort_id:
        return _text(
            {
                "not_comparable_across": list(NOT_COMPARABLE_ACROSS),
                "cohorts": {
                    identifier: _slugs(site_dir, identifier) for identifier in _cohort_ids(index)
                },
            }
        )
    document = _load_cohort(site_dir, str(cohort_id))
    if document is None:
        return _refusal(f"no published cohort {cohort_id!r}", available_cohorts=_cohort_ids(index))
    rows = [row for row in document.get("files", []) if isinstance(row, dict)]
    if grade is not None:
        rows = [row for row in rows if _grade_of(row) == str(grade)]
    return _text(
        {
            "cohort_id": cohort_id,
            "comparison_scope": document.get("comparison_scope"),
            "count": len(rows),
            "files": [
                {
                    "slug": row.get("slug"),
                    "publisher_name": row.get("publisher_name"),
                    "grade": _grade_of(row),
                    "reason": _grade_reason(row),
                }
                for row in rows
            ],
        }
    )


def _slugs(site_dir: Path, cohort_id: str) -> list[str]:
    document = _load_cohort(site_dir, cohort_id)
    if document is None:
        return []
    return [str(row.get("slug")) for row in document.get("files", []) if isinstance(row, dict)]


def _grade_of(row: dict[str, Any]) -> str:
    grade = row.get("grade")
    return str(grade.get("grade")) if isinstance(grade, dict) else ""


def _grade_reason(row: dict[str, Any]) -> str:
    grade = row.get("grade")
    return str(grade.get("reason")) if isinstance(grade, dict) else ""


def _get_file(site_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    index = _load_index(site_dir)
    slug = str(arguments.get("slug") or "")
    if not slug:
        return _refusal("get_file needs a slug")
    wanted = arguments.get("cohort_id")
    matches: list[tuple[str, dict[str, Any]]] = []
    for cohort_id in _cohort_ids(index):
        if wanted and cohort_id != str(wanted):
            continue
        document = _load_cohort(site_dir, cohort_id)
        for row in (document or {}).get("files", []):
            if isinstance(row, dict) and str(row.get("slug")) == slug:
                matches.append((cohort_id, row))
    if not matches:
        return _refusal(f"no published row for slug {slug!r}", cohort_id=wanted)
    if len(matches) > 1:
        return _refusal(
            f"slug {slug!r} is published in more than one cohort; name the cohort_id, because "
            "the two rows were assessed under different policies and are not the same claim",
            cohorts=[cohort_id for cohort_id, _ in matches],
        )
    cohort_id, row = matches[0]
    document = _load_cohort(site_dir, cohort_id) or {}
    return _text(
        {
            "cohort_id": cohort_id,
            "comparison_scope": document.get("comparison_scope"),
            "grade_policy": document.get("grade_policy"),
            "not_comparable_across": list(NOT_COMPARABLE_ACROSS),
            "file": row,
        }
    )


def _cohort_statistics(site_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    index = _load_index(site_dir)
    cohort_id = str(arguments.get("cohort_id") or "")
    document = _load_cohort(site_dir, cohort_id) if cohort_id else None
    if document is None:
        return _refusal(f"no published cohort {cohort_id!r}", available_cohorts=_cohort_ids(index))
    statistics = document.get("statistics")
    if not isinstance(statistics, dict):
        return _refusal(
            f"cohort {cohort_id!r} was published before the statistics layer and carries no "
            "shares; regenerate its comparison document"
        )
    return _text({"cohort_id": cohort_id, "statistics": statistics})


def _grading_method(site_dir: Path) -> dict[str, Any]:
    """The rule table as published, read from every cohort's own grade policy."""

    index = _load_index(site_dir)
    policies: dict[str, Any] = {}
    for entry in index.get("cohorts", []):
        if not isinstance(entry, dict):
            continue
        policy = entry.get("grade_policy")
        if isinstance(policy, dict):
            policies[str(policy.get("version"))] = policy
    return _text(
        {
            "policies": policies,
            "limits": LIMITS,
            "not_comparable_across": list(NOT_COMPARABLE_ACROSS),
            "methodology": "https://github.com/ChelseaKR/mrf-honest/blob/master/docs/how-we-grade.md",
        }
    )


def _list_cohorts_tool(site_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    return _list_cohorts(site_dir)


def _grading_method_tool(site_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    return _grading_method(site_dir)


_Handler = Callable[[Path, dict[str, Any]], dict[str, Any]]

_HANDLERS: dict[str, _Handler] = {
    "list_cohorts": _list_cohorts_tool,
    "list_files": _list_files,
    "get_file": _get_file,
    "cohort_statistics": _cohort_statistics,
    "grading_method": _grading_method_tool,
}


def call_tool(site_dir: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call, refusing an unknown name rather than answering approximately."""

    handler = _HANDLERS.get(name)
    if handler is None:
        return _refusal(f"unknown tool {name!r}", available_tools=sorted(_HANDLERS))
    return handler(site_dir, arguments)


def handle(site_dir: Path, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        arguments = params.get("arguments") or {}
        result = call_tool(site_dir, str(params.get("name") or ""), arguments)
    elif request_id is None:
        return None  # a notification this server does not act on
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method!r}"},
        }
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(site_dir: Path, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Read one JSON-RPC request per line and answer it. A bad request never kills the server."""

    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for raw in source:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _write(sink, {"jsonrpc": "2.0", "id": None, "error": _error(-32700, "parse error")})
            continue
        try:
            response = handle(site_dir, request if isinstance(request, dict) else {})
        except Exception as exc:
            identifier = request.get("id") if isinstance(request, dict) else None
            message = f"{type(exc).__name__}: {exc}"
            response = {"jsonrpc": "2.0", "id": identifier, "error": _error(-32603, message)}
        if response is not None:
            _write(sink, response)
    return 0


def _error(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def _write(sink: TextIO, payload: dict[str, Any]) -> None:
    sink.write(json.dumps(payload) + "\n")
    sink.flush()
