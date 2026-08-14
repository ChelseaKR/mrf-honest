# A hospital MRF still declares the superseded 2.0.0 template, seven months into v3.0.0

*A finding from the first published mrf-honest cohort, observed 2026-08-14. This describes one
published file on one date. It is not a ranking of any hospital, not a statement about care, and
not a legal compliance determination.*

## What was done

On 2026-08-13, `mrf-honest discover` retrieved the CMS-conventional `cms-hpt.txt` document from
`https://www.cedars-sinai.org/cms-hpt.txt`, which names one machine-readable file:

```
https://www.cedars-sinai.org/content/dam/cedars-sinai/billing-insurance/documents/951644600_CEDARS-SINAI-MEDICAL-CENTER_standardcharges.json
```

On 2026-08-14 at 00:47:02 UTC, `mrf-honest scorecard` performed one identified, bounded GET of
that URL (robots.txt checked first; the path is allowed), verified the decoded body by SHA-256,
and streamed the complete file through the local inspector. The exact evidence is the committed
assessment row in [`data/cohorts/2026-08-14.assessments.jsonl`](../../data/cohorts/2026-08-14.assessments.jsonl):

| Evidence | Value |
|---|---|
| Decoded size | 883,973,507 bytes |
| Content SHA-256 | `8c188ab3b02967785256d5867cf760778581e5e849f0fd1141955c2a2dcd2dbf` |
| Declared `version` | `2.0.0` |
| Declared `last_updated_on` | `2025-11-26` |
| Charge items streamed | 162,611 (scan completed; zero parser problems) |
| Payer-rate entries | 1,033,280 (572,912 dollar; 408,093 percentage; 75,572 algorithm) |

## The finding

The file's required `version` field declares CMS template **2.0.0**. CMS's JSON documentation
for the machine-readable file states that **v3.0** — which carries the CY 2026 OPPS/ASC final
rule's newly required data elements — is *"effective January 1, 2026, with CMS enforcement
beginning April 1, 2026."* As retrieved on 2026-08-14, more than seven months after the
effective date and four months after enforcement began, the published file still declares the
superseded template version.

The inspector emits this as one `ERROR`-severity conformance finding,
`CMS_V3_VERSION_UNEXPECTED`, whose catalog message is deliberately narrow: *"The template
version is not the v3.0.0 version implemented here."* Under the published
[file-grade policy](../how-we-compare.md), one dimension with errors makes this file a **C**.

Two consequences follow mechanically, and both are recorded rather than inferred:

1. The v3-only lakehouse **refuses** the file (`unsupported hospital JSON template version:
   '2.0.0'`), so unlike the other five cohort files it carries no executable-contract evidence.
   Its scorecard page states that absence instead of implying a pass.
2. Every downstream consumer that implements the current CMS schema faces the same fork: reject
   the file, or maintain a legacy v2 path CMS's own timeline has retired.

## What cuts the other way

Neutrality requires stating what the file gets right, and it is a lot:

- The envelope carries every field the v3 inspector requires — attestation with attester name,
  `type_2_npi`, license information, hospital name and address — so the *content* is closer to
  v3 expectations than the version string suggests.
- The 884 MB body streams to completion with zero JSON problems: no BOM, no malformed items.
- `last_updated_on` (2025-11-26) is inside the required annual window relative to the
  observation date, and it *predates* the v3 effective date. A plausible benign reading is that
  the hospital updates this file on an annual cadence that has not yet crossed 2026-01-01. That
  reading does not change what a consumer retrieves today, which is the point of the finding.

This inspection is narrower than CMS validation: an emitted finding means the selected check
observed exactly what it says, and the absence of other findings is not a certificate that the
rest of the file is valid.

## Sources

- [CMS hospital price-transparency JSON documentation](https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/README.md)
  (v3.0 effective and enforcement dates)
- [V3.0.0 JSON schema](https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/schemas/V3.0.0_Hospital_price_transparency_schema.json)
- [45 CFR § 180.50](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50)

## A related pattern worth one paragraph

Four of the six files in this cohort — both UC Health files, Stanford Health Care's main file,
and UC Davis Medical Center's file — begin with a UTF-8 byte-order mark.
[RFC 8259 §8.1](https://www.rfc-editor.org/rfc/rfc8259#section-8.1) says implementations *must
not* add a BOM to networked JSON, and the harm is not theoretical: this project's
[phase-0 measurement](../PHASE-0-FINDINGS.md) recorded Python's standard `json.load` failing
outright on such a file. The inspector tolerates the BOM and records it as an `INFO`
observation (`JSON_UTF8_BOM_PRESENT`) that does not lower any grade; it is noted here because a
consumer with a strict parser will hit it before they hit anything else.

## Corrections

If any evidence above is wrong, the correction path is a GitHub issue on this repository; the
registry entry, the assessment row, and this document will be corrected and the correction
dated. Being corrected is preferable to being counted right.
