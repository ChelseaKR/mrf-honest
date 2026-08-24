"""Behavioral tests for the CMS hospital CSV v3 inspector (Tall and Wide)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mrf_honest.inspect import FINDING_CATALOG
from mrf_honest.inspect_csv import (
    ATTESTATION_HEADER_TEXT,
    CSV_FINDING_CATALOG,
    CSV_INSPECTION_FINGERPRINT,
    CsvFileInspection,
    explain_csv_finding,
    inspect_hospital_csv_file,
)

AS_OF = date(2026, 8, 19)

GENERAL_HEADER = (
    "hospital_name,last_updated_on,version,location_name,hospital_address,"
    f'license_number|CA,type_2_npi,"{ATTESTATION_HEADER_TEXT}",attester_name'
)
GENERAL_VALUES = (
    'Example Hospital,2026-05-01,3.0.0,Example Hospital,"1 Main St, Sacramento, CA 95814",'
    "030000123,1234567890,true,Jane Doe"
)
TALL_HEADER = (
    "description,code|1,code|1|type,modifiers,setting,drug_unit_of_measurement,"
    "drug_type_of_measurement,standard_charge|gross,standard_charge|discounted_cash,"
    "payer_name,plan_name,standard_charge|negotiated_dollar,"
    "standard_charge|negotiated_percentage,standard_charge|negotiated_algorithm,"
    "median_amount,10th_percentile,90th_percentile,count,standard_charge|methodology,"
    "standard_charge|min,standard_charge|max,additional_generic_notes"
)
TALL_DOLLAR_ROW = (
    "MRI brain,70551,CPT,,outpatient,,,1200,900,Acme Health,PPO,800,,,,,,,fee schedule,700,950,"
)
WIDE_HEADER = (
    "description,code|1,code|1|type,modifiers,setting,drug_unit_of_measurement,"
    "drug_type_of_measurement,standard_charge|gross,standard_charge|discounted_cash,"
    "standard_charge|Acme Health|PPO|negotiated_dollar,"
    "standard_charge|Acme Health|PPO|negotiated_percentage,"
    "standard_charge|Acme Health|PPO|negotiated_algorithm,"
    "median_amount|Acme Health|PPO,10th_percentile|Acme Health|PPO,"
    "90th_percentile|Acme Health|PPO,count|Acme Health|PPO,"
    "standard_charge|Acme Health|PPO|methodology,additional_payer_notes|Acme Health|PPO,"
    "standard_charge|min,standard_charge|max,additional_generic_notes"
)
WIDE_DOLLAR_ROW = "MRI brain,70551,CPT,,outpatient,,,1200,900,800,,,,,,,fee schedule,,700,950,"


def _write(tmp_path: Path, *lines: str, name: str = "prices.csv") -> Path:
    target = tmp_path / name
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _tall(tmp_path: Path, *rows: str) -> CsvFileInspection:
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, TALL_HEADER, *rows)
    return inspect_hospital_csv_file(path, as_of=AS_OF)


def _wide(tmp_path: Path, *rows: str) -> CsvFileInspection:
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, WIDE_HEADER, *rows)
    return inspect_hospital_csv_file(path, as_of=AS_OF)


def _codes(inspection: CsvFileInspection) -> set[str]:
    return {finding.code for finding in inspection.findings}


def _statuses(inspection: CsvFileInspection) -> dict[str, str]:
    return {d.name: d.status for d in inspection.scorecard.dimensions}


# --- clean files ----------------------------------------------------------------------------


def test_clean_tall_file_observes_every_local_dimension(tmp_path: Path) -> None:
    inspection = _tall(tmp_path, TALL_DOLLAR_ROW)
    assert inspection.layout == "tall"
    assert inspection.row_count == 1
    assert inspection.item_count == 1
    assert inspection.code_count == 1
    assert inspection.dollar_rate_count == 1
    assert inspection.version == "3.0.0"
    assert inspection.period == date(2026, 5, 1)
    assert inspection.missing_general_fields == ()
    assert inspection.missing_columns == ()
    assert _statuses(inspection) == {
        "retrievability": "NOT_ASSESSED",
        "conformance": "OBSERVED",
        "completeness": "OBSERVED",
        "interpretability": "OBSERVED",
        "freshness": "OBSERVED",
    }


def test_clean_wide_file_resolves_the_combination_and_counts_rates(tmp_path: Path) -> None:
    inspection = _wide(tmp_path, WIDE_DOLLAR_ROW)
    assert inspection.layout == "wide"
    assert inspection.payer_plan_combination_count == 1
    assert inspection.payer_rate_count == 1
    assert inspection.dollar_rate_count == 1
    assert _statuses(inspection)["conformance"] == "OBSERVED"
    assert _statuses(inspection)["completeness"] == "OBSERVED"


def test_headers_match_case_insensitively_with_spaces_around_pipes(tmp_path: Path) -> None:
    spaced = TALL_HEADER.replace("standard_charge|gross", "Standard_Charge | GROSS")
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, spaced, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_COLUMN_STANDARD_CHARGE_GROSS_MISSING" not in _codes(inspection)
    assert inspection.missing_columns == ()


def test_general_elements_are_matched_by_name_not_position(tmp_path: Path) -> None:
    header = (
        f'version,hospital_name,last_updated_on,"{ATTESTATION_HEADER_TEXT}",attester_name,'
        "location_name,hospital_address,type_2_npi,license_number|NJ"
    )
    values = (
        '3.0.0,Example Hospital,2026-05-01,TRUE,Jane Doe,Example Hospital,"1 Main St",'
        "1234567890,12102"
    )
    path = _write(tmp_path, header, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert inspection.missing_general_fields == ()
    assert inspection.envelope["license_state"] == "NJ"
    assert inspection.envelope["attestation"] == "TRUE"


# --- general data elements ------------------------------------------------------------------


def test_missing_general_headers_are_named_one_finding_each(tmp_path: Path) -> None:
    path = _write(tmp_path, "hospital_name", "Example Hospital", TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_GENERAL_LAST_UPDATED_ON_MISSING" in codes
    assert "CMS_CSV_GENERAL_ATTESTATION_MISSING" in codes
    assert "CMS_CSV_GENERAL_LICENSE_NUMBER_MISSING" in codes
    assert "CMS_CSV_GENERAL_HOSPITAL_NAME_MISSING" not in codes
    assert "last_updated_on" in inspection.missing_general_fields
    assert "CSV_FRESHNESS_DATE_NOT_USABLE" in codes
    assert _statuses(inspection)["freshness"] == "FINDINGS"


def test_unreplaced_state_placeholder_is_a_finding(tmp_path: Path) -> None:
    header = GENERAL_HEADER.replace("license_number|CA", "license_number|[state]")
    path = _write(tmp_path, header, GENERAL_VALUES, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_PLACEHOLDER_NOT_REPLACED" in codes
    assert "CMS_CSV_GENERAL_LICENSE_NUMBER_MISSING" in codes


def test_attestation_false_is_a_warning_not_an_error(tmp_path: Path) -> None:
    values = GENERAL_VALUES.replace(",true,", ",false,")
    path = _write(tmp_path, GENERAL_HEADER, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    finding = next(f for f in inspection.findings if f.code == "CMS_CSV_ATTESTATION_NOT_CONFIRMED")
    assert finding.severity == "WARNING"


def test_attestation_value_outside_true_false_is_unusable(tmp_path: Path) -> None:
    values = GENERAL_VALUES.replace(",true,", ",yes,")
    path = _write(tmp_path, GENERAL_HEADER, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_GENERAL_ATTESTATION_MISSING" in _codes(inspection)


def test_version_other_than_3_0_0_is_unexpected(tmp_path: Path) -> None:
    values = GENERAL_VALUES.replace(",3.0.0,", ",3.0,")
    path = _write(tmp_path, GENERAL_HEADER, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_VERSION_UNEXPECTED" in _codes(inspection)
    assert inspection.version == "3.0"


def test_slash_dates_are_accepted_for_the_mrf_date(tmp_path: Path) -> None:
    values = GENERAL_VALUES.replace("2026-05-01", "5/1/2026")
    path = _write(tmp_path, GENERAL_HEADER, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert inspection.period == date(2026, 5, 1)
    assert "CMS_CSV_LAST_UPDATED_ON_INVALID" not in _codes(inspection)


def test_an_unparseable_date_is_invalid_and_freshness_not_usable(tmp_path: Path) -> None:
    values = GENERAL_VALUES.replace("2026-05-01", "May 2026")
    path = _write(tmp_path, GENERAL_HEADER, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_LAST_UPDATED_ON_INVALID" in codes
    assert "CSV_FRESHNESS_DATE_NOT_USABLE" in codes
    assert inspection.period is None


def test_an_impossible_calendar_date_is_invalid(tmp_path: Path) -> None:
    values = GENERAL_VALUES.replace("2026-05-01", "2/30/2026")
    path = _write(tmp_path, GENERAL_HEADER, values, TALL_HEADER, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_LAST_UPDATED_ON_INVALID" in _codes(inspection)


def test_freshness_overdue_and_future_mirror_the_json_rules(tmp_path: Path) -> None:
    stale = GENERAL_VALUES.replace("2026-05-01", "2025-05-01")
    path = _write(tmp_path, GENERAL_HEADER, stale, TALL_HEADER, TALL_DOLLAR_ROW)
    assert "FRESHNESS_ANNUAL_UPDATE_OVERDUE" in _codes(inspect_hospital_csv_file(path, as_of=AS_OF))
    future = GENERAL_VALUES.replace("2026-05-01", "2026-12-01")
    path = _write(tmp_path, GENERAL_HEADER, future, TALL_HEADER, TALL_DOLLAR_ROW, name="f.csv")
    assert "FRESHNESS_DATE_IN_FUTURE" in _codes(inspect_hospital_csv_file(path, as_of=AS_OF))


# --- the charge header row ------------------------------------------------------------------


def test_missing_required_columns_are_named_individually(tmp_path: Path) -> None:
    header = TALL_HEADER.replace("standard_charge|min,", "")
    header = header.replace(",additional_generic_notes", "")
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, header, TALL_DOLLAR_ROW)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_COLUMN_STANDARD_CHARGE_MIN_MISSING" in codes
    assert "CMS_CSV_COLUMN_ADDITIONAL_GENERIC_NOTES_MISSING" in codes
    assert "standard_charge|min" in inspection.missing_columns


def test_a_file_without_any_code_pair_column_is_a_finding(tmp_path: Path) -> None:
    header = TALL_HEADER.replace("code|1,code|1|type,", "")
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, header)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_COLUMN_CODE_PAIR_MISSING" in _codes(inspection)


def test_mixed_tall_and_wide_headers_are_ambiguous(tmp_path: Path) -> None:
    header = WIDE_HEADER + ",payer_name,plan_name"
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, header, WIDE_DOLLAR_ROW + ",,")
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_LAYOUT_AMBIGUOUS" in _codes(inspection)
    assert inspection.layout == "wide"


def test_unreplaced_row3_placeholders_are_findings(tmp_path: Path) -> None:
    header = TALL_HEADER.replace("code|1,code|1|type", "code|[i],code|[i]|type")
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, header)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_PLACEHOLDER_NOT_REPLACED" in codes
    assert "CMS_CSV_COLUMN_CODE_PAIR_MISSING" in codes


def test_duplicate_headers_are_a_finding(tmp_path: Path) -> None:
    header = TALL_HEADER + ",description"
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, header, TALL_DOLLAR_ROW + ",")
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_HEADER_NOT_UNIQUE" in _codes(inspection)


def test_an_incomplete_wide_payer_header_set_is_a_finding(tmp_path: Path) -> None:
    header = WIDE_HEADER.replace("count|Acme Health|PPO,", "")
    row = "MRI brain,70551,CPT,,outpatient,,,1200,900,800,,,,,,fee schedule,,700,950,"
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES, header, row)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    assert "CMS_CSV_WIDE_PAYER_HEADER_SET_INCOMPLETE" in _codes(inspection)


def test_a_file_that_ends_before_row_3_has_no_charge_header(tmp_path: Path) -> None:
    path = _write(tmp_path, GENERAL_HEADER, GENERAL_VALUES)
    inspection = inspect_hospital_csv_file(path, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_CHARGE_HEADER_ROW_MISSING" in codes
    assert "CMS_CSV_TABLE_EMPTY" in codes
    assert inspection.layout is None
    assert inspection.scan_completed is True


def test_a_header_only_file_has_an_empty_table(tmp_path: Path) -> None:
    inspection = _tall(tmp_path)
    assert "CMS_CSV_TABLE_EMPTY" in _codes(inspection)
    assert _statuses(inspection)["interpretability"] == "NOT_ASSESSED"


# --- data rows ------------------------------------------------------------------------------


def test_extra_nonblank_cells_beyond_the_headers_are_a_finding(tmp_path: Path) -> None:
    inspection = _tall(tmp_path, TALL_DOLLAR_ROW + ",stray")
    assert "CMS_CSV_ROW_WIDTH_MISMATCH" in _codes(inspection)


def test_short_rows_read_as_blanks_rather_than_failing(tmp_path: Path) -> None:
    row = "MRI brain,70551,CPT,,outpatient,,,1200,900"
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_ROW_WIDTH_MISMATCH" not in _codes(inspection)
    assert inspection.item_count == 1


def test_blank_rows_are_tolerated_and_not_counted(tmp_path: Path) -> None:
    inspection = _tall(tmp_path, TALL_DOLLAR_ROW, ",,,,,,,,,,,,,,,,,,,,,")
    assert inspection.row_count == 1


def test_invalid_enums_are_findings_with_the_offending_value(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",outpatient,", ",er,").replace(",CPT,", ",XYZ,")
    inspection = _tall(tmp_path, row)
    codes = _codes(inspection)
    assert "CMS_CSV_SETTING_INVALID" in codes
    assert "CMS_CSV_CODE_TYPE_INVALID" in codes


def test_a_blank_setting_on_a_charged_item_row_is_a_finding(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",outpatient,", ",,")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_SETTING_INVALID" in _codes(inspection)


def test_numeric_fields_reject_currency_negatives_and_separators(tmp_path: Path) -> None:
    for bad in ("$1200", "-5", "1_000", "0"):
        row = TALL_DOLLAR_ROW.replace(",1200,", f",{bad},")
        inspection = _tall(tmp_path, row)
        assert "CMS_CSV_NUMERIC_VALUE_INVALID" in _codes(inspection), bad


def test_methodology_and_count_values_are_validated(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",fee schedule,", ",bundled,")
    assert "CMS_CSV_METHODOLOGY_INVALID" in _codes(_tall(tmp_path, row))
    derived = (
        "ED visit,99283,CPT,,outpatient,,,450,300,Acme Health,PPO,,60.5,,210,150,300,"
        "2 025,percent of total billed charges,,,"
    )
    assert "CMS_CSV_COUNT_VALUE_INVALID" in _codes(_tall(tmp_path, derived))


def test_count_accepts_the_three_documented_shapes(tmp_path: Path) -> None:
    for good in ("0", "1 through 10", "11", "2025"):
        derived = (
            f"ED visit,99283,CPT,,outpatient,,,450,300,Acme Health,PPO,,60.5,,210,150,300,"
            f"{good},percent of total billed charges,,,note explaining zero"
        )
        inspection = _tall(tmp_path, derived)
        assert "CMS_CSV_COUNT_VALUE_INVALID" not in _codes(inspection), good


# --- conditional requirements ---------------------------------------------------------------


def test_a_payer_charge_without_payer_context_is_incomplete(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",Acme Health,PPO,", ",,,")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_PAYER_CONTEXT_MISSING" in _codes(inspection)


def test_a_named_payer_without_any_charge_is_incomplete(tmp_path: Path) -> None:
    row = "MRI brain,70551,CPT,,outpatient,,,1200,900,Acme Health,PPO,,,,,,,,,,,"
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_PAYER_WITHOUT_CHARGE" in _codes(inspection)


def test_a_charge_without_any_code_pairing_is_incomplete(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace("70551,CPT", ",")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_CODE_PAIRING_MISSING" in _codes(inspection)


def test_a_charged_row_without_codes_still_checks_description_and_setting(
    tmp_path: Path,
) -> None:
    # Row with a standard gross charge, but blank description, codes, and setting
    row = ",,,,,,,150.00,,,,,,,,,,,,,,,"
    inspection = _tall(tmp_path, row)
    codes = _codes(inspection)
    assert "CMS_CSV_CODE_PAIRING_MISSING" in codes
    assert "CMS_CSV_DESCRIPTION_MISSING" in codes
    assert "CMS_CSV_SETTING_INVALID" in codes


def test_a_code_without_its_type_is_unpaired(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace("70551,CPT", "70551,")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_CODE_TYPE_UNPAIRED" in _codes(inspection)


def test_other_methodology_requires_a_note(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",fee schedule,", ",other,")
    assert "CMS_CSV_OTHER_METHODOLOGY_NOTES_MISSING" in _codes(_tall(tmp_path, row))
    noted = row + "explained here"
    assert "CMS_CSV_OTHER_METHODOLOGY_NOTES_MISSING" not in _codes(_tall(tmp_path, noted))


def test_an_item_with_no_charge_at_all_is_incomplete(tmp_path: Path) -> None:
    row = "MRI brain,70551,CPT,,outpatient,,,,,,,,,,,,,,,,,"
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_CHARGE_VALUE_MISSING" in _codes(inspection)


def test_a_dollar_rate_requires_min_and_max(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",700,950,", ",,,")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_DOLLAR_RANGE_MISSING" in _codes(inspection)


def test_derived_rates_require_count_and_percentiles(tmp_path: Path) -> None:
    missing_count = (
        "ED visit,99283,CPT,,outpatient,,,450,300,Acme Health,PPO,,60.5,,210,150,300,"
        ",percent of total billed charges,,,"
    )
    assert "CMS_CSV_DERIVED_RATE_COUNT_MISSING" in _codes(_tall(tmp_path, missing_count))
    missing_percentiles = (
        "ED visit,99283,CPT,,outpatient,,,450,300,Acme Health,PPO,,60.5,,,,,25,"
        "percent of total billed charges,,,"
    )
    assert "CMS_CSV_DERIVED_RATE_PERCENTILES_MISSING" in _codes(
        _tall(tmp_path, missing_percentiles)
    )


def test_a_zero_count_requires_an_explanatory_note(tmp_path: Path) -> None:
    zero = (
        "ED visit,99283,CPT,,outpatient,,,450,300,Acme Health,PPO,,60.5,,,,,0,"
        "percent of total billed charges,,,"
    )
    assert "CMS_CSV_ZERO_COUNT_NOTES_MISSING" in _codes(_tall(tmp_path, zero))
    explained = zero + "no remittances in the window"
    assert "CMS_CSV_ZERO_COUNT_NOTES_MISSING" not in _codes(_tall(tmp_path, explained))


def test_ndc_rows_require_both_drug_measurement_fields(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace("70551,CPT", "0002-1433-80,NDC")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_NDC_DRUG_FIELDS_MISSING" in _codes(inspection)
    dosed = row.replace(",outpatient,,,", ",outpatient,10,ML,")
    assert "CMS_CSV_NDC_DRUG_FIELDS_MISSING" not in _codes(_tall(tmp_path, dosed))


def test_drug_measurement_fields_must_travel_together(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",outpatient,,,", ",outpatient,10,,")
    inspection = _tall(tmp_path, row)
    assert "CMS_CSV_DRUG_FIELDS_UNPAIRED" in _codes(inspection)


def test_a_modifier_row_needs_description_and_context(tmp_path: Path) -> None:
    bare = ",,,25,,,,,,,,,,,,,,,,,,"
    assert "CMS_CSV_MODIFIER_ROW_CONTEXT_MISSING" in _codes(_tall(tmp_path, bare))
    explained = "Modifier 25 adjustment,,,25,,,,,,,,,,,,,,,,,,separate E&M service"
    assert "CMS_CSV_MODIFIER_ROW_CONTEXT_MISSING" not in _codes(_tall(tmp_path, explained))


def test_wide_conditionals_apply_per_payer_combination(tmp_path: Path) -> None:
    no_methodology = "MRI brain,70551,CPT,,outpatient,,,1200,900,800,,,,,,,,,700,950,"
    inspection = _wide(tmp_path, no_methodology)
    assert "CMS_CSV_PAYER_CONTEXT_MISSING" in _codes(inspection)
    derived_no_count = (
        "MRI brain,70551,CPT,,outpatient,,,1200,900,,60.5,,210,150,300,,fee schedule,,700,950,"
    )
    assert "CMS_CSV_DERIVED_RATE_COUNT_MISSING" in _codes(_wide(tmp_path, derived_no_count))


# --- the stream itself ----------------------------------------------------------------------


def test_a_bom_is_tolerated_and_recorded(tmp_path: Path) -> None:
    target = tmp_path / "bom.csv"
    body = "\n".join((GENERAL_HEADER, GENERAL_VALUES, TALL_HEADER, TALL_DOLLAR_ROW)) + "\n"
    target.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    inspection = inspect_hospital_csv_file(target, as_of=AS_OF)
    assert inspection.had_bom is True
    finding = next(f for f in inspection.findings if f.code == "CMS_CSV_UTF8_BOM_PRESENT")
    assert finding.severity == "INFO"
    assert inspection.missing_general_fields == ()


def test_non_utf8_bytes_fall_back_to_latin1_with_a_recorded_info(tmp_path: Path) -> None:
    target = tmp_path / "latin.csv"
    accented = GENERAL_VALUES.replace("Jane Doe", "Jos\xe9 Ru\xedz")
    body = "\n".join((GENERAL_HEADER, accented, TALL_HEADER, TALL_DOLLAR_ROW))
    target.write_bytes(body.encode("latin-1"))
    inspection = inspect_hospital_csv_file(target, as_of=AS_OF)
    assert inspection.encoding == "latin-1"
    assert "CMS_CSV_ENCODING_NOT_UTF8" in _codes(inspection)
    assert inspection.scan_completed is True


def test_an_oversized_field_stops_the_stream_and_is_stated(tmp_path: Path) -> None:
    target = tmp_path / "oversized.csv"
    huge = "x" * (10 * 1024 * 1024 + 16)
    body = "\n".join(
        (GENERAL_HEADER, GENERAL_VALUES, TALL_HEADER, TALL_DOLLAR_ROW, f'"{huge}",broken')
    )
    target.write_text(body, encoding="utf-8")
    inspection = inspect_hospital_csv_file(target, as_of=AS_OF)
    assert inspection.scan_completed is False
    assert "CMS_CSV_STREAM_INCOMPLETE" in _codes(inspection)
    statuses = _statuses(inspection)
    assert statuses["completeness"] == "NOT_ASSESSED"
    assert statuses["interpretability"] == "NOT_ASSESSED"


def test_an_empty_file_reports_every_absence_rather_than_crashing(tmp_path: Path) -> None:
    target = tmp_path / "empty.csv"
    target.write_bytes(b"")
    inspection = inspect_hospital_csv_file(target, as_of=AS_OF)
    codes = _codes(inspection)
    assert "CMS_CSV_GENERAL_HOSPITAL_NAME_MISSING" in codes
    assert "CMS_CSV_CHARGE_HEADER_ROW_MISSING" in codes
    assert inspection.source_size == 0


# --- catalog and serialization --------------------------------------------------------------


def test_every_emitted_code_exists_in_the_catalog() -> None:
    with pytest.raises(KeyError):
        explain_csv_finding("NOT_A_CODE")
    for code, definition in CSV_FINDING_CATALOG.items():
        assert definition.code == code
        assert definition.citations, code


def test_shared_freshness_codes_are_identical_to_the_json_catalog() -> None:
    for code in ("FRESHNESS_ANNUAL_UPDATE_OVERDUE", "FRESHNESS_DATE_IN_FUTURE"):
        assert CSV_FINDING_CATALOG[code] == FINDING_CATALOG[code], code


def test_csv_specific_codes_do_not_collide_with_other_catalogs() -> None:
    overlap = set(CSV_FINDING_CATALOG) & set(FINDING_CATALOG)
    assert overlap == {"FRESHNESS_ANNUAL_UPDATE_OVERDUE", "FRESHNESS_DATE_IN_FUTURE"}


def test_the_inspection_fingerprint_is_stable_and_serializable(tmp_path: Path) -> None:
    assert len(CSV_INSPECTION_FINGERPRINT) == 64
    inspection = _tall(tmp_path, TALL_DOLLAR_ROW)
    payload = inspection.to_dict()
    assert payload["as_of"] == "2026-08-19"
    assert payload["layout"] == "tall"
    assert isinstance(payload["envelope"], dict)


def test_findings_deduplicate_by_code_and_count_occurrences(tmp_path: Path) -> None:
    row = TALL_DOLLAR_ROW.replace(",1200,", ",$1200,")
    inspection = _tall(tmp_path, row, row, row)
    finding = next(f for f in inspection.findings if f.code == "CMS_CSV_NUMERIC_VALUE_INVALID")
    assert finding.occurrences == 3
