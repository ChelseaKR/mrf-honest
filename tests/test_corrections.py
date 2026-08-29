"""The corrections page, and the commits it cites.

`docs/CORRECTIONS.md` is the standing answer to "how would I know if you got something wrong
about me", so it carries a list of what has already gone wrong, each entry naming the commit
that fixed it. A citation that does not resolve, or a promise the site does not actually make,
would turn that page into exactly the reassurance it was written to replace.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from mrf_honest.site import CORRECTIONS_URL

ROOT = Path(__file__).resolve().parent.parent
CORRECTIONS = ROOT / "docs" / "CORRECTIONS.md"
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
TEXT = CORRECTIONS.read_text(encoding="utf-8")

CITED_COMMITS = sorted(set(re.findall(r"commit `([0-9a-f]{7,40})`", TEXT)))


def test_the_page_cites_commits_at_all() -> None:
    """An empty parametrisation below would make every case vacuous."""

    assert len(CITED_COMMITS) >= 6, CITED_COMMITS


def test_the_history_needed_to_check_the_citations_is_present() -> None:
    """A shallow clone fails this loudly rather than letting the citation checks skip.

    CI's default checkout fetches one commit, and every citation below then fails to resolve
    for a reason that has nothing to do with whether the citation is good. Skipping on a
    shallow clone would be worse: the checks would be green in the one place they run
    automatically, which is precisely the shape of guardrail this project exists to avoid.
    """

    shallow = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert shallow.stdout.strip() == "false", (
        "this is a shallow clone, so the commits docs/CORRECTIONS.md cites cannot be resolved. "
        "Check out with fetch-depth: 0."
    )


@pytest.mark.parametrize("sha", CITED_COMMITS)
def test_every_cited_commit_resolves_in_this_repository(sha: str) -> None:
    """A citation nobody can follow is a claim, not evidence."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(ROOT), "cat-file", "-t", sha],  # noqa: S607 - git is the tool
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{sha} does not resolve: {result.stderr.strip()}"
    assert result.stdout.strip() == "commit"


@pytest.mark.parametrize("sha", CITED_COMMITS)
def test_every_cited_commit_is_reachable_from_the_default_branch(sha: str) -> None:
    """A commit that only exists on a branch would vanish for a reader who cloned master."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{sha} is not an ancestor of HEAD"


def test_the_page_promises_a_removal_needs_no_proof() -> None:
    """This is the load-bearing sentence. If it goes, the page is a complaints form."""

    assert "You do not have to prove anything." in TEXT
    assert "honoured on request" in TEXT


def test_the_page_names_the_document_it_points_readers_at() -> None:
    for target in ("SECURITY.md", "docs/findings/", "politeness.py"):
        assert target.split("/")[-1] in TEXT


class TestIssueTemplates:
    def test_both_forms_exist_and_parse(self) -> None:
        for name in ("correction.yml", "removal.yml", "config.yml"):
            document = yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))
            assert document

    def test_the_removal_form_asks_for_one_thing(self) -> None:
        """A removal form with a required 'reason' field would contradict the page."""

        document = yaml.safe_load((TEMPLATES / "removal.yml").read_text(encoding="utf-8"))
        required = [
            field
            for field in document["body"]
            if isinstance(field, dict) and field.get("validations", {}).get("required")
        ]
        assert len(required) == 1
        assert required[0]["id"] == "row"

    def test_neither_form_requires_a_justification(self) -> None:
        for name in ("correction.yml", "removal.yml"):
            document = yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))
            for field in document["body"]:
                if not isinstance(field, dict):
                    continue
                label = str(field.get("attributes", {}).get("label", "")).lower()
                if field.get("validations", {}).get("required"):
                    assert "reason" not in label
                    assert "proof" not in label
                    assert "evidence" not in label

    def test_both_forms_point_at_the_corrections_page(self) -> None:
        for name in ("correction.yml", "removal.yml"):
            assert "CORRECTIONS.md" in (TEMPLATES / name).read_text(encoding="utf-8")


def test_the_site_constant_points_at_the_committed_page() -> None:
    assert CORRECTIONS_URL.endswith("docs/CORRECTIONS.md")
    assert CORRECTIONS.is_file()
