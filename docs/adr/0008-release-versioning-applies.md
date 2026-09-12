# 0008. RELEASE-AND-VERSIONING-STANDARD: applies, from `v0.1.0`

## Status

Accepted - 2026-09-12. Supersedes [0001](0001-release-versioning-na.md).

## Context

[ADR 0001](0001-release-versioning-na.md) declared Release & Versioning **N/A**, for reasons that
were true when it was written: zero tags, no remote, no release workflow, no consumer. It named
its own trigger — *"the repository gains a public remote, or anything external starts consuming
this code"* — and every clause of it has since fired:

- The repository is public and the site is published from it.
- `action.yml` and `.pre-commit-hooks.yaml` are surfaces built for *other* repositories to
  consume, and both instruct a consumer to pin a ref. `action.yml` says in terms that *"pinning
  this action to a tag pins the policy with it"*, `docs/before-you-post-it.md` told a publisher to
  write `@<a tag or commit sha>`, and `.pre-commit-hooks.yaml` told them to write
  `rev: <a tag or commit>`.
- There was nothing to write. Documenting a pinning discipline while shipping nothing to pin is
  the defect, not the absence of a release: it asks a consumer to do the careful thing and then
  makes the careless thing the only option.
- `.github/workflows/release.yml` exists, is complete, and has never run.

The grade this project publishes is a pure function of bytes, an `as_of` date, and a **policy
fingerprint** that the commit determines. That is the whole reason a version matters here. A
consumer who floats a branch floats the grading policy, and two runs a month apart can disagree
for reasons that have nothing to do with the file being graded.

## Decision

**Release & Versioning applies.** The project is versioned with SemVer from `0.1.0`, and a
release is a **signed tag** `vX.Y.Z` on `master`.

- `pyproject.toml` holds the one declared version. Nothing else writes it down:
  `mrf_honest.__version__` and the MCP server's `serverInfo.version` both read the installed
  distribution metadata, and `tests/test_version_reality.py` fails if either drifts.
- A tag must be signed by a key in `.github/allowed_signers`. The signing key is the
  maintainer's, no workflow holds it, and `release.yml` creates no tag — it verifies one.
- `release.yml` additionally requires the tag and the declared version to agree, refuses a
  pre-release version string, requires a `CHANGELOG.md` section for it, and re-runs `make verify`
  at the tagged commit before building. A release is a claim about one commit, so the gate runs
  against that commit and not against the branch it came from.
- **Nothing is published to any index.** There is no PyPI upload step and no registry credential
  anywhere in this repository. The distributions are built and hashed so the maintainer can
  inspect them. Adding a publish step is a separate decision that needs a credential nobody
  currently has to hold.
- The **site is not versioned with the package.** It stays a continuously rebuilt artifact of
  committed data, and the thing that dates a published grade is the cohort's `as_of`, which every
  page already carries. Tying the site to a package version would suggest a grade changes when
  the code is released, and it does not: it changes when the bytes or the policy fingerprint
  change.

`0.x` is deliberate and is not modesty. The two surfaces a consumer pins — the Action and the
pre-commit hook — are stable in shape, but the finding catalog and the grading policy are still
moving, and a minor version that can carry a policy change is a more honest signal than a `1.0`
that cannot.

## Consequences

- The README's Standards Conformance table carries `Release & Versioning | Applies` citing this
  ADR; ADR 0001 is marked superseded with its context left intact.
- `CITATION.cff` gains `version` and `date-released`, which ADR 0001 had it omit.
- `docs/before-you-post-it.md` and `.pre-commit-hooks.yaml` name `v0.1.0` as the ref to pin, so
  the instruction to pin is followable.
- **`uv.lock` records this project's own version, so a version bump is a two-file change.**
  Found the hard way on the first one: `pyproject.toml` moved to `0.1.0` and `uv sync --locked`
  failed in CI seven seconds in, before the gate ran, with *"The lockfile at `uv.lock` needs to
  be updated"*. `make verify`'s `lock` step (`uv lock --check`) is the same gate locally. Every
  future bump runs `uv lock` in the same commit; the diff is one line and touches no dependency.
- `CHANGELOG.md` entries before `0.1.0` stay grouped by date. They were written while nothing had
  been released, and retrofitting them into versions that never existed would be a fabricated
  history of releases.
- `tests/test_version_reality.py` already gates both directions: while there are no tags the
  declared version must carry a PEP 440 `.devN` suffix and four documents must say nothing has
  been released; once a tag exists the suffix must be gone and those sentences must be retired.
  **That is why the version bump and this ADR cannot land before the tag exists.** The order is:
  sign and push the tag at the commit carrying this change, then merge it in a way that keeps
  that commit on `master`.

## Revisit if

The project publishes to an index (that needs a trusted-publisher decision and a credential
policy, neither of which exists), or the grading policy stabilises enough that a `1.0` would mean
something a `0.x` does not.
