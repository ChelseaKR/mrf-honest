# 0007. Suppression, uncertainty, and the refusal that guards them

## Status

Accepted - 2026-08-27

## Context

Phase 4 of [the implementation plan](../IMPLEMENTATION-PLAN.md) is the project's thesis, and three
of its five commitments were unbuilt: small-cell suppression with a stated threshold, uncertainty
intervals on every published comparison, and a refusal to publish a comparison that cannot carry
its own uncertainty. The consequence is stated in the README: **no price comparison is published
anywhere.** It is also why the site publishes counts and no rates.

The rule the code has to enforce was already written down, in prose, in
[SAMPLING-FRAME.md](../SAMPLING-FRAME.md):

> Where a proportion is meant to describe hospitals in general, it is computed over stratum B
> alone and says so.

and

> Its two strata must not be pooled.

Prose is not a gate. A caller who computed a proportion over the whole cohort would be
contradicting the frame document with nothing to stop them, and the resulting number would look
exactly like an honest one.

The committed cohort manifest already records the strata mechanically, so the gate has real inputs
rather than an inferred stratum assignment: `collection.sampling_frame.stratum_a_carry_forward` is
a list of six carry-forward subjects, and `collection.sampling_frame.stratum_b_random_draw` records
`sample_size` 48 drawn from `eligible_count` 3,024.

## Decision

`src/mrf_honest/statistics.py` is the only place a proportion is produced, and it produces either
a `Proportion` or a `Refusal`. There is no third outcome and no entry point that returns a bare
`float`.

### The interval is Wilson score, at 95 percent

The normal approximation is degenerate at 0 and 1, which is exactly where this project's
proportions land most often: 0 of 48, or 48 of 48, would publish a zero-width interval and read as
certainty. Wilson does not collapse there.

Two consequences are recorded because they were surprises:

1. **A finite-population correction is applied when, and only when, the frame records a universe
   larger than the sample.** The draw is 48 without replacement from 3,024, so the variance is
   smaller than an unbounded population's by (N - n) / (N - 1). The correction is implemented as
   an effective sample size of n (N - 1) / (N - n), which is 48.758 for the committed draw. Where
   the frame records no universe, or the sample is the whole frame, the correction is not applied
   and the output says so in `finite_population_correction`.

2. **The interval is clamped to contain its own point estimate.** Wilson guarantees that
   algebraically; binary floating point does not. A property test found the case immediately: at
   numerator == denominator the upper bound evaluates to 0.9999999999999999 against an observed
   1.0, so the published interval would have excluded the value it was computed from. The clamp
   moves a bound by at most one unit in the last place, and the case is pinned by a named
   regression test.

### The suppression floor is a denominator of 20

Stated, and stated honestly about what it is for. This project publishes named rows for named
facilities, so a floor here is **not** disclosure control; every subject of a published grade is
already named on its own page. The floor exists because below it a proportion is not informative
enough to publish, and publishing it anyway would invite a reader to compare two numbers whose
intervals overlap almost entirely.

The number was chosen against the measured width of a 95 percent Wilson interval at the least
favourable point, p = 0.5:

| n | interval width at p = 0.5 |
|---:|---:|
| 10 | 0.527 |
| 15 | 0.452 |
| 19 | 0.410 |
| **20** | **0.401** |
| 21 | 0.393 |
| 25 | 0.365 |
| 48 | 0.272 |

Twenty is the floor at which the interval still spans four tenths of the scale. It is not a
comfortable number and this ADR does not present it as one. It was also chosen so that it does not
retroactively suppress a cohort this project has already published: the committed draw is 48, and
the CSV cohort's declared target list is 25. A floor of 30 would have suppressed the second, and
choosing a floor that happens to clear the data already in hand is the kind of decision that has to
be written down rather than discovered later in a diff.

### Five refusals, each a published outcome

| Code | Fires when |
|---|---|
| `no_sampling_frame` | the manifest records no frame, so there is no population to describe |
| `convenience_stratum` | the stratum is a convenience sample; widening it would not help, so it is refused before its denominator is examined |
| `pooled_strata` | more than one stratum was offered for one proportion, whatever their kinds |
| `empty_denominator` | the denominator is zero |
| `below_suppression_threshold` | the denominator is under the floor above |

A refusal carries its code, its stated reason, the stratum it concerned and the denominator it
saw. It is a thing to render, not a `None` to skip.

`estimate_over` refuses a set of strata of any size other than one, including a set of two
probability strata. It would be easy to have it select the probability stratum from a mixed set,
and that would quietly reintroduce exactly the pooling the frame document forbids, in the one
place a reader would never look for it.

## Consequences

- Nothing is published by this decision. It builds the arithmetic; phase 7 of
  [the expansion plan](../EXPANSION-PLAN.md) puts the results in the comparison document and on
  the site, under the same fail-closed deploy check the rest of the site already has.
- A caller cannot render a point estimate without the denominator and interval that qualify it,
  because they arrive in the same frozen object.
- The threshold, the confidence level, and the interval method are constants with a policy
  version (`population-statistics-v1`). Moving any of them moves the version, and a published
  figure can therefore say which policy produced it.
- The refusals are the load-bearing part. Each one is asserted by a test that turns red when its
  branch is neutered; a guard that cannot fail would be worse here than no guard, because it
  would carry the frame document's authority while enforcing nothing.

## Revisit if

- A cohort is drawn with a design that is not simple random sampling without replacement. Wilson
  over an effective sample size is right for the current design and would understate uncertainty
  under clustering or unequal probabilities; a weighted design needs its own ADR before any figure
  is published from it.
- A published proportion is ever wanted at a denominator below 20. That is a decision to lower the
  floor, with its own measured justification, not a case to special-case.
