"""The detector that answers "is the published site the site this repository has?".

Written from both directions, because the failure it replaces was a green gate.
A detector that cannot fire is noise and gets deleted; a detector that reports a
number it did not really measure is worse than none, because the number reads as
a measurement and nobody re-derives it.

So the cases below cover the drift it must report AND every way the comparison
can be meaningless -- no deployment at all, a deployment that never succeeded, a
commit this clone does not contain, a history that has diverged. Each of those
ends in a refusal. None of them may end in a comfortable zero.

Two of these are about this repository in particular:

``test_a_pages_run_that_uploaded_nothing_is_not_a_deploy`` is the reason the
module reads the deployment record instead of ``pages.yml``'s run history. The
publisher here fires on every push to ``master`` -- the trigger is fine -- and the
failure being caught is a firing that produced nothing: red in the re-derivation
step, canceled, or evicted from its pending slot under
``concurrency: {group: pages, cancel-in-progress: false}``. Those runs exist in
the run list and create no deployment. A run-history sentinel would call each of
them a fresh publish.

``test_every_module_the_renderer_imports_is_on_the_visitor_visible_list`` is the
one that keeps the path list honest as the renderer grows. The list is the whole
verdict -- age alone never fires -- so a renderer that gains an import the list
does not know about becomes a blind spot with no symptom.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"


def _tool(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, not after: `@dataclass` resolves annotations
    # through `sys.modules[cls.__module__]`, so a module that is not there yet
    # raises on the decorator rather than on anything to do with this repository.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


staleness = _tool("deploy_staleness")

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
AUGUST = "2026-08-14T07:29:54Z"


def _deployment(**over: Any) -> dict[str, Any]:
    row = {
        "id": 6423920846,
        "sha": "c" * 40,
        "environment": "github-pages",
        "created_at": AUGUST,
    }
    row.update(over)
    return row


def _succeeded(_id: Any) -> list[Mapping[str, Any]]:
    return [{"state": "success"}]


def _never_succeeded(_id: Any) -> list[Mapping[str, Any]]:
    # The live shape, newest first: a Pages deployment passes through waiting,
    # queued and in_progress before it is anything at all.
    return [{"state": "in_progress"}, {"state": "queued"}, {"state": "waiting"}]


# --- what the deployment record is allowed to mean --------------------------


def test_the_newest_successful_deployment_is_the_live_build() -> None:
    record = staleness.newest_successful_deployment([_deployment()], _succeeded)
    assert record.sha == "c" * 40
    assert record.created_at.date().isoformat() == "2026-08-14"
    assert record.deployment_id == 6423920846


def test_the_newest_deployment_wins_over_an_older_one() -> None:
    newer = _deployment(id=2, sha="d" * 40, created_at="2026-09-13T16:44:23Z")
    record = staleness.newest_successful_deployment([_deployment(), newer], _succeeded)
    assert record.sha == "d" * 40


def test_a_pages_run_that_uploaded_nothing_is_not_a_deploy() -> None:
    """The trap this module exists for, and the reason it reads deployments.

    `pages.yml` fires on every push to `master`. A push whose run is canceled,
    goes red in the re-derivation step, or loses its pending slot to the next
    push leaves a finished run in the run list and creates no deployment. The
    deployment list is therefore still the August one and the answer stays the
    August one. Asserted directly, because the bug would be a silent extra row,
    not an exception.
    """
    evicted_runs_create_no_deployments: list[Mapping[str, Any]] = [_deployment()]
    record = staleness.newest_successful_deployment(evicted_runs_create_no_deployments, _succeeded)
    assert record.created_at.date().isoformat() == "2026-08-14"


def test_no_deployment_at_all_is_a_refusal_not_a_zero() -> None:
    with pytest.raises(staleness.StalenessUnknown, match="no github-pages deployment"):
        staleness.newest_successful_deployment([], _succeeded)


def test_a_deployment_that_never_succeeded_is_a_refusal() -> None:
    with pytest.raises(staleness.StalenessUnknown, match="successful status"):
        staleness.newest_successful_deployment([_deployment()], _never_succeeded)


def test_a_failed_newer_deployment_does_not_hide_the_successful_older_one() -> None:
    """A failed republish leaves the previous build serving; that is the live one."""
    failed = _deployment(id=9, sha="e" * 40, created_at="2026-09-12T00:00:00Z")

    def statuses(deployment_id: Any) -> list[Mapping[str, Any]]:
        return [{"state": "failure"}] if deployment_id == 9 else [{"state": "success"}]

    record = staleness.newest_successful_deployment([_deployment(), failed], statuses)
    assert record.sha == "c" * 40


def test_a_row_without_a_commit_id_is_not_a_deployment() -> None:
    with pytest.raises(staleness.StalenessUnknown, match="no github-pages deployment"):
        staleness.newest_successful_deployment([_deployment(sha="not-a-sha")], _succeeded)


def test_a_deployment_for_another_environment_is_not_the_site() -> None:
    other = _deployment(
        id=3, sha="f" * 40, created_at="2026-09-12T00:00:00Z", environment="staging"
    )
    record = staleness.newest_successful_deployment([_deployment(), other], _succeeded)
    assert record.sha == "c" * 40


# --- which files change what a visitor receives -----------------------------


@pytest.mark.parametrize(
    "path",
    [
        "src/mrf_honest/site.py",
        "src/mrf_honest/dataset.py",
        "src/mrf_honest/receipt.py",
        "src/mrf_honest/cohort.py",
        "src/mrf_honest/inspect.py",
        "src/mrf_honest/inspect_csv.py",
        "src/mrf_honest/scorecard.py",
        "src/mrf_honest/cli.py",
        "src/mrf_honest/__init__.py",
        "data/cohorts/2026-09-12.comparison.json",
        "data/cohorts/2026-09-12.ingest/cedars-sinai__cedars-sinai-medical-center.json",
        "assets/social-card.png",
        ".github/workflows/pages.yml",
    ],
)
def test_the_renders_inputs_ship_to_visitors(path: str) -> None:
    assert staleness.ships_to_visitors(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/mrf_honest/fetch.py",
        "src/mrf_honest/politeness.py",
        "src/mrf_honest/discover.py",
        "src/mrf_honest/lakehouse.py",
        "src/mrf_honest/mcp.py",
        "src/mrf_honest/ai/narrate.py",
        "tests/test_deploy_staleness.py",
        "tools/deploy_staleness.py",
        "docs/ROADMAP.md",
        "perf/baseline.json",
        "README.md",
        ".github/workflows/ci.yml",
    ],
)
def test_everything_else_does_not(path: str) -> None:
    assert not staleness.ships_to_visitors(path)


def test_the_publisher_is_site_source_and_the_other_workflows_are_not() -> None:
    """`pages.yml` chooses which cohort renders and passes the origin, so it ships.

    `ci.yml` and `accessibility.yml` measure a build they perform themselves and
    change no published byte. Both directions are asserted, because a list that
    swallowed `.github/workflows/` whole would pass the first half alone and
    would then count every CI edit as a visitor-visible change.
    """
    assert staleness.ships_to_visitors(".github/workflows/pages.yml")
    assert not staleness.ships_to_visitors(".github/workflows/accessibility.yml")
    assert not staleness.ships_to_visitors(".github/workflows/deploy-staleness.yml")


def test_every_module_the_renderer_imports_is_on_the_visitor_visible_list() -> None:
    """The list is the whole verdict, so a renderer import it does not know is a blind spot.

    `render_site` executes whatever `src/mrf_honest/site.py` imports from this
    package, and a change in any of those modules changes the bytes a visitor
    receives. Nothing else would notice the list going stale: age alone never
    fires, so a missing entry produces no symptom at all -- just a sentinel that
    quietly stops counting one kind of change.
    """
    source = (REPO_ROOT / "src" / "mrf_honest" / "site.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^from mrf_honest\.([A-Za-z0-9_.]+) import", source, re.MULTILINE))
    assert imported, "site.py imports nothing from mrf_honest; this check has stopped checking"
    for module in sorted(imported):
        path = f"src/mrf_honest/{module.replace('.', '/')}.py"
        assert staleness.ships_to_visitors(path), (
            f"site.py imports {module}, so a change there changes a published page, but "
            f"{path} is not in SITE_SOURCE_PREFIXES"
        )


# --- the comparison against master, and every way it can be meaningless -----


def _git_env() -> dict[str, str]:
    """A hermetic git for the fixture repositories below.

    The global configuration on a workstation carries a signing key, a hooks
    path and a default branch name, none of which exist on the runner. Reading
    them would make these tests pass or fail for reasons that have nothing to do
    with the module under test.
    """
    environment = dict(os.environ)
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    return environment


def _git(root: Path, *args: str) -> str:
    """One place the fixtures shell out to git, so one place carries the waiver."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        ["git", "-C", str(root), *args],  # noqa: S607 - git comes from PATH on every runner
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "clone"
    root.mkdir()
    # `master`, as this repository's default branch is.
    _git(root, "init", "-b", "master")
    _git(root, "config", "user.email", "sentinel@example.test")
    _git(root, "config", "user.name", "sentinel")
    return root


def _commit(root: Path, path: str, body: str = "x") -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "-m", f"touch {path}")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _repo(tmp_path)
    monkeypatch.setattr(staleness, "REPO_ROOT", root)
    return root


def _record(sha: str, created_at: datetime) -> Any:
    return staleness.DeployRecord(deployment_id=1, sha=sha, created_at=created_at)


def test_it_counts_the_commits_and_names_the_visitor_visible_ones(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "src/mrf_honest/fetch.py")
    _commit(clone, "src/mrf_honest/site.py")
    _commit(clone, "data/cohorts/2026-09-12.comparison.json")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=30)), "HEAD", NOW)

    assert drift.commits == 3
    assert drift.visitor_commits == 2
    assert drift.days == 30
    assert drift.overdue


def test_age_alone_is_not_overdue(clone: Path) -> None:
    """A site nobody republished because nothing it publishes changed is correct.

    Collection, narration and warehouse work land here constantly and reach a
    visitor only through a committed comparison document. Reporting on age alone
    would fire on every one of them, and a sentinel that always fires is one
    nobody reads.
    """
    deployed = _commit(clone, "README.md")
    _commit(clone, "src/mrf_honest/politeness.py")
    _commit(clone, "docs/ROADMAP.md")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=200)), "HEAD", NOW)

    assert drift.commits == 2
    assert drift.visitor_commits == 0
    assert not drift.overdue
    assert "Up to date" in staleness.render(drift)


def test_inside_the_threshold_is_not_overdue(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "src/mrf_honest/site.py")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=3)), "HEAD", NOW)

    assert drift.visitor_commits == 1
    assert not drift.overdue
    assert "Waiting" in staleness.render(drift)


def test_nothing_since_the_deploy_is_up_to_date(clone: Path) -> None:
    deployed = _commit(clone, "data/cohorts/2026-09-12.comparison.json")

    drift = staleness.measure(_record(deployed, NOW - timedelta(days=1)), "HEAD", NOW)

    assert drift.commits == 0
    assert drift.visitor_commits == 0
    assert not drift.overdue


def test_a_commit_this_clone_does_not_have_is_a_refusal(clone: Path) -> None:
    """The shallow-checkout case, which is the one that reports zero silently.

    `git log <absent>..HEAD` on a shallow clone lists nothing, so the site reads
    as current. This is why the sentinel checks out with `fetch-depth: 0`, and
    why the refusal exists rather than trusting that it did.

    What this pins is the *message*, and deliberately so. Deleting the
    absent-commit check does not produce a zero -- `git merge-base` fails on the
    same input and the module refuses anyway -- so the property under test is
    that the refusal names the shallow checkout instead of quoting git at a
    reader who then has to work out what a bad commit name has to do with
    deployment. Reported as what it is rather than as a claim that the guard is
    all that stands between here and a wrong number.
    """
    _commit(clone, "README.md")

    with pytest.raises(staleness.StalenessUnknown, match="not in this clone"):
        staleness.measure(_record("a" * 40, NOW - timedelta(days=30)), "HEAD", NOW)


def test_a_diverged_history_is_a_refusal(clone: Path) -> None:
    _commit(clone, "README.md")
    _git(clone, "checkout", "-b", "other")
    orphan = _commit(clone, "orphan.txt")
    _git(clone, "checkout", "master")

    with pytest.raises(staleness.StalenessUnknown, match="not an ancestor"):
        staleness.measure(_record(orphan, NOW - timedelta(days=30)), "HEAD", NOW)


def test_a_malformed_deployed_sha_is_a_refusal(clone: Path) -> None:
    _commit(clone, "README.md")

    with pytest.raises(staleness.StalenessUnknown, match="not a commit id"):
        staleness.measure(_record("nope", NOW), "HEAD", NOW)


def test_a_head_ref_that_does_not_exist_is_a_refusal(clone: Path) -> None:
    """`origin/master` is the default; a repository whose default branch was

    renamed, or a checkout without the remote-tracking ref, must refuse rather
    than resolve to something else.
    """
    deployed = _commit(clone, "README.md")

    with pytest.raises(staleness.StalenessUnknown, match="git rev-parse"):
        staleness.measure(_record(deployed, NOW), "origin/main", NOW)


# --- the report, and the exit code ------------------------------------------


def test_the_report_states_the_measurement_before_its_verdict(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "src/mrf_honest/site.py")
    drift = staleness.measure(_record(deployed, NOW - timedelta(days=30)), "HEAD", NOW)

    report = staleness.render(drift)

    assert deployed[:9] in report
    assert "30 days" in report
    assert "OVERDUE" in report
    assert report.index("Behind by") < report.index("OVERDUE")
    assert "master:" in report


def test_the_json_carries_every_number_the_report_states(clone: Path) -> None:
    deployed = _commit(clone, "README.md")
    _commit(clone, "data/cohorts/2026-09-12.comparison.json")
    drift = staleness.measure(_record(deployed, NOW - timedelta(days=30)), "HEAD", NOW)

    payload = staleness.as_json(drift)

    assert payload["deployed_sha"] == deployed
    assert payload["days"] == 30
    assert payload["commits"] == 1
    assert payload["visitor_commits"] == 1
    assert payload["overdue"] is True


def _payload(sha: str) -> str:
    """One successful github-pages deployment, in the shape the API returns."""
    return json.dumps(
        {
            "deployments": [
                {
                    "id": 7,
                    "sha": sha,
                    "environment": "github-pages",
                    "created_at": "2026-09-12T00:00:00Z",
                }
            ],
            "statuses": {"7": [{"state": "success"}]},
        }
    )


def test_the_cli_refuses_with_a_nonzero_exit_when_it_cannot_measure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2, not 0 with a reassuring report. The workflow turns a measurement

    into an issue and a refusal into a red run, so this exit code is the whole
    difference between "the site is fine" and "nobody can tell".
    """
    payload = tmp_path / "deployments.json"
    payload.write_text('{"deployments": [], "statuses": {}}', encoding="utf-8")

    code = staleness.main(["--deployments-json", str(payload)])

    assert code == 2
    assert "cannot measure" in capsys.readouterr().err


def test_the_cli_reports_a_real_measurement_and_exits_zero(
    clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deployed = _commit(clone, "src/mrf_honest/site.py")
    payload = tmp_path / "deployments.json"
    payload.write_text(_payload(deployed), encoding="utf-8")

    code = staleness.main(["--deployments-json", str(payload), "--head", "HEAD"])

    assert code == 0
    assert "Up to date" in capsys.readouterr().out


def test_the_cli_writes_the_workflows_outputs(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`measured` and `overdue` are what the workflow branches on, so they are gated.

    A refusal that reached the reporting step as `measured=true` would be filed
    as a clean bill of health for a comparison that never happened.
    """
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    payload = tmp_path / "deployments.json"
    payload.write_text('{"deployments": [], "statuses": {}}', encoding="utf-8")

    assert staleness.main(["--deployments-json", str(payload)]) == 2
    assert "measured=false" in output.read_text(encoding="utf-8")

    deployed = _commit(clone, "src/mrf_honest/site.py")
    payload.write_text(_payload(deployed), encoding="utf-8")

    assert staleness.main(["--deployments-json", str(payload), "--head", "HEAD"]) == 0
    written = output.read_text(encoding="utf-8")
    assert "measured=true" in written
    assert "overdue=false" in written


# --- the workflow's own promises --------------------------------------------

WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "deploy-staleness.yml").read_text(
    encoding="utf-8"
)


def test_the_sentinel_holds_no_permission_that_could_publish() -> None:
    """It reports on publishing; it must not be able to do any.

    Parsed rather than grepped. The file's own header says the words
    ``pages: write`` and ``id-token: write`` in the sentence promising it holds
    neither, so a text search for them cannot tell the promise from its breach --
    it either passes on prose or fails on it. The grants are read where they are
    declared, and the job's set is compared whole, so a fourth permission
    appearing is a failure and not a silence.
    """
    document = yaml.safe_load(WORKFLOW)
    assert document["permissions"] == {}, "the workflow level must grant nothing"
    jobs = document["jobs"]
    assert list(jobs) == ["report"], "a second job would carry permissions nothing here checks"
    assert jobs["report"]["permissions"] == {
        "contents": "read",
        "deployments": "read",
        "issues": "write",
    }


def test_the_sentinel_checks_out_the_whole_history() -> None:
    """`fetch-depth: 0` is load-bearing: without it the module has to refuse."""
    assert "fetch-depth: 0" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW


def test_the_sentinel_never_cancels_itself_mid_write() -> None:
    assert "cancel-in-progress: false" in WORKFLOW
    assert "timeout-minutes:" in WORKFLOW


def test_the_sentinel_measures_against_master() -> None:
    """This repository's default branch is `master`. A sentinel pointed at

    `origin/main` would refuse every week -- a permanent red that says nothing
    about the site.
    """
    assert "--head origin/master" in WORKFLOW
    assert staleness.DEFAULT_HEAD == "origin/master"
    assert staleness.DEFAULT_REPO == "ChelseaKR/mrf-honest"


def test_every_action_the_sentinel_uses_is_pinned_to_a_commit() -> None:
    used = re.findall(r"uses: (\S+)", WORKFLOW)
    assert used, "the sentinel workflow uses no actions; this check has stopped checking"
    for action in used:
        assert re.search(r"@[0-9a-f]{40}$", action), f"{action} is not pinned to a commit sha"
