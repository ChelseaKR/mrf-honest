# PR triage, 2026-08-28

Eleven open pull requests, triaged so each one is decidable. Written against
`origin/master` = `12ac231` (the merge of #32). Note that the local checkout's `master`
is `f13e426`, one commit behind; `src/` and `tests/` are identical between the two, so
working-tree analysis holds.

Every claim below is marked **verified** (I executed or computed it) or **trusted**
(I read it and did not independently confirm it). No PR was merged, closed, commented
on, or otherwise modified in producing this report.

---

## Status, 2026-08-29: this document is a record, not a plan

Read the rest of this file as a dated snapshot. The queue it triages has since been
drained, and three things in it are now wrong in ways worth naming here rather than
leaving for a reader to trip over.

**1. The recommended plan was carried out, in a shorter form than it describes.**
`#40` was merged as `a0e1486`, which landed phases 6 through 14 in one commit exactly
as the structural analysis below predicted. `#33`-`#39` were then closed as contained
in it, verified by blob-identity: every unique file of every phase resolves to the same
git blob on `master` as on its branch. `#27` merged as `36cb30d`, `#29` and `#30` merged
after it. Nothing in the stack was lost.

**2. The conflict-resolution advice below is now unsafe.** The section "The single most
important structural fact" says each conflict "is resolvable by taking the incoming
branch wholesale (the higher PR's tree is a strict superset)". That was true when
written against `12ac231`. It stopped being true when `#43` and `#27` moved `master`
past the stack: in the files both sides touch, the branch tree is now a strict
*subset*, so the justification the sentence rests on is inverted.

Concretely, `git merge-tree` against today's `master` conflicts for every phase branch
in `CHANGELOG.md`, and additionally in `README.md` and `docs/ROADMAP.md` (phases 9-12)
or `src/mrf_honest/statistics.py` (phase 6). Taking the branch side of those would
restore the `445 tests / 92.44% / 2026-08-21` metrics-ledger rows that `#40` corrected
to `644 passing / 92.79% / 2026-08-28`. Adopting the branch *tree* wholesale, which is
what "strict superset" licenses, would additionally restore `targeted_total` in
`site.py` -- the pooled sum of every cohort's `targeted` that `#43` removed from the
index page's meta description, because the page renders each cohort separately
precisely so their rows are never pooled into one distribution. Merging gains nothing
either way: every unique file already matches `master` blob for blob. That is why
`#33`-`#39` were closed rather than merged.

**3. Both blocking defects were fixed before `#40` merged**, so neither blocks anything
now. The `mcp.py` path traversal is closed by `_cohort_document_path`, which resolves a
cohort id against the published index before it is ever a path, with fifteen hostile
spellings exercised across every tool that takes one. The `release.yml` shallow-clone
defect is closed by `fetch-depth: 0` on the `build` job.

**What remains live is the per-PR technical review.** Those defects are not closed by
the merges: the code they describe is on `master` now, so every finding below about
unpinned constants in `container.py`, the vacuous `PUBLISHED` glob in
`tests/test_dataset.py`, the unpinned Wilson interval in `tests/test_statistics.py`,
the five-versus-six refusal drift in ADR 0007, and `missing_shares` not requiring the
interval is a live follow-up against `master`. That is the reason this file is being
kept rather than discarded.

---

## The single most important structural fact

**PRs #33 through #40 are one linear content stack, and all eight target `master`.**

The PR bodies say "stacked on #39", "stacked on #38", and so on. That is true of the
*content* and false of the *git topology*. Every branch is based on `12ac231` directly
and carries its own rebased copies of every phase below it.

Verified by tree SHA. All eight branches replay the identical trees at each level:

| Level | Tree SHA | Present in |
|---|---|---|
| phase 6 | `c219858167a2…` | #33 #34 #35 #36 #37 #38 #39 #40 |
| phase 7 | `a9ab3ea44894…` | #34 #35 #36 #37 #38 #39 #40 |
| phase 8 | `449093a865ed…` | #35 #36 #37 #38 #39 #40 |
| phase 9 | `e709a9900ca5…` | #36 #37 #38 #39 #40 |
| phase 10 | `e2cb13265fd7…` | #37 #38 #39 #40 |
| ci fetch-depth | `3842628523d4…` | #37 #38 #39 #40 |
| phase 11 | `7adb321d1c7e…` | #38 #39 #40 |
| phase 12 | `fc382f8514bf…`, `e346906aff32…` | #39 #40 |
| phase 14 | `1d2c94e4edae…`, `968e43456c27…` | #40 |

So **#40 ⊃ #39 ⊃ #38 ⊃ #37 ⊃ #36 ⊃ #35 ⊃ #34 ⊃ #33**, exactly. Merging #40 alone lands
all eight phases.

**Consequence, verified with `git merge-tree`: merging them in ascending order does not
work cleanly.** Because each branch carries its *own copy* of the lower phases rather
than building on the merged result, git sees both sides adding the same files
independently:

| Step | Result |
|---|---|
| #33 onto `12ac231` | CLEAN, result equals #33's tree |
| #34 onto #33 | **CONFLICT** — `CHANGELOG.md` (content), `src/mrf_honest/statistics.py` (add/add) |
| #35 onto #34 | **CONFLICT** — `CHANGELOG.md`, `docs/how-we-compare.md` |
| #36 onto #35 | **CONFLICT** — `CHANGELOG.md`, `README.md` |
| #37 onto #36 | **CONFLICT** — `CHANGELOG.md`, `src/mrf_honest/site.py`, `tests/test_site.py` |
| #38 onto #37 | **CONFLICT** — `CHANGELOG.md`, `README.md` |
| #39 onto #38 | **CONFLICT** — `CHANGELOG.md`, `README.md`, `docs/ROADMAP.md` |
| #40 onto #39 | **CONFLICT** — `CHANGELOG.md` only |

Each conflict is resolvable by taking the incoming branch wholesale (the higher PR's tree
is a strict superset), but that is seven manual resolutions for zero content gain. The
efficient path is to review each PR separately, as they were written to be, and then
perform **one merge of #40**, closing #33–#39 as contained in it.

## CI: what is actually failing

No billing starvation anywhere in this repository. Every job I inspected ran real steps
for a real duration.

- **#33–#40, #27, #29: all checks green** (Lighthouse, CodeQL python + actions, gitleaks,
  Python 3.12 and 3.14). Verified via `statusCheckRollup`.
- **#30 is the only red PR, and it is not code.** The failure is
  `Lighthouse over every rendered page` → step 10 → `/404.html: performance 0.93 below
  the floor of 0.95`. Verified from the job log (job 97223899583: 15 steps, 8m52s, real
  execution). #30 changes `uv.lock` and nothing else; a mypy version bump cannot alter a
  static 404 page. **This is Lighthouse performance-measurement noise on a self-imposed
  0.95 floor**, a third non-code red-CI cause to add to the known list.
- `.github/dependabot.yml` correctly declares `package-ecosystem: uv`, so this repo does
  **not** have the pip-against-uv.lock misconfiguration that makes bot PRs born red
  elsewhere in the portfolio. Verified.

---

# Per-PR triage

## #40 — Phase 14: the release path, up to the one act no automation here should take
- **Base:** `master`. **Head:** `feat/phase-14-release-path`. **State:** CLEAN, all checks green.
- **Contains:** every phase, 6 through 14. Unique contribution: `.github/workflows/release.yml`,
  `tests/test_release_workflow.py`, `docs/EXPANSION-PLAN.md`.

**What it does.** Adds a tag-triggered release workflow that verifies an SSH-signed tag
against a committed allowed-signers file, requires the tag and `pyproject.toml` version to
agree and be non-pre-release, requires a `CHANGELOG.md` entry, re-runs `make verify` at the
tagged commit, and uploads hashed distributions. It deliberately ships no signing key, no
publish step and no registry credential.

**Correctness.** The test file is one of the better ones in the stack: it reads the parsed
YAML rather than grepping text for the load-bearing assertions, and
`test_an_absent_allowed_signers_file_stops_the_job` scopes itself to the single named step
after the author found that a job-wide `"exit 1" in run` passed against a warning-downgrade
mutant. Verified: `.github/allowed_signers` is genuinely absent from the tree, so
`test_the_repository_ships_no_placeholder_trust_root` holds.

**Defect — the release job would fail at its own gate.** Verified by reading, with direct
corroborating evidence from inside the same stack:

- `release.yml`'s `build` job checks out with `ref` and `persist-credentials` only, with
  **no `fetch-depth: 0`**, so `actions/checkout` produces a depth-1 shallow clone.
- That job then runs `make verify` → `pytest` over `testpaths = ["tests"]`.
- #37 (contained in #40) adds `tests/test_corrections.py`, whose
  `test_the_history_needed_to_check_the_citations_is_present` asserts
  `git rev-parse --is-shallow-repository == "false"`, plus 16 parametrized cases that
  resolve commits and require them to be ancestors of `HEAD`. All fail on a shallow clone.
- #37's own `ci.yml` commit adds `fetch-depth: 0` with the comment *"The default shallow
  clone has one commit and fails that check, which is how this was found."* The same fix
  was not carried into `release.yml`, which was written later in the same stack.

This is latent rather than visible: `release.yml` only triggers on `push: tags: v*` and
`workflow_dispatch`, no tag exists, and the workflow would refuse the current `0.1.0.dev0`
version anyway. Nothing in CI can catch it.

**Minor.** `uv python install 3.12` in the build job is dead — `.python-version` pins 3.14,
so `uv sync` will fetch 3.14 regardless. `test_the_workflow_uses_at_least_one_action`
guards the SHA-pinning parametrization by asserting `"uses:" in TEXT` rather than that the
parameter list is non-empty; a YAML restructure could empty the list while the guard passes.
The pre-release `case` arms `*a*|*b*` are broader than intended but harmless for numeric
versions.

**Recommendation: `needs work`** — add `fetch-depth: 0` to the `build` job's checkout in
`.github/workflows/release.yml`. One line. Everything else about this PR is sound.

---

## #39 — Phase 12: measure the crash durability the roadmap declined to claim
- **Base:** `master`. **Head:** `feat/phase-12-durability`. **State:** CLEAN, all checks green.
- **Unique contribution:** `tests/test_durability.py` (486 lines, 16 cases) and doc updates. No source change.

**What it does.** SIGKILLs a real ingest subprocess at six named progress markers, asserts
the catalog never reports a snapshot it does not hold, re-runs each killed warehouse to
completion, races two writers, and adds three deterministic fault injections for a window
too narrow for a sampled kill to reach.

**Correctness — this is the strongest engineering in the stack.** Verified:
`MINIMUM_GENUINE_KILLS = 4` really exists (line 177) and is asserted at lines 199, 253 and
268; genuineness is `process.returncode == -signal.SIGKILL`, real evidence rather than an
inference. If no kill lands the suite goes **red, not green**. No skip markers anywhere.
`import duckdb` is unconditional, so a missing DuckDB is a loud collection error, and
`ci.yml:45` runs `uv sync --locked --all-extras`, which installs it. All six marker glob
patterns were checked against the real `lakehouse.py` layout and all six resolve. The
manifest-reader regression the PR body confesses to is genuinely fixed and its guard test
is not itself vacuous.

**Defect (minor).** `tests/test_durability.py:461-466`:
`assert on_disk <= named` over `warehouse.rglob("*.parquet")` is trivially true when
`on_disk` is empty, and nothing asserts it is non-empty. A mutant making `_promote` a no-op
leaves `test_a_recovered_warehouse_holds_no_orphan_artifacts` green. This is the one reader
in the file with no positive guard.

**Operational risk.** ~28 subprocess ingests land in the default `make verify` path
(`_sweep` is called three times uncached, plus six recovery ingests and the concurrency
tests). The `4 of 6` margin is tight because the last marker races process exit. On a loaded
runner this can go red for reasons unrelated to durability. *(Structural estimate, not
measured — I did not run the suite.)*

**Recommendation: `merge after rebase`** *(i.e. merge as part of #40)*, with a follow-up to
add a non-emptiness guard at line 463 and to consider a marker so the sweep does not run on
every commit.

---

## #38 — Phase 11, first half: a container is not the document
- **Base:** `master`. **Head:** `feat/phase-11-zip-containers`. **State:** CLEAN, all checks green.
- **Unique contribution:** `src/mrf_honest/container.py`, `tests/test_container.py`, `cli.py` wiring.

**What it does.** Opens a ZIP publication, applies six stated bounds from the central
directory before decompressing anything, and returns either the one gradeable member or a
typed refusal. Refusing zero or many members is deliberate: choosing among them would be
this project deciding what a publisher meant to publish.

**Defect 1 — two of the three numeric bounds are not pinned by any test.** This is the
"bound asserted with data too far from it to exercise it" shape, verified by mutation
against the real 21-case suite:

| Bound | Verdict | Evidence |
|---|---|---|
| 200:1 expansion ratio | **NOT PINNED** | The bomb fixture's real ratio is **1023.5:1**. Setting the constant to `1000.0`, `50.0` or even **`2.0`** leaves all 21 tests passing. `test_the_ratio_bound_is_the_one_that_is_documented` asserts `ratio > MAX_MEMBER_EXPANSION_RATIO` — the fixture against whatever the constant happens to be — not the documented value. |
| 64 members | **NOT PINNED** | The test builds `range(MAX_MEMBERS + 1)`, symbolically. `MAX_MEMBERS = 4` and `MAX_MEMBERS = 10000` both leave all 21 passing. No literal `64` appears anywhere in the tests. |
| 1 GiB uncompressed | **VALUE PINNED** | Asserts the literal `1024 * 1024 * 1024`; mutating to 64 MiB fails. This is the right pattern, and the other two should copy it. |

`container.py:49-52` describes the 200:1 bound as "Measured, not guessed". The only fixture
exercising it sits five times past it.

**Defect 2 — the filename is a hard prefilter, contradicting the PR's headline claim.**
The CHANGELOG says *"A member is classified by its leading bytes, never by the name it was
stored under."* Verified by execution against the real module:

```
CSV with .csv extension        member='standardcharges.csv'  -> text   (accepted)
CSV with NO extension          member='standardcharges'      -> no_gradeable_member
CSV named .txt                 member='standardcharges.txt'  -> no_gradeable_member
JSON with NO extension         member='charges'              -> no_gradeable_member
```

`_choose` (`container.py:205`) skips any member whose name does not end in `.json`/`.csv`
*before* sniffing. **This project's own README, in this very PR, records four extensionless
CSV endpoints in the committed draw** — so this is an observed shape, not a hypothetical,
and such a member is refused with `no member is a document this project has a profile for`,
which reads as the publisher's fault. Relatedly, the nested-archive check
(`container.py:160`) is purely suffix-based: a real nested ZIP stored as `charges.csv` is
refused as `NO_GRADEABLE_MEMBER` rather than `NESTED_ARCHIVE`. Still refused — no bypass —
but the published reason is wrong.

**Defect 3 — the lifted member leaks, and `--format json` goes silent.** Verified by running
the real CLI on a ZIP that passes every stated bound but whose deflate stream fails its CRC:

```
EXIT=1   stdout (--format json): <empty>
stderr: error: Bad CRC-32 for file 'standardcharges.json'
left behind: .standardcharges.zip.standardcharges.json  (1048576 bytes)
```

`cli.py:52-54` copies the member out with no `try/finally`, contradicting the CHANGELOG's
"removes the lifted member afterwards", and the copy goes to the publisher file's own
directory, not a temp dir, at up to the full 1 GiB ceiling.

**What is clean (verified).** Bounds really are read from the central directory before any
`archive.open`; `zipfile` caps reads at the declared `file_size`, so a lying directory
cannot exceed the ceiling. Path traversal is solid: absolute paths, `..`, Windows
backslashes and drive letters all refuse. The encryption check uses GP bit 0 correctly. A
refused container really does emit no inspection at all.

**Recommendation: `needs work`** — (a) pin the ratio and member-count constants with literal
assertions plus just-under-the-bound acceptance fixtures; (b) wrap the member copy in
`try/finally` and route a mid-copy failure into the `container` refusal channel; (c) either
make candidate selection byte-based or soften the CHANGELOG claim to match the code.

---

## #37 — Phase 10: a removal is honoured on request, and here is what went wrong
- **Base:** `master`. **Head:** `feat/phase-10-corrections`. **State:** CLEAN, all checks green.
- **Unique contribution:** `docs/CORRECTIONS.md`, two issue-form templates, `tests/test_corrections.py`, `ci.yml` fetch-depth, `pyyaml` promoted to the dev group.

**What it does.** Publishes a corrections and removal page whose load-bearing promise is
that a removal is honoured on request with no proof required, wires it into every rendered
page's footer, and writes up ten things this project has already got wrong, each naming the
commit that fixed it.

**Correctness — the best-guarded PR in the stack.** Verified by execution: all **8** commits
cited by `docs/CORRECTIONS.md` (`0411e87`, `12db7ff`, `435270e`, `a9b5946`, `c05ddbb`,
`c0d0886`, `f1b1e82`, `ff8ebe4`) resolve to real commit objects **and** are ancestors of
`origin/master`, so the citation gate will hold after merge. The parametrization hazard the
rest of this portfolio keeps falling into is explicitly guarded:
`test_the_page_cites_commits_at_all` asserts `len(CITED_COMMITS) >= 6`, and a separate test
fails loudly on a shallow clone rather than skipping. `ci.yml` gains `fetch-depth: 0` to
match. `pyyaml` moves from a transitive `pre-commit` dependency to a declared dev
dependency, which is correct.

**Minor.** `test_every_cited_commit_is_reachable_from_the_default_branch` checks ancestry
of `HEAD`, not of `origin/master`, so its docstring is slightly stronger than the assertion.
On `master` post-merge the two coincide.

**Recommendation: `merge`** (as part of #40). No blocking defect found.

---

## #36 — Phase 9: a read-only MCP server that refuses what the site refuses
- **Base:** `master`. **Head:** `feat/phase-9-mcp-server`. **State:** CLEAN, all checks green.
- **Unique contribution:** `src/mrf_honest/mcp.py`, `tests/test_mcp.py`, `cli.py` subcommand.

**What it does.** A stdlib-only JSON-RPC 2.0 stdio server exposing five read-only tools over
the `api/` documents phase 8 writes. Its stated purpose is that it refuses what the site
refuses, above all cross-cohort pooling.

### BLOCKING DEFECT — path traversal in `cohort_id` defeats the boundary the PR exists to defend

`_load_cohort` (`mcp.py:135-140`) joins user input straight into a path with no
normalization and no membership check against the published index:

```python
path = site_dir / "api" / "cohorts" / f"{cohort_id}.json"
```

**Verified by execution** (I built a site tree and called the real handler):

```
cohort_id='no-such-cohort'                        -> refused: "no published cohort"        (control)
cohort_id='../../../secretplace/notacohort'       -> ANSWERED
    { "cohort_id": "../../../secretplace/notacohort",
      "comparison_scope": null,
      "count": 1,
      "files": [ { "slug": "leaked/row", ... } ] }
```

Any readable JSON file on disk becomes a "cohort". The `unknown cohort` refusal is
bypassable, and the answer comes back with **`comparison_scope: null`** — grades served with
no scope boundary at all, which is precisely the one thing `docs/how-we-compare.md` exists to
prevent. `cohort_statistics` has the same hole.

Fix: require `cohort_id in _cohort_ids(index)` before touching the filesystem, and reject
any `cohort_id` that is not a plain name.

**Secondary defects.** `_slugs` (`mcp.py:214-218`) returns `[]` when a cohort named by the
index is missing, so `list_files` with no `cohort_id` answers `{"cohorts": {"gone": []}}`
while the *same* missing document reached by name refuses — both contradict the module's own
"never an empty result set" rule at `mcp.py:148`. `_grading_method` returns
`{"policies": {}}` for an index with no policies: the tool whose stated job is "read this
before characterizing any grade" answers with zero rules and no refusal.

The "names no network module" test is a substring grep over one file's source. It is not the
degenerate case (the list does include `urllib` and `socket`), but verified: importing
`mrf_honest.mcp` pulls `socket`, `ssl`, `urllib.request` and `http.client` into the process
anyway, because `mrf_honest/__init__.py` eagerly imports `fetch`. The docstring's claim is
stronger than the check.

**What is clean (verified).** All five advertised refusals fire and the pooling guard cannot
be bypassed by `cohort_id` shape — absent, `None`, `""`, `" "`, `0` and `["a","b"]` all end
in a refusal, and `grade=""` refuses because the code tests `grade is not None` rather than
truthiness. The grade filter is applied strictly after the cohort is resolved. A 12-mutation
battery killed every guard.

**Recommendation: `needs work` (blocking).** Do not merge #36 — or #37, #38, #39 or #40,
all of which contain it — until `_load_cohort` validates `cohort_id` against the index.

---

## #35 — Phase 8: the grades leave the HTML
- **Base:** `master`. **Head:** `feat/phase-8-dataset-and-api`. **State:** CLEAN, all checks green.
- **Unique contribution:** `src/mrf_honest/dataset.py`, `tests/test_dataset.py`, `site.py` and `pages.yml` wiring.

**What it does.** One `COLUMNS` tuple drives `dataset.csv` (RFC 4180), a generated Table
Schema, and a static JSON API, all written by the existing render so there is no second
pipeline to drift. Adds `missing_exports()` as a deploy-path gate.

**Correctness.** Good. Verified: the Table Schema is genuinely generated from the same
`COLUMNS` tuple as the CSV header (mutating the comprehension to `COLUMNS[:-1]` reddens four
tests), every `missing_exports` check can genuinely fail, and the CRLF test bug the PR body
confesses to is **actually fixed in the committed code** — the tests use
`read_text(...).splitlines()` and rewrite with `newline=""`, and the buggy form provably
returns `[]` where the fixed form returns
`['dataset.csv holds 1 rows against 2 expected']`. I also verified both deploy-path
one-liners in `pages.yml` call real functions with matching signatures.

**Defect 1 — a vacuous glob, including the anti-pooling test.**
`tests/test_dataset.py:33`: `PUBLISHED = sorted(COHORTS.glob("*.comparison.json"))` has
**no non-emptiness guard**, unlike `tests/test_published_claims.py:45`, which has one.
Verified: with the corpus removed, 6 tests fail but **5 pass vacuously**, including
`test_every_row_carries_the_scope_that_makes_it_uncomparable` — the test that enforces the
project's central rule. One `assert PUBLISHED` at module scope fixes it.

**Defect 2 — `missing_exports` claims more than it checks.** Its docstring says "Report
every way the written exports disagree with the documents they came from… An empty list
means the exports say what the comparisons say." Verified by execution against a rendered
site: rewriting **every** `grade` cell to `A` and forging the `profile` column returns `[]`;
emptying every `api/cohorts/*.json` down to `{"cohort_id": …}` returns `[]`. It is a shape
check (row counts, header order, key presence), which is what the `pages.yml` comment
accurately says — the docstring is the overclaim.

**Minor.** The `newline=""` on the dataset write is untested: replacing it with a
LF-normalizing write leaves all 24 tests green, because the RFC 4180 test asserts on the
in-memory string and `missing_exports` reads with universal newlines.

**Recommendation: `merge after rebase`** *(i.e. as part of #40)*, with two small fixes:
add `assert PUBLISHED`, and either strengthen `_dataset_problems` to compare rows rather
than counts or soften the docstring.

---

## #34 — Phase 7: the first population statistic this project has published
- **Base:** `master`. **Head:** `feat/phase-7-published-statistic`. **State:** CLEAN, all checks green.
- **Unique contribution:** `cohort.py` statistics block, `site.py` rendering, `missing_shares()`, regenerated comparison documents.

**What it does.** Wires phase 6's module into `build_comparison` (`COMPARISON_VERSION` 2→3,
a new always-present `statistics` block, a sixth refusal code `incomplete_accounting`),
renders shares as a table or a refusal paragraph, and regenerates the three committed
comparison documents.

**Correctness of the numbers — verified, and they are right.** Every published figure was
independently recomputed and matches the committed JSON to the last digit:

| Claim | Committed | Recomputed |
|---|---|---|
| 11 of 48 = 22.9%, 13.4–36.4 | 0.13365390552860928 / 0.36423853523652006 | identical |
| 32 of 48 = 66.7%, 52.7–78.2 | 0.5265198868962656 / 0.7824693803251931 | identical |
| 4 of 48 = 8.3%, 3.3–19.4 | 0.033110837505783836 / 0.19441599444056903 | identical |
| 1 of 48 = 2.1%, 0.4–10.8 | 0.0037277574388831433 / 0.10792809929942271 | identical |

The cohort accounting balances (11+32+4+1 = 48). The `2026-08-14` convenience cohort
correctly gets `refusal: no_sampling_frame` rather than numbers. Structural diff confirms
the change to the committed documents is purely additive.

**Defect 1 — documentation drift shipped by this PR.** It adds a sixth refusal code, then
leaves ADR 0007's "**Five** refusals, each a published outcome" heading and five-row table
untouched, and `docs/how-we-compare.md` — edited *in this same PR* — still says "the five
refusals". The missing sixth, `incomplete_accounting`, is the one
`_population_statistics`'s own docstring calls "the one that fires most often in practice"
and is the refusal actually rendered on the published CSV cohort page.

**Defect 2 — the ADR's stated guard is not the code that ships.** Verified: `estimate_over`
appears only in `statistics.py`, its tests, and the ADR. ADR 0007 presents it as the
anti-pooling gate; `_population_statistics` re-implements that decision inline at
`cohort.py:605-611` and calls `estimate_proportion` directly. Consequently the
`POOLED_STRATA` arm at `cohort.py:610` is **dead code** — `read_strata` reads exactly one
hard-coded probability-stratum key, so `probability_strata()` can never return more than one
element. The `CONVENIENCE_STRATUM` arm is reachable but untested.

**Defect 3 — the deploy gate does not check the interval reached the page.**
`missing_shares` (`site.py:533`) looks only for `f"{numerator} of {denominator}"`. Delete
the Share and Interval `<td>`s from `_estimate_row` and the gate stays silent — in the module
whose whole thesis is that a point estimate must never be published without its interval.

**Minor.** `_stratum_dispositions` counts every manifest exclusion into the probability
stratum; correct for the committed data, but one stratum-A exclusion plus one stratum-A row
would balance the `denominator != sample_size` check and publish silently wrong shares.

**Recommendation: `needs work`** — fix the five/six refusal drift in ADR 0007 and
`how-we-compare.md`, extend `missing_shares` to require the interval, and either delete the
dead `POOLED_STRATA` arm or route `_population_statistics` through `estimate_over` so the
guard the ADR describes is the one that ships.

---

## #33 — Phase 6: the sampling frame's rule becomes a gate, not a paragraph
- **Base:** `master`. **Head:** `feat/phase-6-suppression-and-uncertainty`. **State:** CLEAN, all checks green.
- **Unique contribution:** `src/mrf_honest/statistics.py`, `tests/test_statistics.py`, ADR 0007.

**What it does.** A stdlib-only module returning either a frozen `Proportion` (Wilson score
interval, finite-population correction, stated denominator) or a `Refusal` with one of five
codes. Publishes nothing itself.

**The arithmetic is correct — verified hard.** `_effective_sample_size` returns
`n(N-1)/(N-n)`; for n=48, N=3024 that is **48.75806451612903**, exactly the claimed 48.758.
The convention is the right one: SRSWOR shrinks variance, so expressing the correction as an
effective size requires `n_eff > n`, and the corrected interval is verifiably *narrower*
than the uncorrected one for all four shares. The Wilson body was re-derived by an
independent second algebra (solving the quadratic for its roots) with agreement to ≤1.1e-16.
All seven rows of ADR 0007's width table check out exactly. The saturated-interval clamp
moves a bound by at most 1 ULP across ~40,000 enumerated cases and masks nothing.

**Defect — the tests gate none of those numbers.** This is the portfolio's dominant defect
shape, in its purest form. Mutation results against the module's own suite:

| Mutation | Tests that fail | What it would publish for 11 of 48 |
|---|---|---|
| `Z_95` doubled | **none** | 7.8% to 51.0% |
| `sqrt` dropped from `spread` | **none** | 22.9% to 25.6% |
| FPC inverted | 1 | 13.3% to 36.7% |
| `center=observed, spread=0` | 3 of 8 | 22.9% to 22.9% |

**No test anywhere pins a single interval value.** The strongest assertions are containment
— which the clamp makes a syntactic identity, so the named regression test
`test_a_saturated_interval_contains_its_own_point_estimate` cannot fail regardless of the
arithmetic above it — plus sign and monotonicity. Two mutations that change every published
number survive the entire suite. In #34 this is *partially* covered by the byte-for-byte
golden comparison against the committed JSON, but that is a snapshot, not a derivation, and
its failure message says "Regenerate it". An error present at introduction would ship green.

**Defect — the suppression floor is not pinned either.** Both boundary tests are written
symbolically (`SUPPRESSION_THRESHOLD - 1`, `SUPPRESSION_THRESHOLD`), so they pin
inclusivity but not the value. The constraint the whole suite imposes is
**13 ≤ threshold ≤ 20**; set it to 15 and everything passes but the golden file. ADR 0007's
seven-row width table, the entire measured justification for choosing 20, has no gate at all.

**Stale rationale.** ADR 0007 argues 20 was chosen partly so as not to retroactively suppress
"the CSV cohort's declared target list is 25". #34 makes that cohort refuse with
`incomplete_accounting` before the floor is ever consulted, so half the floor's stated
rationale is moot as of the very next PR.

**Recommendation: `needs work`** — add at least one test asserting a *literal* interval
(e.g. `wilson_interval(11, 48, …) == (0.13365390552860928, 0.36423853523652006)`), which
kills all four mutants above, and a test that recomputes ADR 0007's width table from the
code so `SUPPRESSION_THRESHOLD = 20` and its published justification are pinned to each
other.

---

## #30 — build: bump mypy from 2.3.0 to 2.3.1
- **Base:** `master`. **Head:** `dependabot/uv/mypy-2.3.1`. **State:** `UNSTABLE` (mergeable, one failing check). Author: dependabot.
- Changes `uv.lock` only (+42/−33, 1 file).

**Merge state is real but the failure is not code.** Verified from the job log: the only red
check is Lighthouse, failing at `/404.html: performance 0.93 below the floor of 0.95`. The
job ran 15 steps over 8m52s — not starved. A lockfile bump cannot change a static 404 page;
this is measurement noise against a self-imposed floor 0.05 above the standard's 0.90.
Verified that `origin/master` currently locks mypy 2.3.0, so the bump is current, not stale.

**Recommendation: `merge`** — re-run the Lighthouse job first if a green tick is wanted.
Merge *after* the stack, so Dependabot rebases onto the `uv.lock` that #37 modifies.

---

## #29 — build: bump hypothesis from 6.165.9 to 6.165.10
- **Base:** `master`. **Head:** `dependabot/uv/hypothesis-6.165.10`. **State:** CLEAN, all checks green. Author: dependabot.
- Changes `uv.lock` only (+64/−64, 1 file). Verified `origin/master` locks 6.165.9, so it is current.
- Verified: merges cleanly against the stack tip, and against #30.

**Recommendation: `merge`** — after the stack, for the same rebase reason as #30.

---

## #27 — narrate refuses before the model call when no passage can be offered
- **Base:** `master`. **Head:** `fix/narrate-refuses-without-passages`. **State:** CLEAN, all checks green. Closes issue #26.
- +190/−4 across 7 files. Merge-base is `f13e426`, one commit behind `origin/master`.

**What it does.** When a record's findings cite no retained corpus document — or there are no
findings — every claim the model could write would be withheld for lack of a citation, so
`narrate()` now returns before calling the provider. The refusal is recorded in provenance
(`refusal`, `model_called: false`, zero tokens, provider and model still named).

**Correctness.** Verified that the fix is **not** already on `origin/master`, so the PR is
live and not stale. `refusal_reason()` is a clean two-branch function that returns `None`
whenever any passage exists, so it cannot suppress a narration that could have said
something. The test is the right shape: it drives both languages through
`ScriptedProvider([])`, where any call would raise, so it proves the call is *not made*
rather than asserting on its absence indirectly, and it asserts the full provenance record
including that provider, model, grade and label survive the refusal.

**Overlap.** Touches `CHANGELOG.md` and `src/mrf_honest/cli.py`, both of which the stack also
touches. Verified with `git merge-tree`: **the stack and #27 merge cleanly, exit 0, no
conflicts.**

**Recommendation: `merge`.** The smallest, cleanest, most self-contained PR in the queue, and
the only one that closes an open issue. Merge it first.

---

# Cross-cutting finding: the stack repeats a mistake its own corrections page documents

Verified: the whole stack (`12ac231..15b52f6`) **never updates** the metrics-ledger row in
`docs/ROADMAP.md:84` — `92.44%, 445 tests passing, 2026-08-21` — nor the README's
`Current: 445 tests, 92.44% branch coverage … (2026-08-21)`. The PR bodies report the suite
growing 445 → 476 → 500 → 524 → 548 → 573 → 598 → 614 → **655**.

`docs/CORRECTIONS.md`, added by #37 *inside this same stack*, lists as correction 9:

> A ledger row said 262 tests when the merged stack had 324; the number came from one branch…

Merging phases 6–14 without touching that row reproduces the exact defect the page publishes
an apology for. Both figures are dated, so they are stale rather than fabricated — but this
project's own standard is higher than that.

---

# Safe order of operations

Nothing below should be executed until the two blocking items are fixed.

**Blocking, in order of severity:**

1. **#36's path traversal** (`mcp.py:135-140`). Blocks #36, #37, #38, #39 and #40, since
   every one of them contains it. This is the only finding I would call a security issue.
2. **#40's release-job `fetch-depth: 0`.** One line; blocks only #40.

**Then:**

| Step | Action | Why this order |
|---|---|---|
| 1 | Merge **#27** | Independent, green, closes issue #26. Verified conflict-free with everything else. Landing it first keeps the smallest change out of the stack's blast radius. |
| 2 | Fix the two blocking items on `feat/phase-14-release-path` | Both live in commits #40 already contains, so fixing them there fixes them for the whole stack. |
| 3 | Merge **#40** alone | Contains phases 6–14 exactly. One merge, zero conflicts (verified `mergeStateStatus: CLEAN`). |
| 4 | Close **#33–#39** as *contained in #40* | Their branches are not merged, so GitHub will not auto-close them; after step 3 their diffs against `master` are empty. None is stale or a duplicate — each is a real review unit whose content ships in #40. |
| 5 | Merge **#29**, then **#30** | Both touch `uv.lock`, which #37 also modifies (adding `pyyaml`). Dependabot will rebase them automatically after step 3. Re-run #30's Lighthouse job if a green tick is wanted. |
| 6 | Follow-ups | The non-blocking fixes listed per PR above, plus the ledger row. |

**Do not merge the stack in ascending order.** Verified: seven of the eight steps conflict,
in `CHANGELOG.md` every time and in `statistics.py`, `site.py`, `test_site.py`, `README.md`,
`ROADMAP.md` and `how-we-compare.md` besides. There is no content gain, because #40's tree
already equals the result.

**If per-phase merge history is wanted anyway**, the only clean way is to rebase each branch
onto its predecessor for real (`git rebase --onto`) so that the stack is a stack in git as
well as in prose, then merge bottom-up. That is a rewrite of eight branches and is probably
not worth it.

---

# Separately: a defect on `master` that no open PR addresses

**Issue #28** — the CSV inspector skips the mandatory-description and mandatory-setting
checks on a charged row that also lacks a code pairing. No open PR touches
`src/mrf_honest/inspect_csv.py`; verified across all eleven.

PR #31 attempted it and was **correctly closed**: widening the gate to `is_item or
any_charge` also captures modifier rows, where CMS note 11 makes a blank `setting`
acceptable, and it made `CMS_CSV_MODIFIER_ROW_CONTEXT_MISSING` unreachable.

Reproduced on `master` (Tall template, one data row):

| Row | `master` reports | Should report |
|---|---|---|
| charged, no codes, no description, no setting | `CODE_PAIRING_MISSING` | + `DESCRIPTION_MISSING`, `SETTING_INVALID` |
| charged, no codes, has description, blank setting | `CODE_PAIRING_MISSING` | + `SETTING_INVALID` |
| modifier row, description + payer dollar, blank setting | *(clean)* | *(clean — note 11)* |
| modifier row, no description | `MODIFIER_ROW_CONTEXT_MISSING` | unchanged |

Spec basis verified in the corpus: `corpus/cms/csv-data-dictionary.md:70` (`description`)
and `:73` (`setting`) are both **Blanks Accepted: No** in the required standard-charge
data-element table, independent of whether the row's code columns are populated.

### The fix, as applied in the working tree (not committed, no PR opened)

`_check_item_completeness` now dispatches to three named helpers instead of one nested
chain, and gains a branch for charged rows that are neither items nor modifier rows.
The modifier branch is reached first and is unchanged, so every behaviour PR #31 broke
stays intact. The refactor was required, not cosmetic: adding the branch inline pushed the
function to McCabe 11 against the repo's `max-complexity=10`, and CONTRIBUTING says to fix
the change rather than the floor.

Both new tests were verified non-vacuous by mutation:

| Test | Fails when |
|---|---|
| `test_a_charged_row_without_codes_still_owes_a_description_and_a_setting` | the fix is reverted to pre-fix `master` |
| `test_a_charged_modifier_row_is_still_exempt_from_the_setting_requirement` | PR #31's `is_item or any_charge` approach is applied |

Gates: `ruff check`, `ruff format --check` and `mypy --strict` all clean.

### One test is deliberately left red, and it is the gate working

`inspect_csv.py:511` says *"Bump this value whenever inspection behavior changes without a
corresponding catalog or rule-set change"*, so `CSV_INSPECTION_POLICY_VERSION` moves to
`…-inspection-v2`. That changes the assessment policy fingerprint, and the committed
`hospital-csv-v3-2026-08-19` cohort was assessed under v1, so:

```
FAILED tests/test_published_claims.py::test_committed_comparison_is_reproducible_from_committed_inputs[2026-08-19-csv.comparison.json]
        AssessmentRegistryError: comparison scope does not match assessment context
446 passed, 4 skipped, 1 failed
```

That is the fail-closed design doing exactly what its own comment promises — prior findings
cannot be silently reused after grading semantics change — not a regression from the fix.

**It cannot be resolved from here.** Re-assessing the cohort needs the retrieved bodies
(`data/cache/`, 6 GB and gitignored), and it will change published finding counts for named
hospitals. That is a publishing decision and an operator-invoked act, so it is left to the
maintainer. The two options are: re-assess and regenerate
`data/cohorts/2026-08-19-csv.comparison.json`, or drop the version bump and accept that two
inspection semantics share one fingerprint — which the code comment forbids, and which is
the weaker choice for a project whose case rests on findings being traceable to the exact
policy that produced them.
