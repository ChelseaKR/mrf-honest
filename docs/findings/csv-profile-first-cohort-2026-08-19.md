# The CSV profile's first cohort: where 118,411 payer names have no charge beside them

*Findings from the first published CSV-profile cohort (`hospital-csv-v3-2026-08-19`), observed
2026-08-19. Each describes one published file on one date. Nothing here is a ranking of any
hospital, a statement about care, or a legal compliance determination.*

## What was done

On 2026-08-19, one serial, operator-invoked run of `mrf-honest scorecard --profile csv`
assessed every facility of the committed 2026-08-19 random draw whose `cms-hpt.txt` publication
the sibling JSON cohort had recorded as a CSV-retrievable `format_outside_profile` exclusion —
25 targets, each an identified, bounded, robots-checked GET, each verified by SHA-256 and
streamed through the CSV inspector (`src/mrf_honest/inspect_csv.py`), which implements CMS's
CSV v3.0 data dictionary for the Tall and Wide templates. The exact evidence is the committed
assessment registry,
[`data/cohorts/2026-08-19-csv.assessments.jsonl`](../../data/cohorts/2026-08-19-csv.assessments.jsonl),
and every number below re-derives from it (`tests/test_published_claims.py`).

Twenty of the 25 produced a verified body and all twenty streamed to completion — the largest,
877,150,757 bytes and 2,749,881 rows, without loading into memory. The other five are rows too:
two hosts' `robots.txt` said no and were honored (`NOT_GRADED`), two files exceeded this
project's 1 GiB decoded ceiling (`NOT_GRADED`; a project limit is never a publisher failure),
and one URL — published by the hospital's own `cms-hpt.txt` — answered HTTP 404 (**F**, with
the dated reason).

## Finding 1: the dictionary's first conditional requirement, violated at scale — mostly by superseded templates

CMS's CSV data dictionary, conditional requirement 1: *"if values are encoded in the 'Payer
Name' or 'Plan Name' columns in the CSV tall format, at least one of the three payer-specific
charge properties must also be encoded."* The inspector emits
`CMS_CSV_PAYER_WITHOUT_CHARGE` once per offending row.

Across six files, 118,411 rows name a payer or plan with no dollar, percentage, or algorithm
charge beside it. The distribution of that count is the finding:

| File | Declared `version` | Offending rows | Of total data rows |
|---|---|---:|---:|
| frederickhealth/frederick-health-hospital | `2.0.0` | 36,135 | 36,135 (every row) |
| mindenmedicalcenter/minden-medical-center | `2.0.0` | 81,961 | 2,749,881 |
| slhn/st-luke-s-hospital-monroe-campus | `3.0.0` | 109 | 1,127,130 |
| upmc/upmc-mercy | `3.0.0` | 105 | 941,910 |
| slhn/st-luke-s-warren-hospital | `3.0.0` | 90 | 1,508,671 |
| marshallmedical/marshall-medical-center | `3.0.0` | 11 | 338,049 |

118,096 of the 118,411 instances — 99.7% — sit in the two files that still declare the
superseded v2.0.0 template. In Frederick Health's file it is every single data row, which is
what a file organized under an older template's semantics looks like when read against the
template CMS has required since 2026-01-01 and enforced since 2026-04-01. The four
current-template files carry only a few-hundred-row residual each. Both facts matter and they
are different facts: the first is a template-migration failure wearing a completeness finding,
the second is the ordinary residual defect rate of files that did migrate.

## Finding 2: the superseded-template class, now measured in CSV

The [2026-08-14 finding](superseded-template-version-2026-08-14.md) documented an 884 MB JSON
file declaring template 2.0.0 seven months after v3.0.0's effective date. The CSV profile's
first cohort found the same class on its first pass: **Frederick Health Hospital** and **Minden
Medical Center** both declare `version` 2.0.0, and their missing v3.0.0 general elements
(attestation statement, attester name, location name, type-2 NPI) and missing allowed-amount
columns (median, 10th percentile, 90th percentile, count) are exactly the elements the CY 2026
OPPS/ASC final rule added. A third file, **Kaiser Foundation Hospital San Rafael**, declares
`3.0.1` — a version string CMS has never published; the file otherwise carries the v3.0.0 Wide
shape, so the declaration appears to be a vendor's own invention.

Every version finding is one `ERROR`-severity `CMS_CSV_VERSION_UNEXPECTED` per file, with the
declared value quoted; grading a 2.0.0-declared file against the v3 profile is stated in the
cohort manifest rather than silently assumed, exactly as the JSON cohort did for Cedars-Sinai.

## Finding 3: one hospital's own TXT points at a 404

**Williamson Medical Center's** `cms-hpt.txt` names an MRF URL that answered HTTP 404 to an
identified client on 2026-08-19. That is the cohort's one **F**: not because the file was bad,
but because the URL the hospital itself publishes for automated discovery does not serve a
file. The dated evidence — attempt time, status, final URL — is in the assessment row, and the
grade sentence names it.

## Smaller observations, recorded rather than implied

- **4,785 methodology values outside the accepted set in one file** (Minden, `2.0.0`):
  `CMS_CSV_METHODOLOGY_INVALID`, one more count of what non-migration looks like at row level.
- **3 of the 20 verified bodies are not valid UTF-8** and were read as Latin-1, recorded as a
  tolerated `INFO` observation — the dictionary requires plaintext CSV and never names an
  encoding, so an encoding is not a deficiency this project may invent.
- **8 of the 25 begin with a UTF-8 byte-order mark**, tolerated and recorded as `INFO`,
  mirroring the JSON cohort's 5 of 17.
- **Header order is not template order in the wild.** The two St. Luke's files and the SGMC
  file order their general elements differently from the template while carrying the required
  set; the inspector matches headers by name, because the dictionary's tolerance notes say
  spaces and case must not generate deficiencies and nothing in it fixes column positions.

## Why the two cohorts are published side by side and never pooled

Each profile grades under its own fingerprinted inspection and assessment policies. A letter
distribution computed across both would compare findings produced by different rule sets, which
is the conflation `docs/how-we-compare.md` exists to refuse. The site renders one clearly
scoped section per cohort, and a test walks the seam between them so no drawn facility can
vanish: every CSV-retrievable exclusion of the JSON cohort is a declared target of the CSV
cohort, every declared target is a published row, nothing is graded twice, and the 7 ZIP
publications remain recorded exclusions.
