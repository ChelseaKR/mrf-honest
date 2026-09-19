# Both CSV cohorts re-assessed under inspection policy v2, 2026-09-18

*A dated note on a re-assessment this project made of its own published grades, and why. It
describes a change in this project's grading policy, not a change in any hospital's file. Nothing
here is a ranking of any hospital, a statement about care, or a legal compliance determination.*

## Why

Issue #28 (PR #42) fixed a gap in the CSV inspector. A data row that disclosed a standard charge
but paired no code reported `CMS_CSV_CODE_PAIRING_MISSING` and nothing else, because
`CMS_CSV_DESCRIPTION_MISSING` and `CMS_CSV_SETTING_INVALID` were checked only for rows with a
complete code pairing. The data dictionary marks `description` and `setting` "Blanks Accepted:
No" without conditioning either on the code columns, so a charged row with no code still owes
both.

That changes what the inspector reports, so `CSV_INSPECTION_POLICY_VERSION` moved from
`cms-hospital-csv-v3-inspection-v1` to `cms-hospital-csv-v3-inspection-v2`. The version is hashed
into the assessment policy fingerprint, and the comparison refuses rows graded under any other
fingerprint. Every committed CSV cohort therefore had to be re-assessed before it could be
published again:

| | before | after |
|---|---|---|
| CSV inspection policy | `cms-hospital-csv-v3-inspection-v1` | `cms-hospital-csv-v3-inspection-v2` |
| inspection fingerprint | `58c6761c26254ee1…` | `77c60cefd43f095d…` |
| assessment policy fingerprint | `55ed46872e7fc9dd…` | `28d7b4380d786c9d…` |

Both CSV cohorts were re-assessed: `hospital-csv-v3-2026-08-19` and `hospital-csv-v3-2026-09-12`.
The second is the one the site renders, and a cohort left on the old fingerprint could no longer
be re-derived by the publish job. Every row and comparison in both now carries the new
fingerprint. The JSON cohorts use a different profile and fingerprint and were not touched.

## How

`tools/reassess_cohort.py`, run on 2026-09-18 from the verified local cache, with no network
access:

- **Retrieval is carried over, never re-collected.** Each row's retrieval evidence is rebuilt from
  the committed record and must re-serialize to exactly the committed evidence before anything
  else runs. The retrieval date, the URL, the HTTP outcome and the retrieval policy fingerprint
  are unchanged in every row.
- **The same bytes are read again.** 40 bodies were inspected (20 per cohort; 3.81 GB and
  3.89 GB). Each was found in the cache by its recorded SHA-256 and re-hashed before inspection.
  None was missing and none was downloaded.
- **Rows with no body stay that way.** The 10 rows that were never inspected (4 robots.txt
  disallows, 4 over the size ceiling, 2 HTTP errors) were rebuilt from the same evidence with no
  inspection. They are `NOT_GRADED` with their stated reasons, as before. No absence became a
  zero or a letter.
- **Null control.** Before the policy changed, the tool ran under v1 over 11 rows of the
  2026-08-19 cohort (every uninspected row and every inspected body under 40 MB). Its output was
  byte-identical to the committed registry, which shows the rebuild is faithful.
  `tests/test_reassess_cohort.py` repeats that control on every uninspected committed row, and
  refuses a missing body, a body that no longer matches its hash, and evidence that would
  serialize differently.

## What moved

**No hospital's grade or findings moved in either cohort: 0 of 25 in each.**

The fix can only add findings to a row that already reports `CMS_CSV_CODE_PAIRING_MISSING`,
because that finding fires on the same condition: a charge, no complete code pairing, and not a
modifier row. None of the 40 inspected files reported it under v1, so v2 had nothing to add.
Field by field, every re-assessed row differs from the committed one only in the four fields
derived from the policy: `assessment_policy_fingerprint` (also inside `comparison_scope`),
`inspection_fingerprint`, `assessment_id` and `assessment_body_sha256`. The comparisons differ
only in those identifiers, the cohort's two fingerprints and `generated_at`.

| Cohort | Hospitals whose grade moved | Hospitals whose findings moved | Distribution (unchanged) |
|---|---:|---:|---|
| `hospital-csv-v3-2026-08-19` | 0 of 25 | 0 of 25 | 11 A, 2 B, 4 C, 3 D, 0 F, 5 not graded |
| `hospital-csv-v3-2026-09-12` | 0 of 25 | 0 of 25 | 12 A, 2 B, 4 C, 2 D, 0 F, 5 not graded |

Per hospital, generated from the committed comparisons before (`master` at `5a03727`) and after
this re-assessment. Names are as each comparison publishes them:

| Hospital | 2026-08-19: before → after | 2026-09-12: before → after | Finding codes that moved |
|---|---|---|---|
| Bay Area Hospital (Coos Bay, OR) | A → A | A → A | 0 |
| Bayshore Medical Center (Holmdel, NJ) | not graded → not graded | not graded → not graded | 0 |
| Centura Health-St Anthony Hospital (Lakewood, CO) | not graded → not graded | not graded → not graded | 0 |
| Flowers Hospital (Dothan, AL) | not graded → not graded | not graded → not graded | 0 |
| Frederick Health Hospital (Frederick, MD) | D → D | D → D | 0 |
| Guadalupe Regional Medical Center (Seguin, TX) | A → A | A → A | 0 |
| Hillcrest Hospital Henryetta (Henryetta, OK) | A → A | A → A | 0 |
| Insight Hospital And Medical Center Coldwater (Coldwater, MI) | A → A | A → A | 0 |
| Kaiser Foundation Hospital (San Rafael, CA) | D → D | D → D | 0 |
| Lafayette Surgical Specialty Hospital (Lafayette, LA) | A → A | A → A | 0 |
| Marshall Medical Center (Placerville, CA) | C → C | C → C | 0 |
| Medical Center Of The Rockies (Loveland, CO) | A → A | A → A | 0 |
| Minden Medical Center (Minden, LA) | D → D | A → A | 0 |
| Princeton Community Hospital Assn Inc (Princeton, WV) | A → A | A → A | 0 |
| Scripps Memorial Hospital La Jolla (La Jolla, CA) | A → A | A → A | 0 |
| Sgmc Berrien Campus (Nashville, GA) | A → A | A → A | 0 |
| St Luke'S Hospital - Monroe Campus (Stroudsburg, PA) | C → C | C → C | 0 |
| St Luke'S Warren Hospital (Phillipsburg, NJ) | C → C | C → C | 0 |
| Taylor Regional Hospital (Campbellsville, KY) | A → A | A → A | 0 |
| Texas Health Presbyterian Hospital Flower Mound (Flower Mound, TX) | not graded → not graded | not graded → not graded | 0 |
| Umd Rehabilitation &  Orthopaedic Institute (Baltimore, MD) | B → B | B → B | 0 |
| United Memorial Medical Center (Batavia, NY) | A → A | A → A | 0 |
| Upmc Mercy (Pittsburgh, PA) | C → C | C → C | 0 |
| Whitfield Medical Surgical Hospital (Whitfield, MS) | B → B | B → B | 0 |
| Williamson Medical Center (Franklin, TN) | not graded → not graded | not graded → not graded | 0 |

Minden's D → A is the change between the two collection dates. The 2026-09-12 re-collection
recorded it ([what-moved-between-two-collections-2026-09-12.md](what-moved-between-two-collections-2026-09-12.md)),
and this re-assessment did not cause it.

## What this does not claim

The cohorts' retrieval dates are still 2026-08-19 and 2026-09-12. Nothing was re-collected, so
neither cohort says anything about these files as they stand on 2026-09-18.
