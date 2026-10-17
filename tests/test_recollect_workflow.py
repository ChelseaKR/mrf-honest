"""The scheduled re-collection workflow, held to what its header promises.

Parsed, not grepped: the header comment names the very tokens the promises forbid, so a text
search cannot tell a promise from its breach. Each test reads the structure where it is declared.

What matters most is that merging the file starts nothing. A schedule is a standing instruction
to retrieve files from other organizations' servers, and the project has not yet declared what it
promises to keep running (docs/adr/0010-scheduled-recollection.md), so the schedule must be gated
on a repository variable a maintainer sets deliberately.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "recollect.yml").read_text(encoding="utf-8")
DOCUMENT = cast(dict[str, Any], yaml.safe_load(TEXT))
JOB = cast(dict[str, Any], DOCUMENT["jobs"]["recollect"])
STEPS = cast(list[dict[str, Any]], JOB["steps"])


def _triggers() -> dict[str, Any]:
    return cast(dict[str, Any], DOCUMENT.get("on", DOCUMENT.get(True)))


def _step(name_start: str) -> dict[str, Any]:
    return next(step for step in STEPS if str(step.get("name", "")).startswith(name_start))


def test_the_schedule_is_registered_but_gated_on_a_variable_a_maintainer_sets() -> None:
    assert _triggers()["schedule"], "the workflow has no schedule"
    condition = str(JOB["if"])
    assert "vars.MRF_RECOLLECT_ENABLED == 'true'" in condition
    # A manual dispatch is an act by a person, so it is allowed to bypass the variable. Nothing
    # else is.
    assert condition.count("||") == 1
    assert "github.event_name == 'workflow_dispatch'" in condition


def test_the_default_cadence_is_no_more_often_than_monthly() -> None:
    [entry] = _triggers()["schedule"]
    minute, hour, day_of_month, month, day_of_week = str(entry["cron"]).split()
    assert day_of_month.isdigit(), "the cron must name one day of the month"
    assert month == "*" and day_of_week == "*"
    assert minute.isdigit() and hour.isdigit()
    assert minute not in {"0", "30"}, "a minute of its own, not a pile-up on the hour"


def test_the_workflow_grants_nothing_by_default_and_the_job_grants_only_what_writes() -> None:
    assert DOCUMENT["permissions"] == {}
    assert list(DOCUMENT["jobs"]) == ["recollect"]
    assert JOB["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "issues": "write",
    }


def test_the_checkout_holds_no_credential_and_runs_never_overlap() -> None:
    checkout = next(step for step in STEPS if "actions/checkout" in str(step.get("uses")))
    assert checkout["with"]["persist-credentials"] is False
    assert DOCUMENT["concurrency"]["cancel-in-progress"] is False


def test_the_token_never_reaches_a_step_that_talks_to_a_publisher() -> None:
    """Only the two steps that write to this repository may be given the token."""
    holders = {
        str(step.get("name"))
        for step in STEPS
        if "github.token" in str(step.get("env", {})) or "secrets." in str(step.get("env", {}))
    }
    assert holders == {
        "Open a pull request with the dated results",
        "Open, update or close the regression issue",
    }
    fetching = _step("Re-collect")
    assert "github.token" not in str(fetching) and "secrets." not in str(fetching)


def test_a_run_without_an_identifying_contact_refuses_before_it_fetches() -> None:
    run = str(_step("Re-collect")["run"])
    assert run.index('[ -z "${CONTACT}" ]') < run.index("mrf_honest recollect")
    assert "exit 1" in run.split("mrf_honest recollect")[0]


def test_no_dispatch_input_is_interpolated_into_a_script() -> None:
    for step in STEPS:
        assert "inputs." not in str(step.get("run", "")), step.get("name")
    assert _step("Re-collect")["env"]["MAX_FILES"] == "${{ inputs.max_files_per_publisher || '5' }}"


def test_pushing_to_the_default_branch_is_never_attempted() -> None:
    run = str(_step("Open a pull request")["run"])
    assert 'git push origin "${branch}"' in run
    assert not re.search(r"git push[^\n]*(master|main|DEFAULT_BRANCH)", run)
    assert "--force" not in run
    assert "MRF_RECOLLECT_OPEN_PR == 'true'" in str(_step("Open a pull request")["if"])


def test_every_action_is_pinned_to_a_commit() -> None:
    used = re.findall(r"uses: (\S+)", TEXT)
    assert used
    for action in used:
        assert re.search(r"@[0-9a-f]{40}$", action), f"{action} is not pinned to a commit sha"


def test_the_artifact_name_is_the_run_id_and_not_an_input() -> None:
    upload = next(step for step in STEPS if "upload-artifact" in str(step.get("uses")))
    assert upload["with"]["name"] == "recollection-${{ github.run_id }}"
