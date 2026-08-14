# 0005. A versioned presentation grade in the comparison layer, not the assessment

## Status

Accepted - 2026-08-14

## Context

The persisted `FileAssessment` deliberately produces five independent dimension statuses and no
composite score, rank, letter grade, or compliance label (ADR 0004, `docs/how-we-grade.md`). That
remains correct for the durable evidence artifact: a composite baked into the record would freeze
one weighting forever and invite readers to treat it as a legal ruling.

Publishing a cohort creates a different problem. A public index that lists five statuses and a
finding table per file is accurate but unreadable; the proven sibling scorecards (`gtfs-scorecard`,
`fhir-scorecard`) demonstrated that one deterministic letter with the sentence that justifies it is
what makes findings actionable for a non-engineer. Phase 3 also recorded a prerequisite: matching
default-policy rows are comparable only when the caller knows they came from one controlled
collection run, and that context had no encoding yet.

There is a written non-goal to protect: this project grades files, never organizations or care
(`docs/IMPLEMENTATION-PLAN.md`, "Deliberately not doing"; `docs/RESPONSIBLE-TECH-AUDITS.md` §A).

## Decision

1. Keep the assessment artifact rank-free. Nothing in `mrf_honest.scorecard` changes.
2. Add `mrf_honest.cohort`, a comparison layer that derives one **presentation grade per file**
   from the persisted record under a separate, versioned policy (`file-grade-v1`). The complete
   rule table is hashed into `GRADE_POLICY_FINGERPRINT` and embedded in every output, so a rule
   change creates a new fingerprint instead of silently regrading old cohorts.
3. The grade is fail-closed and never conflates local limits with publisher failures:
   - a download attempt that failed is a stated `F` with the dated reason, never a missing row;
   - retrievability `NOT_ASSESSED` (invalid input, the project size ceiling, local cache trouble)
     is `NOT_GRADED` with the reason, because an `F` there would attribute a project limit to the
     publisher;
   - an incomplete charge-array stream is an `F`; content that could not be read is failed, not
     passed;
   - a local dimension without evidence counts exactly like a dimension with structural errors;
   - `INFO` findings never lower a grade: the catalog defines them as tolerated observations.
4. Grades come only from `build_comparison`, which refuses to run without a manifest attesting
   that every row came from one operator-controlled collection run, refuses mixed comparison
   scopes via the existing `require_comparable`, refuses duplicate subjects, and refuses ingest
   evidence that does not belong to the cohort. This satisfies the phase-3 prerequisite by making
   the controlled-run context an explicit, recorded input.
5. The grade describes one file under one stated policy. It is not an organization rating, not a
   ranking across hospitals, and not a CMS compliance determination; published copy must carry
   that boundary wherever a grade appears.

## Consequences

- The public site can show letter grades without the durable evidence artifact ever containing
  one; regenerating grades under a future policy needs no re-collection.
- `NOT_GRADED` rows keep unretrieved or size-capped targets visible in every denominator without
  accusing the publisher, at the cost of a slightly more complex index page.
- The grade bands are deliberately coarse (counts of dimensions with errors, warnings splitting A
  from B). Anything finer would imply precision the five-dimension evidence does not carry.
- A change to the local finding catalog changes inspection fingerprints and therefore comparison
  scopes; cohorts graded under older semantics stay readable but are never silently merged.

## Sources

- [45 CFR § 180.50](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50)
- `docs/PHASE-3-FINDINGS.md` (controlled-run prerequisite)
- `docs/how-we-grade.md` (dimension and severity semantics)
