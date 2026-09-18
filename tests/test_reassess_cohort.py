"""tools/reassess_cohort.py: a re-assessment moves the policy and nothing else.

The tool exists because a change to inspection semantics moves the assessment policy fingerprint,
and the comparison then refuses every committed row of that profile until it is re-assessed. What
it must never do is change anything a retrieval established, grade a body it cannot prove is the
recorded one, or fetch a missing body to fill the gap. These tests hold it to that against the
committed CSV cohorts, which the cache-free CI runner can read but cannot re-inspect.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COHORTS = REPO_ROOT / "data" / "cohorts"
CSV_COHORTS = ("2026-08-19-csv", "2026-09-12-csv")


def _tool() -> Any:
    name = "reassess_cohort"
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tool = _tool()


def _rows(cohort: str) -> list[dict[str, Any]]:
    path = COHORTS / f"{cohort}.assessments.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _uninspected(cohort: str) -> list[dict[str, Any]]:
    return [row for row in _rows(cohort) if row["inspection"] is None]


def _inspected_with_a_plain_url(cohort: str) -> dict[str, Any]:
    for row in _rows(cohort):
        subject = row["subject"]
        plain = tool.text_digest(subject["requested_url"]) == subject["requested_url_sha256"]
        if row["inspection"] is not None and plain:
            return row
    raise AssertionError(f"{cohort} has no inspected row with an unredacted URL")


@pytest.mark.parametrize("cohort", CSV_COHORTS)
def test_a_committed_row_reassessed_under_its_own_policy_is_reproduced_exactly(
    cohort: str, tmp_path: Path
) -> None:
    """The null control, over every committed row that needs no body to rebuild.

    Every committed row already carries the current policy, so re-assessing it must change
    nothing at all: not the retrieval evidence, not the scorecard, not the digests. A tool that
    moved anything here would be moving it on every real re-assessment too.
    """
    rows = _uninspected(cohort)
    assert rows, f"{cohort} has no uninspected row; this control would examine nothing"
    for row in rows:
        rebuilt = tool.reassess_row(row, tmp_path, {}).to_dict()
        assert rebuilt == row, row["subject"]["location_id"]


def test_an_inspected_row_whose_body_is_not_in_the_cache_stops_rather_than_fetching(
    tmp_path: Path,
) -> None:
    row = _inspected_with_a_plain_url("2026-08-19-csv")
    with pytest.raises(tool.ReassessmentError, match="is not in"):
        tool.reassess_row(row, tmp_path, {})


def test_a_cached_body_that_no_longer_hashes_to_its_digest_stops_the_run(tmp_path: Path) -> None:
    row = _inspected_with_a_plain_url("2026-08-19-csv")
    digest = row["retrieval"]["content_sha256"]
    blob = tmp_path / "blobs" / digest[:2] / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"not the recorded body\n")
    with pytest.raises(tool.ReassessmentError, match="no longer matches"):
        tool.reassess_row(row, tmp_path, {})


def test_retrieval_evidence_that_would_serialize_differently_stops_the_run(
    tmp_path: Path,
) -> None:
    """The evidence is carried over, never re-derived into something else.

    A multi-line error is folded onto one line when evidence is written, so a committed record
    holding one could not have been written by this code; rebuilding it would publish different
    evidence under the same retrieval, and the tool refuses.
    """
    row = copy.deepcopy(_uninspected("2026-08-19-csv")[0])
    row["retrieval"]["error"] = "first line\nsecond line"
    with pytest.raises(tool.ReassessmentError, match="differs from the committed record"):
        tool.reassess_row(row, tmp_path, {})


def test_a_redacted_url_is_rebuilt_only_from_what_the_cache_recorded(tmp_path: Path) -> None:
    """A published row strips a URL's query, so only the cache can say what was requested."""
    redacted = [
        row
        for cohort in CSV_COHORTS
        for row in _rows(cohort)
        if tool.text_digest(row["subject"]["requested_url"])
        != row["subject"]["requested_url_sha256"]
    ]
    assert redacted, "no committed row has a redacted URL; this test would examine nothing"
    with pytest.raises(tool.ReassessmentError, match="redacted"):
        tool.reassess_row(redacted[0], tmp_path, {})

    metadata = tmp_path / "metadata"
    metadata.mkdir()
    exact = "https://files.example.org/prices.csv?file=1"
    (metadata / "record.json").write_text(json.dumps({"url": exact}), encoding="utf-8")
    assert tool._cached_urls(tmp_path) == {tool.text_digest(exact): exact}
