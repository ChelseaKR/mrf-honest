"""The workflow-lint job runs a pinned, checksum-verified actionlint, and fails closed.

Parsed, not grepped, for the same reason as tests/test_recollect_workflow.py: the header comment
names the tokens the promises are about. What is pinned here is the supply-chain shape (a fixed
version, a committed SHA-256 that is checked before the archive is unpacked) and that the job
cannot go green without shellcheck, because actionlint skips every ``run:`` script silently when
shellcheck is missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "workflow-lint.yml").read_text(encoding="utf-8")
DOCUMENT = cast(dict[str, Any], yaml.safe_load(TEXT))
JOB = cast(dict[str, Any], DOCUMENT["jobs"]["actionlint"])
STEPS = cast(list[dict[str, Any]], JOB["steps"])


def _triggers() -> dict[str, Any]:
    return cast(dict[str, Any], DOCUMENT.get("on", DOCUMENT.get(True)))


def test_it_runs_on_every_pull_request_and_on_master_with_read_only_permissions() -> None:
    triggers = _triggers()
    assert "pull_request" in triggers
    assert triggers["push"]["branches"] == ["master"]
    assert DOCUMENT["permissions"] == {"contents": "read"}


def test_the_binary_is_a_fixed_version_with_a_committed_sha256() -> None:
    env = cast(dict[str, str], JOB["env"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", env["ACTIONLINT_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", env["ACTIONLINT_SHA256"])


def test_the_checksum_is_verified_before_the_archive_is_unpacked() -> None:
    install = next(s for s in STEPS if str(s.get("name", "")).startswith("Install actionlint"))
    script = str(install["run"])
    assert "set -euo pipefail" in script
    assert script.index("sha256sum --check --strict") < script.index("tar -xzf")


def test_the_lint_step_refuses_to_pass_without_shellcheck() -> None:
    lint = next(s for s in STEPS if str(s.get("name", "")).startswith("Lint every workflow"))
    script = str(lint["run"])
    assert "command -v shellcheck" in script
    assert script.index("command -v shellcheck") < script.index('actionlint" -color')


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = [str(s["uses"]) for s in STEPS if "uses" in s]
    assert uses, "the job checks out the repository"
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref
