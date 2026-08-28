"""The published dataset, its Table Schema, and the static JSON API.

The load-bearing claims here are that the exports are derived from the same documents the site
renders, that every row carries the scope that makes it uncomparable to rows of another
profile, and that a refusal reaches a consumer as a stated field rather than a missing key.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import cast

from test_cohort import GENERATED_AT, _framed_manifest, _two_records

from mrf_honest.cohort import build_comparison
from mrf_honest.dataset import (
    COLUMNS,
    DATASET_NAME,
    DATASET_VERSION,
    api_documents,
    dataset_csv,
    dataset_rows,
    missing_exports,
    table_schema,
)
from mrf_honest.site import DEFAULT_ORIGIN, render_site

ROOT = Path(__file__).resolve().parent.parent
COHORTS = ROOT / "data" / "cohorts"
PUBLISHED = sorted(COHORTS.glob("*.comparison.json"))


def _published() -> list[dict[str, object]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in PUBLISHED]


def _newest_per_profile() -> list[dict[str, object]]:
    """What `pages.yml` renders: the newest committed cohort of each profile.

    Two cohorts of the same profile share file slugs, and `render_site` refuses that, because a
    file page needs exactly one source row.
    """

    newest: dict[str, tuple[str, dict[str, object]]] = {}
    for document in _published():
        cohort = cast(dict[str, object], document["cohort"])
        profile = str(cast(dict[str, object], cohort["comparison_scope"])["profile"])
        key = str(cohort["as_of"])
        if profile not in newest or key > newest[profile][0]:
            newest[profile] = (key, document)
    return [document for _, document in newest.values()]


def _rows_of(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


class TestSchema:
    def test_the_schema_describes_exactly_the_columns_the_csv_carries(self) -> None:
        schema = table_schema()
        declared = [str(field["name"]) for field in cast(list[dict[str, object]], schema["fields"])]
        assert declared == [name for name, _, _ in COLUMNS]
        header = _rows_of(dataset_csv(_published()))[0]
        assert list(header) == declared

    def test_every_column_has_a_type_and_a_description(self) -> None:
        for field in cast(list[dict[str, object]], table_schema()["fields"]):
            assert field["type"]
            assert str(field["description"]).strip()

    def test_the_schema_names_itself_and_its_version(self) -> None:
        schema = table_schema()
        assert schema["name"] == DATASET_NAME
        assert schema["version"] == DATASET_VERSION
        assert schema["primaryKey"] == ["cohort_id", "slug"]
        assert schema["missingValues"] == [""]

    def test_the_schema_carries_the_caveat_the_site_carries(self) -> None:
        """A consumer who never opens the page still meets the boundary."""

        caveat = str(table_schema()["caveat"])
        assert "does not rank an organization" in caveat
        assert "not comparable across profile" in caveat


class TestRows:
    def test_one_row_per_published_file_across_every_cohort(self) -> None:
        comparisons = _published()
        expected = sum(len(cast(list[object], c["files"])) for c in comparisons)
        assert len(dataset_rows(comparisons)) == expected

    def test_the_primary_key_is_unique(self) -> None:
        rows = dataset_rows(_published())
        keys = [(row["cohort_id"], row["slug"]) for row in rows]
        assert len(set(keys)) == len(keys)

    def test_every_row_carries_the_scope_that_makes_it_uncomparable(self) -> None:
        """A grade with no profile beside it invites the pooling how-we-compare.md forbids."""

        for row in dataset_rows(_published()):
            assert row["profile"]
            assert row["publisher_type"]
            assert row["assessment_policy_fingerprint"]
            assert row["retrieval_policy_fingerprint"]
            assert row["grade_policy_fingerprint"]

    def test_the_two_profiles_stay_distinguishable_in_one_file(self) -> None:
        """One file holds rows of both profiles, and every row says which it belongs to."""

        rows = dataset_rows(_published())
        profiles = {row["profile"] for row in rows}
        assert len(profiles) > 1, "the committed cohorts should span more than one profile"
        for comparison in _published():
            cohort = cast(dict[str, object], comparison["cohort"])
            cohort_id = str(cohort["cohort_id"])
            profile = str(cast(dict[str, object], cohort["comparison_scope"])["profile"])
            in_cohort = {row["profile"] for row in rows if row["cohort_id"] == cohort_id}
            assert in_cohort == {profile}

    def test_a_refused_warehouse_load_is_a_stated_status_not_a_blank(self) -> None:
        rows = dataset_rows(_published())
        assert any(row["lakehouse_status"] == "refused" for row in rows)

    def test_finding_counts_agree_with_the_document_they_came_from(self) -> None:
        for comparison in _published():
            for entry in cast(list[dict[str, object]], comparison["files"]):
                severities = [
                    finding["severity"]
                    for dimension in cast(
                        dict[str, dict[str, object]], entry["dimensions"]
                    ).values()
                    for finding in cast(list[dict[str, str]], dimension["findings"])
                ]
                row = next(r for r in dataset_rows([comparison]) if r["slug"] == entry["slug"])
                assert int(row["error_findings"]) == severities.count("ERROR")
                assert int(row["warning_findings"]) == severities.count("WARNING")
                assert int(row["info_findings"]) == severities.count("INFO")

    def test_booleans_render_as_words_not_python_repr(self) -> None:
        for row in dataset_rows(_published()):
            assert row["network_attempted"] in {"true", "false"}

    def test_the_csv_uses_rfc_4180_line_endings(self) -> None:
        assert dataset_csv(_published()).endswith("\r\n")

    def test_rows_are_sorted_so_the_bytes_are_reproducible(self) -> None:
        rows = dataset_rows(_published())
        assert rows == sorted(rows, key=lambda row: (row["cohort_id"], row["slug"]))
        assert dataset_csv(_published()) == dataset_csv(_published())


class TestApi:
    def test_the_index_lists_every_cohort_with_its_scope_and_caveat(self) -> None:
        comparisons = _published()
        documents = api_documents(comparisons, "https://example.test")
        index = documents["api/index.json"]
        assert "does not rank an organization" in str(index["caveat"])
        assert "profile" in cast(list[str], index["not_comparable_across"])
        listed = {
            str(entry["cohort_id"]) for entry in cast(list[dict[str, object]], index["cohorts"])
        }
        expected = {str(cast(dict[str, object], c["cohort"])["cohort_id"]) for c in comparisons}
        assert listed == expected

    def test_a_cohort_document_carries_its_rows_exclusions_and_matrix(self) -> None:
        comparisons = _published()
        documents = api_documents(comparisons, "https://example.test")
        for comparison in comparisons:
            cohort_id = str(cast(dict[str, object], comparison["cohort"])["cohort_id"])
            document = documents[f"api/cohorts/{cohort_id}.json"]
            assert len(cast(list[object], document["files"])) == len(
                cast(list[object], comparison["files"])
            )
            assert len(cast(list[object], document["exclusions"])) == len(
                cast(list[object], comparison["exclusions"])
            )

    def test_a_statistics_refusal_reaches_the_api_as_a_stated_field(self) -> None:
        """The same rule as the page: a refusal is a thing to read, not a missing key."""

        comparisons = _published()
        documents = api_documents(comparisons, "https://example.test")
        outcomes = [
            cast(dict[str, object], entry["statistics"])
            for entry in cast(list[dict[str, object]], documents["api/index.json"]["cohorts"])
        ]
        assert all(outcome is not None for outcome in outcomes)
        refused = [outcome for outcome in outcomes if outcome["refusal"]]
        assert refused, "the committed cohorts should include at least one refusal"
        for outcome in refused:
            assert str(cast(dict[str, object], outcome["refusal"])["reason"]).strip()


class TestRenderWritesThem:
    def test_the_render_writes_the_dataset_schema_and_api(self, tmp_path: Path) -> None:
        comparisons = _newest_per_profile()
        render_site(comparisons, tmp_path, origin="https://example.test")
        assert (tmp_path / "dataset.csv").is_file()
        assert (tmp_path / "dataset.schema.json").is_file()
        assert (tmp_path / "api" / "index.json").is_file()
        assert missing_exports(comparisons, tmp_path) == []

    def test_the_index_page_links_the_exports(self, tmp_path: Path) -> None:
        render_site(_newest_per_profile(), tmp_path, origin="https://example.test")
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'href="dataset.csv"' in html
        assert 'href="dataset.schema.json"' in html
        assert 'href="api/index.json"' in html


class TestMissingExports:
    """This is the deploy path's check, so here it is failing on purpose."""

    def _rendered(self, tmp_path: Path) -> list[dict[str, object]]:
        comparisons = [
            build_comparison(
                _two_records(tmp_path / "bodies"),
                _framed_manifest(sample_size=22, exclusions=20),
                generated_at=GENERATED_AT,
            )
        ]
        render_site(comparisons, tmp_path / "site", origin=DEFAULT_ORIGIN)
        return comparisons

    def test_a_truncated_dataset_is_reported(self, tmp_path: Path) -> None:
        comparisons = self._rendered(tmp_path)
        path = tmp_path / "site" / "dataset.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\r\n".join(lines[:2]) + "\r\n", encoding="utf-8", newline="")
        assert missing_exports(comparisons, tmp_path / "site") == [
            "dataset.csv holds 1 rows against 2 expected"
        ]

    def test_a_deleted_dataset_is_reported(self, tmp_path: Path) -> None:
        comparisons = self._rendered(tmp_path)
        (tmp_path / "site" / "dataset.csv").unlink()
        assert missing_exports(comparisons, tmp_path / "site") == ["dataset.csv was not written"]

    def test_a_schema_that_stops_describing_its_columns_is_reported(self, tmp_path: Path) -> None:
        comparisons = self._rendered(tmp_path)
        path = tmp_path / "site" / "dataset.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["fields"] = schema["fields"][:3]
        path.write_text(json.dumps(schema), encoding="utf-8")
        assert "dataset.schema.json does not describe dataset.csv's columns" in missing_exports(
            comparisons, tmp_path / "site"
        )

    def test_a_missing_api_index_is_reported(self, tmp_path: Path) -> None:
        comparisons = self._rendered(tmp_path)
        (tmp_path / "site" / "api" / "index.json").unlink()
        assert missing_exports(comparisons, tmp_path / "site") == ["api/index.json was not written"]

    def test_an_index_that_forgets_a_cohort_is_reported(self, tmp_path: Path) -> None:
        comparisons = self._rendered(tmp_path)
        path = tmp_path / "site" / "api" / "index.json"
        index = json.loads(path.read_text(encoding="utf-8"))
        index["cohorts"] = []
        path.write_text(json.dumps(index), encoding="utf-8")
        problems = missing_exports(comparisons, tmp_path / "site")
        assert any("does not list" in problem for problem in problems)

    def test_a_reordered_header_is_reported(self, tmp_path: Path) -> None:
        comparisons = self._rendered(tmp_path)
        path = tmp_path / "site" / "dataset.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        lines[0] = ",".join([header[1], header[0], *header[2:]])
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
        assert "dataset.csv's header is not the declared column order" in missing_exports(
            comparisons, tmp_path / "site"
        )
