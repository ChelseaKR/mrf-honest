"""Which published cohorts the sampling-frame gates examine, and which they do not.

Two gates in ``tests/test_published_claims.py`` re-derive the claims a stated
frame makes -- that the random stratum really is the seeded draw, and that
nothing drawn was quietly dropped. Both resolved their frame from the
comparison's **filename**::

    prefix = comparison_path.name.removesuffix(".comparison.json")
    candidate = FRAMES / f"{prefix}.frame.json"

``data/frames/2026-08-19-csv.frame.json`` does not exist, so both gates skipped
the CSV cohort with the reason ``"predates the sampling frame"``. The CSV cohort
does not predate the frame. Its own document names one::

    collection.sampling_frame.record = "data/frames/2026-08-19.frame.json"

So a lookup that missed was rendered as a fact about the data -- this
repository's own dominant defect class, inside the gate written to catch it.
Measured before the change: **3** published comparisons, **1** examined by each
gate, **17** of the 48 published file rows within reach.

Three things follow, and they are the reason this module exists rather than a
one-term patch:

* **The frame is whatever the document names.** A filename convention is a
  guess; ``collection.sampling_frame.record`` is a statement the document makes
  and can be held to.
* **A named record that does not resolve fails.** It is the one case where a
  skip and a pass are indistinguishable, because the gate cannot tell "this
  cohort has no frame" from "this cohort's frame moved".
* **A skip needs its own true reason.** The two gates do not have the same
  scope, so they do not have the same reason: ``2026-08-14`` carries no
  ``sampling_frame`` at all and was a convenience sample, while the CSV cohort
  is a *sibling* of the cohort the frame's ``attempts`` describe (see below).
  One sentence cannot be true of both.

**The sibling shape, chosen and written down.** ``data/frames/2026-08-19.frame.json``
records one ``attempts[]`` row per drawn facility, and each row's ``outcome``
and ``detail`` describe the **JSON** cohort: ``detail`` is a
``hospital-json-v3-2026-08-19`` slug, and a facility recorded ``graded`` there
is graded *as JSON*. The CSV cohort is the sibling of that draw -- it declares
``collection.sampling_frame.sibling_cohort`` and grades the CSV-retrievable
targets the JSON cohort recorded as format exclusions. So pointing the
per-facility accounting gate at the CSV cohort unchanged fails 48 of 48 times,
correctly and uselessly.

Of the two shapes ``#95`` puts up, this takes the second: the accounting gate is
scoped to the cohort the frame's ``attempts`` describe, and says so, naming the
sibling. What keeps that from being a hole is that the deferral is *checked* --
:data:`SEAM_GATE` walks the seam between the two documents and is asserted to
exist, so a cohort deferred here is a cohort covered there rather than a cohort
nobody looks at. ``docs/SAMPLING-FRAME.md`` carries the same statement in prose.

The seeded-draw gate has no such restriction: the draw is a property of the
frame, both cohorts declare the same seed, sample size and eligible-identifier
digest, and re-running it over the CSV cohort passes exactly.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COHORTS = ROOT / "data" / "cohorts"
FRAMES = ROOT / "data" / "frames"

#: The gates this census is a census of, named rather than described so that
#: renaming one without updating the record fails a test instead of quietly
#: leaving the census pointing at nothing.
DRAW_GATE = "test_the_random_stratum_is_the_seeded_draw_it_claims_to_be"
ACCOUNTING_GATE = "test_no_drawn_facility_is_missing_from_the_published_cohort"

#: The gate that covers a cohort the accounting gate defers. Named here because
#: the deferral is only defensible while this exists.
SEAM_GATE = "test_every_drawn_facility_is_accounted_for_across_both_profile_cohorts"

#: The closed vocabulary of verdicts. A comparison that lands outside it is a
#: comparison nobody decided about, which is the state this module refuses.
EXAMINED = "examined"
NO_FRAME = "no_frame"
UNRESOLVABLE = "unresolvable_frame_record"
DEFERRED = "deferred_to_sibling_cohort"
VERDICTS = frozenset({EXAMINED, NO_FRAME, UNRESOLVABLE, DEFERRED})

#: A floor, not a count. One examined cohort is the difference between a gate
#: and a sentence; the number of cohorts is data and must never be typed here,
#: because a gate whose denominator a human maintains jams every open branch the
#: day a cohort is added.
MINIMUM_EXAMINED = 1


@dataclass(frozen=True)
class FrameScope:
    """One published comparison, and what each frame gate can say about it."""

    comparison: str
    cohort_id: str
    declared_record: str | None
    frame_path: Path | None
    sibling_cohort: str | None

    @property
    def draw_verdict(self) -> str:
        if self.declared_record is None:
            return NO_FRAME
        if self.frame_path is None:
            return UNRESOLVABLE
        return EXAMINED

    @property
    def accounting_verdict(self) -> str:
        if self.declared_record is None:
            return NO_FRAME
        if self.frame_path is None:
            return UNRESOLVABLE
        if self.sibling_cohort is not None:
            return DEFERRED
        return EXAMINED

    def reason(self, verdict: str) -> str:
        """Why a gate did not examine this comparison, in its own terms."""
        if verdict == NO_FRAME:
            return (
                f"{self.comparison} carries no collection.sampling_frame; it predates the "
                "frame and was a convenience sample, and says so in its own document"
            )
        if verdict == UNRESOLVABLE:
            return (
                f"{self.comparison} names the frame record {self.declared_record!r}, which is "
                "not committed. A named record that does not resolve is the one case where a "
                "skip cannot be told from a pass, so this fails rather than skipping."
            )
        if verdict == DEFERRED:
            return (
                f"{self.comparison} is the sibling of {self.sibling_cohort}; the attempts in "
                f"{self.declared_record} record that cohort's per-facility outcome, not this "
                f"one's. Its own accounting against the shared draw is {SEAM_GATE}."
            )
        return f"{self.comparison} is examined"


def published_comparisons() -> tuple[Path, ...]:
    """Every committed comparison document, in name order."""
    return tuple(sorted(COHORTS.glob("*.comparison.json")))


def scope_of(comparison_path: Path) -> FrameScope:
    """Read a comparison's own statement about the frame it was drawn from.

    Deliberately not a filename convention. The document names its frame; a path
    built from the file's name is a guess that fails silently when a cohort is a
    sibling, which is what it did.
    """
    document = json.loads(comparison_path.read_text(encoding="utf-8"))
    cohort = document.get("cohort")
    cohort_id = str(cohort.get("cohort_id")) if isinstance(cohort, dict) else "<unnamed>"
    collection = document.get("collection")
    frame = collection.get("sampling_frame") if isinstance(collection, dict) else None
    # Membership, not truthiness: a ``sampling_frame`` key present with a null or
    # empty body is a document that claims a frame and does not name one, which
    # is a different failure from a document that claims none.
    if not isinstance(collection, dict) or "sampling_frame" not in collection:
        return FrameScope(comparison_path.name, cohort_id, None, None, None)
    if not isinstance(frame, dict) or "record" not in frame:
        return FrameScope(comparison_path.name, cohort_id, "<no record named>", None, None)
    declared = str(frame["record"])
    candidate = ROOT / declared
    sibling = frame.get("sibling_cohort")
    return FrameScope(
        comparison=comparison_path.name,
        cohort_id=cohort_id,
        declared_record=declared,
        frame_path=candidate if candidate.is_file() else None,
        sibling_cohort=str(sibling) if sibling is not None else None,
    )


def scopes() -> tuple[FrameScope, ...]:
    return tuple(scope_of(path) for path in published_comparisons())


def census_lines(known: Sequence[FrameScope] | None = None) -> tuple[str, ...]:
    """The two numbers, per gate, in the terminal summary of every run.

    Present tense on purpose: this is a property of the committed documents and
    the gates' scope rules, not a report on what one invocation executed, so a
    filtered run does not make it a lie.
    """
    scoped = tuple(known) if known is not None else scopes()
    lines: list[str] = []
    for gate, verdict_of in (
        (DRAW_GATE, lambda scope: scope.draw_verdict),
        (ACCOUNTING_GATE, lambda scope: scope.accounting_verdict),
    ):
        examined = [scope for scope in scoped if verdict_of(scope) == EXAMINED]
        lines.append(
            f"sampling frame: {gate} examines {len(examined)} of {len(scoped)} "
            "published comparisons"
        )
        for scope in scoped:
            verdict = verdict_of(scope)
            if verdict != EXAMINED:
                lines.append(f"  [{verdict}] {scope.reason(verdict)}")
    return tuple(lines)
