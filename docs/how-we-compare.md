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
- a finding matrix: every emitted finding code and exactly which files emitted it.

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
