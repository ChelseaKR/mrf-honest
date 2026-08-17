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
   That refusal is a limit of this project's warehouse, not a finding about the file, and the
   scorecard page states it with that reason attached rather than as a bare absence.
2. A downstream consumer that dispatches on the declared `version` string — which is what the
   field is for, and what CMS's own versioned schemas invite — reaches the same fork: reject the
   file, or keep a legacy v2 path alive. That is a claim about the label, not about the payload,
   and the next section is why the distinction matters here.

## What the file actually contains: v3.0.0 structure under a 2.0.0 label

The version string is stale in a specific and checkable way. Comparing the retrieved body
against CMS's own published schemas — the current
[V3.0.0 schema](https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/schemas/V3.0.0_Hospital_price_transparency_schema.json)
and the archived V2.0.0 schema at the commit before CMS moved the v2 documents to `archive/`
([`33833d4`](https://github.com/CMSgov/hospital-price-transparency/commit/33833d4c89), "Add v3
documents, move v2 documents to archive") — the envelope is v3, not v2:

| Envelope element | Required by V2.0.0 | Required by V3.0.0 | In this file |
|---|---|---|---|
| `hospital_location` | yes | — | **absent** (0 occurrences) |
| `affirmation` / `confirm_affirmation` | yes | — | **absent** (0 occurrences) |
| `location_name` | — | yes | present |
| `type_2_npi` | — | yes | present |
| `attestation` / `confirm_attestation` / `attester_name` | — | yes | present |

The two schemas also fix the attestation text as a `const`, and the two constants differ. This
file's attestation string is byte-identical to the **V3.0.0** constant, all 927 characters of
it, and is therefore not valid against the V2.0.0 schema it claims to follow. Every element the
v3 schema requires at the top level is present and usable, which is why the inspector emits no
`CMS_V3_ENVELOPE_*_MISSING` finding against it.

The honest reading is that this publisher migrated its content to v3.0.0 and did not update the
`version` field. That does not make the finding go away — the file as retrieved still declares a
version its own contents contradict, and a consumer keying on the declared version still cannot
use it as v3 — but it does bound what the finding says. This is a mislabelled file, and this
document should not be read as evidence that the hospital failed to adopt the CY 2026 data
elements. On the evidence above, it adopted them.

## What cuts the other way

Neutrality requires stating what the file gets right, and it is a lot:

- The envelope is v3.0.0 in structure and content, as the section above establishes against
  CMS's own schemas; only the version string is not.
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
- [V2.0.0 JSON schema](https://github.com/CMSgov/hospital-price-transparency/blob/33833d4c89~1/documentation/JSON/schemas/V2.0.0_Hospital_price_transparency_schema.json),
  read at the commit before CMS moved the v2 documents to `archive/`, for the element-name and
  attestation-constant comparison above
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

**2026-08-16.** Amended, without changing the finding or the grade. The original text said the
warehouse refusal left the file with no contract evidence but did not say on the published page
*why*, and it framed the consequence for downstream consumers more broadly than the evidence
supports. Both are corrected above: the refusal is now published with its reason, and the
element-by-element comparison against CMS's V2.0.0 and V3.0.0 schemas is stated, because it
shows this is a stale label on migrated content rather than an unadopted rule. The observed
facts — declared `version` of `2.0.0`, one ERROR-severity `CMS_V3_VERSION_UNEXPECTED`, grade
**C** — are unchanged.

If any evidence above is wrong, the correction path is a GitHub issue on this repository; the
registry entry, the assessment row, and this document will be corrected and the correction
dated. Being corrected is preferable to being counted right.
