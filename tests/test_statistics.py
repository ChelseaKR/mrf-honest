"""The statistics layer, and every refusal it is supposed to make.

Each refusal below is a guard that has to be capable of failing. Where a test asserts a refusal,
neutering the corresponding branch in `mrf_honest.statistics` turns that test red; the guard is
not decoration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mrf_honest.statistics import (
    CONFIDENCE,
    SUPPRESSION_THRESHOLD,
    Proportion,
    Refusal,
    RefusalCode,
    Stratum,
    StratumKind,
    estimate_over,
    estimate_proportion,
    probability_strata,
    read_strata,
    refuse,
    wilson_interval,
)

COHORT = Path("data/cohorts/2026-08-19.json")

PROBABILITY = Stratum(
    identifier="stratum B, seeded random draw",
    kind=StratumKind.PROBABILITY,
    sample_size=48,
    universe_size=3024,
)
CONVENIENCE = Stratum(
    identifier="stratum A, carry-forward",
    kind=StratumKind.CONVENIENCE,
    sample_size=6,
)


def estimate(numerator: int, denominator: int, stratum: Stratum = PROBABILITY) -> object:
    return estimate_proportion(
        label="test", stratum=stratum, numerator=numerator, denominator=denominator
    )


class TestRefusals:
    def test_a_convenience_stratum_is_refused_not_estimated(self) -> None:
        """The sampling frame's rule, enforced: a convenience sample estimates nothing."""

        result = estimate(3, 48, CONVENIENCE)
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.CONVENIENCE_STRATUM
        assert "convenience" in result.reason

    def test_a_convenience_stratum_is_refused_even_with_a_large_denominator(self) -> None:
        """Widening a convenience sample does not make it a probability sample."""

        result = estimate(500, 5000, Stratum("big", StratumKind.CONVENIENCE, 5000))
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.CONVENIENCE_STRATUM

    def test_an_empty_denominator_is_refused_not_reported_as_zero(self) -> None:
        result = estimate(0, 0)
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.EMPTY_DENOMINATOR
        assert result.denominator == 0

    def test_a_denominator_below_the_floor_is_suppressed(self) -> None:
        result = estimate(5, SUPPRESSION_THRESHOLD - 1)
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.BELOW_SUPPRESSION_THRESHOLD
        assert result.denominator == SUPPRESSION_THRESHOLD - 1

    def test_a_denominator_exactly_at_the_floor_is_estimated(self) -> None:
        """The floor is inclusive, and the test pins which side of it is which."""

        result = estimate(5, SUPPRESSION_THRESHOLD)
        assert isinstance(result, Proportion)

    def test_two_strata_are_refused_rather_than_pooled(self) -> None:
        result = estimate_over(
            label="test", strata=[PROBABILITY, CONVENIENCE], numerator=3, denominator=54
        )
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.POOLED_STRATA

    def test_two_probability_strata_are_still_refused(self) -> None:
        """Pooling is refused on the count of strata, not on their kind."""

        other = Stratum("stratum C", StratumKind.PROBABILITY, 40, universe_size=1000)
        result = estimate_over(
            label="test", strata=[PROBABILITY, other], numerator=3, denominator=88
        )
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.POOLED_STRATA

    def test_no_frame_is_refused(self) -> None:
        result = estimate_over(label="test", strata=[], numerator=0, denominator=48)
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.NO_SAMPLING_FRAME

    def test_one_probability_stratum_estimates(self) -> None:
        result = estimate_over(label="test", strata=[PROBABILITY], numerator=11, denominator=48)
        assert isinstance(result, Proportion)
        assert result.numerator == 11

    def test_every_refusal_code_has_stated_text(self) -> None:
        """A refusal with an empty reason would render as a blank on a page."""

        for code in RefusalCode:
            stated = refuse(code)
            assert stated.code is code
            assert stated.reason.strip()

    def test_an_impossible_numerator_raises_rather_than_refusing(self) -> None:
        """A numerator above its denominator is a caller bug, not a publishable outcome."""

        with pytest.raises(ValueError):
            estimate(49, 48)
        with pytest.raises(ValueError):
            estimate(-1, 48)


class TestInterval:
    def test_the_point_estimate_lies_inside_its_interval(self) -> None:
        result = estimate(11, 48)
        assert isinstance(result, Proportion)
        assert result.low <= result.point <= result.high

    def test_a_zero_count_still_carries_a_nonzero_upper_bound(self) -> None:
        """The normal approximation is degenerate at 0; Wilson is not, which is why it is used."""

        result = estimate(0, 48)
        assert isinstance(result, Proportion)
        assert result.low == 0.0
        assert result.high > 0.0

    def test_a_saturated_count_still_carries_a_lower_bound_below_one(self) -> None:
        result = estimate(48, 48)
        assert isinstance(result, Proportion)
        assert result.high == 1.0
        assert result.low < 1.0

    def test_the_finite_population_correction_narrows_the_interval(self) -> None:
        """A draw without replacement from 3,024 is more informative than one from a limitless
        population, and the recorded flag says which was computed."""

        corrected = estimate(11, 48)
        uncorrected = estimate(11, 48, Stratum("no universe recorded", StratumKind.PROBABILITY, 48))
        assert isinstance(corrected, Proportion)
        assert isinstance(uncorrected, Proportion)
        assert corrected.finite_population_correction is True
        assert uncorrected.finite_population_correction is False
        assert corrected.high - corrected.low < uncorrected.high - uncorrected.low

    def test_a_census_is_not_corrected_below_its_own_size(self) -> None:
        """When the sample is the whole frame the correction is not applied, because the
        effective size it implies is unbounded."""

        result = estimate(20, 20, Stratum("census", StratumKind.PROBABILITY, 20, universe_size=20))
        assert isinstance(result, Proportion)
        assert result.finite_population_correction is False

    def test_a_saturated_interval_contains_its_own_point_estimate(self) -> None:
        """Regression. The property test below found this: at numerator == denominator the
        Wilson arithmetic evaluates the upper bound as 0.9999999999999999 against an observed
        1.0, so the published interval excluded the value it was computed from."""

        low, high = wilson_interval(25, 25, effective_size=25 * 3023 / (3024 - 25))
        assert low <= 1.0 <= high

    def test_wilson_rejects_an_empty_denominator(self) -> None:
        with pytest.raises(ValueError):
            wilson_interval(0, 0)

    def test_wilson_rejects_an_impossible_numerator(self) -> None:
        with pytest.raises(ValueError):
            wilson_interval(5, 4)

    @given(
        denominator=st.integers(min_value=SUPPRESSION_THRESHOLD, max_value=5000),
        fraction=st.floats(min_value=0.0, max_value=1.0),
    )
    def test_the_interval_always_brackets_the_estimate_and_stays_on_the_scale(
        self, denominator: int, fraction: float
    ) -> None:
        numerator = round(fraction * denominator)
        result = estimate(numerator, denominator)
        assert isinstance(result, Proportion)
        assert 0.0 <= result.low <= result.point <= result.high <= 1.0
        assert result.confidence == CONFIDENCE

    @given(denominator=st.integers(min_value=SUPPRESSION_THRESHOLD, max_value=400))
    def test_a_wider_denominator_never_widens_the_interval_at_the_midpoint(
        self, denominator: int
    ) -> None:
        """More observations cannot buy less precision at the least favourable point."""

        here = estimate(denominator // 2, denominator)
        there = estimate(denominator, denominator * 2)
        assert isinstance(here, Proportion)
        assert isinstance(there, Proportion)
        assert there.high - there.low <= here.high - here.low + 1e-12


class TestRendering:
    def test_the_sentence_carries_the_qualifiers_it_cannot_be_read_without(self) -> None:
        result = estimate(11, 48)
        assert isinstance(result, Proportion)
        sentence = result.sentence()
        assert "11 of 48" in sentence
        assert "95% interval" in sentence
        assert "stratum B" in sentence

    def test_a_proportion_serialises_with_its_denominator_and_interval(self) -> None:
        result = estimate(11, 48)
        assert isinstance(result, Proportion)
        payload = result.as_dict()
        assert payload["outcome"] == "estimated"
        assert payload["denominator"] == 48
        assert payload["interval_low"] is not None
        assert payload["interval_high"] is not None
        assert json.dumps(payload)

    def test_a_refusal_serialises_with_its_code_and_reason(self) -> None:
        result = estimate(3, 4)
        assert isinstance(result, Refusal)
        payload = result.as_dict()
        assert payload["outcome"] == "refused"
        assert payload["code"] == "below_suppression_threshold"
        assert payload["reason"]
        assert json.dumps(payload)


class TestReadingTheCommittedFrame:
    def test_the_published_cohort_yields_both_strata_with_their_recorded_sizes(self) -> None:
        """Read from the manifest this project actually published, not from a fixture."""

        manifest = json.loads(COHORT.read_text())
        strata = read_strata(manifest["collection"])
        assert len(strata) == 2
        by_kind = {stratum.kind: stratum for stratum in strata}
        assert by_kind[StratumKind.CONVENIENCE].sample_size == 6
        probability = by_kind[StratumKind.PROBABILITY]
        assert probability.sample_size == 48
        assert probability.universe_size == 3024

    def test_the_committed_draw_estimates_and_the_carry_forward_does_not(self) -> None:
        manifest = json.loads(COHORT.read_text())
        strata = read_strata(manifest["collection"])
        drawn = probability_strata(strata)
        assert len(drawn) == 1
        graded = estimate_over(
            label="drawn facilities graded under the JSON profile",
            strata=drawn,
            numerator=11,
            denominator=48,
        )
        assert isinstance(graded, Proportion)
        pooled = estimate_over(label="pooled", strata=strata, numerator=17, denominator=54)
        assert isinstance(pooled, Refusal)
        assert pooled.code is RefusalCode.POOLED_STRATA

    def test_a_manifest_without_a_frame_reads_as_no_strata(self) -> None:
        assert read_strata({}) == ()
        assert read_strata({"sampling_frame": "a sentence, not a record"}) == ()

    def test_a_frame_whose_draw_is_unreadable_yields_no_probability_stratum(self) -> None:
        """A sample size that is missing, zero, a bool, or a string is absent, never guessed."""

        for bad in ({}, {"sample_size": 0}, {"sample_size": True}, {"sample_size": "48"}):
            strata = read_strata({"sampling_frame": {"stratum_b_random_draw": bad}})
            assert probability_strata(strata) == ()

    def test_an_unreadable_eligible_count_drops_the_correction_not_the_estimate(self) -> None:
        draw = {"sample_size": 48, "eligible_count": "x"}
        strata = read_strata({"sampling_frame": {"stratum_b_random_draw": draw}})
        assert len(strata) == 1
        assert strata[0].universe_size is None


class TestStratum:
    def test_a_negative_sample_size_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Stratum("bad", StratumKind.PROBABILITY, -1)

    def test_a_universe_smaller_than_its_sample_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Stratum("bad", StratumKind.PROBABILITY, 48, universe_size=10)
