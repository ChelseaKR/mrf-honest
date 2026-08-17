from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import mrf_honest.lakehouse as lakehouse
from mrf_honest.contracts import ContractError, validate_contracts
from mrf_honest.lakehouse import (
    LakehouseError,
    PublisherRef,
    ingest_hospital_file,
    query_file_profile,
)
from mrf_honest.stream import BOM


def _document(*, setting: str = "outpatient") -> dict[str, object]:
    return {
        "hospital_name": "Example Medical Center",
        "last_updated_on": "2026-04-01",
        "version": "3.0.0",
        "location_name": ["Example Main"],
        "hospital_address": ["1 Main Street, Example, CA 90000"],
        "license_information": {"license_number": "123", "state": "CA"},
        "attestation": {
            "attestation": "CMS-defined text omitted from this compact test fixture",
            "confirm_attestation": True,
            "attester_name": "A. Attester",
        },
        "type_2_npi": ["1234567890"],
        "financial_aid_policy": ["40% off facility standard gross charges."],
        "modifier_information": [
            {
                "description": "CLIA-waived test",
                "code": "QW",
                "setting": "both",
                "modifier_payer_information": [
                    {
                        "payer_name": "Example Payer",
                        "plan_name": "Example PPO",
                        "description": "The stated rate includes this modifier.",
                    }
                ],
            },
            {
                "description": "Reduced services",
                "code": " 52 ",
                "modifier_payer_information": [
                    {
                        "payer_name": " example payer ",
                        "plan_name": " example ppo ",
                        "description": "Canonical comparison only; raw text is retained.",
                    }
                ],
            },
        ],
        "standard_charge_information": [
            {
                "description": "Office visit",
                "code_information": [{"code": "99213", "type": "CPT"}],
                "standard_charges": [
                    {
                        "minimum": 80.0,
                        "maximum": 110.0,
                        "gross_charge": 200.0,
                        "discounted_cash": 120.0,
                        "setting": setting,
                        "billing_class": "facility",
                        "modifier_code": ["QW", "52", "ZZ"],
                        "payers_information": [
                            {
                                "payer_name": "Example Payer",
                                "plan_name": "Example PPO",
                                "methodology": "fee schedule",
                                "standard_charge_dollar": 95.0,
                            },
                            {
                                "payer_name": "Example Payer",
                                "plan_name": "Example Percentage Plan",
                                "methodology": "percent of total billed charges",
                                "standard_charge_percentage": 65.5,
                                "count": "0",
                                "additional_payer_notes": "No remittances in the lookback period.",
                            },
                            {
                                "payer_name": "Example Payer",
                                "plan_name": "Example Formula Plan",
                                "methodology": "other",
                                "standard_charge_algorithm": "Base schedule plus case adjustment",
                                "count": "0",
                                "additional_payer_notes": "Contract formula; no remittances.",
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _write(path: Path, document: dict[str, object], *, bom: bool = False) -> Path:
    raw = json.dumps(document).encode()
    path.write_bytes((BOM if bom else b"") + raw)
    return path


def _modifier(code: str, setting: str | None, description: str) -> dict[str, object]:
    value: dict[str, object] = {
        "description": description,
        "code": code,
        "modifier_payer_information": [
            {
                "payer_name": "Example Payer",
                "plan_name": "Example PPO",
                "description": f"{description} for the example plan.",
            }
        ],
    }
    if setting is not None:
        value["setting"] = setting
    return value


def test_ingests_contracts_and_exports_a_segmented_lakehouse(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document(), bom=True)
    warehouse = tmp_path / "warehouse"
    result = ingest_hospital_file(
        source,
        warehouse,
        publisher=PublisherRef("example-health", "Example Health"),
    )

    assert result.status == "success"
    assert not result.reused
    assert result.counts.items == 1
    assert result.counts.codes == 1
    assert result.counts.charge_groups == 1
    assert result.counts.payer_rates == 3
    assert result.counts.modifiers == 2
    assert result.counts.modifier_payer_mappings == 2
    assert result.counts.charge_modifiers == 3
    assert result.counts.had_bom
    assert len(result.parquet_files) == 13
    assert all(path.is_file() for path in result.parquet_files)

    profile = query_file_profile(warehouse, result.run_id)
    assert {row["rate_kind"] for row in profile} == {"dollar", "percentage", "algorithm"}
    eligibility = {row["rate_kind"]: row["eligible_for_segmented_comparison"] for row in profile}
    assert eligibility == {"dollar": True, "percentage": False, "algorithm": False}

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["contracts"] == {"status": "passed"}
    assert manifest["schema_version"] == lakehouse.MANIFEST_SCHEMA_VERSION == 4
    assert len(manifest["transformation_fingerprint"]) == 64
    assert manifest["inspection_fingerprint"] == lakehouse.INSPECTION_FINGERPRINT
    assert manifest["manifest_body_sha256"] == lakehouse._manifest_body_sha256(manifest)
    assert manifest["source"]["sha256"] == result.source_file_id
    assert manifest["inspection"]["as_of"]
    assert manifest["inspection"]["scorecard"]["retrievability"]["status"] == "NOT_ASSESSED"
    assert manifest["envelope"]["financial_aid_policy"] == [
        "40% off facility standard gross charges."
    ]
    assert manifest["duckdb"]["effective_threads"] == 2
    assert manifest["duckdb"]["effective_memory_limit"]
    assert "RSS cap" in manifest["duckdb"]["memory_limit_scope"]
    assert all(metric["wall_time_ms"] >= 0 for metric in manifest["model_metrics"])
    assert {artifact["path"] for artifact in manifest["artifacts"]} == {
        manifest["source_archive"],
        *manifest["parquet_files"],
    }
    source_archive = warehouse / manifest["source_archive"]
    assert source_archive.is_file()
    assert hashlib.sha256(source_archive.read_bytes()).hexdigest() == result.source_file_id

    finding_count = sum(
        len(dimension["findings"]) for dimension in manifest["inspection"]["scorecard"].values()
    )

    with duckdb.connect(str(result.database_path), read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM int_rate_observation WHERE run_id = ?", [result.run_id]
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM mart_segmented_dollar_rate WHERE run_id = ?", [result.run_id]
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM file_finding WHERE run_id = ?", [result.run_id]
            ).fetchone()[0]
            == finding_count
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM stg_modifier WHERE run_id = ?", [result.run_id]
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM stg_modifier_payer WHERE run_id = ?", [result.run_id]
            ).fetchone()[0]
            == 2
        )
        context_row = connection.execute(
            """SELECT modifier_context_json FROM mart_segmented_dollar_rate
            WHERE run_id = ? AND plan_name = 'Example PPO'""",
            [result.run_id],
        ).fetchone()
        assert context_row is not None
        context = json.loads(context_row[0])
        assert [item["modifier_resolution_status"] for item in context] == [
            "exact",
            "canonical",
            "unresolved",
        ]
        assert [item["payer_mapping_resolution_status"] for item in context] == [
            "exact",
            "canonical",
            "unresolved_modifier",
        ]
        assert [item["modifier_setting"] for item in context] == ["both", None, None]


def test_modifier_resolution_reports_a_setting_mismatch(tmp_path: Path) -> None:
    document = _document()
    document["modifier_information"] = [_modifier("IP", "inpatient", "Inpatient only")]
    rows = document["standard_charge_information"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    charges = rows[0]["standard_charges"]
    assert isinstance(charges, list) and isinstance(charges[0], dict)
    charges[0]["modifier_code"] = ["IP"]
    source = _write(tmp_path / "hospital.json", document)

    result = ingest_hospital_file(
        source,
        tmp_path / "warehouse",
        publisher=PublisherRef("example-health"),
    )

    with duckdb.connect(str(result.database_path), read_only=True) as connection:
        row = connection.execute(
            """SELECT modifier_context_json FROM mart_segmented_dollar_rate
            WHERE run_id = ? AND plan_name = 'Example PPO'""",
            [result.run_id],
        ).fetchone()
    assert row is not None
    context = json.loads(row[0])
    assert context == [
        {
            "modifier_code": "IP",
            "modifier_description": None,
            "modifier_setting": None,
            "candidate_modifier_settings": ["inpatient"],
            "modifier_resolution_status": "setting_mismatch",
            "payer_mapping_description": None,
            "payer_mapping_resolution_status": "modifier_setting_mismatch",
        }
    ]


def test_setting_specific_modifier_definitions_resolve_without_ambiguity(tmp_path: Path) -> None:
    document = _document()
    document["modifier_information"] = [
        _modifier("ZX", "inpatient", "Inpatient definition"),
        _modifier(" zx ", "outpatient", "Outpatient definition"),
    ]
    rows = document["standard_charge_information"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    charges = rows[0]["standard_charges"]
    assert isinstance(charges, list) and isinstance(charges[0], dict)
    charges[0]["modifier_code"] = ["ZX"]
    source = _write(tmp_path / "hospital.json", document)

    result = ingest_hospital_file(
        source,
        tmp_path / "warehouse",
        publisher=PublisherRef("example-health"),
    )

    with duckdb.connect(str(result.database_path), read_only=True) as connection:
        row = connection.execute(
            """SELECT modifier_context_json FROM mart_segmented_dollar_rate
            WHERE run_id = ? AND plan_name = 'Example PPO'""",
            [result.run_id],
        ).fetchone()
    assert row is not None
    context = json.loads(row[0])
    assert context[0]["modifier_description"] == "Outpatient definition"
    assert context[0]["modifier_setting"] == "outpatient"
    assert context[0]["modifier_resolution_status"] == "canonical"


def test_overlapping_modifier_setting_definitions_fail_the_contract(tmp_path: Path) -> None:
    document = _document()
    document["modifier_information"] = [
        _modifier("ZX", "both", "Every setting"),
        _modifier(" zx ", "outpatient", "Outpatient overlap"),
    ]
    source = _write(tmp_path / "hospital.json", document)

    with pytest.raises(ContractError, match="unambiguous_canonical_code_setting"):
        ingest_hospital_file(
            source,
            tmp_path / "warehouse",
            publisher=PublisherRef("example-health"),
        )


def test_raw_item_retains_high_precision_numeric_lexeme(tmp_path: Path) -> None:
    serialized = json.dumps(_document())
    exact = "9007199254740993.123456789012345678901"
    serialized = serialized.replace(
        '"standard_charge_dollar": 95.0',
        f'"standard_charge_dollar": {exact}',
    )
    source = tmp_path / "hospital.json"
    source.write_text(serialized, encoding="utf-8")

    result = ingest_hospital_file(
        source,
        tmp_path / "warehouse",
        publisher=PublisherRef("example-health"),
    )

    with duckdb.connect(str(result.database_path), read_only=True) as connection:
        raw_row = connection.execute(
            """SELECT payload_text, payload_sha256 = sha256(payload_text)
            FROM raw_hospital_items WHERE run_id = ?""",
            [result.run_id],
        ).fetchone()
        typed_row = connection.execute(
            """SELECT standard_charge_dollar FROM stg_payer_rate
            WHERE run_id = ? AND plan_name = 'Example PPO'""",
            [result.run_id],
        ).fetchone()

    assert raw_row is not None and exact in raw_row[0] and raw_row[1]
    assert typed_row is not None
    assert typed_row[0] == Decimal("9007199254740993.1234567890")


def test_success_retains_exact_source_after_original_is_removed(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    admitted = source.read_bytes()
    result = ingest_hospital_file(
        source,
        tmp_path / "warehouse",
        publisher=PublisherRef("example-health"),
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    archive = tmp_path / "warehouse" / manifest["source_archive"]

    source.unlink()

    assert archive.read_bytes() == admitted
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result.source_file_id


def test_identical_publisher_and_content_reuses_the_snapshot(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    second = ingest_hospital_file(source, warehouse, publisher=publisher)

    assert second.run_id == first.run_id
    assert second.reused
    with duckdb.connect(str(second.database_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM stg_payer_rate").fetchone()[0] == 3


def test_inspection_as_of_is_part_of_run_and_artifact_identity(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(
        source,
        warehouse,
        publisher=publisher,
        as_of=date(2026, 8, 9),
    )

    second = ingest_hospital_file(
        source,
        warehouse,
        publisher=publisher,
        as_of=date(2027, 8, 9),
    )

    assert first.run_id != second.run_id
    assert set(first.parquet_files).isdisjoint(second.parquet_files)
    assert all(path.is_file() for path in (*first.parquet_files, *second.parquet_files))


def test_transformation_fingerprint_change_produces_a_disjoint_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    changed_fingerprint = "f" * 64
    assert changed_fingerprint != lakehouse.TRANSFORMATION_FINGERPRINT
    monkeypatch.setattr(lakehouse, "TRANSFORMATION_FINGERPRINT", changed_fingerprint)

    second = ingest_hospital_file(source, warehouse, publisher=publisher)

    assert second.run_id != first.run_id
    assert set(first.parquet_files).isdisjoint(second.parquet_files)
    manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    assert manifest["transformation_fingerprint"] == changed_fingerprint


def test_reuse_refuses_a_tampered_parquet_artifact(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    first.parquet_files[0].write_bytes(first.parquet_files[0].read_bytes() + b"tampered")

    with pytest.raises(LakehouseError, match="failed integrity check"):
        ingest_hospital_file(source, warehouse, publisher=publisher)

    with duckdb.connect(str(first.database_path), read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT status FROM ingest_run WHERE run_id = ?", [first.run_id]
            ).fetchone()[0]
            == "success"
        )


def test_reuse_rejects_an_empty_manifest_inventory_without_mutating_success(
    tmp_path: Path,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["parquet_files"] = []
    manifest["artifacts"] = []
    manifest["manifest_body_sha256"] = lakehouse._manifest_body_sha256(manifest)
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LakehouseError, match="unexpected artifact inventory"):
        ingest_hospital_file(source, warehouse, publisher=publisher)

    with duckdb.connect(str(first.database_path), read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT status FROM ingest_run WHERE run_id = ?", [first.run_id]
            ).fetchone()[0]
            == "success"
        )


@pytest.mark.parametrize("field", ["inspection", "envelope", "model_metrics"])
def test_reuse_rejects_tampered_manifest_provenance_without_mutating_success(
    tmp_path: Path,
    field: str,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    if field == "inspection":
        manifest["inspection"]["item_count"] = 999
    elif field == "envelope":
        manifest["envelope"]["hospital_name"] = "Altered Medical Center"
    else:
        manifest["model_metrics"][0]["rows_produced"] += 1
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LakehouseError, match="manifest body failed integrity check"):
        ingest_hospital_file(source, warehouse, publisher=publisher)

    with duckdb.connect(str(first.database_path), read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT status FROM ingest_run WHERE run_id = ?", [first.run_id]
            ).fetchone()[0]
            == "success"
        )


def test_reuse_rejects_a_tampered_transformation_fingerprint_without_mutating_success(
    tmp_path: Path,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["transformation_fingerprint"] = "0" * 64
    manifest["manifest_body_sha256"] = lakehouse._manifest_body_sha256(manifest)
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LakehouseError, match="identity does not match"):
        ingest_hospital_file(source, warehouse, publisher=publisher)

    with duckdb.connect(str(first.database_path), read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT status FROM ingest_run WHERE run_id = ?", [first.run_id]
            ).fetchone()[0]
            == "success"
        )


def test_publisher_case_variants_have_disjoint_physical_artifacts(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"

    upper = ingest_hospital_file(source, warehouse, publisher=PublisherRef("Case"))
    lower = ingest_hospital_file(source, warehouse, publisher=PublisherRef("case"))

    assert upper.run_id != lower.run_id
    assert set(upper.parquet_files).isdisjoint(lower.parquet_files)
    assert ingest_hospital_file(source, warehouse, publisher=PublisherRef("Case")).reused


def test_build_reads_an_immutable_snapshot_if_source_changes_before_spooling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    real_spool = lakehouse.spool_hospital_file

    def mutate_then_spool(
        snapshot: Path,
        spool_dir: Path,
        context: object,
    ) -> object:
        changed = _document()
        rows = changed["standard_charge_information"]
        assert isinstance(rows, list)
        rows.append(rows[0])
        _write(source, changed)
        return real_spool(snapshot, spool_dir, context)  # type: ignore[arg-type]

    monkeypatch.setattr(lakehouse, "spool_hospital_file", mutate_then_spool)
    result = ingest_hospital_file(
        source,
        warehouse,
        publisher=PublisherRef("example-health"),
        as_of=date(2026, 8, 9),
    )

    assert result.counts.items == 1


def test_contract_failure_rolls_back_rows_and_promotes_no_parquet(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document(setting="telehealth"))
    warehouse = tmp_path / "warehouse"

    with pytest.raises(ContractError, match="accepted_setting"):
        ingest_hospital_file(source, warehouse, publisher=PublisherRef("example-health"))

    with duckdb.connect(str(warehouse / "warehouse.duckdb"), read_only=True) as connection:
        assert connection.execute("SELECT status FROM ingest_run").fetchone()[0] == "failed"
        assert connection.execute("SELECT count(*) FROM stg_charge_group").fetchone()[0] == 0
    assert not list((warehouse / "parquet").rglob("*.parquet"))


def test_contract_rejects_disallowed_numeric_count_categories(tmp_path: Path) -> None:
    document = _document()
    rows = document["standard_charge_information"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    charges = rows[0]["standard_charges"]
    assert isinstance(charges, list) and isinstance(charges[0], dict)
    payers = charges[0]["payers_information"]
    assert isinstance(payers, list) and isinstance(payers[1], dict)
    payers[1]["count"] = "1"
    source = _write(tmp_path / "hospital.json", document)

    with pytest.raises(ContractError, match="valid_allowed_count"):
        ingest_hospital_file(
            source,
            tmp_path / "warehouse",
            publisher=PublisherRef("example-health"),
        )


def test_contracts_detect_deleted_intermediate_and_profile_rows(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    result = ingest_hospital_file(
        source,
        tmp_path / "warehouse",
        publisher=PublisherRef("example-health"),
    )
    with duckdb.connect(str(result.database_path)) as connection:
        connection.execute("DELETE FROM int_rate_observation WHERE run_id = ?", [result.run_id])
        rules = {item.rule for item in validate_contracts(connection, result.run_id)}

    assert "source_representation_reconciliation" in rules
    assert "intermediate_group_reconciliation" in rules


def test_ingests_when_required_metadata_follows_the_charge_array(tmp_path: Path) -> None:
    document = _document()
    rows = document.pop("standard_charge_information")
    source = _write(
        tmp_path / "hospital.json",
        {"standard_charge_information": rows, **document},
    )

    result = ingest_hospital_file(
        source,
        tmp_path / "warehouse",
        publisher=PublisherRef("example-health"),
    )

    assert result.counts.items == 1


def test_refuses_missing_v3_envelope_before_creating_a_snapshot(tmp_path: Path) -> None:
    source = _write(tmp_path / "hospital.json", {"standard_charge_information": []})
    with pytest.raises(LakehouseError, match="missing required CMS v3 envelope"):
        ingest_hospital_file(
            source,
            tmp_path / "warehouse",
            publisher=PublisherRef("example-health"),
        )


def test_out_of_scope_template_version_refuses_with_publishable_evidence(tmp_path: Path) -> None:
    """The reason a v3-only warehouse gave has to survive as evidence, not just as an exit code.

    A refusal that exists only as a raised message leaves the comparison layer with nothing to
    publish, and the file page then shows an absence of contract evidence with no reason for
    it -- which reads as an unnamed defect in someone's file. ``docs/how-we-compare.md`` calls
    that conflation a false accusation, so the refusal carries its own structured evidence.
    """
    document = _document()
    document["version"] = "2.0.0"
    source = _write(tmp_path / "hospital.json", document)

    with pytest.raises(lakehouse.LakehouseScopeRefusal) as raised:
        ingest_hospital_file(
            source,
            tmp_path / "warehouse",
            publisher=PublisherRef("example-health"),
        )

    refusal = raised.value
    assert isinstance(refusal, LakehouseError)  # still fail-closed for every existing caller
    assert refusal.reason == "unsupported hospital JSON template version: '2.0.0'"
    assert refusal.implemented_scope == "CMS hospital JSON template version 3.0.0"
    assert refusal.observed_scope == "CMS hospital JSON template version 2.0.0"
    assert refusal.source_file_id == hashlib.sha256(source.read_bytes()).hexdigest()
    assert refusal.to_dict(publisher_id="example-health") == {
        "status": "refused",
        "source_file_id": refusal.source_file_id,
        "publisher_id": "example-health",
        "reason": refusal.reason,
        "implemented_scope": refusal.implemented_scope,
        "observed_scope": refusal.observed_scope,
    }


def test_partial_parquet_promotion_is_cleaned_if_an_atomic_move_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    real_link = lakehouse.os.link
    promotion_calls = 0

    def fail_second_promotion(source_path: str | Path, destination: str | Path) -> None:
        nonlocal promotion_calls
        if ".staging" in str(source_path):
            promotion_calls += 1
            if promotion_calls == 2:
                raise OSError("simulated atomic move failure")
        real_link(source_path, destination)

    monkeypatch.setattr(lakehouse.os, "link", fail_second_promotion)

    with pytest.raises(LakehouseError, match="simulated atomic move failure"):
        ingest_hospital_file(source, warehouse, publisher=PublisherRef("example-health"))

    assert promotion_calls == 2
    assert not list((warehouse / "parquet").rglob("*.parquet"))
    assert not list((warehouse / "runs").glob("*.json"))


def test_prepared_manifest_recovers_after_post_commit_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    real_finalize = lakehouse._finalize_manifest
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LakehouseError("simulated interruption after catalog commit")
        real_finalize(path)

    monkeypatch.setattr(lakehouse, "_finalize_manifest", fail_once)

    with pytest.raises(LakehouseError, match="simulated interruption"):
        ingest_hospital_file(source, warehouse, publisher=publisher)

    manifest_path = next((warehouse / "runs").glob("*.json"))
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "prepared"
    with duckdb.connect(str(warehouse / "warehouse.duckdb"), read_only=True) as connection:
        assert connection.execute("SELECT status FROM ingest_run").fetchone()[0] == "success"

    recovered = ingest_hospital_file(source, warehouse, publisher=publisher)

    assert recovered.reused
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "success"


def test_running_catalog_with_prepared_artifacts_is_rebuilt_after_precommit_interruption(
    tmp_path: Path,
) -> None:
    source = _write(tmp_path / "hospital.json", _document())
    warehouse = tmp_path / "warehouse"
    publisher = PublisherRef("example-health")
    first = ingest_hospital_file(source, warehouse, publisher=publisher)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "prepared"
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with duckdb.connect(str(first.database_path)) as connection:
        connection.execute(
            "UPDATE ingest_run SET status = 'running' WHERE run_id = ?",
            [first.run_id],
        )

    rebuilt = ingest_hospital_file(source, warehouse, publisher=publisher)

    assert rebuilt.run_id == first.run_id
    assert not rebuilt.reused
    assert all(path.is_file() for path in rebuilt.parquet_files)
    assert json.loads(rebuilt.manifest_path.read_text(encoding="utf-8"))["status"] == "success"
    with duckdb.connect(str(rebuilt.database_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM stg_payer_rate").fetchone()[0] == 3


def test_query_requires_an_existing_warehouse(tmp_path: Path) -> None:
    with pytest.raises(LakehouseError, match="does not exist"):
        query_file_profile(tmp_path, "missing")


def test_spool_load_declares_quoting_beyond_the_sniffer_sample(tmp_path: Path) -> None:
    """A quoted field appearing after the CSV sniffer's sample must still load.

    The spool writer is ``csv.writer`` with minimal quoting, so a charge-level modifier list is
    the rare quoted field. On the first real file where that field first appeared tens of
    thousands of rows in (Stanford Health Care, 2026-08-14), DuckDB's sniffer had already locked
    in "no quoting" from its sample and the load failed. The COPY now declares the dialect.
    """
    import csv

    from mrf_honest.models import SCHEMA_SQL
    from mrf_honest.normalize import SPOOL_COLUMNS

    columns = SPOOL_COLUMNS["stg_charge_group"]
    path = tmp_path / "stg_charge_group.tsv"
    sample_rows = 20_481  # larger than the sniffer's default 20,480-row sample
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(columns)
        for ordinal in range(sample_rows):
            writer.writerow(
                [
                    "run-1",
                    "source-1",
                    "example-health",
                    "item-1",
                    f"group-{ordinal}",
                    ordinal,
                    "10.0",
                    "20.0",
                    "30.0",
                    "15.0",
                    "outpatient",
                    "facility",
                    "[]",
                    "__MRF_HONEST_NULL__",
                ]
            )
        writer.writerow(
            [
                "run-1",
                "source-1",
                "example-health",
                "item-1",
                "group-last",
                sample_rows,
                "10.0",
                "20.0",
                "30.0",
                "15.0",
                "both",
                "professional",
                '["51"]',
                "__MRF_HONEST_NULL__",
            ]
        )

    with duckdb.connect(":memory:") as connection:
        connection.execute(SCHEMA_SQL)
        lakehouse._load_spools(
            connection, "run-1", {"stg_charge_group": path}, tmp_path / "profiles"
        )
        total = connection.execute(
            "SELECT count(*) FROM stg_charge_group WHERE run_id = 'run-1'"
        ).fetchone()[0]
        assert total == sample_rows + 1
        modifier_json = connection.execute(
            "SELECT modifier_codes_json FROM stg_charge_group WHERE charge_group_id = 'group-last'"
        ).fetchone()[0]
        assert json.loads(modifier_json) == ["51"]
