"""Executable data contracts at the raw, staging, intermediate, and mart boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


class QueryResult(Protocol):
    def fetchone(self) -> tuple[Any, ...] | None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


class SqlConnection(Protocol):
    def execute(self, query: str, parameters: object = None) -> QueryResult: ...


@dataclass(frozen=True)
class ContractViolation:
    model: str
    rule: str
    violating_rows: int
    message: str


class ContractError(RuntimeError):
    """Raised when a layer boundary would otherwise admit invalid data."""

    def __init__(self, violations: list[ContractViolation]) -> None:
        self.violations = tuple(violations)
        summary = "; ".join(
            f"{item.model}.{item.rule}: {item.violating_rows} row(s)" for item in violations
        )
        super().__init__(f"data contract failed: {summary}")


@dataclass(frozen=True)
class _Rule:
    model: str
    name: str
    sql: str
    message: str


EXPECTED_TYPES: dict[str, dict[str, str]] = {
    "raw_hospital_items": {
        "run_id": "VARCHAR",
        "item_ordinal": "UBIGINT",
        "payload_text": "VARCHAR",
        "payload_sha256": "VARCHAR",
    },
    "raw_modifier_information": {
        "run_id": "VARCHAR",
        "modifier_ordinal": "UBIGINT",
        "payload_text": "VARCHAR",
        "payload_sha256": "VARCHAR",
    },
    "stg_charge_item": {
        "run_id": "VARCHAR",
        "item_id": "VARCHAR",
        "item_ordinal": "UBIGINT",
        "description": "VARCHAR",
        "drug_unit": "DECIMAL(38,10)",
    },
    "stg_charge_code": {
        "item_id": "VARCHAR",
        "code_ordinal": "UBIGINT",
        "code": "VARCHAR",
        "code_type": "VARCHAR",
    },
    "stg_charge_group": {
        "charge_group_id": "VARCHAR",
        "charge_ordinal": "UBIGINT",
        "setting": "VARCHAR",
        "gross_charge": "DECIMAL(38,10)",
        "modifier_codes_json": "JSON",
    },
    "stg_payer_rate": {
        "payer_rate_id": "VARCHAR",
        "payer_name": "VARCHAR",
        "methodology": "VARCHAR",
        "standard_charge_dollar": "DECIMAL(38,10)",
        "standard_charge_percentage": "DECIMAL(38,10)",
        "canonical_payer_plan_key": "VARCHAR",
    },
    "stg_modifier": {
        "modifier_id": "VARCHAR",
        "modifier_ordinal": "UBIGINT",
        "code": "VARCHAR",
        "canonical_code": "VARCHAR",
        "description": "VARCHAR",
        "setting": "VARCHAR",
    },
    "stg_modifier_payer": {
        "modifier_id": "VARCHAR",
        "modifier_payer_id": "VARCHAR",
        "payer_ordinal": "UBIGINT",
        "canonical_payer_plan_key": "VARCHAR",
    },
    "stg_charge_modifier": {
        "charge_group_id": "VARCHAR",
        "modifier_ordinal": "UBIGINT",
        "modifier_code": "VARCHAR",
        "canonical_modifier_code": "VARCHAR",
    },
    "int_rate_observation": {
        "rate_observation_id": "VARCHAR",
        "rate_kind": "VARCHAR",
        "rate_numeric": "DECIMAL(38,10)",
        "eligible_for_segmented_comparison": "BOOLEAN",
    },
    "file_finding": {
        "finding_ordinal": "UBIGINT",
        "code": "VARCHAR",
        "dimension": "VARCHAR",
        "severity": "VARCHAR",
        "citations_json": "JSON",
        "occurrences": "UBIGINT",
    },
    "mart_file_rate_profile": {
        "methodology": "VARCHAR",
        "rate_kind": "VARCHAR",
        "observation_count": "UBIGINT",
    },
    "mart_segmented_dollar_rate": {
        "rate_observation_id": "VARCHAR",
        "codes_json": "JSON",
        "modifier_codes_json": "JSON",
        "modifier_context_json": "JSON",
        "dollar_amount": "DECIMAL(38,10)",
    },
}


RULES = (
    _Rule(
        "raw_hospital_items",
        "unique_item_ordinal",
        """SELECT count(*) - count(DISTINCT item_ordinal)
        FROM raw_hospital_items WHERE run_id = ?""",
        "source item ordinals must be unique within a run",
    ),
    _Rule(
        "raw_hospital_items",
        "one_raw_row_per_item",
        """SELECT abs(
            (SELECT count(*) FROM raw_hospital_items WHERE run_id = ?) -
            (SELECT count(*) FROM stg_charge_item WHERE run_id = ?)
        )""",
        "raw and staged item counts must agree",
    ),
    _Rule(
        "raw_hospital_items",
        "exact_payload_hash",
        """SELECT count(*) FROM raw_hospital_items WHERE run_id = ? AND (
            NOT json_valid(payload_text) OR sha256(payload_text) <> payload_sha256
        )""",
        "raw item text must remain valid JSON with its exact admitted byte hash",
    ),
    _Rule(
        "raw_modifier_information",
        "unique_modifier_ordinal",
        """SELECT count(*) - count(DISTINCT modifier_ordinal)
        FROM raw_modifier_information WHERE run_id = ?""",
        "source modifier ordinals must be unique within a run",
    ),
    _Rule(
        "raw_modifier_information",
        "exact_payload_hash",
        """SELECT count(*) FROM raw_modifier_information WHERE run_id = ? AND (
            NOT json_valid(payload_text) OR sha256(payload_text) <> payload_sha256
        )""",
        "raw modifier text must remain valid JSON with its exact admitted byte hash",
    ),
    _Rule(
        "stg_charge_item",
        "has_code",
        """SELECT count(*) FROM stg_charge_item i
        WHERE i.run_id = ? AND NOT EXISTS (
            SELECT 1 FROM stg_charge_code c
            WHERE c.run_id = i.run_id AND c.item_id = i.item_id
        )""",
        "every charge item must retain at least one billing or accounting code",
    ),
    _Rule(
        "stg_charge_item",
        "unique_item_id",
        """SELECT count(*) - count(DISTINCT item_id)
        FROM stg_charge_item WHERE run_id = ?""",
        "source-scoped item identifiers must be unique",
    ),
    _Rule(
        "stg_charge_item",
        "has_charge_group",
        """SELECT count(*) FROM stg_charge_item i
        WHERE i.run_id = ? AND NOT EXISTS (
            SELECT 1 FROM stg_charge_group g
            WHERE g.run_id = i.run_id AND g.item_id = i.item_id
        )""",
        "every item must retain at least one standard-charge group",
    ),
    _Rule(
        "stg_charge_item",
        "ndc_requires_drug_information",
        """SELECT count(*) FROM stg_charge_item i
        WHERE i.run_id = ? AND EXISTS (
            SELECT 1 FROM stg_charge_code c
            WHERE c.run_id = i.run_id AND c.item_id = i.item_id AND c.code_type = 'NDC'
        ) AND (i.drug_unit IS NULL OR i.drug_type IS NULL)""",
        "items carrying an NDC code must retain CMS drug unit and type",
    ),
    _Rule(
        "stg_charge_item",
        "accepted_drug_type",
        """SELECT count(*) FROM stg_charge_item WHERE run_id = ?
        AND drug_type IS NOT NULL
        AND drug_type NOT IN ('GR', 'ML', 'ME', 'UN', 'F2', 'GM', 'EA')""",
        "drug type must use a CMS v3 data-dictionary value",
    ),
    _Rule(
        "stg_charge_code",
        "item_reference",
        """SELECT count(*) FROM stg_charge_code c
        LEFT JOIN stg_charge_item i ON i.run_id = c.run_id AND i.item_id = c.item_id
        WHERE c.run_id = ? AND i.item_id IS NULL""",
        "every code must reference a staged item",
    ),
    _Rule(
        "stg_charge_code",
        "unique_item_code_ordinal",
        """SELECT count(*) - count(DISTINCT item_id || chr(31) || code_ordinal::VARCHAR)
        FROM stg_charge_code WHERE run_id = ?""",
        "code ordinals must be unique within an item",
    ),
    _Rule(
        "stg_charge_code",
        "contiguous_item_code_ordinals",
        """SELECT count(*) FROM (
            SELECT item_id FROM stg_charge_code WHERE run_id = ? GROUP BY item_id
            HAVING min(code_ordinal) <> 0 OR max(code_ordinal) + 1 <> count(*)
        ) invalid""",
        "code ordinals must form a zero-based contiguous source sequence",
    ),
    _Rule(
        "stg_charge_code",
        "accepted_code_type",
        """SELECT count(*) FROM stg_charge_code WHERE run_id = ? AND code_type NOT IN (
            'CPT', 'NDC', 'HCPCS', 'RC', 'ICD', 'DRG', 'MS-DRG', 'R-DRG', 'S-DRG',
            'APS-DRG', 'AP-DRG', 'APR-DRG', 'APC', 'LOCAL', 'EAPG', 'HIPPS', 'CDT', 'CDM',
            'TRIS-DRG', 'CMG', 'MS-LTC-DRG'
        )""",
        "code type must be one of the CMS v3 data-dictionary values",
    ),
    _Rule(
        "stg_charge_group",
        "item_reference",
        """SELECT count(*) FROM stg_charge_group g
        LEFT JOIN stg_charge_item i ON i.run_id = g.run_id AND i.item_id = g.item_id
        WHERE g.run_id = ? AND i.item_id IS NULL""",
        "every charge group must reference a staged item",
    ),
    _Rule(
        "stg_charge_group",
        "unique_charge_group_id",
        """SELECT count(*) - count(DISTINCT charge_group_id)
        FROM stg_charge_group WHERE run_id = ?""",
        "source-scoped charge-group identifiers must be unique",
    ),
    _Rule(
        "stg_charge_group",
        "accepted_setting",
        """SELECT count(*) FROM stg_charge_group
        WHERE run_id = ? AND setting NOT IN ('inpatient', 'outpatient', 'both')""",
        "setting must be inpatient, outpatient, or both",
    ),
    _Rule(
        "stg_charge_group",
        "positive_amounts",
        """SELECT count(*) FROM stg_charge_group WHERE run_id = ? AND (
            minimum_amount <= 0 OR maximum_amount <= 0 OR gross_charge <= 0 OR discounted_cash <= 0
        )""",
        "CMS v3 numeric charge values must be positive",
    ),
    _Rule(
        "stg_charge_group",
        "has_charge_value",
        """SELECT count(*) FROM stg_charge_group g WHERE g.run_id = ?
        AND g.gross_charge IS NULL AND g.discounted_cash IS NULL AND NOT EXISTS (
            SELECT 1 FROM stg_payer_rate p
            WHERE p.run_id = g.run_id AND p.charge_group_id = g.charge_group_id
        )""",
        "a charge group must retain a gross, cash, or payer-specific charge",
    ),
    _Rule(
        "stg_charge_group",
        "dollar_rates_require_range",
        """SELECT count(*) FROM stg_charge_group g WHERE g.run_id = ?
        AND (g.minimum_amount IS NULL OR g.maximum_amount IS NULL) AND EXISTS (
            SELECT 1 FROM stg_payer_rate p
            WHERE p.run_id = g.run_id AND p.charge_group_id = g.charge_group_id
            AND p.standard_charge_dollar IS NOT NULL
        )""",
        "charge groups with dollar payer rates require de-identified minimum and maximum",
    ),
    _Rule(
        "stg_payer_rate",
        "charge_group_reference",
        """SELECT count(*) FROM stg_payer_rate p
        LEFT JOIN stg_charge_group g
          ON g.run_id = p.run_id AND g.charge_group_id = p.charge_group_id
        WHERE p.run_id = ? AND g.charge_group_id IS NULL""",
        "every payer rate must reference a charge group",
    ),
    _Rule(
        "stg_payer_rate",
        "unique_payer_rate_id",
        """SELECT count(*) - count(DISTINCT payer_rate_id)
        FROM stg_payer_rate WHERE run_id = ?""",
        "source-scoped payer-rate identifiers must be unique",
    ),
    _Rule(
        "stg_payer_rate",
        "accepted_methodology",
        """SELECT count(*) FROM stg_payer_rate WHERE run_id = ? AND methodology NOT IN (
            'case rate', 'fee schedule', 'percent of total billed charges', 'per diem', 'other'
        )""",
        "methodology must be a CMS v3 data-dictionary value",
    ),
    _Rule(
        "stg_payer_rate",
        "has_rate_representation",
        """SELECT count(*) FROM stg_payer_rate WHERE run_id = ?
        AND standard_charge_dollar IS NULL
        AND standard_charge_percentage IS NULL
        AND standard_charge_algorithm IS NULL""",
        "a payer rate must state a dollar, percentage, or algorithm representation",
    ),
    _Rule(
        "stg_payer_rate",
        "canonical_payer_plan_key_present",
        """SELECT count(*) FROM stg_payer_rate
        WHERE run_id = ? AND canonical_payer_plan_key = ''""",
        "payer rows must retain an explicit derived comparison key",
    ),
    _Rule(
        "stg_payer_rate",
        "algorithm_nonempty",
        """SELECT count(*) FROM stg_payer_rate WHERE run_id = ?
        AND standard_charge_algorithm IS NOT NULL
        AND length(trim(standard_charge_algorithm)) = 0""",
        "algorithm representations must be non-empty strings",
    ),
    _Rule(
        "stg_payer_rate",
        "positive_numeric_values",
        """SELECT count(*) FROM stg_payer_rate WHERE run_id = ? AND (
            standard_charge_dollar <= 0 OR standard_charge_percentage <= 0 OR
            median_amount <= 0 OR p10_amount <= 0 OR p90_amount <= 0
        )""",
        "CMS v3 payer numeric values must be positive",
    ),
    _Rule(
        "stg_payer_rate",
        "valid_allowed_count",
        """SELECT count(*) FROM stg_payer_rate WHERE run_id = ? AND allowed_count IS NOT NULL
        AND NOT regexp_full_match(
            allowed_count,
            '(0|1 through 10|1[1-9]|[2-9][0-9]+|[1-9][0-9]{2,})'
        )""",
        "allowed count must use the CMS v3 count categories",
    ),
    _Rule(
        "stg_payer_rate",
        "derived_rate_allowed_amounts",
        """SELECT count(*) FROM stg_payer_rate WHERE run_id = ?
        AND (standard_charge_percentage IS NOT NULL OR standard_charge_algorithm IS NOT NULL)
        AND (
            allowed_count IS NULL OR
            (allowed_count <> '0' AND (
                median_amount IS NULL OR p10_amount IS NULL OR p90_amount IS NULL
            ))
        )""",
        "percentage and algorithm rates need count and nonzero-count allowed-amount context",
    ),
    _Rule(
        "stg_modifier",
        "raw_reference",
        """SELECT count(*) FROM stg_modifier m
        LEFT JOIN raw_modifier_information r
          ON r.run_id = m.run_id AND r.modifier_ordinal = m.modifier_ordinal
        WHERE m.run_id = ? AND r.modifier_ordinal IS NULL""",
        "every typed modifier must retain its raw source object",
    ),
    _Rule(
        "stg_modifier",
        "unique_modifier_id",
        """SELECT count(*) - count(DISTINCT modifier_id)
        FROM stg_modifier WHERE run_id = ?""",
        "source-scoped modifier identifiers must be unique",
    ),
    _Rule(
        "stg_modifier",
        "unambiguous_canonical_code_setting",
        """SELECT count(*) FROM stg_modifier left_modifier
        JOIN stg_modifier right_modifier
          ON right_modifier.run_id = left_modifier.run_id
         AND right_modifier.canonical_code = left_modifier.canonical_code
         AND right_modifier.modifier_ordinal > left_modifier.modifier_ordinal
        WHERE left_modifier.run_id = ? AND (
            left_modifier.setting IS NULL OR right_modifier.setting IS NULL OR
            left_modifier.setting = 'both' OR right_modifier.setting = 'both' OR
            left_modifier.setting = right_modifier.setting
        )""",
        "canonical modifier definitions must not overlap for an applicable setting",
    ),
    _Rule(
        "stg_modifier",
        "accepted_setting",
        """SELECT count(*) FROM stg_modifier WHERE run_id = ?
        AND setting IS NOT NULL AND setting NOT IN ('inpatient', 'outpatient', 'both')""",
        "an optional modifier setting must use the CMS v3 accepted values",
    ),
    _Rule(
        "stg_modifier_payer",
        "modifier_reference",
        """SELECT count(*) FROM stg_modifier_payer p
        LEFT JOIN stg_modifier m ON m.run_id = p.run_id AND m.modifier_id = p.modifier_id
        WHERE p.run_id = ? AND m.modifier_id IS NULL""",
        "every modifier-payer mapping must reference a source modifier",
    ),
    _Rule(
        "stg_modifier_payer",
        "unique_mapping_id",
        """SELECT count(*) - count(DISTINCT modifier_payer_id)
        FROM stg_modifier_payer WHERE run_id = ?""",
        "source-scoped modifier-payer identifiers must be unique",
    ),
    _Rule(
        "stg_modifier_payer",
        "unique_canonical_payer_plan",
        """SELECT count(*) - count(DISTINCT
            modifier_id || chr(31) || canonical_payer_plan_key
        ) FROM stg_modifier_payer WHERE run_id = ?""",
        "a modifier must have at most one mapping per canonical payer-plan pair",
    ),
    _Rule(
        "stg_charge_modifier",
        "charge_group_reference",
        """SELECT count(*) FROM stg_charge_modifier cm
        LEFT JOIN stg_charge_group g
          ON g.run_id = cm.run_id AND g.charge_group_id = cm.charge_group_id
        WHERE cm.run_id = ? AND g.charge_group_id IS NULL""",
        "every charge modifier must reference its source charge group",
    ),
    _Rule(
        "stg_charge_modifier",
        "unique_charge_modifier_ordinal",
        """SELECT count(*) - count(DISTINCT
            charge_group_id || chr(31) || modifier_ordinal::VARCHAR
        ) FROM stg_charge_modifier WHERE run_id = ?""",
        "modifier ordinals must be unique within a charge group",
    ),
    _Rule(
        "int_rate_observation",
        "payer_rate_reference",
        """SELECT count(*) FROM int_rate_observation o
        LEFT JOIN stg_payer_rate p ON p.run_id = o.run_id AND p.payer_rate_id = o.payer_rate_id
        WHERE o.run_id = ? AND p.payer_rate_id IS NULL""",
        "every observation must reference its payer-rate source row",
    ),
    _Rule(
        "int_rate_observation",
        "unique_rate_observation_id",
        """SELECT count(*) - count(DISTINCT rate_observation_id)
        FROM int_rate_observation WHERE run_id = ?""",
        "each preserved rate representation must have one stable identifier",
    ),
    _Rule(
        "int_rate_observation",
        "one_row_per_payer_rate_kind",
        """SELECT count(*) - count(DISTINCT payer_rate_id || chr(31) || rate_kind)
        FROM int_rate_observation WHERE run_id = ?""",
        "each payer-rate representation must materialize exactly once",
    ),
    _Rule(
        "int_rate_observation",
        "source_representation_reconciliation",
        """SELECT abs(
            (SELECT count(*) FROM int_rate_observation WHERE run_id = ?) -
            (SELECT coalesce(sum(
                (standard_charge_dollar IS NOT NULL)::INTEGER +
                (standard_charge_percentage IS NOT NULL)::INTEGER +
                (standard_charge_algorithm IS NOT NULL)::INTEGER
            ), 0) FROM stg_payer_rate WHERE run_id = ?)
        )""",
        "intermediate rows must reconcile to every source rate representation",
    ),
    _Rule(
        "int_rate_observation",
        "accepted_rate_kind",
        """SELECT count(*) FROM int_rate_observation
        WHERE run_id = ? AND rate_kind NOT IN ('dollar', 'percentage', 'algorithm')""",
        "rate kind must preserve the source representation",
    ),
    _Rule(
        "int_rate_observation",
        "comparison_eligibility",
        """SELECT count(*) FROM int_rate_observation WHERE run_id = ? AND (
            (rate_kind = 'dollar' AND NOT eligible_for_segmented_comparison) OR
            (rate_kind <> 'dollar' AND eligible_for_segmented_comparison)
        )""",
        "only stated dollar observations may enter the segmented comparison mart",
    ),
    _Rule(
        "int_rate_observation",
        "rate_value_shape",
        """SELECT count(*) FROM int_rate_observation WHERE run_id = ? AND NOT (
            (rate_kind = 'dollar' AND rate_numeric IS NOT NULL AND rate_algorithm IS NULL) OR
            (rate_kind = 'percentage' AND rate_numeric IS NOT NULL AND rate_algorithm IS NULL) OR
            (rate_kind = 'algorithm' AND rate_numeric IS NULL AND rate_algorithm IS NOT NULL)
        )""",
        "each rate kind must retain exactly its numeric or algorithm source value",
    ),
    _Rule(
        "file_finding",
        "accepted_dimension_and_severity",
        """SELECT count(*) FROM file_finding WHERE run_id = ? AND (
            dimension NOT IN (
                'retrievability', 'conformance', 'completeness',
                'interpretability', 'freshness'
            ) OR severity NOT IN ('INFO', 'WARNING', 'ERROR') OR occurrences = 0
        )""",
        "findings must retain a declared dimension, severity, and positive occurrence count",
    ),
    _Rule(
        "file_finding",
        "unique_finding_code",
        """SELECT count(*) - count(DISTINCT code)
        FROM file_finding WHERE run_id = ?""",
        "finding codes are deduplicated within an inspection",
    ),
    _Rule(
        "mart_file_rate_profile",
        "positive_denominator",
        """SELECT count(*) FROM mart_file_rate_profile
        WHERE run_id = ? AND observation_count = 0""",
        "profile rows must carry an explicit nonzero denominator",
    ),
    _Rule(
        "mart_file_rate_profile",
        "unique_segment",
        """SELECT count(*) - count(DISTINCT
            methodology || chr(31) || rate_kind || chr(31) ||
            eligible_for_segmented_comparison::VARCHAR
        ) FROM mart_file_rate_profile WHERE run_id = ?""",
        "the file profile must have one denominator per explicit segment",
    ),
    _Rule(
        "mart_file_rate_profile",
        "intermediate_group_reconciliation",
        """SELECT count(*) FROM (
            SELECT
                coalesce(i.methodology, p.methodology) AS methodology,
                coalesce(i.rate_kind, p.rate_kind) AS rate_kind,
                coalesce(
                    i.eligible_for_segmented_comparison,
                    p.eligible_for_segmented_comparison
                ) AS eligible
            FROM (
                SELECT methodology, rate_kind, eligible_for_segmented_comparison,
                    count(*)::UBIGINT AS observation_count
                FROM int_rate_observation WHERE run_id = ? GROUP BY ALL
            ) i
            FULL OUTER JOIN (
                SELECT methodology, rate_kind, eligible_for_segmented_comparison,
                    observation_count
                FROM mart_file_rate_profile WHERE run_id = ?
            ) p USING (methodology, rate_kind, eligible_for_segmented_comparison)
            WHERE i.observation_count IS DISTINCT FROM p.observation_count
        ) invalid""",
        "profile segments and denominators must exactly reconcile to intermediate rows",
    ),
    _Rule(
        "mart_segmented_dollar_rate",
        "eligible_rate_reconciliation",
        """SELECT abs(
            (SELECT count(*) FROM mart_segmented_dollar_rate WHERE run_id = ?) -
            (SELECT count(*) FROM int_rate_observation
             WHERE run_id = ? AND rate_kind = 'dollar'
             AND eligible_for_segmented_comparison)
        )""",
        "the comparison mart must retain one row per eligible dollar observation",
    ),
)

CONTRACT_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "expected_types": EXPECTED_TYPES,
            "rules": [
                {
                    "model": rule.model,
                    "name": rule.name,
                    "sql": rule.sql,
                    "message": rule.message,
                }
                for rule in RULES
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def _column_types(connection: SqlConnection, model: str) -> dict[str, str]:
    rows = connection.execute(f"PRAGMA table_info('{model}')").fetchall()
    return {str(row[1]): str(row[2]).upper() for row in rows}


def _type_violations(connection: SqlConnection) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    for model, expected in EXPECTED_TYPES.items():
        observed = _column_types(connection, model)
        for column, expected_type in expected.items():
            if observed.get(column) != expected_type:
                violations.append(
                    ContractViolation(
                        model=model,
                        rule=f"column_type_{column}",
                        violating_rows=1,
                        message=(
                            f"expected {column} {expected_type}, observed "
                            f"{observed.get(column, 'missing')}"
                        ),
                    )
                )
    return violations


def validate_contracts(connection: SqlConnection, run_id: str) -> list[ContractViolation]:
    """Return every contract violation for ``run_id`` without weakening failures to warnings."""
    violations = _type_violations(connection)
    for rule in RULES:
        parameter_count = rule.sql.count("?")
        row = connection.execute(rule.sql, [run_id] * parameter_count).fetchone()
        count = int(row[0]) if row is not None else 0
        if count:
            violations.append(
                ContractViolation(
                    model=rule.model,
                    rule=rule.name,
                    violating_rows=count,
                    message=rule.message,
                )
            )
    return violations


def enforce_contracts(connection: SqlConnection, run_id: str) -> None:
    violations = validate_contracts(connection, run_id)
    if violations:
        raise ContractError(violations)
