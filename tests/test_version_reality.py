"""The version this project declares, held against the releases it actually has.

`pyproject.toml` declares `0.1.0.dev0` and `git tag` returns nothing, which is the honest
state: ADR 0001 declares Release & Versioning N/A while nothing downstream consumes this
code, and a PEP 440 developmental release says "not released" in a form pip, `packaging`
and an SBOM consumer all read correctly. `tests/test_release_workflow.py` already pins the
other end of that -- the release workflow refuses a `.dev` version.

What nothing checked was the middle. The declared version was written down again in two
places, and one of them had already drifted: `mcp.py` advertised
`serverInfo.version = "0.1.0"` to every MCP client that connected, a version this project
has never released and which disagrees with the version it declares. A number published as
though it meant something, with no artifact behind it, and disagreeing with its own source.

Two states are distinguished here, and only one is a defect:

* **No tags at all** -- where this project stands. It passes, but only if the declared
  version says so in a form a tool can read (the `.devN` suffix) *and* the documents a
  reader opens say so too. If nothing says it, the silence is the finding.
* **Tags exist and none matches the declared version** -- a defect. The failure names the
  declared version and the newest tag.

The second branch is unreachable from this repository today, so it is driven below against
synthetic tag lists, and was proved end to end against a throwaway clone carrying real
`v9.9.9` and `v0.0.1` tags. It is never proved by tagging this repository: no job in
`release.yml` may create or push a tag, and `test_release_workflow.py` holds that.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tokenize
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from mrf_honest import __version__ as PACKAGE_VERSION
from mrf_honest.mcp import SERVER_INFO

ROOT = Path(__file__).resolve().parent.parent

#: PEP 440 developmental release: `0.1.0.dev0` sorts below `0.1.0` and is skipped by a
#: plain `pip install`, which is exactly right for something that was never released.
DEV_SUFFIX = re.compile(r"\.dev\d+$")


def declared_version(root: Path = ROOT) -> str:
    """The one source of truth for this project's version."""

    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def repository_tags(root: Path = ROOT) -> list[str]:
    """Every tag this checkout can see. Empty means "none visible", not "none exist"."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "tag", "--list"],  # noqa: S607 - git is the tool
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_shallow(root: Path = ROOT) -> bool:
    """Whether this checkout was truncated -- an empty tag list then proves nothing."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "true"


def remote_tags(root: Path = ROOT) -> list[str] | None:
    """The tags the remote actually has, or None when the remote could not be asked.

    The local list is only as complete as the fetch that produced it: `actions/checkout`
    fetches no tags unless asked, so a gate reading `git tag` off a default checkout
    answers "none" whatever the truth is, and passes blind. `git ls-remote` is the
    authoritative answer, and it goes over the git protocol -- it costs no GitHub API
    quota at all.
    """

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - git is the tool
            "git",
            "-C",
            str(root),
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return sorted(
        line.rsplit("refs/tags/", 1)[-1]
        for line in result.stdout.splitlines()
        if "refs/tags/" in line
    )


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def newest_tag(tags: Sequence[str]) -> str:
    """The highest tag by numeric components, ties broken by name.

    Deliberately not `--sort=-creatordate`: a tag re-cut later would then read as newer
    than the release it replaced, and a shallow fetch may not carry creator dates at all.
    """

    def key(tag: str) -> tuple[tuple[int, ...], str]:
        return tuple(int(part) for part in re.findall(r"\d+", strip_v(tag))), tag

    return max(tags, key=key)


def version_against_tags(declared: str, tags: Sequence[str]) -> str | None:
    """The finding, or None when the declared version is answerable to the tags."""

    if not tags:
        return None
    if any(strip_v(tag) == declared for tag in tags):
        return None
    return (
        f"pyproject.toml declares version {declared!r}, and none of the {len(tags)} tag(s) in "
        f"this repository matches it. The newest tag is {newest_tag(tags)!r}. Either a release "
        "was cut without bumping the declared version, or the declared version has run ahead "
        "of what was released and should keep its PEP 440 '.devN' suffix until it is tagged."
    )


#: Where a reader is told, in prose, that nothing has been released. Required while there
#: are no tags; retired by the commit that cuts the first one -- both directions gated.
UNRELEASED_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("README.md", r"Release & Versioning \| N/A \(pre-publication as a package: no tags"),
    ("CHANGELOG.md", r"The project is pre-release with\nno version tags yet"),
    ("CITATION.cff", r"Pre-release: version and date-released will be added"),
    ("docs/adr/0001-release-versioning-na.md", r"mrf-honest has zero git tags"),
)


def unreleased_statement_findings(root: Path, tags: Sequence[str]) -> list[str]:
    findings = []
    for name, pattern in UNRELEASED_STATEMENTS:
        text = (root / name).read_text(encoding="utf-8")
        present = re.search(pattern, text) is not None
        if not tags and not present:
            findings.append(
                f"{name} no longer tells a reader that nothing has been released (expected to "
                f"match {pattern!r}), and no tag exists to make that true."
            )
        if tags and present:
            findings.append(
                f"{name} still says nothing has been released, but {newest_tag(tags)!r} exists."
            )
    return findings


def citation_findings(root: Path, tags: Sequence[str]) -> list[str]:
    """ADR 0001: `CITATION.cff` omits `version` and `date-released` until a real release.

    Both name a release. Without a tag there is none to name, and CFF permits their
    absence for pre-release software.
    """

    text = (root / "CITATION.cff").read_text(encoding="utf-8")
    findings = []
    if not tags:
        for key in ("version", "date-released"):
            if re.search(rf"^{key}:", text, re.MULTILINE):
                findings.append(
                    f"CITATION.cff declares {key}, but this repository has no tags "
                    "(ADR 0001 omits both until the first dated release)."
                )
    return findings


def restated_versions() -> dict[str, str]:
    """Every place that states the version at runtime.

    Both read the installed distribution metadata, which the build takes from
    `pyproject.toml`. Neither writes the number down again. `mcp.py` derives it
    independently rather than importing the package, because ADR 0002 keeps the MCP
    server's import graph to the standard library.
    """

    return {
        "mrf_honest.__version__": PACKAGE_VERSION,
        "mcp.py serverInfo.version": str(SERVER_INFO["version"]),
    }


def restatement_findings(declared: str) -> list[str]:
    return [
        f"{where} reports {found!r}, but pyproject.toml declares {declared!r}."
        for where, found in restated_versions().items()
        if found != declared
    ]


DECLARED = declared_version()
TAGS = repository_tags()


# --- the repository as it actually is --------------------------------------------------


def test_the_declared_version_is_answerable_to_the_tags_that_exist() -> None:
    finding = version_against_tags(DECLARED, TAGS)
    assert finding is None, finding


def test_an_empty_tag_list_is_measured_rather_than_inherited_from_a_shallow_clone() -> None:
    """An unfetched ref namespace looks exactly like a project that never released.

    `tests/test_corrections.py` already refuses a shallow clone for the same reason, and
    `ci.yml` checks out with `fetch-depth: 0`, which fetches `refs/tags/*` explicitly.
    """

    assert TAGS or not is_shallow(), (
        "this checkout is shallow and reports zero tags, which is indistinguishable from a "
        "repository that has never been released. Check out with fetch-depth: 0."
    )


def test_the_checkout_can_see_every_tag_the_remote_has() -> None:
    """The half the local tag list cannot prove about itself.

    A checkout that fetched no tags and a repository that has none are the same empty
    list, and the checks above would take the second reading and pass. The remote settles
    it. A skip here is a visible "not checked", not a pass: it means the remote could not
    be reached, which does not happen in CI.
    """

    published = remote_tags()
    if published is None:
        pytest.skip("the remote could not be reached; only this checkout's tag list is available")

    missing = sorted(set(published) - set(TAGS))
    assert not missing, (
        f"the remote has tag(s) this checkout cannot see: {missing}. A gate reading tags from "
        "this checkout would report 'never released' and pass. Check out with fetch-depth: 0."
    )

    finding = version_against_tags(DECLARED, published)
    assert finding is None, finding


def test_an_untagged_version_says_so_in_a_form_a_tool_can_read() -> None:
    """The prose is for people; this is the half a machine can act on."""

    if TAGS:
        assert not DEV_SUFFIX.search(DECLARED), (
            f"{DECLARED!r} is a developmental version, but {newest_tag(TAGS)!r} is tagged."
        )
    else:
        assert DEV_SUFFIX.search(DECLARED), (
            f"pyproject.toml declares {DECLARED!r} and this repository has no tags, so nothing "
            "was ever built or signed under that number. Keep the PEP 440 '.devN' suffix until "
            "one is; release.yml refuses to build a '*dev*' version, which is the point."
        )


def test_every_runtime_statement_of_the_version_agrees_with_pyproject() -> None:
    """The MCP server told clients '0.1.0'. Nothing has ever been released under it."""

    findings = restatement_findings(DECLARED)
    assert findings == [], findings


def test_the_documents_say_nothing_has_been_released_while_nothing_has() -> None:
    findings = unreleased_statement_findings(ROOT, TAGS) + citation_findings(ROOT, TAGS)
    assert findings == [], findings


def code_without_comments(path: Path) -> str:
    """The module's code with `#` comments removed.

    Comments are excluded deliberately: both modules explain, in a comment, which version
    string used to be typed there and why it is not any more. That explanation is the
    reason the check exists and must not be what trips it.
    """

    with path.open("rb") as handle:
        kept = [
            token for token in tokenize.tokenize(handle.readline) if token.type != tokenize.COMMENT
        ]
    return tokenize.untokenize(kept).decode("utf-8")


@pytest.mark.parametrize("module", ["src/mrf_honest/__init__.py", "src/mrf_honest/mcp.py"])
def test_no_module_writes_the_version_down_again(module: str) -> None:
    code = code_without_comments(ROOT / module)
    assert DECLARED not in code, (
        f"{module} hard-codes the version; read it from the installed distribution metadata."
    )
    assert "metadata" in code, module


def test_the_comment_stripper_removes_comments_and_keeps_code(tmp_path: Path) -> None:
    """A stripper that removed too much would make the check above vacuous."""

    sample = tmp_path / "sample.py"
    sample.write_text(
        'X = "9.9.9"  # trailing 9.9.9\n# whole-line 9.9.9\nY = 1\n', encoding="utf-8"
    )
    code = code_without_comments(sample)
    assert code.count("9.9.9") == 1, "both comments should be gone and the assignment kept"
    assert 'X = "9.9.9"' in code
    assert "Y = 1" in code

    # And on the real module: the explanatory comment goes, the assignment stays.
    mcp_code = code_without_comments(ROOT / "src" / "mrf_honest" / "mcp.py")
    assert "SERVER_INFO = " in mcp_code
    assert "ADR 0002 keeps this module" not in mcp_code


# --- the branches this repository cannot reach ------------------------------------------


def test_a_tag_that_matches_the_declared_version_is_the_passing_case() -> None:
    assert version_against_tags("0.1.0", ["v0.1.0"]) is None
    assert version_against_tags("0.1.0", ["0.1.0"]) is None
    assert version_against_tags("0.2.0", ["v0.1.0", "v0.2.0"]) is None


@pytest.mark.parametrize(
    ("declared", "tags", "newest"),
    [
        ("0.1.0.dev0", ["v0.1.0"], "v0.1.0"),
        ("0.1.0", ["v0.2.0"], "v0.2.0"),
        ("0.3.0", ["v0.1.0", "v0.10.0", "v0.9.0"], "v0.10.0"),
    ],
)
def test_a_declared_version_no_tag_backs_is_a_failure_that_names_both(
    declared: str, tags: list[str], newest: str
) -> None:
    """A gate that cannot fail is not a gate, and this repository cannot reach the failing
    state on its own, so it is driven here with tag lists it does not have."""

    finding = version_against_tags(declared, tags)
    assert finding is not None
    assert repr(declared) in finding
    assert repr(newest) in finding


def test_the_newest_tag_is_chosen_numerically_not_lexically() -> None:
    assert newest_tag(["v0.9.0", "v0.10.0"]) == "v0.10.0"
    assert newest_tag(["v1.0.0", "v0.10.0"]) == "v1.0.0"


# --- negative controls: each check is shown to bite on a sabotaged copy ------------------


@pytest.fixture
def sabotage(tmp_path: Path) -> Path:
    """A copy of the documents these checks read, so a mutation cannot touch the repo."""

    copy = tmp_path / "repo"
    (copy / "docs" / "adr").mkdir(parents=True)
    for name in ("README.md", "CHANGELOG.md", "CITATION.cff", "pyproject.toml"):
        shutil.copy(ROOT / name, copy / name)
    adr = "docs/adr/0001-release-versioning-na.md"
    shutil.copy(ROOT / adr, copy / adr)
    return copy


def test_the_clean_tree_produces_no_findings_at_all(sabotage: Path) -> None:
    """Without this, every negative control below could pass for the wrong reason."""

    assert declared_version(sabotage) == DECLARED
    assert unreleased_statement_findings(sabotage, []) == []
    assert citation_findings(sabotage, []) == []


@pytest.mark.parametrize(("document", "pattern"), UNRELEASED_STATEMENTS)
def test_deleting_a_release_stance_sentence_is_caught(
    sabotage: Path, document: str, pattern: str
) -> None:
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = re.sub(pattern, "REMOVED", before)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {pattern!r} never matched {document}"
    assert re.search(pattern, after) is None

    findings = unreleased_statement_findings(sabotage, [])
    assert any(document in finding for finding in findings), findings


def test_a_stance_sentence_left_standing_after_a_release_is_caught(sabotage: Path) -> None:
    """The other direction: the first tag has to retire these sentences."""

    findings = unreleased_statement_findings(sabotage, ["v0.1.0"])
    assert len(findings) == len(UNRELEASED_STATEMENTS)
    assert all("v0.1.0" in finding for finding in findings)


@pytest.mark.parametrize("key", ["version", "date-released"])
def test_a_citation_release_field_without_a_tag_is_caught(sabotage: Path, key: str) -> None:
    """ADR 0001's consequence, reproduced deliberately on a copy."""

    path = sabotage / "CITATION.cff"
    before = path.read_text(encoding="utf-8")
    after = f"{before}{key}: 9.9.9\n"
    path.write_text(after, encoding="utf-8")

    assert re.search(rf"^{key}:", after, re.MULTILINE), "the sabotage did not land"

    assert citation_findings(sabotage, []) != []
    assert citation_findings(sabotage, ["v0.1.0"]) == []


def test_a_runtime_version_drifting_from_pyproject_is_caught() -> None:
    """The defect this file was written for: `mcp.py` said '0.1.0' for months.

    Driven against the checker directly rather than by mutating a live module, because
    `SERVER_INFO` is read once at import and a monkeypatched copy would prove nothing
    about the module every other test imports.
    """

    assert restatement_findings("9.9.9") != []
    assert all("9.9.9" in finding for finding in restatement_findings("9.9.9"))
    assert len(restatement_findings("9.9.9")) == len(restated_versions())
