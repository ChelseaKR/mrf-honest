# How files are compared, and what a letter grade means here

`mrf-honest compare` turns one attested collection run of persisted assessments into a published
comparison: which files emitted which findings, and one deterministic presentation grade per file.
The underlying assessment artifact stays rank-free by design ([how-we-grade.md](how-we-grade.md),
[ADR 0004](adr/0004-separate-remote-scorecard-artifacts.md)); the grade defined here is a
documented presentation of that record, added by the comparison layer and versioned separately
([ADR 0005](adr/0005-presentation-grade-layer.md)).

Two boundaries hold everywhere:

- **Grades describe files, not organizations.** A hospital's file getting an `F` says the
  published document was not usable as retrieved that day. It does not rank the hospital, price
  its care, or determine compliance with 45 CFR part 180.
- **A grade is not a certificate.** An `A` means the implemented checks emitted no error or
  warning findings over the assessed scope. It is not exhaustive schema validation and not the
  official CMS validator.

## The comparison boundary

`build_comparison` refuses to produce output unless all of the following hold:

1. Every row shares one comparison scope: publisher type, assessment profile, URL provenance,
   assessment-policy fingerprint, retrieval-policy fingerprint, and UTC `as_of` date
   (`require_comparable`, unchanged from phase 3).
2. A manifest attests that every row came from **one operator-controlled collection run**. Phase 3
   recorded that matching fingerprints alone cannot establish this; the manifest is the explicit
   encoding of that context, and comparison without it is refused.
3. The snapshot carries exactly one row per subject (publisher, location, exact-URL digest).
4. Any lakehouse ingest evidence supplied must belong to a cohort file by content SHA-256; foreign
   or duplicated evidence is refused.

## The file grade, `file-grade-v1`

The complete rule table is hashed into a policy fingerprint that every output embeds. The rules,
in evaluation order:

| Situation | Grade | Why |
|---|---|---|
| The identified download attempt failed (HTTP error, network error, unusable content, unsafe redirect) | **F** | Fail closed: a file the public cannot retrieve is stated as such, with the dated reason, never dropped. |
| Retrievability `NOT_ASSESSED`: invalid pre-network URL, the project's decoded-size ceiling, local cache trouble | **NOT_GRADED** | A project limit or operator problem is not a publisher failure; conflating it with `F` would be a false accusation. The reason is always stated. |
| Verified body, but the charge array could not be streamed to completion | **F** | Content that could not be read is failed, not passed. |
| Complete scan; no `ERROR` findings; no `WARNING` findings | **A** | Tolerated `INFO` observations (a UTF-8 BOM, non-dollar rate representations) are listed but never lower a grade. |
| Complete scan; no `ERROR` findings; at least one `WARNING` | **B** | Warnings merit attention without being structural errors. |
| `ERROR` findings (or missing evidence) in exactly one of the four local dimensions | **C** | |
| In exactly two dimensions | **D** | |
| In three or more dimensions | **F** | |

A local dimension that is `NOT_ASSESSED` after a completed scan counts exactly like a dimension
with errors: absence of evidence is stated and graded against the file, never implied as a pass.

The four local dimensions are conformance, completeness, interpretability, and freshness, exactly
as defined with their finding catalog in [how-we-grade.md](how-we-grade.md). Every finding shown
next to a grade keeps its stable code, severity, occurrence count, and primary-source citations.

## What the comparison output contains

One JSON document per cohort, fully derived from persisted inputs:

- the cohort identity: `as_of`, comparison scope, inspection fingerprint, and the grade policy
  with its fingerprint and rule table;
- the collection attestation and discovery evidence summary from the manifest, including targets
  that were checked and recorded but not included, with the reason;
- a summary with honest denominators: targeted, network-attempted, verified-body, completed-scan,
  graded, and not-graded counts are all reported separately;
- one row per file: grade with its one-sentence reason, five dimension statuses and notes, every
  finding, coverage flags, content SHA-256, byte size, observation timestamp, and the outcome of
  the warehouse ingest attempt (below);
- a finding matrix: every emitted finding code and exactly which files emitted it;
- a statistics block, always present: the disposition of the cohort's probability stratum, each
  share carrying its own numerator, denominator and 95 percent Wilson interval, or one stated
  refusal in place of every share. See [ADR 0007](adr/0007-suppression-uncertainty-and-refusal.md)
  for the interval method, the suppression floor, and the six refusals.

The same render writes `dataset.csv`, `dataset.schema.json` (a Frictionless Table Schema) and a
static JSON API under `api/`, all derived from these same documents in the same run, so there is
no second pipeline to drift from the pages. Every dataset row carries its cohort, profile,
publisher type, URL provenance and all three policy fingerprints, because rows assessed under
different profiles must never be pooled, and a grade with no profile beside it invites exactly
that. `mrf_honest.dataset.missing_exports` runs on the deploy path and fails the publish when the
written exports stop agreeing with the cohorts they came from.

A refusal there is a published outcome, not an absence. A cohort with no sampling frame, a cohort
whose only stratum is a convenience sample, and a cohort that accounts for part of a draw (the CSV
cohort covers the 25 CSV-retrievable targets of a draw of 48; its sibling holds the rest) each
state why no share was computed rather than omitting the section.

A code absent from the matrix was not emitted by any graded file. For files whose scan completed,
that means the implemented check found nothing; it is not a claim that the data is valid.

The document carries a `comparison_version`, which is the schema of the document and not the
grade policy. It moves when the shape changes; the grade policy fingerprint moves only when a
grading rule changes, so a schema change never implies that anything was regraded.

## Warehouse evidence, and why a refusal is stated

Each row's `lakehouse` field is the recorded outcome of this project's contracted DuckDB +
Parquet ingest for that file, and it is never a grading input in either direction:

| `lakehouse` | Means |
|---|---|
| an object with `status: "success"` | the verified body was loaded; the run identity and contracted model counts are published with it |
| an object with `status: "refused"` | the warehouse declined the file, with `reason`, the scope it implements, and the scope the file presented |
| `null` | no ingest attempt was recorded for this file in this cohort |

The refused branch exists because the first published cohort proved the alternative is a false
implication. This project's warehouse implements CMS hospital JSON v3.0.0 only, so it refused
one file that declares template `2.0.0`. That refusal reached the published page as an absence
with no reason attached, which is precisely the conflation the `NOT_GRADED` row of the table
above forbids: a reader could not tell a limit of this project from an unnamed defect in a named
hospital's file. A project limit is stated with its reason, wherever it appears.

Evidence for a refusal is bound to the cohort exactly like evidence for a load: it must match a
cohort file by content SHA-256, only one document per file is accepted, and a refusal record
missing its reason or its scopes is refused rather than published half-stated.

## Comparing two cohorts over time (`mrf-honest diff`)

A cohort is a dated snapshot. `mrf-honest diff <before> <after>` relates two of them, subject by
subject, over the file slugs they share. The rule it exists to enforce is the one this document
states everywhere else: **a change this repository made to itself is never published as a change
a hospital made to its file.**

That is not one yes/no, because a cohort's identity carries three fingerprints that can move
independently. Each governs a different layer, and each layer is compared only when its own
fingerprint held:

| Layer | Compared when | Fields |
|---|---|---|
| `retrieval` | `retrieval_policy_fingerprint` matches | `content_sha256`, `size_bytes`, retrieval coverage |
| `document` | `inspection_fingerprint` matches | `template_version`, `last_updated_on` |
| `judgement` | `assessment_policy_fingerprint` **and** the grade's `policy_fingerprint` both match | the grade letter and the finding list |

A layer whose fingerprint moved is reported as `policy_changed`, carrying both fingerprints, and
its fields are not compared. It is never reported as an unchanged layer: "compared and equal" and
"not compared" are different statements, and only one of them is true.

Two cohorts of different profiles, publisher types or URL provenance are refused outright rather
than diffed under a policy heading. A JSON grade and a CSV grade are measurements of different
file formats whose finding catalogues do not share codes.

Three absences are stated rather than scored:

- **A subject in one cohort only** is reported as that, with no comparison. These cohorts are
  drawn samples ([SAMPLING-FRAME.md](SAMPLING-FRAME.md)); a facility drawn once has said nothing
  about whether its file changed, and "added" or "removed" would turn this project's sampling
  into a claim about a hospital.
- **A move between a letter and `NOT_GRADED`** is stated, and the regression is left undetermined.
  `NOT_GRADED` is not a position on the A-to-F scale — it records a limit of this tool — so
  scoring it as a worse grade would publish this project's failure as the publisher's.
- **A location graded at a different URL** closes every layer for that subject. Comparing the
  bytes of two different files as though one had become the other is the same conflation in
  smaller print.

`--fail-on-regression` turns the diff into a gate: `1` when a subject's letter got worse or a new
error-severity finding appeared, `0` when at least one subject's judgement layer was comparable
and none of those regressed, and `2` when **no** subject's judgement layer was comparable. The
third is the one that matters: returning `0` there would report a clean run over zero comparisons.

## The publication set, beside the files (`mrf-honest systems`)

A cohort grades files. CMS requires one machine-readable file per hospital location, and a system
publishes one `cms-hpt.txt` naming every location with the file that serves it — so some facts are
about a system's *publication set* and cannot be expressed by any file's letter, and must not be
absorbed into one. `mrf-honest systems` reports them separately, from committed evidence, opening
no socket and deriving no grade.

**The join.** A graded row is matched to a listed location by `sha256(mrf-url)` against the row's
`requested_url_sha256`. A row's published `requested_url` has its query string redacted while the
digest is of the raw URL, so a join on the string is not file identity: in the committed CSV
cohort two unrelated publishers use one vendor endpoint differing only in a query parameter, and
a string join reports each as listing the other's file.

**What is a fact about the publication set:**

| Row | Means |
|---|---|
| `locations_listed_without_an_mrf_url` | a location this system's `cms-hpt.txt` names, with no file behind it |
| `graded_without_a_listed_location` | a file this cohort graded that no retrieved `cms-hpt.txt` declares — a defect in the cohort's own discovery claim, not in the publisher |
| `location_name_agreement` | whether a graded file's own `location_name` array covers the locations the discovery file pointed at it |
| `disagreements` where `basis` is `must_agree_within_a_system` | the system's files disagree about `version` or `last_updated_on` |

**What is not.** `locations_not_assessed_in_this_cohort` counts the locations a system lists that
this cohort did not grade. A cohort draws a sample ([SAMPLING-FRAME.md](SAMPLING-FRAME.md)), so
that number is this project's scope and never a publisher's defect; it is published in its own
field, with a sentence saying so, and it is never added to any defect count. A parsed block
carrying neither a location name nor an `mrf-url` is not counted as a location at all — one
system's file opens with an ASCII-art banner, and counting the parser's record of it would invent
four locations that system never claimed to list.

**Elements.** Where a system's files differ on any CMS general data element, the difference is
published with every file named. Only `version` and `last_updated_on` are counted as a
disagreement: they describe the template a file was written to and the date it was last updated.
`hospital_name`, `location_name`, `hospital_address` and `license_information` are defined per
location by the dictionary, so a system's files differing on them is those files doing their job.

**When there is no evidence.** If no discovery record on or before the cohort's date declares any
URL it graded, the reconciliation is refused with the reason and every count that would read as a
finding is absent rather than zero. "No listed location declares this file" is a claim nobody
holding no discovery file is in a position to make.

## What this comparison refuses to do

- It never averages, ranks, or scores across hospitals; the only ordering anywhere is
  alphabetical.
- It never compares prices. Dollar, percentage, and algorithm representations stay structurally
  separated in the lakehouse, and no rate comparison is published without the phase-4 suppression
  and uncertainty work ([IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md)).
- It never mixes cohorts with different policies, provenance, or dates, and never joins a current
  failed retrieval to an older cached inspection.

## Targets that were reviewed and not graded

A cohort with a stated sampling frame ([SAMPLING-FRAME.md](SAMPLING-FRAME.md)) draws its subjects
before it knows anything about them, so most of what it draws will not end up with a letter beside
it. Those targets are published anyway, in the manifest's `exclusions` and on the index page under
"Checked and recorded, not graded", each with the origin that was checked, the date, and a `basis`
that says how far the review got:

| `basis` | Means |
|---|---|
| `format_outside_profile` | the file the facility's `cms-hpt.txt` points at is not a CMS hospital JSON document (CSV, ZIP, or a vendor endpoint declaring another media type). This profile reads JSON v3 only, and grading a conforming CSV against a JSON profile would measure the wrong thing. |
| `txt_fetch_failed` | the `cms-hpt.txt` retrieval did not succeed at the origin resolved for this facility — an HTTP error, or a `robots.txt` this tool could not read, which RFC 9309 § 2.3.1.4 makes a complete disallow. |
| `txt_not_found_at_origin` | the origin answered but served no TXT. The conventional TXT belongs at the hospital's *selected* MRF-hosting origin, which may be elsewhere. |
| `txt_published_without_mrf_url` | a TXT was served and parsed, but the location entry for this facility declares no `mrf-url`, so the conventional path yields nothing to retrieve. |
| `discovery_reviewed` | reviewed at discovery time for some other stated reason, given in full on the entry. |

Two rules keep this list from becoming a place to hide results. **An exclusion is never a
grade**: none of these bases says anything about the hospital, and `txt_fetch_failed` in
particular records one failed probe of one origin on one date. And **a retrieval failure is not
an exclusion**: where a facility publishes a JSON file and the request for it fails, the row stays
in the cohort and is graded `F` with the dated reason. Format is read from the publication, never
from a failed request, so the two can never be confused — which is what stops a 403 from being
quietly reclassified as "not our format".
