# 0010. Scheduled re-collection ships switched off, and its service declaration is still open

## Status

Accepted for the code, 2026-10-17. The service/job tier declaration it depends on is **not**
decided and is recorded below as an open question for the maintainer.

## Context

`docs/EXPANSION-PLAN.md` phase 14 and `docs/ROADMAP.md` both say the same thing: the retrieval
politeness work that used to block a scheduled job shipped on 2026-08-15, and the one remaining
gate is "a real service/job tier declaration", meaning a statement of what this project promises
to keep running. That is a decision about commitments, not an implementation detail, and it was
left to the maintainer on purpose.

Two things are nevertheless true without that declaration:

- Every grade this project publishes is dated, and a dated grade is only as useful as the next
  dated grade that can be compared with it. `mrf-honest diff` already relates two collections.
  What was missing was a way to make the second collection without a person typing 40 commands.
- The cost of a re-collection is measured, not guessed: the 2026-09-12 re-collection of 42
  subjects moved 2,984,626,191 wire bytes across other organizations' servers to learn that four
  files had changed
  ([finding](../findings/what-a-re-collection-actually-cost-2026-09-12.md)). A scheduled job that
  is on by default would impose that load on third parties before anyone had decided to.

## Decision

Build the mechanism, and ship it switched off.

1. **`mrf-honest recollect`** re-runs discovery (`https://<domain>/cms-hpt.txt`) and assessment for
   a committed watchlist (`data/recollections/watchlist.json`). It adds no retrieval or grading
   rule: every file goes through `assess_hospital_url`, so robots.txt (no override), the per-host
   interval, `Retry-After`, conditional revalidation and the size ceiling are the ones the cohorts
   were collected under, and one `Politeness` object spans the run.
2. **A failure is "no evidence".** A publisher whose `cms-hpt.txt` could not be read, a file that
   could not be retrieved, and a URL whose format the run will not guess are recorded with the
   reason and with no letter. The result document's per-file `grade` is `null` unless a verified
   body was read.
3. **Dated, idempotent, versioned.** `<date>.recollection.json` (schema version 1) is written last
   and atomically and is the completion marker. A second run on the same UTC date fetches nothing.
   An interrupted run resumes without refetching. A different watchlist on a completed date is
   refused.
4. **Bounded by default.** At most 5 distinct files per publisher per run; the rest are listed as
   not attempted, with the reason.
5. **`mrf-honest alert`** turns `mrf-honest diff` of two collections into Markdown, Atom or JSON.
   It reports a regression only when a verified file was read on both dates. A file that could not
   be retrieved is "no evidence", never a regression, even where the cohort grade policy gives it
   an `F`.
6. **`.github/workflows/recollect.yml`** runs the above monthly, but the schedule fires only when
   the repository variable `MRF_RECOLLECT_ENABLED` is `"true"`. A manual dispatch always runs.
   Merging the workflow starts no retrieval. Writing results back to the repository (a pull
   request, never a push to `master`) is a second, separate switch, `MRF_RECOLLECT_OPEN_PR`.
7. **Nothing in this path can change a grade.** Re-collection and alerts read grades and never set
   them; the grading modules do not import either new module.

## Open question for the maintainer

What does this project promise to keep running? The code above makes the following choices
explicit instead of implicit, and none of them is decided here:

- **Cadence.** Monthly is the shipped default. The alternatives are weekly (which also keeps a
  GitHub Actions cache warm, so conditional requests can answer `304`) or on demand only.
- **Revalidation cache.** Hosted runners start empty, so a monthly run is a cold pass and moves
  full bodies. A persistent cache (a self-hosted runner, or object storage) would make
  conditional revalidation effective, at the cost of one more thing to operate.
- **Size of the watchlist and the per-publisher cap.** The bandwidth cost scales with both. The
  measured figures above are the basis for choosing.
- **Availability statement.** Whether a missed or failed run is a defect to be fixed by a date, or
  best effort with the gap published. The run results already make a gap visible, because a date
  with no `recollection.json` is a date with no collection.
- **Where results live.** Pull requests into `data/recollections/` (as built), a separate
  branch, or object storage.

Until the maintainer answers, the variables stay unset and the workflow does nothing on its own.

## Consequences

- Turning it on is two repository variables (`MRF_RECOLLECT_ENABLED`, `MRF_RECOLLECT_CONTACT`) and
  turning it off is deleting one. No commit is needed either way.
- `operator_controlled_single_run` in a re-collection manifest is `true` because the run is one
  process under one committed workflow that a maintainer switched on, not because a person typed
  each command. A reader who holds the stricter meaning of "operator-controlled" can see from the
  manifest's `population.kind` (`committed_watchlist`) that these rows are a different kind of
  collection from a cohort, and the comparison records no sampling frame, so its statistics block
  refuses rather than estimating a share of a population nobody drew.
- Re-collection output lives in `data/recollections/`, outside `data/cohorts/`, so the site
  continues to render only cohorts. A watchlist is not a sample, and mixing the two would change
  what the site's interval estimates estimate.
- Pull requests created with the workflow token do not trigger other workflows. The repository
  setting that lets Actions open pull requests must be enabled for the optional step to work.
- Subjects are identified as `publisher/location-slug`, with the slug taken from the first
  location name that lists a URL. It will not match a cohort's hand-assigned slugs, so the first
  re-collection has nothing to be compared with, which is stated rather than approximated.
