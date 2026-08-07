# mrf-honest

**Status: early build, phases 0 and 1 of 5.** Phase 0 measured the constraint (naive parsing of
one 65 MB hospital file peaks at 506 MB resident); phase 1 shipped a streaming reader that holds
the same file to 27 MB. The numbers, and the bug found on the way, are in
[docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md). Nothing here claims more than what is
committed and measured.

## The idea in one paragraph

US hospitals and health insurers are legally required to publish machine-readable files (MRFs) of
their prices: what hospitals charge, and what payers have negotiated to pay. The files are public,
mandated, and enormous, and they are also famously difficult to use, because technical compliance
and actual usability are different things. `mrf-honest` is being built to ingest those files at
real scale, grade each publisher on whether their file is genuinely usable, and publish price comparisons
with honest statistics attached, including the uncertainty and the suppression that most price
comparisons quietly omit.

## Quickstart

```sh
uv sync --extra dev    # Python 3.14 (.python-version); creates .venv from uv.lock
make verify            # ruff (security rules) + mypy --strict + pytest, branch-coverage floor 85
```

## Why this shape

Two reasons, and the second one is the honest one.

**The public-interest reason.** Price transparency rules produced files, and files are not
transparency. A hospital can publish a technically conforming document that no one can act on, and
nothing in the rule distinguishes that from a good-faith publication. Grading the difference is
useful and nobody is doing it in the open.

**The portfolio reason.** This closes two specific gaps in the author's record, documented in
[docs/CONTEXT.md](docs/CONTEXT.md): modern data-platform engineering (lakehouse, declarative
modeling, data contracts, warehouse-scale cost thinking) and payer/claims economics. Those gaps
have cost real opportunities. This project is designed to close them with a working artifact
rather than a claim.

## What would make it different from what already exists

Commercial products already parse MRFs. Turquoise Health, Serif Health, and Payless Health all
work in this space and several are well funded. **This is not a first mover and the plan should
never pretend otherwise.**

The differentiator is the same one that runs through the rest of this portfolio: the methodology
is public, the statistics are honest about uncertainty, the quality grading is deterministic and
spec-cited, and the project publishes its own errors. A commercial product has a structural reason
to make its data look more complete and more comparable than it is. An open project has the
opposite incentive, and that is the whole value proposition.

## What exists today, and what is planned

Built and measured (phases 0-1):

- A streaming JSON reader (`src/mrf_honest/stream.py`) that handles files that do not fit in
  memory: 27 MB peak RSS on the 65 MB reference file that naive `json.load` takes to 506 MB, with
  UTF-8 BOM tolerance, standard library only ([ADR 0002](docs/adr/0002-stdlib-only-streaming-core.md))
- A `cms-hpt.txt` discovery module (`src/mrf_honest/discover.py`) for building the hospital
  registry automatically from domain lists, with findings instead of exceptions for unusable files

Planned (phases 2-5, see [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)):

- A local lakehouse (Parquet + DuckDB) with declared models and enforced data contracts
- A per-file quality scorecard, in the pattern of `gtfs-scorecard` and `fhir-scorecard`
- A published dataset with a documented schema, plus a static site and API
- Price comparisons that carry small-cell suppression and uncertainty intervals by construction

## Documents

| Document | What it covers |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | Why this project exists, what gaps it closes, when to build it |
| [docs/DATA-LANDSCAPE.md](docs/DATA-LANDSCAPE.md) | What MRFs actually are, the schemas, the scale, the known pitfalls |
| [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) | Phased build plan with decision points and stop conditions |
| [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md) | Measured phase-0 constraint study and the phase-1 streaming result |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Current position, observability declaration, metrics ledger |
| [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) | Responsible-tech declarations for the current scope |

## Standards Conformance

Per the portfolio's standards set. N/A rows carry a reason, and the judgment-call ones cite an
ADR in [docs/adr/](docs/adr/). No blank rows, no silent skips.

| Standard | State |
|---|---|
| Code Quality | Applies: `make verify` runs ruff (security `S` rules, `max-complexity=10`), `mypy --strict`, and pytest with a branch-coverage floor of 85 (93.48% measured 2026-08-07). Floors: Python >= 3.12 (`.python-version` pins 3.14), ruff >= 0.15, mypy >= 1.18, locked in `uv.lock`. |
| Security & Supply-Chain | Applies, gap tracked: runtime is dependency-free by design ([ADR 0002](docs/adr/0002-stdlib-only-streaming-core.md)); dev toolchain locked (`uv.lock`); ruff `S` rules in the gate. CI scanners (SAST, secret scan, dependency audit) land when the repo gains a remote and CI, a phase-5 deliverable. Declarations: [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) section F. |
| CI/CD | Applies, gap tracked: the repo is local-only with no remote, so no CI exists yet. `make verify` is the exact gate CI will mirror when it is wired (phase 5), with SHA-pinned actions per the portfolio standard. |
| Observability | N/A (no runtime service, hosted surface, or scheduled job at phases 0-1). Declared with its re-entry trigger in [docs/ROADMAP.md](docs/ROADMAP.md). |
| Accessibility | N/A (no human-facing HTML). The phase-5 static site brings this into scope before it ships. |
| Internationalization | N/A (no user-facing surface yet; English-only operator output): [docs/I18N.md](docs/I18N.md). |
| AI Evaluation | N/A (no LLM or model component; the grading and comparison path is deterministic by design, a written engineering standard in [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)). |
| Quality & Metrics | Applies: metrics ledger in [docs/ROADMAP.md](docs/ROADMAP.md); every published number is measured, never estimated. |
| Documentation | Applies: README, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff`, ADR log ([docs/adr/](docs/adr/)). |
| Responsible-Tech Framework | Applies: [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) (grades files, never organizations or care). |
| Release & Versioning | N/A (pre-publication, not consumed downstream): no tags, no consumers, no release process until phase 5. [docs/adr/0001-release-versioning-na.md](docs/adr/0001-release-versioning-na.md). |

## Provenance

Personal open-source project, planned and built on personal time and equipment, unaffiliated with
any employer or client, past or present.

Built AI-assisted (Claude Code). Every number in the docs and this README was measured, not
generated; the maintainer reviews and owns every line.

License: Apache-2.0.
