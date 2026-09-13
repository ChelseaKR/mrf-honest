#!/usr/bin/env python3
"""Is the site at chelseakr.github.io/mrf-honest the site this repository has?

Nothing in this repository has ever asked. ``live-integrity.yml`` does not exist
here, no scheduled job reaches the published URL, and every existing gate runs
against the working tree: ``make verify`` re-derives the committed comparisons,
``accessibility.yml`` renders the site and audits the render, and ``pages.yml``
checks the bytes it is about to upload. All of them measure a build. None of
them measures the *published* build.

The publisher is not the problem and this module does not change it.
``pages.yml`` fires on every push to ``master`` with no path filter, plus
``workflow_dispatch``; the trigger is right. What is missing is anyone noticing
when a firing produced nothing. A run that fails in the re-derivation step, is
cancelled, or loses its pending slot to the next push under
``concurrency: {group: pages, cancel-in-progress: false}`` leaves the site on
the previous commit with every gate in this repository green, because the gates
are green -- the tree is fine. It is the deploy that did not happen.

So this measures the distance between the commit the live site was built from
and ``origin/master``. It publishes nothing, holds no ``pages: write``, no
``id-token: write`` and no credential that could, and it does not dispatch
``pages.yml`` or any other workflow.

Which publishing model this assumes
-----------------------------------
Model 1 of the four: **Pages built by a workflow from source**. ``gh api
repos/ChelseaKR/mrf-honest/pages`` reports ``build_type: workflow`` with source
branch ``master``, path ``/``. The repository commits no built site -- ``site/``
is produced on the runner by ``mrf-honest site`` and uploaded as an artifact --
so there is no published subtree to compare and the deployed *commit* is the
right handle: the source tree at that commit is what built the bytes. Comparing
``git rev-parse <deployed>:<path>`` against head, which is the correct measure
for a committed-tree publisher, has nothing to read here.

The comparison is therefore: deployed SHA versus ``origin/master``, counting the
commits in between and, separately, how many of those touched a path that
changes what a visitor receives.

Why the deployment record and not ``pages.yml``'s run history
-------------------------------------------------------------
A workflow run is an attempt. A ``github-pages`` deployment exists only because
bytes were uploaded and accepted, and it names the commit they were built from.
The failure this module exists to catch is precisely a run that finished without
publishing -- cancelled, evicted from its concurrency pending slot, or red in the
build job -- and run history is the one source that cannot tell those apart from
a deploy. Counting runs would report the site as fresh on the strength of the
run that failed to update it.

A deployment row is still only a *request* to publish, so its newest status must
read ``success`` before its commit may be treated as live. A failed republish
leaves the previous build serving; that older successful deployment is the live
one, and reporting the failed newer commit would make the site look fresher than
it is, which is the exact direction of error this file exists to prevent.

What counts as a change a visitor would receive
-----------------------------------------------
Age alone is never the verdict. A site nobody republished because nothing it
publishes changed is correct, not stale, so the threshold applies only to
commits that touched a path the published bytes are a function of.

``pages.yml`` renders with ``python -m mrf_honest.cli site --comparison <the
newest committed comparison of each profile> --out site --origin
https://chelseakr.github.io/mrf-honest``. That render reads exactly three things
from this repository: the comparison documents under ``data/cohorts/``, the
Open Graph card at ``assets/social-card.png`` (``site.SOCIAL_CARD_SOURCE``,
copied into the output), and the modules it executes. Hence:

* ``src/mrf_honest/site.py`` -- the renderer itself.
* ``src/mrf_honest/dataset.py`` -- ``dataset.csv``, its Table Schema and the JSON
  API, written by the same render and served from the same site.
* ``src/mrf_honest/receipt.py`` -- the per-file receipts the pages link to.
* ``src/mrf_honest/cohort.py`` -- the grade and ingest-status vocabulary
  (``NOT_GRADED``, ``INGEST_REFUSED``, ``INGEST_CONTRACT_FAILED``,
  ``LOCAL_DIMENSIONS``) that ``site.py`` prints onto every page.
* ``src/mrf_honest/inspect.py``, ``src/mrf_honest/inspect_csv.py``,
  ``src/mrf_honest/scorecard.py`` -- the three finding catalogues whose text is
  rendered onto the file pages and into ``how-we-grade``.
* ``src/mrf_honest/cli.py`` -- the ``site`` subcommand: the origin, the output
  layout, which documents are handed to the renderer.
* ``src/mrf_honest/__init__.py`` -- ``__version__``, stamped into every
  published receipt by ``receipt.receipts_for``.
* ``data/cohorts/`` -- the content. The whole directory rather than
  ``*.comparison.json`` alone: the assessments, manifest and ingest evidence are
  committed together with the comparison they derive, and ``pages.yml`` re-derives
  every comparison from them byte-for-byte before rendering, so a change to one
  without the other does not publish a different site -- it fails the publish.
* ``assets/social-card.png`` -- copied into the render verbatim.
* ``.github/workflows/pages.yml`` -- the publisher is itself site source here. It
  chooses which cohort renders for each profile, passes the origin, and carries
  the canonical/card and export checks. A change to it changes what ships.

Everything else in ``src/mrf_honest`` is deliberately off the list: ``fetch``,
``politeness``, ``discover``, ``registry``, ``stream``, ``normalize``,
``statistics``, ``container``, ``gate``, ``diff``, ``census``, ``systems``,
``lakehouse``, ``mcp``, ``models``, ``types``, ``contracts`` and the whole ``ai``
package collect, derive or narrate. They change ``master`` constantly without
changing a published byte, because their output reaches a visitor only through a
committed comparison document -- which is on the list -- and any change to a
derivation that was *not* accompanied by regenerated data fails ``pages.yml``'s
byte-for-byte re-derivation step rather than silently altering the site. Counting
them would make the number meaningless well before it made it alarming.
``tests/``, ``docs/``, ``perf/`` and ``tools/`` likewise: the render never reads
them.

The rule this file follows
--------------------------
A detector that cannot tell must refuse, never report a comfortable zero. Every
unmeasurable case below raises `StalenessUnknown` and exits non-zero rather than
returning a number that would read as a measurement.

Standard library only, and it imports nothing from ``mrf_honest``, so it runs on
a bare ``python3`` with no dependency resolution and cannot be broken by one --
the same constraint ADR 0002 places on the graded path, for a related reason.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_REPO = "ChelseaKR/mrf-honest"

#: This repository's default branch is ``master``, not ``main``. Every default
#: here says so; a sentinel pointed at a ref that does not exist refuses, which
#: is survivable, but one pointed at a stale ref that does would not.
DEFAULT_HEAD = "origin/master"

#: The Pages environment a deployment has to belong to. A repository can carry
#: deployments for other environments; only this one is the published site.
PAGES_ENVIRONMENT = "github-pages"

#: How long an unpublished visitor-visible commit may wait before this reports.
#: Generous on purpose: it exists to catch a publish that silently stopped
#: landing, not to complain about an afternoon.
DEFAULT_MAX_AGE_DAYS = 14

#: Paths whose change alters what a visitor receives. Derived from what
#: ``pages.yml``'s render actually executes and reads; the module docstring
#: gives the derivation and says why everything else is excluded. A trailing
#: slash means "this directory"; anything else is an exact path.
SITE_SOURCE_PREFIXES = (
    "src/mrf_honest/__init__.py",
    "src/mrf_honest/cli.py",
    "src/mrf_honest/cohort.py",
    "src/mrf_honest/dataset.py",
    "src/mrf_honest/inspect.py",
    "src/mrf_honest/inspect_csv.py",
    "src/mrf_honest/receipt.py",
    "src/mrf_honest/scorecard.py",
    "src/mrf_honest/site.py",
    "data/cohorts/",
    "assets/social-card.png",
    ".github/workflows/pages.yml",
)

_SHA = re.compile(r"^[0-9a-f]{40}$")

#: Called with a deployment id; returns that deployment's statuses, newest first.
StatusesFor = Callable[[Any], Sequence[Mapping[str, Any]]]


class StalenessUnknown(Exception):
    """The comparison could not be made, so no number is reported.

    Raised in preference to returning zero anywhere the inputs do not support a
    measurement. The caller turns this into a red run: a sentinel that cannot
    tell is a broken sentinel, and it has to look broken.
    """


@dataclass(frozen=True)
class DeployRecord:
    """The published build: which commit it came from, and when it went out."""

    deployment_id: int
    sha: str
    created_at: datetime


@dataclass(frozen=True)
class Drift:
    """How far the published build is behind ``master``."""

    deployed: DeployRecord
    head: str
    days: int
    commits: int
    visitor_commits: int
    max_age_days: int

    @property
    def overdue(self) -> bool:
        """Report only when something a visitor would receive is waiting.

        Age alone is not the signal. This repository can go a fortnight with
        nothing but collection and narration work landing, and a site that was
        not republished across it is correct, not stale.
        """
        return self.visitor_commits > 0 and self.days > self.max_age_days


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def newest_successful_deployment(
    deployments: Iterable[Mapping[str, Any]],
    statuses_for: StatusesFor,
) -> DeployRecord:
    """The most recent ``github-pages`` deployment that actually published.

    A deployment row is a *request* to publish; its statuses are what say
    whether bytes landed. A deployment whose newest status is ``failure``,
    ``error``, ``in_progress`` or ``queued`` never became a site, and treating
    its commit as the live one would report the site as fresher than it is.
    """
    candidates = [
        deployment
        for deployment in deployments
        if deployment.get("environment") in (None, PAGES_ENVIRONMENT)
        and _SHA.match(str(deployment.get("sha", "")))
    ]
    if not candidates:
        raise StalenessUnknown(
            "no github-pages deployment in this repository's history: there is no published "
            "build to compare master against"
        )
    candidates.sort(key=lambda d: _parse_timestamp(str(d["created_at"])), reverse=True)

    for deployment in candidates:
        states = [str(status.get("state", "")) for status in statuses_for(deployment["id"])]
        if states and states[0] == "success":
            return DeployRecord(
                deployment_id=int(deployment["id"]),
                sha=str(deployment["sha"]),
                created_at=_parse_timestamp(str(deployment["created_at"])),
            )

    raise StalenessUnknown(
        f"none of the {len(candidates)} github-pages deployment(s) reports a successful status: "
        "nothing here proves any build was ever published"
    )


def ships_to_visitors(path: str) -> bool:
    """Does changing this file change what the published site shows?"""
    return any(
        path.startswith(prefix) if prefix.endswith("/") else path == prefix
        for prefix in SITE_SOURCE_PREFIXES
    )


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    # S603/S607: a fixed argument vector with no shell, and `git` is resolved
    # from PATH because this runs on a hosted runner and on a workstation where
    # it lives in different places. Nothing here interpolates user input into a
    # command; the one caller-supplied value is a ref name passed as its own
    # argv element.
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(REPO_ROOT), *args],  # noqa: S607 - git comes from PATH
        capture_output=True,
        text=True,
        check=False,
    )


def _git(*args: str) -> str:
    result = _run_git(*args)
    if result.returncode != 0:
        raise StalenessUnknown(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _has_commit(sha: str) -> bool:
    """Whether this clone contains the commit, without raising on absence.

    ``git cat-file`` exits non-zero for a commit that is simply not here, which
    is the ordinary shallow-clone case and not a git failure. Routing it through
    `_git` would report it as one, and the refusal the caller raises -- the one
    that names the shallow checkout and says why a zero would be wrong -- would
    never be reached.
    """
    return _run_git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def require_comparable(deployed_sha: str, head: str) -> None:
    """Refuse unless this clone can place the deployed commit on ``master``.

    Both failures below report zero drift if they are not caught, and both are
    ordinary. A shallow checkout does not contain a commit from a month ago, so
    ``git log <deployed>..HEAD`` lists nothing and the site reads as up to date
    -- which is why the sentinel workflow checks out with ``fetch-depth: 0`` and
    why this refuses rather than trusting that it did. A force-push or a rebase
    leaves the deployed commit off ``master`` entirely, where "commits since" is
    not a question with an answer.
    """
    if not _SHA.match(deployed_sha):
        raise StalenessUnknown(f"deployed commit {deployed_sha!r} is not a commit id")
    if not _has_commit(deployed_sha):
        raise StalenessUnknown(
            f"deployed commit {deployed_sha[:9]} is not in this clone: the checkout is shallow, "
            "and a comparison against a history that does not reach the published build would "
            "report no drift at all"
        )
    merge_base = _git("merge-base", deployed_sha, head)
    if merge_base != _git("rev-parse", deployed_sha):
        raise StalenessUnknown(
            f"deployed commit {deployed_sha[:9]} is not an ancestor of {head}: the history has "
            "diverged and 'commits since the deploy' has no answer"
        )


def commits_between(deployed_sha: str, head: str) -> list[tuple[str, list[str]]]:
    """Each commit after the deployed one, with the paths it touched."""
    raw = _git("log", "--format=%x00%H", "--name-only", f"{deployed_sha}..{head}")
    commits: list[tuple[str, list[str]]] = []
    for block in raw.split("\x00"):
        stripped = block.strip("\n")
        if not stripped:
            continue
        lines = [line for line in stripped.splitlines() if line.strip()]
        commits.append((lines[0], lines[1:]))
    return commits


def measure(
    deployed: DeployRecord,
    head: str,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> Drift:
    """Place the published build against ``master``, or refuse."""
    head_sha = _git("rev-parse", head)
    require_comparable(deployed.sha, head_sha)
    commits = commits_between(deployed.sha, head_sha)
    visitor = [commit for commit in commits if any(ships_to_visitors(p) for p in commit[1])]
    return Drift(
        deployed=deployed,
        head=head_sha,
        days=(now - deployed.created_at).days,
        commits=len(commits),
        visitor_commits=len(visitor),
        max_age_days=max_age_days,
    )


def render(drift: Drift) -> str:
    """The report. States the measurement before its verdict, always."""
    lines = [
        f"Published build:  {drift.deployed.sha[:9]}  "
        f"({drift.deployed.created_at.date().isoformat()}, "
        f"deployment {drift.deployed.deployment_id})",
        f"master:           {drift.head[:9]}",
        f"Behind by:        {drift.days} days, {drift.commits} commits, "
        f"{drift.visitor_commits} of them changing what a visitor receives",
    ]
    if drift.overdue:
        lines.append(
            f"\nOVERDUE: {drift.visitor_commits} visitor-visible commit(s) have waited "
            f"{drift.days} days, past the {drift.max_age_days}-day threshold. "
            "The live site is not what this repository says it is."
        )
    elif drift.visitor_commits:
        lines.append(
            f"\nWaiting: {drift.visitor_commits} visitor-visible commit(s), "
            f"{drift.days} days, within the {drift.max_age_days}-day threshold."
        )
    else:
        lines.append("\nUp to date: nothing published has changed since the live build.")
    return "\n".join(lines)


def as_json(drift: Drift) -> dict[str, Any]:
    return {
        "deployed_sha": drift.deployed.sha,
        "deployed_at": drift.deployed.created_at.isoformat(),
        "deployment_id": drift.deployed.deployment_id,
        "head": drift.head,
        "days": drift.days,
        "commits": drift.commits,
        "visitor_commits": drift.visitor_commits,
        "overdue": drift.overdue,
    }


def _gh(path: str) -> Any:
    """Read the API through ``gh``, which the runner already authenticates."""
    # S603/S607: same shape as `_run_git` -- fixed argv, no shell, `gh` from
    # PATH. `path` is built from the `--repo` argument and a numeric deployment
    # id and is passed as one argv element, so it is a URL path, never a command.
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["gh", "api", path],  # noqa: S607 - gh comes from PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise StalenessUnknown(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _from_file(payload_path: Path) -> tuple[list[Mapping[str, Any]], StatusesFor]:
    """Deployments and statuses from a file, for offline use and for the tests."""
    payload = cast(Mapping[str, Any], json.loads(payload_path.read_text(encoding="utf-8")))
    deployments = cast(list[Mapping[str, Any]], payload["deployments"])
    statuses = cast(Mapping[str, Sequence[Mapping[str, Any]]], payload["statuses"])

    def statuses_for(deployment_id: Any) -> Sequence[Mapping[str, Any]]:
        return statuses.get(str(deployment_id), [])

    return deployments, statuses_for


def _from_api(repo: str) -> tuple[list[Mapping[str, Any]], StatusesFor]:
    deployments = cast(
        list[Mapping[str, Any]],
        _gh(f"repos/{repo}/deployments?environment={PAGES_ENVIRONMENT}&per_page=20"),
    )

    def statuses_for(deployment_id: Any) -> Sequence[Mapping[str, Any]]:
        return cast(
            list[Mapping[str, Any]],
            _gh(f"repos/{repo}/deployments/{deployment_id}/statuses?per_page=10"),
        )

    return deployments, statuses_for


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="How far behind master is the published site?")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--head", default=DEFAULT_HEAD)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--json", action="store_true", help="emit the measurement as JSON")
    parser.add_argument(
        "--deployments-json",
        type=Path,
        help="read deployments from a file instead of the API (offline use and tests)",
    )
    args = parser.parse_args(argv)

    try:
        if args.deployments_json is not None:
            deployments, statuses_for = _from_file(cast(Path, args.deployments_json))
        else:
            deployments, statuses_for = _from_api(str(args.repo))
        deployed = newest_successful_deployment(deployments, statuses_for)
        drift = measure(deployed, str(args.head), datetime.now(UTC), int(args.max_age_days))
    except StalenessUnknown as exc:
        print(f"cannot measure deploy staleness: {exc}", file=sys.stderr)
        _write_github_output(None, str(exc))
        return 2

    print(json.dumps(as_json(drift), indent=2) if args.json else render(drift))
    _write_github_output(drift, None)
    return 0


def _write_github_output(drift: Drift | None, error: str | None) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        if drift is None:
            handle.write("measured=false\n")
            handle.write(f"error={error or 'unknown'}\n")
        else:
            handle.write("measured=true\n")
            handle.write(f"overdue={str(drift.overdue).lower()}\n")
            handle.write(f"days={drift.days}\n")
            handle.write(f"commits={drift.commits}\n")
            handle.write(f"visitor_commits={drift.visitor_commits}\n")
            handle.write(f"deployed_sha={drift.deployed.sha}\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
