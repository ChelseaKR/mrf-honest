# Expansion plan, phases 6 through 14

[IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) carries phases 0 through 5 and stops there. Phases
0 through 3 are built. Phase 4 has one of its five boxes ticked; phase 5 has one of its seven,
although its static site is built and shipping. The unticked ones are the reason the README's
"Still open" list reads the way it does. This
document continues that plan across a two to three year arc, in the same form: a goal, a checklist,
what it depends on, and what would tell you it is done.

Nothing here changes the phase 0 through 5 numbering or relitigates a decision already recorded in
`docs/adr/`. Where a phase would reverse an ADR, it says which one and what evidence would justify
it.

## The rule this plan is subordinate to

The house rules in [CONTRIBUTING.md](../CONTRIBUTING.md) outrank every item below:

1. Standard library only on the graded path (ADR 0002); DuckDB begins at the optional lakehouse
   boundary (ADR 0003).
2. No model anywhere on the graded or comparison path. The narration layer of ADR 0006 reads a
   finished grade and cannot change it, and nothing in this plan moves that boundary.
3. Every number that appears in a doc, the README, or a rendered page is measured, never
   estimated.
4. Grades describe files, not organizations. No phase below ranks a hospital or a payer, prices
   care, or determines compliance.
5. Fail closed. A phase that cannot evaluate something states that, and never lets it read as a
   pass.

An item that would break one of these is not on this plan however useful it would be. The
"Deliberately not doing" list in the implementation plan still holds and is not repeated here.

## Where the arc is going, in one paragraph

The project can grade a file and publish the grade. It cannot yet say anything about a
*population* of files, because saying so honestly needs suppression and uncertainty, and neither
exists (phase 4). Once it can, the results need to leave the HTML: a dataset, a schema, an API, and
a way for a subject to dispute a row (phase 5). After that the two structural limits are the
warehouse understanding only one of the two profiles that are already graded, and the durability
claims the ROADMAP already declines to make. The last frontier is the second publisher class the
project was named for and has never touched: payers.

---

## Phase 6: suppression and uncertainty

*Goal: the arithmetic that phase 4 says has to exist before any proportion is published. Nothing
is published in this phase; it builds the thing that would make publishing honest.*

Phase 4's unticked boxes are the whole of this phase:

- [x] Small-cell suppression before anything is displayed, with the threshold stated
- [x] Uncertainty intervals on every published comparison
- [x] Refuse to publish a comparison that cannot carry its own uncertainty

The inputs already exist and are committed. `data/cohorts/2026-08-19.json` records a sampling frame
with two strata: `stratum_a_carry_forward` (six subjects, a convenience sample) and
`stratum_b_random_draw` (a seeded uniform draw of 48 from 3,024 eligible facilities).
[SAMPLING-FRAME.md](SAMPLING-FRAME.md) already states the rule this phase has to enforce in code:
"Where a proportion is meant to describe hospitals in general, it is computed over stratum B alone
and says so," and "Its two strata must not be pooled." Today that rule is prose, and prose is not a
gate.

What this phase builds:

- A statistics module on the graded path, standard library only, that computes a proportion only
  when it is given a probability sample, attaches a Wilson score interval with a finite-population
  correction, and carries the denominator and the stratum identifier in the same object as the
  number.
- A stated small-cell threshold, applied before a proportion is representable at all, so a
  suppressed cell cannot be rendered by a caller that forgot to check.
- A refusal: a convenience stratum, a pooled stratum set, an absent frame, or a denominator of
  zero produces a refusal carrying its reason, never a number and never a silent zero.
- An ADR recording the interval choice, the threshold and the refusal rule, because all three are
  expensive to reverse once a figure is published under them.

**Depends on:** nothing. The frame, the strata and the committed cohorts are already there.

**Done when:** the module exists with property-based tests over generated inputs, the threshold and
the interval method are recorded in an ADR, and a test proves each refusal path fires. A test that
passes a convenience stratum and gets a number back is the failure this phase exists to prevent, so
that test must exist and must fail against the pre-change tree.

**Stop condition:** if the committed frames turn out not to identify their strata mechanically,
stop and fix the frame record first. A statistic derived from a stratum assignment that a human
inferred at render time is exactly the fragility this project criticises elsewhere.

---

## Phase 7: the published statistic

*Goal: put phase 6's numbers where a reader sees them, under the same fail-closed discipline the
rest of the site already has.*

- [x] The comparison document carries a statistics block: every proportion with its interval, its
      denominator, its stratum, and its suppression state
- [x] The site renders it, and renders a refusal as a refusal rather than omitting the section
- [x] The publish workflow's byte-for-byte re-derivation covers the new block, so a stale statistic
      cannot deploy
- [x] `comparison_version` moves, and the change is recorded

**Depends on:** phase 6.

**Done when:** a rendered page states a proportion with its interval and its denominator in one
sentence, a cohort with no probability stratum renders the stated refusal instead, and
`tests/test_published_claims.py` re-derives the published figures rather than trusting them.

---

## Phase 8: machine-readable publication

*Goal: phase 5's first three bullets. The grades leave the HTML.*

- [ ] `dataset.csv` with a Frictionless Table Schema description
- [ ] A static JSON API under the site, generated from the same comparison documents
- [ ] Both regenerated by the render, never hand-maintained, and both checked on the deploy path

**Depends on:** phase 7, so the exported dataset carries the statistics rather than needing a
second export later.

**Done when:** the dataset round-trips, the schema validates it, the JSON API is byte-identical to
what the render produces from committed data, and the deploy path fails when they disagree.

---

## Phase 9: a read-only MCP server over the published dataset

*Goal: phase 5's MCP bullet, with the `grading_method` tool that returns the documented limits.*

- [ ] A read-only server over the published dataset, with no write path and no network at answer
      time
- [ ] A `grading_method` tool returning the documented limits, sourced from the same policy the
      grades were produced under rather than from a hand-written summary
- [ ] A refusal for any question the dataset cannot answer, including every comparison the
      comparison layer itself refuses

**Depends on:** phase 8. There is nothing to serve until the dataset exists.

**Done when:** the server answers from committed data only, refuses out-of-scope questions with
the reason, and a test proves a question that crosses a scope boundary is refused rather than
answered approximately.

---

## Phase 10: correction flow, and the record of what this project got wrong

*Goal: phase 5's last two bullets.*

- [ ] A claim and correction flow, non-adversarial, honouring a removal request without demanding
      proof
- [ ] A write-up in the pattern of the findings documents, including a section on what this
      project got wrong

The second is not a retrospective to be written from memory. The material is already in the
repository and dated: a CSV dialect the spool reader guessed instead of declared, a memory ceiling
two exports exceeded, a first cohort with no sampling frame, ten first-pass candidate origins that
were wrong, two fabricated figures found in the ROADMAP's own ledger, and a letter distribution
that described hospitals that chose JSON while implying it described hospitals.

**Depends on:** nothing technically. It is placed here because a correction flow with no published
dataset to correct is a form with no subject.

**Done when:** the flow is documented and reachable from the site, a removal request has a stated
path that does not require the requester to prove anything, and the write-up cites the commit or
document for every mistake it describes.

---

## Phase 11: the warehouse learns the second profile

*Goal: close the gap the README states plainly. Two profiles are graded; the warehouse implements
one.*

- [ ] A CSV profile in the lakehouse, with the same contract enforcement the JSON profile has
- [ ] ZIP-container handling, so a `.zip` publication is read rather than recorded and excluded
- [ ] The CSV cohort's rows stop stating that no contract evidence exists

**Depends on:** nothing in this plan. It is placed after phase 10 because publication surface
reaches a reader and warehouse coverage does not.

**Done when:** a CSV cohort ingests under contract, the seven ZIP publications of the committed
draw are readable rather than excluded, and the model DAG documents the second profile's grains.

**Stop condition:** if a ZIP container turns out to require an unbounded read to classify, stop.
The bounded-memory property of ADR 0002 outranks the coverage gain.

---

## Phase 12: durability

*Goal: retire the three limits the ROADMAP's observability section explicitly declines to claim.*

- [ ] Safe concurrent-writer coordination
- [ ] Supported warehouse migrations across schema versions
- [ ] A full SIGKILL and fsync crash matrix

**Depends on:** phase 11, so migrations are exercised against more than one profile's models.

**Done when:** the ROADMAP's observability paragraph can drop the sentence naming these three as
open, and each is backed by a test that fails against the pre-change tree.

---

## Phase 13: the payer publisher class

*Goal: the second half of the thing the project is named for.*

Phase 0's unticked boxes belong here, because they were never a half-day of work once the hospital
side was chosen first:

- [ ] Pull CMS's payer schema and validator from the published guide
- [ ] Retrieve one payer index file and record its actual byte size
- [ ] Attempt a naive whole-file parse and record the failure
- [ ] A payer profile, a payer adapter, and payer grading that never compares across publisher
      types

**Depends on:** phases 6 through 8 for the statistical and publication surface, and on an operator
retrieval that this repository cannot perform for itself (see "What only a person can do").

**Done when:** a payer file grades under its own profile with its own fingerprinted policy, and
`require_comparable` refuses a hospital-versus-payer comparison as it already does today.

**Stop condition:** if payer index files turn out to be uniformly unretrievable under this
project's politeness rules, record that as the finding and stop. It is a publishable result.

---

## Phase 14: cadence, and the obligations only a person can discharge

*Goal: say out loud which parts of phases 4 and 5 are not engineering.*

- [ ] Dated releases
- [ ] A scheduled refresh
- [ ] The manual screen-reader pass

### What only a person can do

These are recorded here so that no future phase quietly reports them as done:

- **A dated release.** The release process ends in a signed tag. The signing key is the
  maintainer's, and no automation should ever hold it. Everything up to the tag can be built and
  tested; the tag cannot.
- **A scheduled refresh.** The ROADMAP states the precondition: "any future scheduled job must
  take a real service/job tier declaration before shipping." That declaration is an owner
  decision about what this project promises to keep running, not a file an agent should author.
- **The manual screen-reader pass.** The metrics ledger already carries it as **not performed**,
  the standing open obligation. Automated Lighthouse scores are not a substitute and the ledger
  says so.
- **New evidence collection.** `data/cache/`, the registry and the scorecards are gitignored.
  Grading a new cohort means retrieving files from publishers' servers under the politeness gate,
  which is an operator-invoked act, and the sampling frame's origin-resolution step is explicitly
  "the one manual step in the frame."

---

## Sequencing

| Order | Phase | Depends on | Done when |
|---|---|---|---|
| 1 | 6, suppression and uncertainty | nothing | refusals fire, ADR recorded, property tests green |
| 2 | 7, the published statistic | 6 | a page states a proportion with its interval and denominator |
| 3 | 8, machine-readable publication | 7 | dataset and API re-derive byte-for-byte on the deploy path |
| 4 | 9, MCP server | 8 | out-of-scope questions refused, not approximated |
| 5 | 10, correction flow and the record | nothing technically | every mistake in the write-up cites its commit |
| 6 | 11, second profile in the warehouse | nothing in this plan | CSV ingests under contract; ZIPs read |
| 7 | 12, durability | 11 | the ROADMAP drops the three open limits |
| 8 | 13, payers | 6 through 8, plus an operator retrieval | a payer file grades under its own policy |
| 9 | 14, cadence and human obligations | continuous | each item is either done or named as blocked, never implied |

Phases 6 through 10 are the critical path: they finish the two phases the implementation plan
already committed to. Phases 11 and 12 are structural debt the README already discloses. Phase 13
is new scope and should not start before the statistics are trustworthy, because a second publisher
class multiplied by an unsound comparison layer is two wrong answers instead of one.
