# How local files are assessed

`mrf-honest inspect` reports five independent dimensions for one local CMS hospital JSON v3
file. It deliberately does **not** calculate a composite score, rank, letter grade, pass/fail
result, or CMS compliance label. The word “grade” in this document means only the deterministic
assignment of a status and source-cited findings within each dimension.

The inspector streams `standard_charge_information` with bounded retained state. It records one
finding per finding code, plus an `occurrences` value, instead of retaining an unbounded list of
row-level errors. The caller must supply `as_of`, so the same file and date produce the same
freshness result.

The inspection policy, accepted sets, freshness rule, and complete finding catalog are hashed into
an inspection fingerprint. The lakehouse transformation fingerprint incorporates it, so a grading
policy or catalog change creates a new run identity instead of silently reusing findings produced
under older semantics. The measured fingerprint for the current acceptance is recorded in
[Phase 2 validation](PHASE-2-FINDINGS.md).

## Dimension and status semantics

| Dimension | What the local inspector observes | When it is `NOT_ASSESSED` |
|---|---|---|
| Retrievability | Nothing. Network access is outside local-file inspection. | Always. The note explicitly says that local inspection does not perform or infer a network retrieval. |
| Conformance | Selected CMS v3 envelope, structure, accepted-value, JSON-stream, and attestation checks. | Never after the file has been opened; a stream failure is itself a conformance finding. |
| Completeness | Presence and basic usability of selected envelope and charge fields. | When the charge-array scan does not complete. Existing completeness findings remain attached for transparency. |
| Interpretability | Whether payer rates exist and whether percentage or algorithm representations require separate treatment. | When the scan does not complete, or when it completes without a usable item object. |
| Freshness | `last_updated_on` relative to the caller-supplied `as_of` date. | Never. An absent or invalid date produces `FRESHNESS_DATE_NOT_USABLE`. |

For a dimension that is assessed:

- `OBSERVED` means the inspector emitted no catalog finding for that dimension over the scope it
  examined. It does not mean “valid,” “accurate,” or “compliant.”
- `FINDINGS` means the inspector emitted at least one finding of any severity. An `INFO` finding
  therefore changes the status to `FINDINGS`; severity is not a numerical weight.
- `NOT_ASSESSED` means the inspector did not have the evidence needed for that dimension. It is
  not equivalent to either success or failure.

The dimensions are not rolled up. Consumers should preserve the five statuses, findings, and
notes independently.

## Finding records and occurrences

Each finding has a stable `code`, `dimension`, `severity`, contextual `message`, primary-source
`citations`, and a positive `occurrences` count. Findings are sorted by code before serialization.
The lakehouse persists one `file_finding` row per emitted code per ingest run; `finding_ordinal`
preserves that stable order and `occurrences` preserves repeated emissions without multiplying
rows.

`occurrences` is the number of times that code was emitted by the implemented checks, not a
universal affected-row denominator. Some checks run once per nested object and can accumulate;
some stream-level checks summarize multiple underlying parser problems in one emission. Use the
inspection's separate item, code, charge-group, payer-rate, and `problem_count` fields when a
denominator is needed.

Severities have intentionally narrow meanings:

- `ERROR`: the selected check found missing, unusable, structurally invalid, or uninspectable
  data.
- `WARNING`: the condition merits attention but is not represented as a structural error.
- `INFO`: a tolerated or interpretation-relevant observation, such as a UTF-8 BOM or a
  non-dollar rate representation.

These labels express the inspector's reporting priority. They do not express legal materiality.

## Authoritative finding catalog

The following 44 codes mirror `FINDING_CATALOG` in `mrf_honest.inspect`. The catalog description
is stable; an emitted finding's message can add file-specific context such as the observed value
or dates.

### Conformance

| Code | Severity | Catalog description | Citations |
|---|---|---|---|
| `CMS_V3_ATTESTATION_NOT_CONFIRMED` | WARNING | The v3 attestation is explicitly not confirmed. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_CHARGE_GROUP_NOT_OBJECT` | ERROR | A standard charge group is not a JSON object. | [JSON schema] |
| `CMS_V3_ENVELOPE_ATTESTATION_MISSING` | ERROR | Required envelope field `attestation` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_HOSPITAL_ADDRESS_MISSING` | ERROR | Required envelope field `hospital_address` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_HOSPITAL_NAME_MISSING` | ERROR | Required envelope field `hospital_name` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_LAST_UPDATED_ON_MISSING` | ERROR | Required envelope field `last_updated_on` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_LICENSE_INFORMATION_MISSING` | ERROR | Required envelope field `license_information` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_LOCATION_NAME_MISSING` | ERROR | Required envelope field `location_name` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_STANDARD_CHARGE_INFORMATION_MISSING` | ERROR | The required `standard_charge_information` array is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_TYPE_2_NPI_MISSING` | ERROR | Required envelope field `type_2_npi` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ENVELOPE_VERSION_MISSING` | ERROR | Required envelope field `version` is absent or unusable. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_LAST_UPDATED_ON_INVALID` | ERROR | `last_updated_on` is not a valid ISO `YYYY-MM-DD` date. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_METHODOLOGY_INVALID` | ERROR | A payer methodology is outside the CMS v3 accepted set. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_PAYERS_INFORMATION_INVALID` | ERROR | A present `payers_information` value is not a non-empty array. | [JSON schema] |
| `CMS_V3_PAYER_RATE_NOT_OBJECT` | ERROR | A payer rate entry is not a JSON object. | [JSON schema] |
| `CMS_V3_SETTING_INVALID` | ERROR | A charge setting is outside the CMS v3 accepted set. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_VERSION_UNEXPECTED` | ERROR | The template version is not the v3.0.0 version implemented here. | [JSON schema], [JSON dictionary] |
| `JSON_ARRAY_ITEM_PROBLEM` | ERROR | One or more charge-array entries could not be decoded as objects. | [JSON schema] |
| `JSON_STREAM_INCOMPLETE` | ERROR | The charge array could not be completely streamed. | [JSON schema] |
| `JSON_UTF8_BOM_PRESENT` | INFO | A UTF-8 byte-order mark was present and tolerated. | [JSON dictionary] |

### Completeness

| Code | Severity | Catalog description | Citations |
|---|---|---|---|
| `CMS_V3_CHARGE_VALUE_MISSING` | ERROR | A charge group contains no gross, cash, or payer-specific charge. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_CODE_TYPE_MISSING` | ERROR | A code information entry has no usable code type. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_CODE_VALUE_MISSING` | ERROR | A code information entry has no usable code value. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_DERIVED_RATE_10TH_PERCENTILE_MISSING` | ERROR | A derived rate has no `10th_percentile` allowed amount. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_DERIVED_RATE_90TH_PERCENTILE_MISSING` | ERROR | A derived rate has no `90th_percentile` allowed amount. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_DERIVED_RATE_COUNT_MISSING` | ERROR | A percentage or algorithm rate has no allowed-amount count. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_DERIVED_RATE_MEDIAN_AMOUNT_MISSING` | ERROR | A derived rate has no `median_amount` allowed amount. | [JSON dictionary], [45 CFR § 180.50] |
| `CMS_V3_DOLLAR_RANGE_MAXIMUM_MISSING` | ERROR | A dollar-rate charge group has no `maximum`. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_DOLLAR_RANGE_MINIMUM_MISSING` | ERROR | A dollar-rate charge group has no `minimum`. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ITEM_CODE_INFORMATION_MISSING` | ERROR | An item has no non-empty `code_information` array. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ITEM_DESCRIPTION_MISSING` | ERROR | An item has no usable description. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ITEM_STANDARD_CHARGES_MISSING` | ERROR | An item has no non-empty `standard_charges` array. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_OTHER_METHODOLOGY_NOTES_MISSING` | ERROR | An `other` methodology has no explanatory payer notes. | [JSON dictionary], [JSON schema] |
| `CMS_V3_PAYER_CHARGE_MISSING` | ERROR | A payer entry has no dollar, percentage, or algorithm charge. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_PAYER_PAYER_NAME_MISSING` | ERROR | A payer rate has no usable `payer_name`. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_PAYER_PLAN_NAME_MISSING` | ERROR | A payer rate has no usable `plan_name`. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_STANDARD_CHARGE_INFORMATION_EMPTY` | ERROR | The charge array contains no usable item objects. | [JSON schema], [45 CFR § 180.50] |
| `CMS_V3_ZERO_COUNT_NOTES_MISSING` | ERROR | A derived rate with count zero has no explanatory payer notes. | [JSON dictionary], [JSON schema] |

### Interpretability

| Code | Severity | Catalog description | Citations |
|---|---|---|---|
| `INTERPRETABILITY_ALGORITHM_RATES` | INFO | Algorithm rates were observed and kept separate from dollar rates. | [JSON dictionary], [45 CFR § 180.50] |
| `INTERPRETABILITY_NO_PAYER_RATES` | WARNING | No payer-specific rate objects were observed. | [JSON dictionary], [45 CFR § 180.50] |
| `INTERPRETABILITY_PERCENTAGE_RATES` | INFO | Percentage rates were observed and kept separate from dollar rates. | [JSON dictionary], [45 CFR § 180.50] |

### Freshness

| Code | Severity | Catalog description | Citations |
|---|---|---|---|
| `FRESHNESS_ANNUAL_UPDATE_OVERDUE` | WARNING | The source publication date is more than one year before `as_of`. | [45 CFR § 180.50] |
| `FRESHNESS_DATE_IN_FUTURE` | WARNING | The source publication date is after `as_of`. | [45 CFR § 180.50] |
| `FRESHNESS_DATE_NOT_USABLE` | ERROR | Freshness cannot be assessed from `last_updated_on`. | [JSON dictionary], [45 CFR § 180.50] |

Retrievability currently has no local-file finding codes because it is always `NOT_ASSESSED` by
this inspector.

## Freshness boundary

Freshness uses calendar dates, not a rolling count of seconds:

- a publication date after `as_of` emits `FRESHNESS_DATE_IN_FUTURE`;
- a publication is overdue only when `as_of` is later than its one-year anniversary, so the
  anniversary itself is not overdue; and
- February 29 uses February 28 as the anniversary in a non-leap following year.

An absent or unusable date can produce both a conformance finding about the envelope/date and the
freshness finding `FRESHNESS_DATE_NOT_USABLE`. That is intentional: the findings answer different
questions.

## What this assessment does not establish

This inspection is narrower than CMS validation and legal review:

- It does not run the official CMS validator and does not claim exhaustive schema validation.
- It does not determine compliance with 45 CFR part 180 or any other law or regulation.
- It does not prove that published prices, payer names, plan names, codes, or attestations are
  factually accurate.
- It does not retrieve a remote URL, evaluate TLS, redirects, HTTP freshness, `robots.txt`, or
  download reliability. Those belong to the separate retrieval workflow.
- It does not compare hospitals or mix dollar, percentage, and algorithm representations.
- It does not turn severities, occurrence counts, or dimension statuses into an overall score.

An `OBSERVED` status therefore means only that no catalog finding was emitted in the assessed
scope. Preserve the evidence and citations when presenting any result.

[JSON dictionary]: https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/README.md
[JSON schema]: https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/schemas/V3.0.0_Hospital_price_transparency_schema.json
[45 CFR § 180.50]: https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50
