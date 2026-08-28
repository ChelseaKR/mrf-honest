"""The release path, and the one act it deliberately cannot take.

`docs/EXPANSION-PLAN.md` phase 14 names four things no automation in this repository should do.
Signing the release tag is the first, because the key is the maintainer's. Everything up to the
tag can be built and tested, and this is the test: the workflow must verify a signature it did
not create, refuse a placeholder trust root, refuse a version disagreement, re-run the gate at
the tagged commit, and hold no credential that could publish anything anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKFLOW = cast(dict[str, Any], yaml.safe_load(TEXT))
JOBS = cast(dict[str, dict[str, Any]], WORKFLOW["jobs"])


def _steps(job: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], JOBS[job]["steps"])


def _run_text(job: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


class TestTheKeyStaysWithTheMaintainer:
    def test_the_workflow_verifies_a_signature_it_did_not_create(self) -> None:
        run = _run_text("verify-tag")
        assert "git verify-tag" in run
        assert "gpg.ssh.allowedSignersFile" in run

    def test_no_step_creates_or_pushes_a_tag(self) -> None:
        """A release workflow that can tag is a release workflow that can release itself."""

        for job in JOBS:
            run = _run_text(job)
            assert "git tag" not in run
            assert "git push" not in run

    def test_no_signing_key_or_passphrase_is_referenced(self) -> None:
        for forbidden in ("SIGNING_KEY", "GPG_PRIVATE", "SSH_PRIVATE", "passphrase"):
            assert forbidden not in TEXT

    def test_an_absent_allowed_signers_file_stops_the_job(self) -> None:
        """Not a warning. A trust root nobody configured must not verify anything.

        Asserted against that one step rather than against the job's whole run text, which was
        the first version and passed against a mutant that downgraded the failure to a warning:
        every other step in the job carries an `exit 1` of its own.
        """

        step = next(
            entry
            for entry in _steps("verify-tag")
            if "allowed-signers" in str(entry.get("name", "")).lower()
        )
        run = str(step["run"])
        assert "allowed_signers" in run
        assert "exit 1" in run
        assert "::error" in run
        assert "::warning" not in run

    def test_the_repository_ships_no_placeholder_trust_root(self) -> None:
        """The point of the check above. A committed placeholder key would look configured and
        trust nobody, which is worse than an absent file that stops the job."""

        assert not (ROOT / ".github" / "allowed_signers").exists()


class TestTheReleaseIsAClaimAboutOneCommit:
    def test_the_gate_runs_again_at_the_tagged_commit(self) -> None:
        assert "make verify" in _run_text("build")

    def test_the_build_job_checks_out_the_tag_the_verify_job_resolved(self) -> None:
        checkout = next(step for step in _steps("build") if "checkout" in str(step.get("uses")))
        assert "needs.verify-tag.outputs.tag" in str(checkout["with"]["ref"])

    def test_the_build_waits_for_the_verification(self) -> None:
        assert JOBS["build"]["needs"] == "verify-tag"

    def test_the_build_does_not_reuse_a_cache_it_did_not_verify(self) -> None:
        setup = next(step for step in _steps("build") if "setup-uv" in str(step.get("uses")))
        assert setup["with"]["enable-cache"] is False

    def test_the_locked_environment_is_asserted_not_merely_installed(self) -> None:
        assert "uv sync --locked" in _run_text("build")
        assert "--frozen" not in _run_text("build")


class TestVersionAgreement:
    def test_the_tag_and_the_declared_version_must_agree(self) -> None:
        run = _run_text("verify-tag")
        assert "pyproject.toml" in run
        assert "v${declared}" in run

    def test_a_pre_release_version_is_refused(self) -> None:
        assert "*dev*" in _run_text("verify-tag")

    def test_a_changelog_entry_is_required(self) -> None:
        assert "CHANGELOG.md" in _run_text("verify-tag")

    def test_the_current_version_would_be_refused_today(self) -> None:
        """This is the honest state of the repository: 0.1.0.dev0 is not releasable, and the
        workflow says so rather than shipping a wheel that claims otherwise."""

        version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
        assert version is not None
        assert "dev" in version.group(1)


class TestNothingIsPublished:
    def test_no_registry_credential_is_referenced(self) -> None:
        for forbidden in ("PYPI", "TWINE", "pypi-", "trusted-publish", "id-token"):
            assert forbidden not in TEXT

    def test_every_job_holds_read_only_permissions(self) -> None:
        assert WORKFLOW["permissions"] == {"contents": "read"}
        for job in JOBS.values():
            assert "permissions" not in job or job["permissions"] == {"contents": "read"}

    def test_the_distributions_are_uploaded_for_inspection_not_release(self) -> None:
        uses = [str(step.get("uses", "")) for step in _steps("build")]
        assert any("upload-artifact" in entry for entry in uses)
        assert not any("softprops/action-gh-release" in entry for entry in uses)
        assert "gh release create" not in _run_text("build")


class TestSupplyChain:
    @pytest.mark.parametrize(
        "uses",
        [
            str(step["uses"])
            for job in cast(dict[str, dict[str, Any]], yaml.safe_load(TEXT)["jobs"]).values()
            for step in job["steps"]
            if "uses" in step
        ],
    )
    def test_every_action_is_pinned_to_a_full_sha(self, uses: str) -> None:
        _, _, reference = uses.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", reference), f"{uses} is not SHA-pinned"

    def test_the_workflow_uses_at_least_one_action(self) -> None:
        """An empty parametrisation above would make the pinning check vacuous."""

        assert "uses:" in TEXT

    def test_no_checkout_persists_credentials(self) -> None:
        for job in JOBS.values():
            for step in cast(list[dict[str, Any]], job["steps"]):
                if "checkout" in str(step.get("uses")):
                    assert step["with"]["persist-credentials"] is False
