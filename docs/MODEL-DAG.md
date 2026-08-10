# Hospital lakehouse model DAG

The implemented `hospital-json-v2` path is deliberately narrow: one CMS hospital JSON v3 file
becomes a content-addressed source snapshot plus 13 contracted Parquet models. Hospital CSV and
payer MRFs are not silently treated as if they have the same grain.

```text
CMS v3 JSON ──► sources/sha256/<prefix>/<content-sha256>.json
       │
       ├──► bounded inspection ──► manifest inspection
       │                           └──► file_finding
       │
       ├──► raw_hospital_items ──► stg_charge_item ──┬──► stg_charge_code
       │                                              └──► stg_charge_group
       │                                                     ├──► stg_payer_rate
       │                                                     │          └──► int_rate_observation
       │                                                     │                    └──► mart_file_rate_profile
       │                                                     └──► stg_charge_modifier
       │
       └──► raw_modifier_information ──► stg_modifier ──► stg_modifier_payer

mart_segmented_dollar_rate inputs:
  int_rate_observation + stg_charge_item/group/code +
  stg_charge_modifier + stg_modifier + stg_modifier_payer
```

The inspector and normalizer read the archived bytes admitted for the run, not a mutable original
path. Run identity hashes the pipeline version, publisher identifier, source-content SHA-256, and
explicit inspection `as_of` date together with a fingerprint of the schema, transformations,
parser/normalization policy, inspection policy/finding catalog, manifest schema, export set, and
contracts. Repeating that tuple verifies and reuses the same snapshot; changing any member creates
a different run.

The complete inspection is embedded in the run manifest, and deduplicated findings are exported
as `file_finding` rows. See [How local files are assessed](how-we-grade.md) for finding semantics.

## Grains and keys

| Model | One row per | Stable source-scoped key |
|---|---|---|
| `raw_hospital_items` | source charge-information array element | source digest + source item ordinal |
| `raw_modifier_information` | source modifier-information array element | source digest + source modifier ordinal |
| `stg_charge_item` | described item/service | `item_id` |
| `stg_charge_code` | code attached to an item | `item_id` + source code ordinal |
| `stg_charge_group` | standard-charge object | `charge_group_id` |
| `stg_payer_rate` | payer-information object | `payer_rate_id` |
| `stg_modifier` | modifier definition | `modifier_id` |
| `stg_modifier_payer` | payer-plan mapping within a modifier definition | `modifier_payer_id` |
| `stg_charge_modifier` | modifier-code reference on a charge group | `charge_group_id` + source modifier ordinal |
| `int_rate_observation` | stated dollar, percentage, or algorithm representation | `rate_observation_id` |
| `file_finding` | deduplicated finding code emitted for a run | `run_id` + `finding_ordinal` |
| `mart_file_rate_profile` | methodology × representation × eligibility segment | segment fields |
| `mart_segmented_dollar_rate` | comparison-eligible stated-dollar observation | `rate_observation_id` |

Every normalized row carries `run_id`, `source_file_id`, and `publisher_id`. The raw item and
modifier models retain the exact admitted JSON element text and its SHA-256 alongside a DuckDB JSON
projection. Typed numeric columns use `DECIMAL(38,10)`; if a source lexeme has greater scale, its
exact text and hash remain available rather than being overwritten by the typed projection.

The dollar mart retains every item code as an ordered `codes_json` array of `{code, code_type}`
objects. It does not promote arbitrary code ordinal zero to a primary code. It also carries the
source modifier-code array and resolved modifier context. Resolution is explicit:

- `modifier_setting` records the applicable selected definition and
  `candidate_modifier_settings` records every known definition for that canonical code;
- modifier definitions resolve as `exact`, `canonical`, `setting_mismatch`, or `unresolved`;
- payer-plan mappings resolve as `exact`, `canonical`, `unresolved_payer_plan`, or
  `unresolved_modifier`; a known modifier excluded by charge setting instead reports
  `modifier_setting_mismatch`.

Canonical matching uses NFKC normalization, surrounding-whitespace removal, and case folding while
retaining the publisher's exact text in the typed source rows. A modifier definition applies when
its optional setting is absent, is `both`, or equals the charge-group setting. Same-canonical
inpatient and outpatient definitions may coexist; definitions are rejected when their applicable
settings overlap.

## Physical reproducibility boundary

The immutable source archive is stored at:

```text
sources/sha256/<first-two-sha256-characters>/<source-sha256>.json
```

Each exported model is partitioned as:

```text
parquet/<layer>/<model>/publisher_id=<id>/period=<date>/file_version=<version>/
  run_id=<run-id>/<source-sha256>.parquet
```

A schema-v4 manifest records the transformation and inspection fingerprints and inventories the
source archive and all 13 Parquet files with relative path, byte size, and SHA-256. Its
`manifest_body_sha256` covers every immutable field except `status` and the digest itself; reuse
rejects tampered inspection, envelope, metrics, or other body content. Physical Parquet bytes are
not promised to match across independent clean builds; archived input bytes, stable keys,
contracts, counts, logical results, and the artifacts inventoried for a particular run are the
reproducibility boundary.

## Boundary contracts

The build fails closed when a contract is violated. Current contracts cover:

- declared columns and DuckDB types, including `DECIMAL(38,10)` numeric projections;
- exact raw item/modifier text hashes and unique source ordinals;
- source-scoped identifiers and item → code/group → payer → observation references;
- modifier definition, charge-reference, and payer-plan mapping references, plus setting-aware
  canonical-definition non-overlap;
- CMS v3 setting, code-type, methodology, drug-type, and allowed-count values;
- positive numeric charges and rates plus required derived-rate context;
- exact reconciliation of source representations into intermediate and profile rows;
- exclusion of every non-dollar representation from the comparison-ready mart; and
- accepted finding dimensions/severities, positive occurrence counts, and unique finding codes.

Passing these contracts means the declared local model is internally consistent. It is not a CMS
compliance determination and does not establish that a publisher's prices are accurate.

## Query without crossing methodologies

This exploratory shape retains the complete item-code set, modifier-code set, comparison segment,
and denominator. It is not yet publishable Phase 4 statistics: unresolved/setting-mismatch policy,
small-cell suppression, and uncertainty remain required.

```sql
WITH rates AS (
    SELECT
        publisher_id,
        CAST(codes_json AS VARCHAR) AS codes_json,
        CAST(modifier_codes_json AS VARCHAR) AS modifier_codes_json,
        comparison_segment,
        setting,
        dollar_amount
    FROM mart_segmented_dollar_rate
)
SELECT
    codes_json,
    modifier_codes_json,
    comparison_segment,
    setting,
    count(*) AS stated_rate_count,
    count(DISTINCT publisher_id) AS publisher_count,
    median(dollar_amount) AS median_stated_dollar
FROM rates
GROUP BY codes_json, modifier_codes_json, comparison_segment, setting
HAVING count(DISTINCT publisher_id) >= 2
ORDER BY publisher_count DESC, codes_json;
```

No row can mix a percentage or algorithm with a dollar amount, no aggregate can mix methodology
segments, and no item is grouped by an invented primary code. A publication query must additionally
define how unresolved or setting-mismatched modifier context is excluded or reported.
