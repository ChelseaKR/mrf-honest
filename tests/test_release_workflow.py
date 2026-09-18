"""The release path, and the one act it deliberately cannot take.

`docs/EXPANSION-PLAN.md` phase 14 names four things no automation in this repository should do.
Signing the release tag is the first, because the key is the maintainer's. Everything up to the
tag can be built and tested, and this is the test: the workflow must verify a signature it did
not create, refuse a placeholder trust root, refuse a version disagreement, re-run the gate at
the tagged commit, and hold no credential that could publish anything anywhere.
"""

from __future__ import annotations

import base64
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

    def test_the_committed_trust_root_is_real_keys_and_nothing_else(self) -> None:
        """The point of the check above, and it had to change shape to stay honest.

        This used to assert that `.github/allowed_signers` did not exist at all. That was true
        while no release had ever been prepared, and it became the thing standing between this
        repository and its first one: `release.yml` stops at its second step without the file,
        and the file could not be added while a test forbade it.

        What the check was ever about is not the file's absence. It is that a committed trust
        root must be a real public key and not a placeholder -- a placeholder looks configured
        and trusts nobody, which is worse than an absent file that stops the job. So every line
        is parsed as OpenSSH's `allowed_signers` format and the key material is decoded: the
        SSH wire format begins with a length-prefixed copy of the algorithm name, so a
        plausible-looking base64 blob that is not a key fails here rather than at a release.
        """

        path = ROOT / ".github" / "allowed_signers"
        assert path.is_file() and path.stat().st_size > 0, (
            "the release trust root is missing. release.yml refuses to verify a tag without it, "
            "so no release can be cut; if it was removed deliberately, this test is the record."
        )

        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert lines, "the trust root has no entries; an empty file trusts nobody"

        for line in lines:
            lowered = line.lower()
            for marker in ("example", "placeholder", "replace", "changeme", "todo", "your-key"):
                assert marker not in lowered, f"placeholder trust root: {line!r}"

            principals, algorithm, material = line.split(" ", 2)
            assert "@" in principals, f"no principal on {line!r}"
            assert algorithm in {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}, algorithm

            blob = base64.b64decode(material.split(" ")[0], validate=True)
            length = int.from_bytes(blob[:4], "big")
            assert blob[4 : 4 + length].decode("ascii") == algorithm, (
                f"the key material in {line!r} does not encode {algorithm}; it is not a key."
            )


class TestNoInputReachesAShell:
    """`${{ }}` inside a `run:` block is substitution into the script before bash sees a token.

    Demonstrated rather than asserted, on a copy of the old and new shapes with the dispatched
    value `v1"; touch <path>; echo "`: the old form created the file, the new form printed the
    whole string as `tag`. The exposure here was bounded -- `workflow_dispatch` on a public
    repository requires write access, and the tag's signature is checked two steps later -- but
    this is the one workflow that exists to be trusted, and the repair is an `env:` entry.

    The taint does not stop at the input: `steps.resolve.outputs.tag` is that same dispatched
    value one hop later, so all three sites were the same defect and are checked together here.
    A rule that covered only the literal `github.event.inputs.tag` would have passed a workflow
    that still pasted the same string into bash twice.
    """

    def test_no_expression_is_interpolated_into_any_run_block(self) -> None:
        offenders = [
            (job, str(step.get("name")), expression.strip())
            for job in JOBS
            for step in _steps(job)
            if step.get("run")
            for expression in re.findall(r"\$\{\{([^}]*)\}\}", str(step["run"]))
        ]
        assert offenders == [], (
            f"these reach bash by substitution into the script rather than through env: {offenders}"
        )

    def test_the_dispatched_tag_reaches_the_script_as_an_environment_variable(self) -> None:
        """The other direction: the value still has to get there, or the step does nothing."""
        resolve = next(
            step for step in _steps("verify-tag") if str(step.get("id", "")) == "resolve"
        )
        env = cast(dict[str, str], resolve["env"])
        assert "github.event.inputs.tag" in env["DISPATCHED_TAG"]
        assert "github.ref_name" in env["REF_NAME"]
        # The bash fallback has to mean what GitHub's `||` meant: dispatched value if non-empty,
        # otherwise the ref name.
        assert "${DISPATCHED_TAG:-${REF_NAME}}" in str(resolve["run"])


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

    def test_the_declared_version_is_one_this_workflow_would_accept(self) -> None:
        """The honest state of the repository, held against the workflow's own refusals.

        This used to assert the opposite -- that the declared version was `0.1.0.dev0` and
        therefore *not* releasable -- which was the honest state while nothing had been
        released, and was one of the things that made the first release impossible to land.
        What it was checking is that the declared version and the workflow agree about whether
        a release is possible, and that is what it checks now, against the same two refusals
        `release.yml` implements: the `*dev*|*rc*|*a*|*b*` case pattern, and the changelog
        section keyed on the declared version.
        """

        version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
        assert version is not None
        declared = version.group(1)
        for marker in ("dev", "rc"):
            assert marker not in declared, (
                f"pyproject.toml declares {declared!r}; release.yml refuses a pre-release version."
            )
        assert f"[{declared}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), (
            f"release.yml requires a CHANGELOG.md section named [{declared}] and there is none."
        )


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
        """An empty parametrization above would make the pinning check vacuous."""

        assert "uses:" in TEXT

    def test_no_checkout_persists_credentials(self) -> None:
        for job in JOBS.values():
            for step in cast(list[dict[str, Any]], job["steps"]):
                if "checkout" in str(step.get("uses")):
                    assert step["with"]["persist-credentials"] is False
