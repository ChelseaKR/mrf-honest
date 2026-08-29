"""Population statistics for published comparisons, and the refusals that guard them.

`docs/SAMPLING-FRAME.md` already states the rule this module enforces: "Where a proportion is
meant to describe hospitals in general, it is computed over stratum B alone and says so," and
"Its two strata must not be pooled." Until now that rule was prose, and prose is not a gate.

Every entry point here returns either a `Proportion` carrying its own denominator, stratum and
interval, or a `Refusal` carrying the reason it produced no number. There is deliberately no
third outcome and no bare `float`: a caller cannot render a point estimate without also holding
the interval and the denominator that qualify it, and cannot mistake a refusal for a zero.

Standard library only, per ADR 0002. See ADR 0007 for the interval method, the suppression
threshold, and why the refusals are shaped this way.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

STATISTICS_POLICY_VERSION = "population-statistics-v1"

#: Two-sided normal quantile for a 95 percent interval.
Z_95 = 1.959963984540054

CONFIDENCE = 0.95

#: A proportion is not representable below this denominator. See ADR 0007: at n = 20 a 95
#: percent Wilson interval at the least favourable point still spans 0.401 of the scale, and
#: the width grows quickly below that (0.410 at 19, 0.452 at 15, 0.527 at 10).
SUPPRESSION_THRESHOLD = 20


class StratumKind(StrEnum):
    """How a stratum's members were selected."""

    PROBABILITY = "probability"
    CONVENIENCE = "convenience"


class RefusalCode(StrEnum):
    """Why no number was produced. Every one of these is a published outcome, not an error."""

    NO_SAMPLING_FRAME = "no_sampling_frame"
    CONVENIENCE_STRATUM = "convenience_stratum"
    POOLED_STRATA = "pooled_strata"
    EMPTY_DENOMINATOR = "empty_denominator"
    BELOW_SUPPRESSION_THRESHOLD = "below_suppression_threshold"
    INCOMPLETE_ACCOUNTING = "incomplete_accounting"


_REFUSAL_TEXT = {
    RefusalCode.NO_SAMPLING_FRAME: (
        "the cohort manifest records no sampling frame, so there is no population for a "
        "proportion to describe"
    ),
    RefusalCode.CONVENIENCE_STRATUM: (
        "the stratum is a convenience sample; it supports a description of the files it "
        "contains and no estimate of anything beyond them"
    ),
    RefusalCode.POOLED_STRATA: (
        "more than one stratum was offered for one proportion; strata selected by different "
        "rules must not be pooled into a rate"
    ),
    RefusalCode.EMPTY_DENOMINATOR: (
        "the denominator is zero, so there is nothing to take a proportion of"
    ),
    RefusalCode.BELOW_SUPPRESSION_THRESHOLD: (
        f"the denominator is below the stated floor of {SUPPRESSION_THRESHOLD}, at which a 95 "
        "percent interval already spans more than 0.40 of the scale"
    ),
    RefusalCode.INCOMPLETE_ACCOUNTING: (
        "this cohort accounts for only part of its stratum, so a proportion computed here would "
        "have a denominator the frame does not recognise"
    ),
}


@dataclass(frozen=True)
class Stratum:
    """One selection rule, its size, and the universe it was drawn from.

    `universe_size` is the size of the frame the sample was drawn from, where the frame records
    one. It is used only for the finite-population correction and is never required.
    """

    identifier: str
    kind: StratumKind
    sample_size: int
    universe_size: int | None = None

    def __post_init__(self) -> None:
        if self.sample_size < 0:
            raise ValueError("sample_size cannot be negative")
        if self.universe_size is not None and self.universe_size < self.sample_size:
            raise ValueError("universe_size cannot be smaller than sample_size")


@dataclass(frozen=True)
class Refusal:
    """A stated reason that no proportion was produced."""

    code: RefusalCode
    reason: str
    stratum: str | None = None
    denominator: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": "refused",
            "code": str(self.code),
            "reason": self.reason,
            "stratum": self.stratum,
            "denominator": self.denominator,
        }


@dataclass(frozen=True)
class Proportion:
    """A proportion that carries everything needed to read it honestly."""

    label: str
    stratum: str
    numerator: int
    denominator: int
    point: float
    low: float
    high: float
    method: str
    confidence: float
    universe_size: int | None
    finite_population_correction: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": "estimated",
            "label": self.label,
            "stratum": self.stratum,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "point": self.point,
            "interval_low": self.low,
            "interval_high": self.high,
            "method": self.method,
            "confidence": self.confidence,
            "universe_size": self.universe_size,
            "finite_population_correction": self.finite_population_correction,
        }

    def sentence(self) -> str:
        """One sentence a page can print without dropping the qualifiers."""

        return (
            f"{self.numerator} of {self.denominator} ({self.point * 100:.1f}%, "
            f"{self.confidence * 100:.0f}% interval {self.low * 100:.1f}% to "
            f"{self.high * 100:.1f}%), over {self.stratum}"
        )


Estimate = Proportion | Refusal


def refuse(
    code: RefusalCode, *, stratum: str | None = None, denominator: int | None = None
) -> Refusal:
    """Build the stated refusal for a code, so the wording cannot drift between call sites."""

    return Refusal(code=code, reason=_REFUSAL_TEXT[code], stratum=stratum, denominator=denominator)


def _effective_sample_size(denominator: int, universe_size: int | None) -> tuple[float, bool]:
    """Return the sample size the interval is computed at, and whether the FPC was applied.

    A draw without replacement from a finite frame carries less uncertainty than one from an
    unbounded population. The correction multiplies the variance by (N - n) / (N - 1), which is
    the same as computing the interval at an effective size of n (N - 1) / (N - n).
    """

    if universe_size is None or universe_size <= denominator or denominator <= 1:
        return float(denominator), False
    return denominator * (universe_size - 1) / (universe_size - denominator), True


def wilson_interval(
    numerator: int, denominator: int, *, z: float = Z_95, effective_size: float | None = None
) -> tuple[float, float]:
    """The Wilson score interval, clamped to [0, 1] and to the observed proportion.

    Chosen over the normal approximation because the normal interval is degenerate at 0 and 1,
    which is exactly where this project's proportions land most often.

    The interval is also clamped so that it always contains its own point estimate. Wilson
    guarantees that algebraically; binary floating point does not. At numerator == denominator
    the arithmetic evaluates the upper bound as 0.9999999999999999 against an observed 1.0, and
    an interval that excludes the value it was computed from would be a false statement on a
    page. The clamp moves a bound by at most one unit in the last place.
    """

    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if not 0 <= numerator <= denominator:
        raise ValueError("numerator must lie between zero and the denominator")
    size = float(denominator) if effective_size is None else effective_size
    observed = numerator / denominator
    denom = 1.0 + z * z / size
    center = (observed + z * z / (2 * size)) / denom
    spread = z / denom * math.sqrt(observed * (1 - observed) / size + z * z / (4 * size * size))
    low = min(observed, max(0.0, center - spread))
    high = max(observed, min(1.0, center + spread))
    return low, high


def estimate_proportion(
    *,
    label: str,
    stratum: Stratum,
    numerator: int,
    denominator: int,
) -> Estimate:
    """Estimate one proportion, or state why no number was produced.

    The refusals are ordered so that the most fundamental reason is the one reported: a
    convenience sample is refused before its denominator is examined, because widening it would
    not help.
    """

    if numerator < 0 or numerator > denominator:
        raise ValueError("numerator must lie between zero and the denominator")
    if stratum.kind is not StratumKind.PROBABILITY:
        return refuse(RefusalCode.CONVENIENCE_STRATUM, stratum=stratum.identifier)
    if denominator == 0:
        return refuse(RefusalCode.EMPTY_DENOMINATOR, stratum=stratum.identifier, denominator=0)
    if denominator < SUPPRESSION_THRESHOLD:
        return refuse(
            RefusalCode.BELOW_SUPPRESSION_THRESHOLD,
            stratum=stratum.identifier,
            denominator=denominator,
        )
    size, corrected = _effective_sample_size(denominator, stratum.universe_size)
    low, high = wilson_interval(numerator, denominator, effective_size=size)
    return Proportion(
        label=label,
        stratum=stratum.identifier,
        numerator=numerator,
        denominator=denominator,
        point=numerator / denominator,
        low=low,
        high=high,
        method="wilson-score",
        confidence=CONFIDENCE,
        universe_size=stratum.universe_size,
        finite_population_correction=corrected,
    )


def estimate_over(
    *,
    label: str,
    strata: Sequence[Stratum],
    numerator: int,
    denominator: int,
) -> Estimate:
    """Estimate over exactly one stratum, refusing a pooled set rather than choosing one.

    `build_comparison` knows a cohort's strata but not which one a caller meant. Handing this
    function the whole set and letting it silently pick the probability one would reintroduce
    the pooling the sampling frame forbids, so a set of any size other than one is refused.
    """

    if not strata:
        return refuse(RefusalCode.NO_SAMPLING_FRAME)
    if len(strata) != 1:
        return refuse(RefusalCode.POOLED_STRATA)
    return estimate_proportion(
        label=label, stratum=strata[0], numerator=numerator, denominator=denominator
    )


def read_strata(collection: Mapping[str, object]) -> tuple[Stratum, ...]:
    """Read the strata a cohort manifest records, mechanically and without inference.

    Returns an empty tuple when the manifest records no frame, which `estimate_over` turns into
    the stated `no_sampling_frame` refusal. A stratum this function cannot read is not guessed
    at; it is simply absent, and its absence refuses rather than estimates.
    """

    frame = collection.get("sampling_frame")
    if not isinstance(frame, Mapping):
        return ()
    strata: list[Stratum] = []
    carry_forward = frame.get("stratum_a_carry_forward")
    if isinstance(carry_forward, Sequence) and not isinstance(carry_forward, str | bytes):
        strata.append(
            Stratum(
                identifier="stratum A, carry-forward",
                kind=StratumKind.CONVENIENCE,
                sample_size=len(carry_forward),
            )
        )
    draw = frame.get("stratum_b_random_draw")
    if isinstance(draw, Mapping):
        drawn = _positive_int(draw.get("sample_size"))
        if drawn is not None:
            strata.append(
                Stratum(
                    identifier="stratum B, seeded random draw",
                    kind=StratumKind.PROBABILITY,
                    sample_size=drawn,
                    universe_size=_positive_int(draw.get("eligible_count")),
                )
            )
    return tuple(strata)


def probability_strata(strata: Sequence[Stratum]) -> tuple[Stratum, ...]:
    """The probability strata of a set, so a caller states which subset it is estimating over."""

    return tuple(stratum for stratum in strata if stratum.kind is StratumKind.PROBABILITY)


def _positive_int(value: object) -> int | None:
    """Read a positive integer, refusing bools and anything that is not already an int."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
