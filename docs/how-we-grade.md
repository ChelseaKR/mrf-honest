# How files are assessed

`mrf-honest inspect` reports local-file evidence, while `mrf-honest scorecard` (also available as
`grade`) performs one identified retrieval and durably combines that remote evidence with the
local inspection. Both report five independent dimensions for one CMS hospital JSON v3 file. They
deliberately do **not** calculate a composite score, rank, letter grade, pass/fail result, or CMS
compliance label. The word “grade” in this document means only the deterministic assignment of a
status and source-cited findings within each dimension.

The inspector streams `standard_charge_information` with bounded retained state. It records one
finding per finding code, plus an `occurrences` value, instead of retaining an unbounded list of
row-level errors. The caller must supply `as_of`, so the same file and date produce the same
freshness result.

The inspection policy, accepted sets, freshness rule, and local finding catalog are hashed into
an inspection fingerprint. The lakehouse transformation fingerprint incorporates it, so a grading
policy or catalog change creates a new run identity instead of silently reusing findings produced
under older semantics. The measured fingerprint for the current acceptance is recorded in
[Phase 2 validation](PHASE-2-FINDINGS.md).

The remote scorecard has a separate policy fingerprint and artifact. Retrieval evidence changes
over time and therefore does not mutate the content-based lakehouse run identity. Each scorecard
record embeds sanitized fetch evidence, policy limits, the integrated dimensions, explicit
coverage flags, a portable semantic assessment ID, and a digest over the complete record body in
single-writer JSONL. Each append atomically replaces the complete validated file, so the workflow
does not leave a partial trailing line. Local cache paths, URL query/fragment values, and the
operator's required contact are not published; exact URL hashes preserve identity without
publishing query tokens.

## Remote scorecard workflow

```sh
uv run mrf-honest scorecard https://files.example.org/standardcharges.json \
  --publisher-id example-health \
  --publisher-type hospital \
  --location-id main-campus \
  --url-provenance cms_hpt \
  --registry data/scorecards.jsonl \
  --cache-dir data/cache \
  --contact operator@example.org \
  --format json
```

`url-provenance` is required. `cms_hpt` is a caller assertion that the URL came from confirmed CMS
TXT evidence; `operator` means it was supplied directly by the operator. Version 1 does not yet
embed the discovery-record digest, so provenance alone never turns a pre-network invalid URL into a
publisher finding. It remains a comparison-scope field and prevents operator and discovered cohorts
from being treated as methodologically identical.

The scorecard maps every terminal fetch status explicitly:

| Retrieval evidence | Retrievability result |
|---|---|
| `fetched` or verified `not_modified`, with matching body digest and size and a completed local inspection | `OBSERVED` |
| HTTP, network, or decoded-content failure | `FINDINGS`, retaining status, HTTP status when available, attempt count, time, final URL, and bounded cause |
| Invalid or unsafe redirect target observed after a request began | `FINDINGS` |
| Malformed, non-credential URL reported as `invalid_url` before a network attempt | `NOT_ASSESSED`; caller provenance alone cannot attribute invalid input to the publisher |
| Configured decoded-size limit (`too_large`) | `NOT_ASSESSED`; a project limit is not a publisher availability finding |
| Local cache error or cache miss | `NOT_ASSESSED`; local infrastructure is not attributed to the publisher |
| Fetch says success but the exact cached body cannot be verified and inspected | `NOT_ASSESSED` and an operational problem |

Every terminal status still produces a target row in the scorecard registry. Publisher/file
findings return a successful command status because they are the result; local cache, integrity,
inspection, or persistence failures are operational command failures. When no verified body is
available, conformance, completeness, interpretability, and freshness are all explicitly
`NOT_ASSESSED` rather than omitted. Version 1 also does not stitch a current failed attempt to an
older cached inspection; a future join must reference the prior assessment by digest rather than
quietly mixing observation dates.

HTTP 401 and 403 produce `MRF_AUTOMATION_BARRIER_OBSERVED`; other observed direct-download
failures produce `MRF_DIRECT_DOWNLOAD_FAILED`. These are dated technical observations, not legal
conclusions. CMS currently requires the MRF to be accessible without an account, password, or
personally identifying information and to permit automated search and direct download; CMS also
names CAPTCHA, terms acceptance, blocking code, and required information submission as barriers.
See [45 CFR § 180.50] and the [CMS policy FAQ].

`as_of` is the UTC calendar date of the recorded retrieval attempt. MRF freshness remains based
only on the file's required `last_updated_on`; HTTP `Last-Modified`, cache validation time, and the
age of `cms-hpt.txt` are not substitutes.

## Comparison boundary

Each scorecard carries an explicit publisher type and comparison scope. Direct comparison is
refused unless publisher type, assessment profile, URL provenance, assessment-policy fingerprint,
retrieval-policy fingerprint, and `as_of` all match. The implemented profile accepts hospitals
only; `payer` is a reserved explicit type, not a silent default or a claim that a payer adapter
exists. Multiple locations remain separate assessment subjects even when they share an
organization or hosting origin.

Injected openers, sleepers, backoff functions, or clocks are marked `custom` in retrieval-policy
evidence and are categorically refused by `require_comparable`; the marker cannot fully identify
arbitrary code. Version 1 also has no persisted collector-run or network-vantage identifier.
Accordingly, even matching default-policy rows are candidates for comparison only when the caller
knows they came from the same controlled collection run. Encoding that context is a phase-4
prerequisite before any public retrievability comparison is published.

Coverage flags preserve the distinctions needed for denominators: `targeted`, `network_attempted`,
`verified_body_available`, `inspection_performed`, and `inspection_scan_completed`. An invalid URL
with zero attempts remains targeted but is not counted as a network attempt; an unreachable target
that was attempted therefore remains visible instead of disappearing from the dataset.

## Dimension and status semantics

| Dimension | What the local inspector observes | When it is `NOT_ASSESSED` |
|---|---|---|
| Retrievability | Nothing. Network access is outside `inspect`; use `scorecard` for the separate remote workflow. | Always for `inspect`. The note explicitly says that local inspection does not perform or infer a network retrieval. |
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

## Authoritative local finding catalog

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

### Retrievability (remote scorecard only)

These two codes are owned by the remote scorecard policy, not the local inspection fingerprint.
`mrf-honest explain CODE` resolves both local and remote codes.

| Code | Severity | Catalog description | Citations |
|---|---|---|---|
| `MRF_AUTOMATION_BARRIER_OBSERVED` | ERROR | The direct-download request received an HTTP access barrier. | [45 CFR § 180.50], [CMS policy FAQ] |
| `MRF_DIRECT_DOWNLOAD_FAILED` | ERROR | The direct-download request did not produce a verified local body. | [45 CFR § 180.50] |

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
- Local `inspect` does not retrieve a URL. Remote `scorecard` performs one bounded GET, validates
  HTTPS redirects, and records that attempt; it does not establish long-run download reliability,
  inspect `robots.txt`, or infer MRF freshness from HTTP metadata.
- It does not compare hospitals or mix dollar, percentage, and algorithm representations.
- It does not turn severities, occurrence counts, or dimension statuses into an overall score.

An `OBSERVED` status therefore means only that no catalog finding was emitted in the assessed
scope. Preserve the evidence and citations when presenting any result.

[JSON dictionary]: https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/README.md
[JSON schema]: https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/schemas/V3.0.0_Hospital_price_transparency_schema.json
[45 CFR § 180.50]: https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50
[CMS policy FAQ]: https://www.cms.gov/files/document/hpt-policy-faqs-june-2026.pdf
