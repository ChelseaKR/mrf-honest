"""Workflow inputs, and the public surfaces they must not reach.

This repository is public, so everything GitHub renders about a workflow run is a
publication: the run name, the concurrency group, every step's name and ``if:``, every
``with:`` value, every annotation, and the run summary. A ``workflow_dispatch`` input that
lands on any of those has been published to anybody who can open the Actions tab.

That is not a hypothetical. On 2026-09-12 a sibling project in this portfolio shipped a
fulfilment workflow whose 32-hex order id **was** the download credential, and used it as the
uploaded artifact's name. An unauthenticated ``GET`` of that run's ``/artifacts`` endpoint
returned that name, and the id in it answered 302 to a live presigned URL. The repair was to
hash the id into a separate, one-way ``dispatch_key`` for every rendered string, and to write
one test per surface that could republish it. This module is that guard, written here **before**
any such workflow exists, because the leak was found the day the workflow launched and the fix
is cheap only while the workflow is still unwritten.

Two rules, and they are different rules:

**A capability never reaches a rendered surface.** An input is a *capability* when possessing
its value is what authorizes something -- a download token, a signed URL, a delivery address.
Today no workflow in this repository declares one, and :data:`CAPABILITY_INPUTS` says so per
workflow rather than by silence, so the first workflow that adds one has to answer the question
in a diff rather than inherit an absence.

**No input reaches a shell except through ``env:``.** This one holds for every input, capability
or not. ``${{ inputs.x }}`` inside a ``run:`` block is textual substitution *into the script*
before bash sees a token, which is the documented command-injection shape; ``env:`` makes the
value a variable the shell can only read. It is also what gives the rule above something to
look for, since a variable has a name and an interpolation does not.

**Every rule below is driven from synthetic workflow documents that exercise both directions in
the same test**, and only then applied to the committed ones. A refusal whose state the committed
files cannot reach is a gate that cannot fail -- this repository has already been caught shipping
one (issue #95, where two of a classifier's four verdicts were unreachable from anything it
publishes) -- and a rule proved only by the absence of a violation is indistinguishable from a
rule that refuses nothing. Each test therefore shows a document the rule must refuse *and* a
document it must accept.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

#: Which of each workflow's ``workflow_dispatch`` inputs are capabilities: values whose
#: possession authorizes something, and which therefore may not be rendered anywhere public.
#:
#: Every workflow that declares dispatch inputs appears here, including the ones whose answer is
#: "none of them" -- an empty set is an answer, an absent entry is an unanswered question, and
#: ``test_every_dispatchable_workflow_has_answered_the_capability_question`` refuses the second.
CAPABILITY_INPUTS: Mapping[str, frozenset[str]] = {
    # How many days an unpublished commit may wait before the sentinel reports. A threshold, not
    # a secret: knowing it authorizes nothing, and it reaches the shell only through env:.
    "deploy-staleness.yml": frozenset(),
    # The tag to release from. It names a public git ref that is already pushed; knowing it
    # authorizes nothing, and release.yml verifies its signature before anything is built.
    "release.yml": frozenset(),
}

#: Interpolations of a dispatch input into a ``run:`` block that are known, recorded, and not
#: yet repaired. Keyed by (workflow file, the expression as it appears), valued by why it is
#: still here.
#:
#: This is an allowlist, which is the honest shape for a defect somebody else is holding the
#: file for -- the alternative is a rule narrowed until the defect stops being a violation,
#: which deletes the finding instead of recording it.
#: ``test_every_recorded_shell_interpolation_is_still_there`` fails when an entry no longer
#: matches, so a repair forces the entry out rather than leaving a permanent hole.
SHELL_INTERPOLATION_NOT_YET_REPAIRED: Mapping[tuple[str, str], str] = {}

#: Anything GitHub renders on a run page from a ``run:`` block: a workflow command becomes an
#: annotation, and an ``echo`` in a step that writes ``$GITHUB_STEP_SUMMARY`` becomes the run
#: summary. Both are readable by anyone who can open the run, which here is anyone.
_WORKFLOW_COMMANDS = ("::error", "::warning", "::notice")
_PRINTS = re.compile(r"\b(echo|printf)\b")
_INPUT_EXPRESSION = re.compile(r"\b(?:inputs|github\.event\.inputs)\.([A-Za-z_][A-Za-z0-9_-]*)")


def _load(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8")))


def _parse(text: str) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(text))


def dispatch_inputs(document: Mapping[str, Any]) -> frozenset[str]:
    """The names of a workflow's ``workflow_dispatch`` inputs.

    ``on`` is read through both keys PyYAML can produce for it: unquoted ``on:`` is the YAML 1.1
    boolean ``True``, and a workflow that quotes it is not a different workflow.
    """
    triggers = document.get("on", document.get(True))
    if not isinstance(triggers, Mapping):
        return frozenset()
    dispatch = triggers.get("workflow_dispatch")
    if not isinstance(dispatch, Mapping):
        return frozenset()
    declared = dispatch.get("inputs")
    return frozenset(declared) if isinstance(declared, Mapping) else frozenset()


def _steps(document: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    jobs = document.get("jobs")
    if not isinstance(jobs, Mapping):
        return []
    found: list[tuple[str, Mapping[str, Any]]] = []
    for job, spec in jobs.items():
        steps = spec.get("steps") if isinstance(spec, Mapping) else None
        if isinstance(steps, Sequence):
            found += [(str(job), step) for step in steps if isinstance(step, Mapping)]
    return found


def _names(expression: object, capabilities: frozenset[str]) -> list[str]:
    return [name for name in _INPUT_EXPRESSION.findall(str(expression)) if name in capabilities]


def top_level_leaks(document: Mapping[str, Any], capabilities: frozenset[str]) -> list[str]:
    """Capability inputs named by the run's title or its concurrency group.

    A concurrency expression cannot hash anything, so a workflow that needs per-order grouping
    has to be handed a key derived by whatever dispatched it. That constraint is why this is a
    rule rather than a style note.
    """
    surfaces: list[tuple[str, object]] = [
        ("name", document.get("name")),
        ("run-name", document.get("run-name")),
    ]
    concurrency = document.get("concurrency")
    if isinstance(concurrency, Mapping):
        surfaces.append(("concurrency.group", concurrency.get("group")))
    elif concurrency is not None:
        surfaces.append(("concurrency", concurrency))
    return [
        f"{field} names the capability input {name!r}"
        for field, value in surfaces
        for name in _names(value, capabilities)
    ]


def rendered_field_leaks(document: Mapping[str, Any], capabilities: frozenset[str]) -> list[str]:
    """Capability inputs named by a step's ``name``, ``if``, or any ``with:`` value.

    ``with:`` is the one that leaked in the sibling project: an uploaded artifact's name is a
    ``with:`` value and is served by the public artifacts API.
    """
    found: list[str] = []
    for job, step in _steps(document):
        rendered: list[tuple[str, object]] = [("name", step.get("name")), ("if", step.get("if"))]
        with_ = step.get("with")
        if isinstance(with_, Mapping):
            rendered += [(f"with.{key}", value) for key, value in with_.items()]
        found += [
            f"{job}/{step.get('name')}: {field} names the capability input {name!r}"
            for field, value in rendered
            for name in _names(value, capabilities)
        ]
    return found


def shell_interpolations(document: Mapping[str, Any], inputs: frozenset[str]) -> list[str]:
    """Dispatch inputs substituted straight into a ``run:`` block, as their expressions.

    Every input, not only the capabilities: this is the injection rule, and it is also what
    gives :func:`printed_capability_leaks` a variable name to look for.
    """
    return [
        match.group(0)
        for _, step in _steps(document)
        for match in _INPUT_EXPRESSION.finditer(str(step.get("run") or ""))
        if match.group(1) in inputs
    ]


def _protected_variables(step: Mapping[str, Any], capabilities: frozenset[str]) -> set[str]:
    """Shell variable names in this step whose value is a capability input."""
    environment = step.get("env")
    if not isinstance(environment, Mapping):
        return set()
    return {str(name) for name, value in environment.items() if _names(value, capabilities)}


def _rendered_lines(run: str) -> list[str]:
    lines = run.splitlines()
    writes_summary = any("$GITHUB_STEP_SUMMARY" in line for line in lines)
    return [
        line
        for line in lines
        if any(command in line for command in _WORKFLOW_COMMANDS)
        or (writes_summary and _PRINTS.search(line))
    ]


def printed_capability_leaks(
    document: Mapping[str, Any], capabilities: frozenset[str]
) -> list[str]:
    """Capability-carrying shell variables written to an annotation or the run summary.

    This is the rule that stops the leak coming back through a helpful error message after the
    names have been cleaned up. A failing fulfilment run has to say that an order failed; it must
    not say which one.
    """
    found: list[str] = []
    for job, step in _steps(document):
        protected = _protected_variables(step, capabilities)
        found += [
            f"{job}/{step.get('name')}: publishes {variable} in {line.strip()!r}"
            for line in _rendered_lines(str(step.get("run") or ""))
            for variable in protected
            if variable in line
        ]
    return found


# ---------------------------------------------------------------------------
# The rules, each shown refusing something and accepting something else.
# ---------------------------------------------------------------------------

_LEAKY_TITLE = """
name: Deliver ${{ inputs.order_id }}
on:
  workflow_dispatch:
    inputs:
      order_id: {required: true, type: string}
concurrency:
  group: deliver-${{ inputs.order_id }}
  cancel-in-progress: false
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - run: echo done
"""

_SAFE_TITLE = """
name: Deliver
on:
  workflow_dispatch:
    inputs:
      order_id: {required: true, type: string}
      run_key: {required: false, type: string, default: ""}
concurrency:
  group: deliver-${{ inputs.run_key || github.run_id }}
  cancel-in-progress: false
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - run: echo done
"""


def test_a_capability_may_not_name_the_run_or_group_it() -> None:
    """The title and the concurrency group are rendered before a job has run at all.

    Grouping still has to be per order. One shared group would make two buyers' runs queue, and
    GitHub keeps a single pending run per group, so a third arrival evicts the queued one -- a
    paid order dropped in silence. The accepted document keeps per-order grouping through a key
    the dispatcher derived, which is the only way to have both.
    """
    capabilities = frozenset({"order_id"})
    leaks = top_level_leaks(_parse(_LEAKY_TITLE), capabilities)
    assert len(leaks) == 2, leaks
    assert any("name names" in leak for leak in leaks)
    assert any("concurrency.group names" in leak for leak in leaks)

    assert top_level_leaks(_parse(_SAFE_TITLE), capabilities) == []
    group = str(_parse(_SAFE_TITLE)["concurrency"]["group"])
    assert "inputs.run_key" in group, "grouping must stay per order, through the derived key"


_LEAKY_WITH = """
on:
  workflow_dispatch:
    inputs:
      order_id: {required: true, type: string}
      run_key: {required: false, type: string, default: ""}
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - name: Keep the archive
        uses: actions/upload-artifact@v6
        with:
          name: order-${{ inputs.order_id }}
          path: out.zip
"""

_SAFE_WITH = """
on:
  workflow_dispatch:
    inputs:
      order_id: {required: true, type: string}
      run_key: {required: false, type: string, default: ""}
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - name: Keep the archive
        uses: actions/upload-artifact@v6
        with:
          name: order-${{ inputs.run_key || github.run_id }}
          path: out.zip
"""


def test_a_capability_may_not_be_a_rendered_field_of_a_step() -> None:
    """The exact shape of the measured leak: an artifact name is a ``with:`` value, and the
    artifacts API serves it to anyone, with no token."""
    capabilities = frozenset({"order_id"})
    leaks = rendered_field_leaks(_parse(_LEAKY_WITH), capabilities)
    assert leaks == ["deliver/Keep the archive: with.name names the capability input 'order_id'"]
    assert rendered_field_leaks(_parse(_SAFE_WITH), capabilities) == []


_LEAKY_RUN = """
on:
  workflow_dispatch:
    inputs:
      tag: {required: true, type: string}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          tag="${{ inputs.tag }}"
          git verify-tag "$tag"
"""

_SAFE_RUN = """
on:
  workflow_dispatch:
    inputs:
      tag: {required: true, type: string}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - env:
          TAG: ${{ inputs.tag }}
        run: |
          git verify-tag "$TAG"
"""


def test_an_input_reaches_a_shell_only_through_env() -> None:
    """``${{ inputs.x }}`` in a ``run:`` block is substituted into the script before bash reads
    a single token, so the value's own characters become syntax. Through ``env:`` the same value
    is a variable the shell can only read.

    The accepted document is the same step doing the same work, which is the point: this rule
    costs two lines and is refused by nothing.
    """
    assert shell_interpolations(_parse(_LEAKY_RUN), frozenset({"tag"})) == ["inputs.tag"]
    assert shell_interpolations(_parse(_SAFE_RUN), frozenset({"tag"})) == []


_LEAKY_PRINT = """
on:
  workflow_dispatch:
    inputs:
      order_id: {required: true, type: string}
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - name: Say that a paid order failed
        if: ${{ failure() }}
        env:
          ORDER_ID: ${{ inputs.order_id }}
        run: |
          echo "::error::order ${ORDER_ID} did not complete"
          echo "- order: ${ORDER_ID}" >> "$GITHUB_STEP_SUMMARY"
"""

_SAFE_PRINT = """
on:
  workflow_dispatch:
    inputs:
      order_id: {required: true, type: string}
      run_key: {required: false, type: string, default: ""}
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - name: Say that a paid order failed
        if: ${{ failure() }}
        env:
          ORDER_ID: ${{ inputs.order_id }}
          RUN_KEY: ${{ inputs.run_key }}
        run: |
          echo "::error::An order did not complete. Which one is deliberately not
          printed here, because this log is public."
          echo "- run group: ${RUN_KEY:-none}" >> "$GITHUB_STEP_SUMMARY"
"""


def test_a_failure_may_be_announced_without_naming_the_order() -> None:
    """Both directions matter here and the second is the harder one.

    A rule that only refused printing would be satisfied by a workflow that fails in silence,
    and silence is its own defect: the buyer was told the build had started, the delivery mail
    only exists on the success path, and nothing else in the repository would notice. The
    accepted document still writes an annotation and still writes a run summary -- it just says
    *that* an order failed rather than *which*.
    """
    capabilities = frozenset({"order_id"})
    leaks = printed_capability_leaks(_parse(_LEAKY_PRINT), capabilities)
    assert len(leaks) == 2, leaks
    assert any("::error" in leak for leak in leaks)
    assert any("GITHUB_STEP_SUMMARY" in leak for leak in leaks)

    safe = _parse(_SAFE_PRINT)
    assert printed_capability_leaks(safe, capabilities) == []
    announcement = "\n".join(str(step.get("run") or "") for _, step in _steps(safe))
    assert "::error" in announcement, "a failed fulfilment run must still say that it failed"
    assert "$GITHUB_STEP_SUMMARY" in announcement


def test_the_rules_do_not_fire_on_an_input_nobody_called_a_capability() -> None:
    """The classification is what narrows these rules, so it has to be load-bearing.

    The same leaky documents, read with an empty capability set, produce nothing. That is
    correct -- a release tag on a run page is not a leak -- and it is also the thing that makes
    :data:`CAPABILITY_INPUTS` worth maintaining rather than a comment.
    """
    for text in (_LEAKY_TITLE, _LEAKY_WITH, _LEAKY_PRINT):
        document = _parse(text)
        assert top_level_leaks(document, frozenset()) == []
        assert rendered_field_leaks(document, frozenset()) == []
        assert printed_capability_leaks(document, frozenset()) == []


# ---------------------------------------------------------------------------
# The same rules, over the workflows this repository actually ships.
# ---------------------------------------------------------------------------


def _dispatchable() -> dict[str, dict[str, Any]]:
    return {
        path.name: document
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if dispatch_inputs(document := _load(path.name))
    }


def test_every_dispatchable_workflow_has_answered_the_capability_question() -> None:
    """A workflow may not arrive with dispatch inputs nobody classified.

    This is the part that makes the guard bite before the leak rather than after it. The rules
    above are narrowed by :data:`CAPABILITY_INPUTS`, so a workflow missing from that table is
    exempt from all of them -- which is exactly how a fulfilment workflow would slip past a
    guard written for it.
    """
    declared = set(_dispatchable())
    assert declared == set(CAPABILITY_INPUTS), (
        "these workflows declare workflow_dispatch inputs and are not in CAPABILITY_INPUTS: "
        f"{sorted(declared - set(CAPABILITY_INPUTS))}; these are listed and no longer declare "
        f"any: {sorted(set(CAPABILITY_INPUTS) - declared)}"
    )
    for name, capabilities in CAPABILITY_INPUTS.items():
        unknown = capabilities - dispatch_inputs(_load(name))
        assert not unknown, f"{name} classifies inputs it does not declare: {sorted(unknown)}"


@pytest.mark.parametrize("name", sorted(CAPABILITY_INPUTS))
def test_no_committed_workflow_renders_a_capability(name: str) -> None:
    """Vacuous today, and deliberately so: nothing here declares a capability yet.

    It is not vacuous as a gate, because the classification above cannot stay empty by
    accident -- adding one is what the previous test forces a new workflow's author to do, and
    the moment they do, these three rules apply to that workflow without anybody remembering to
    wire them up.
    """
    document = _load(name)
    capabilities = CAPABILITY_INPUTS[name]
    assert top_level_leaks(document, capabilities) == []
    assert rendered_field_leaks(document, capabilities) == []
    assert printed_capability_leaks(document, capabilities) == []


@pytest.mark.parametrize("name", sorted(CAPABILITY_INPUTS))
def test_no_committed_workflow_interpolates_an_input_into_a_shell(name: str) -> None:
    """Every input, not only the capabilities. Known exceptions are recorded, not ignored."""
    unrecorded = [
        expression
        for expression in shell_interpolations(_load(name), dispatch_inputs(_load(name)))
        if (name, expression) not in SHELL_INTERPOLATION_NOT_YET_REPAIRED
    ]
    assert not unrecorded, (
        f"{name} substitutes {unrecorded} into a run: block. Pass the value through env: and "
        "read it as a shell variable, or record it in SHELL_INTERPOLATION_NOT_YET_REPAIRED with "
        "the reason it is still there."
    )


def test_every_recorded_shell_interpolation_is_still_there() -> None:
    """An allowlist that outlives its defect is a permanent hole in the rule.

    Each entry is re-derived from the file it names, so a repair fails this test and the entry
    has to be deleted in the same change. A stale exemption cannot sit here quietly exempting
    something nobody wrote.
    """
    for (name, expression), reason in SHELL_INTERPOLATION_NOT_YET_REPAIRED.items():
        document = _load(name)
        assert expression in shell_interpolations(document, dispatch_inputs(document)), (
            f"{name} no longer interpolates {expression} into a run: block. Delete this entry "
            f"from SHELL_INTERPOLATION_NOT_YET_REPAIRED; the reason recorded for it was: {reason}"
        )
