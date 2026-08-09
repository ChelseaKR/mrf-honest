"""Declared DuckDB model graph for the local hospital lakehouse.

This is intentionally a small, inspectable DAG rather than a home-grown orchestration framework.
Each statement materializes one documented layer and is measured by :mod:`mrf_honest.lakehouse`.
"""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_file (
    source_file_id VARCHAR PRIMARY KEY,
    sha256 VARCHAR NOT NULL,
    byte_size UBIGINT NOT NULL,
    source_path VARCHAR NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id VARCHAR PRIMARY KEY,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    pipeline_version VARCHAR NOT NULL,
    transformation_fingerprint VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    inspection_as_of DATE NOT NULL,
    period DATE,
    file_version VARCHAR,
    error_message VARCHAR,
    source_bytes_read UBIGINT,
    item_count UBIGINT,
    code_count UBIGINT,
    charge_group_count UBIGINT,
    payer_rate_count UBIGINT,
    modifier_count UBIGINT,
    modifier_payer_mapping_count UBIGINT,
    charge_modifier_count UBIGINT,
    UNIQUE (
        publisher_id, source_file_id, pipeline_version,
        transformation_fingerprint, inspection_as_of
    )
);

CREATE TABLE IF NOT EXISTS hospital_file (
    run_id VARCHAR PRIMARY KEY,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    hospital_name VARCHAR NOT NULL,
    last_updated_on DATE NOT NULL,
    file_version VARCHAR NOT NULL,
    envelope_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_hospital_items (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    period DATE NOT NULL,
    file_version VARCHAR NOT NULL,
    item_ordinal UBIGINT NOT NULL,
    payload_text VARCHAR NOT NULL,
    payload_sha256 VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_modifier_information (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    period DATE NOT NULL,
    file_version VARCHAR NOT NULL,
    modifier_ordinal UBIGINT NOT NULL,
    payload_text VARCHAR NOT NULL,
    payload_sha256 VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_charge_item (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    item_ordinal UBIGINT NOT NULL,
    description VARCHAR NOT NULL,
    drug_unit DECIMAL(38, 10),
    drug_type VARCHAR
);

CREATE TABLE IF NOT EXISTS stg_charge_code (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    code_ordinal UBIGINT NOT NULL,
    code VARCHAR NOT NULL,
    code_type VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_charge_group (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    item_id VARCHAR NOT NULL,
    charge_group_id VARCHAR NOT NULL,
    charge_ordinal UBIGINT NOT NULL,
    minimum_amount DECIMAL(38, 10),
    maximum_amount DECIMAL(38, 10),
    gross_charge DECIMAL(38, 10),
    discounted_cash DECIMAL(38, 10),
    setting VARCHAR NOT NULL,
    billing_class VARCHAR,
    modifier_codes_json JSON NOT NULL,
    additional_generic_notes VARCHAR
);

CREATE TABLE IF NOT EXISTS stg_payer_rate (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    charge_group_id VARCHAR NOT NULL,
    payer_rate_id VARCHAR NOT NULL,
    payer_ordinal UBIGINT NOT NULL,
    payer_name VARCHAR NOT NULL,
    plan_name VARCHAR NOT NULL,
    methodology VARCHAR NOT NULL,
    standard_charge_dollar DECIMAL(38, 10),
    standard_charge_percentage DECIMAL(38, 10),
    standard_charge_algorithm VARCHAR,
    median_amount DECIMAL(38, 10),
    p10_amount DECIMAL(38, 10),
    p90_amount DECIMAL(38, 10),
    allowed_count VARCHAR,
    additional_payer_notes VARCHAR,
    canonical_payer_plan_key VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_modifier (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    modifier_id VARCHAR NOT NULL,
    modifier_ordinal UBIGINT NOT NULL,
    code VARCHAR NOT NULL,
    canonical_code VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    setting VARCHAR
);

CREATE TABLE IF NOT EXISTS stg_modifier_payer (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    modifier_id VARCHAR NOT NULL,
    modifier_payer_id VARCHAR NOT NULL,
    payer_ordinal UBIGINT NOT NULL,
    payer_name VARCHAR NOT NULL,
    plan_name VARCHAR NOT NULL,
    canonical_payer_plan_key VARCHAR NOT NULL,
    description VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_charge_modifier (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    charge_group_id VARCHAR NOT NULL,
    modifier_ordinal UBIGINT NOT NULL,
    modifier_code VARCHAR NOT NULL,
    canonical_modifier_code VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS int_rate_observation (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    rate_observation_id VARCHAR NOT NULL,
    payer_rate_id VARCHAR NOT NULL,
    charge_group_id VARCHAR NOT NULL,
    payer_name VARCHAR NOT NULL,
    plan_name VARCHAR NOT NULL,
    methodology VARCHAR NOT NULL,
    setting VARCHAR NOT NULL,
    billing_class VARCHAR,
    rate_kind VARCHAR NOT NULL,
    rate_numeric DECIMAL(38, 10),
    rate_algorithm VARCHAR,
    eligible_for_segmented_comparison BOOLEAN NOT NULL,
    exclusion_reason VARCHAR
);

CREATE TABLE IF NOT EXISTS mart_file_rate_profile (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    publisher_id VARCHAR NOT NULL,
    methodology VARCHAR NOT NULL,
    rate_kind VARCHAR NOT NULL,
    eligible_for_segmented_comparison BOOLEAN NOT NULL,
    observation_count UBIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_finding (
    run_id VARCHAR NOT NULL,
    finding_ordinal UBIGINT NOT NULL,
    code VARCHAR NOT NULL,
    dimension VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    citations_json JSON NOT NULL,
    occurrences UBIGINT NOT NULL,
    PRIMARY KEY (run_id, finding_ordinal)
);

CREATE TABLE IF NOT EXISTS model_metric (
    run_id VARCHAR NOT NULL,
    model_name VARCHAR NOT NULL,
    layer VARCHAR NOT NULL,
    rows_produced UBIGINT NOT NULL,
    rows_scanned UBIGINT NOT NULL,
    bytes_read UBIGINT NOT NULL,
    bytes_written UBIGINT NOT NULL,
    wall_time_ms DOUBLE NOT NULL,
    system_peak_buffer_memory_bytes UBIGINT NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, model_name)
);

CREATE OR REPLACE VIEW mart_segmented_dollar_rate AS
WITH item_codes AS (
    SELECT
        run_id,
        item_id,
        to_json(list(
            struct_pack(code := code, code_type := code_type)
            ORDER BY code_ordinal
        )) AS codes_json
    FROM stg_charge_code
    GROUP BY run_id, item_id
),
known_modifier_codes AS (
    SELECT
        run_id,
        canonical_code,
        list(setting ORDER BY modifier_ordinal) AS candidate_modifier_settings
    FROM stg_modifier
    GROUP BY run_id, canonical_code
),
modifier_context AS (
    SELECT
        p.run_id,
        p.payer_rate_id,
        to_json(list(
            struct_pack(
                modifier_code := cm.modifier_code,
                modifier_description := m.description,
                modifier_setting := m.setting,
                candidate_modifier_settings := known.candidate_modifier_settings,
                modifier_resolution_status := CASE
                    WHEN m.modifier_id IS NULL AND known.canonical_code IS NOT NULL
                        THEN 'setting_mismatch'
                    WHEN m.modifier_id IS NULL THEN 'unresolved'
                    WHEN m.code = cm.modifier_code THEN 'exact'
                    ELSE 'canonical'
                END,
                payer_mapping_description := mp.description,
                payer_mapping_resolution_status := CASE
                    WHEN m.modifier_id IS NULL AND known.canonical_code IS NOT NULL
                        THEN 'modifier_setting_mismatch'
                    WHEN m.modifier_id IS NULL THEN 'unresolved_modifier'
                    WHEN mp.modifier_payer_id IS NULL THEN 'unresolved_payer_plan'
                    WHEN mp.payer_name = p.payer_name AND mp.plan_name = p.plan_name THEN 'exact'
                    ELSE 'canonical'
                END
            ) ORDER BY cm.modifier_ordinal
        )) AS modifier_context_json
    FROM stg_payer_rate p
    JOIN stg_charge_modifier cm
      ON cm.run_id = p.run_id AND cm.charge_group_id = p.charge_group_id
    JOIN stg_charge_group g
      ON g.run_id = p.run_id AND g.charge_group_id = p.charge_group_id
    LEFT JOIN known_modifier_codes known
      ON known.run_id = cm.run_id AND known.canonical_code = cm.canonical_modifier_code
    LEFT JOIN stg_modifier m
      ON m.run_id = cm.run_id AND m.canonical_code = cm.canonical_modifier_code
     AND (m.setting IS NULL OR m.setting = 'both' OR m.setting = g.setting)
    LEFT JOIN stg_modifier_payer mp
      ON mp.run_id = m.run_id
     AND mp.modifier_id = m.modifier_id
     AND mp.canonical_payer_plan_key = p.canonical_payer_plan_key
    GROUP BY p.run_id, p.payer_rate_id
)
SELECT
    o.run_id,
    o.source_file_id,
    o.publisher_id,
    o.rate_observation_id,
    o.payer_rate_id,
    o.charge_group_id,
    g.item_id,
    i.description,
    c.codes_json,
    g.modifier_codes_json,
    coalesce(mc.modifier_context_json, CAST('[]' AS JSON)) AS modifier_context_json,
    o.payer_name,
    o.plan_name,
    o.methodology AS comparison_segment,
    o.setting,
    o.billing_class,
    o.rate_numeric AS dollar_amount
FROM int_rate_observation o
JOIN stg_charge_group g
  ON g.run_id = o.run_id AND g.charge_group_id = o.charge_group_id
JOIN stg_charge_item i
  ON i.run_id = g.run_id AND i.item_id = g.item_id
JOIN item_codes c
  ON c.run_id = i.run_id AND c.item_id = i.item_id
LEFT JOIN modifier_context mc
  ON mc.run_id = o.run_id AND mc.payer_rate_id = o.payer_rate_id
WHERE o.rate_kind = 'dollar'
  AND o.eligible_for_segmented_comparison;
"""


INTERMEDIATE_SQL = """
INSERT INTO int_rate_observation
SELECT
    p.run_id,
    p.source_file_id,
    p.publisher_id,
    sha256(p.payer_rate_id || ':dollar'),
    p.payer_rate_id,
    p.charge_group_id,
    p.payer_name,
    p.plan_name,
    p.methodology,
    g.setting,
    g.billing_class,
    'dollar',
    p.standard_charge_dollar,
    NULL,
    TRUE,
    NULL
FROM stg_payer_rate p
JOIN stg_charge_group g
  ON g.run_id = p.run_id AND g.charge_group_id = p.charge_group_id
WHERE p.run_id = ? AND p.standard_charge_dollar IS NOT NULL
UNION ALL
SELECT
    p.run_id,
    p.source_file_id,
    p.publisher_id,
    sha256(p.payer_rate_id || ':percentage'),
    p.payer_rate_id,
    p.charge_group_id,
    p.payer_name,
    p.plan_name,
    p.methodology,
    g.setting,
    g.billing_class,
    'percentage',
    p.standard_charge_percentage,
    NULL,
    FALSE,
    'percentage rates are not dollar amounts'
FROM stg_payer_rate p
JOIN stg_charge_group g
  ON g.run_id = p.run_id AND g.charge_group_id = p.charge_group_id
WHERE p.run_id = ? AND p.standard_charge_percentage IS NOT NULL
UNION ALL
SELECT
    p.run_id,
    p.source_file_id,
    p.publisher_id,
    sha256(p.payer_rate_id || ':algorithm'),
    p.payer_rate_id,
    p.charge_group_id,
    p.payer_name,
    p.plan_name,
    p.methodology,
    g.setting,
    g.billing_class,
    'algorithm',
    NULL,
    p.standard_charge_algorithm,
    FALSE,
    'algorithmic rates are not stated dollar amounts'
FROM stg_payer_rate p
JOIN stg_charge_group g
  ON g.run_id = p.run_id AND g.charge_group_id = p.charge_group_id
WHERE p.run_id = ? AND p.standard_charge_algorithm IS NOT NULL
"""


MART_SQL = """
INSERT INTO mart_file_rate_profile
SELECT
    run_id,
    source_file_id,
    publisher_id,
    methodology,
    rate_kind,
    eligible_for_segmented_comparison,
    count(*)::UBIGINT
FROM int_rate_observation
WHERE run_id = ?
GROUP BY ALL
"""


MODEL_DAG: dict[str, tuple[str, ...]] = {
    "raw_hospital_items": (),
    "raw_modifier_information": (),
    "stg_charge_item": ("raw_hospital_items",),
    "stg_charge_code": ("stg_charge_item",),
    "stg_charge_group": ("stg_charge_item",),
    "stg_payer_rate": ("stg_charge_group",),
    "stg_modifier": ("raw_modifier_information",),
    "stg_modifier_payer": ("stg_modifier",),
    "stg_charge_modifier": ("stg_charge_group", "stg_modifier"),
    "int_rate_observation": ("stg_payer_rate", "stg_charge_group"),
    "file_finding": ("raw_hospital_items",),
    "mart_file_rate_profile": ("int_rate_observation",),
    "mart_segmented_dollar_rate": (
        "int_rate_observation",
        "stg_charge_code",
        "stg_charge_modifier",
        "stg_modifier_payer",
    ),
}

LAYER_BY_MODEL = {
    "raw_hospital_items": "raw",
    "raw_modifier_information": "raw",
    "stg_charge_item": "staging",
    "stg_charge_code": "staging",
    "stg_charge_group": "staging",
    "stg_payer_rate": "staging",
    "stg_modifier": "staging",
    "stg_modifier_payer": "staging",
    "stg_charge_modifier": "staging",
    "int_rate_observation": "intermediate",
    "file_finding": "mart",
    "mart_file_rate_profile": "mart",
    "mart_segmented_dollar_rate": "mart",
}
