from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from mrf_honest.inspect import (
    FINDING_CATALOG,
    INSPECTION_FINGERPRINT,
    PublisherRef,
    explain_finding,
    inspect_hospital_file,
)
from mrf_honest.scorecard import RETRIEVAL_FINDING_CATALOG
from mrf_honest.stream import BOM


def _payer_rates() -> list[dict[str, object]]:
    allowed = {"count": "12", "10th_percentile": 50, "median_amount": 75, "90th_percentile": 100}
    return [
        {
            "payer_name": "Alpha",
            "plan_name": "PPO",
            "methodology": "fee schedule",
            "standard_charge_dollar": 80,
        },
        {
            "payer_name": "Beta",
            "plan_name": "HMO",
            "methodology": "percent of total billed charges",
            "standard_charge_percentage": 70,
            **allowed,
        },
        {
            "payer_name": "Gamma",
            "plan_name": "POS",
            "methodology": "case rate",
            "standard_charge_algorithm": "base + implant",
            **allowed,
        },
    ]


def _document(*, updated: str = "2026-04-01") -> dict[str, object]:
    return {
        "hospital_name": "Example Hospital",
        "last_updated_on": updated,
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
                "code_information": [
                    {"code": "12345", "type": "CPT"},
                    {"code": "100", "type": "CDM"},
                ],
                "standard_charges": [
                    {
                        "gross_charge": 120,
                        "discounted_cash": 90,
                        "minimum": 70,
                        "maximum": 100,
                        "setting": "outpatient",
                        "payers_information": _payer_rates(),
                    }
                ],
            }
        ],
    }


def _write(path: Path, document: object, *, bom: bool = False) -> bytes:
    raw = json.dumps(document, separators=(",", ":")).encode()
    if bom:
        raw = BOM + raw
    path.write_bytes(raw)
    return raw


def _codes(result: object) -> set[str]:
    return {finding.code for finding in result.findings}  # type: ignore[attr-defined]


def test_inspects_valid_v3_and_keeps_rate_forms_separate(tmp_path: Path) -> None:
    path = tmp_path / "hospital.json"
    raw = _write(path, _document())
    publisher = PublisherRef("example.test", "Example", "https://example.test/mrf.json")

    result = inspect_hospital_file(path, publisher, as_of=date(2026, 8, 9))

    assert result.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.source_size == result.byte_count == len(raw)
    assert result.publisher == publisher
    assert result.version == "3.0.0"
    assert result.period == result.last_updated_on == date(2026, 4, 1)
    assert (result.item_count, result.code_count, result.charge_group_count) == (1, 2, 1)
    assert result.payer_rate_count == 3
    assert result.dollar_rate_count == 1
    assert result.percentage_rate_count == 1
    assert result.algorithm_rate_count == 1
    assert result.settings_seen == ("outpatient",)
    assert result.methodologies_seen == (
        "case rate",
        "fee schedule",
        "percent of total billed charges",
    )
    assert result.missing_envelope_fields == ()
    assert result.scan_completed and not result.had_bom and result.problems == ()
    assert result.scorecard.retrievability.status == "NOT_ASSESSED"
    assert result.scorecard.conformance.status == "OBSERVED"
    assert result.scorecard.completeness.status == "OBSERVED"
    assert result.scorecard.interpretability.status == "FINDINGS"
    assert result.scorecard.freshness.status == "OBSERVED"
    assert "INTERPRETABILITY_PERCENTAGE_RATES" in _codes(result)
    assert "INTERPRETABILITY_ALGORITHM_RATES" in _codes(result)
    assert not hasattr(result.scorecard, "score")
    assert not hasattr(result.scorecard, "overall")


def test_envelope_fields_after_charge_array_are_inspected(tmp_path: Path) -> None:
    document = _document()
    rows = document.pop("standard_charge_information")
    reordered = {"standard_charge_information": rows, **document}
    path = tmp_path / "array-first.json"
    _write(path, reordered)

    result = inspect_hospital_file(path, as_of=date(2026, 8, 9))

    assert result.scan_completed
    assert result.missing_envelope_fields == ()
    assert result.version == "3.0.0"
    assert result.period == date(2026, 4, 1)
    assert result.envelope["hospital_name"] == "Example Hospital"


def test_missing_and_invalid_fields_become_cited_findings(tmp_path: Path) -> None:
    document = _document(updated="2026-02-30")
    document.pop("type_2_npi")
    document["version"] = "2.0.0"
    attestation = document["attestation"]
    assert isinstance(attestation, dict)
    attestation["confirm_attestation"] = False
    rows = document["standard_charge_information"]
    assert isinstance(rows, list)
    rows[0] = {
        "description": "",
        "code_information": [{"code": "", "type": ""}],
        "standard_charges": [
            {
                "setting": "clinic",
                "payers_information": [
                    {"payer_name": "", "plan_name": "", "methodology": "mystery"}
                ],
            }
        ],
    }
    path = tmp_path / "invalid.json"
    _write(path, document)

    result = inspect_hospital_file(path, as_of=date(2026, 8, 9))
    codes = _codes(result)

    assert result.missing_envelope_fields == ("type_2_npi",)
    assert result.period is None
    assert {
        "CMS_V3_ENVELOPE_TYPE_2_NPI_MISSING",
        "CMS_V3_VERSION_UNEXPECTED",
        "CMS_V3_LAST_UPDATED_ON_INVALID",
        "CMS_V3_ATTESTATION_NOT_CONFIRMED",
        "CMS_V3_ITEM_DESCRIPTION_MISSING",
        "CMS_V3_CODE_VALUE_MISSING",
        "CMS_V3_CODE_TYPE_MISSING",
        "CMS_V3_SETTING_INVALID",
        "CMS_V3_METHODOLOGY_INVALID",
        "CMS_V3_PAYER_PAYER_NAME_MISSING",
        "CMS_V3_PAYER_PLAN_NAME_MISSING",
        "CMS_V3_PAYER_CHARGE_MISSING",
        "FRESHNESS_DATE_NOT_USABLE",
    } <= codes
    assert all(finding.citations for finding in result.findings)
    assert result.scorecard.conformance.status == "FINDINGS"
    assert result.scorecard.completeness.status == "FINDINGS"
    assert result.scorecard.freshness.status == "FINDINGS"


def test_bom_and_non_object_items_are_recorded_without_rows(tmp_path: Path) -> None:
    document = _document()
    document["standard_charge_information"] = [42]
    path = tmp_path / "bom.json"
    _write(path, document, bom=True)

    result = inspect_hospital_file(path, as_of=date(2026, 8, 9))

    assert result.had_bom
    assert result.scan_completed
    assert result.item_count == 0
    assert result.problem_count == 1
    assert len(result.problems) == 1
    assert "JSON_UTF8_BOM_PRESENT" in _codes(result)
    assert "JSON_ARRAY_ITEM_PROBLEM" in _codes(result)
    assert "CMS_V3_STANDARD_CHARGE_INFORMATION_EMPTY" in _codes(result)
    assert result.scorecard.interpretability.status == "NOT_ASSESSED"


def test_unterminated_document_returns_partial_facts_not_rows(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_bytes(
        b'{"hospital_name":"Example","standard_charge_information":[{"description":"x"}'
    )

    result = inspect_hospital_file(path, as_of=date(2026, 8, 9))

    assert not result.scan_completed
    assert result.problem_count == 1
    assert "JSON_STREAM_INCOMPLETE" in _codes(result)
    assert result.scorecard.completeness.status == "NOT_ASSESSED"
    assert result.scorecard.interpretability.status == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "updated,as_of,expected_code",
    [
        ("2025-08-09", date(2026, 8, 9), None),
        ("2025-08-09", date(2026, 8, 10), "FRESHNESS_ANNUAL_UPDATE_OVERDUE"),
        ("2026-08-10", date(2026, 8, 9), "FRESHNESS_DATE_IN_FUTURE"),
        ("2024-02-29", date(2025, 3, 1), "FRESHNESS_ANNUAL_UPDATE_OVERDUE"),
    ],
)
def test_freshness_is_deterministic_from_supplied_as_of(
    tmp_path: Path, updated: str, as_of: date, expected_code: str | None
) -> None:
    path = tmp_path / f"{updated}.json"
    _write(path, _document(updated=updated))

    result = inspect_hospital_file(path, as_of=as_of)
    freshness_codes = {finding.code for finding in result.scorecard.freshness.findings}

    if expected_code is None:
        assert freshness_codes == set()
        assert result.scorecard.freshness.status == "OBSERVED"
    else:
        assert expected_code in freshness_codes
        assert result.scorecard.freshness.status == "FINDINGS"


def test_serialization_is_json_safe_and_stable(tmp_path: Path) -> None:
    path = tmp_path / "hospital.json"
    _write(path, _document())
    result = inspect_hospital_file(
        path,
        PublisherRef("id", source_url="https://example.test/file.json"),
        as_of=date(2026, 8, 9),
    )

    serialized = result.to_dict()

    assert serialized["as_of"] == "2026-08-09"
    assert serialized["period"] == "2026-04-01"
    assert serialized["settings_seen"] == ["outpatient"]
    assert serialized["publisher"] == {
        "identifier": "id",
        "name": None,
        "source_url": "https://example.test/file.json",
    }
    assert json.dumps(serialized, sort_keys=True) == json.dumps(result.to_dict(), sort_keys=True)


def test_repeated_bad_values_are_aggregated_by_finite_code(tmp_path: Path) -> None:
    document = _document()
    rows = document["standard_charge_information"]
    assert isinstance(rows, list)
    template = rows[0]
    assert isinstance(template, dict)
    rows[:] = [template for _ in range(50)]
    charges = template["standard_charges"]
    assert isinstance(charges, list)
    assert isinstance(charges[0], dict)
    charges[0]["setting"] = "invalid-setting"
    path = tmp_path / "repeated.json"
    _write(path, document)

    result = inspect_hospital_file(path, as_of=date(2026, 8, 9))
    finding = next(item for item in result.findings if item.code == "CMS_V3_SETTING_INVALID")

    assert result.item_count == 50
    assert finding.occurrences == 50
    assert len([item for item in result.findings if item.code == finding.code]) == 1


def test_conditional_v3_fields_are_checked(tmp_path: Path) -> None:
    document = _document()
    rows = document["standard_charge_information"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    charges = rows[0]["standard_charges"]
    assert isinstance(charges, list) and isinstance(charges[0], dict)
    charges[0].pop("minimum")
    charges[0].pop("maximum")
    charges[0]["payers_information"] = [
        {
            "payer_name": "Alpha",
            "plan_name": "PPO",
            "methodology": "other",
            "standard_charge_dollar": 20,
        },
        {
            "payer_name": "Beta",
            "plan_name": "HMO",
            "methodology": "per diem",
            "standard_charge_algorithm": "contract terms",
            "count": "0",
        },
    ]
    path = tmp_path / "conditional.json"
    _write(path, document)

    result = inspect_hospital_file(path, as_of=date(2026, 8, 9))
    codes = _codes(result)

    assert "CMS_V3_DOLLAR_RANGE_MINIMUM_MISSING" in codes
    assert "CMS_V3_DOLLAR_RANGE_MAXIMUM_MISSING" in codes
    assert "CMS_V3_OTHER_METHODOLOGY_NOTES_MISSING" in codes
    assert "CMS_V3_ZERO_COUNT_NOTES_MISSING" in codes


def test_finding_catalog_is_authoritative_and_explainable() -> None:
    definition = explain_finding("CMS_V3_SETTING_INVALID")

    assert definition is FINDING_CATALOG[definition.code]
    assert definition.dimension == "conformance"
    assert definition.severity == "ERROR"
    assert definition.citations
    with pytest.raises(KeyError, match="unknown finding code: NOT_A_FINDING"):
        explain_finding("NOT_A_FINDING")


def test_inspection_policy_has_a_content_fingerprint() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", INSPECTION_FINGERPRINT)


def test_grading_document_covers_the_authoritative_catalogs() -> None:
    document = Path("docs/how-we-grade.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([A-Z0-9_]+)` \|", document, flags=re.MULTILINE))

    assert documented == set(FINDING_CATALOG) | set(RETRIEVAL_FINDING_CATALOG)
