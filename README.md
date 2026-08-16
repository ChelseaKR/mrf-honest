# mrf-honest

**Deterministic, spec-cited grades for hospital price-transparency files, published with the
evidence attached.**

The first graded cohort is live: six machine-readable files across four health systems,
discovered from CMS-conventional `cms-hpt.txt` documents, retrieved in one identified run,
streamed without loading into memory, and graded fail-closed. Five files grade **A** under the
published policy; one grades **C** — an 884 MB file that still declares the superseded 2.0.0
template more than seven months after CMS's v3.0.0 effective date
([the finding, with evidence](docs/findings/superseded-template-version-2026-08-14.md)). Four of
the six files begin with a UTF-8 byte-order mark that RFC 8259 forbids and strict JSON parsers
reject. Every grade, count, and finding on the
[site](https://chelseakr.github.io/mrf-honest/) is generated from the committed
[comparison document](data/cohorts/2026-08-14.comparison.json), never typed in, and each finding
cites the CMS rule ([45 CFR § 180.50]) or
[CMS schema documentation](https://github.com/CMSgov/hospital-price-transparency) it rests on.

The first real cohort also broke the pipeline twice, and both breaks are published: a CSV
dialect the spool reader guessed instead of declared (fixed, regression-pinned), and a default
memory ceiling the two largest exports exceeded (an operator setting, documented). Finding that
out on six files instead of six hundred is the point of grading a small cohort first.

## The idea in one paragraph

US hospitals and health insurers are legally required to publish machine-readable files (MRFs)
of their prices: what hospitals charge, and what payers have negotiated to pay. The files are
public, mandated, and enormous, and they are also famously difficult to use, because technical
compliance and actual usability are different things. `mrf-honest` ingests those files at real
scale, grades each published *file* on whether it is genuinely usable, and publishes the method,
the evidence, and its own mistakes alongside the grades. Rate comparisons are deliberately not
published until the suppression and uncertainty work exists to publish them honestly.

## Quickstart

```sh
uv sync
make verify

# Inspect a local CMS hospital JSON v3 file. Findings are observations, not a compliance ruling.
uv run mrf-honest inspect prices.json --as-of 2026-08-09 --format json

# Build a contracted local snapshot. DuckDB is supplied by the dev group or the lakehouse extra.
uv run mrf-honest ingest prices.json \
  --publisher-id example-health \
  --warehouse warehouse \
  --as-of 2026-08-09 \
  --format json

# Retrieve one file and atomically retain its remote-plus-local scorecard.
uv run mrf-honest scorecard https://files.example.org/standardcharges.json \
  --publisher-id example-health \
  --publisher-type hospital \
  --location-id main-campus \
  --url-provenance cms_hpt \
  --registry data/scorecards.jsonl \
  --cache-dir data/cache \
  --contact operator@example.org \
  --format json

# Turn one attested collection run into the published comparison, then render the site.
uv run mrf-honest compare \
  --assessments data/cohorts/2026-08-14.assessments.jsonl \
  --manifest data/cohorts/2026-08-14.json > comparison.json
uv run mrf-honest site --comparison comparison.json --out site
```

The CLI also provides `discover`, `fetch`, `profile`, and `explain`; `grade` is an alias for
`scorecard`. Retrieval requires an identifying contact string, caches decoded content by
SHA-256, validates HTTPS redirects, applies size limits and retry backoff, and records discovery
attempts—including failures—in append-only JSONL evidence.

## What a grade is, and is not

A letter grade here describes **one published file under one stated, fingerprinted policy on
one date** ([docs/how-we-compare.md](docs/how-we-compare.md), [ADR 0005](docs/adr/0005-presentation-grade-layer.md)).
It never ranks hospitals, never prices care, and never determines compliance with
[45 CFR § 180.50] or any other law. The grade is fail-closed in both directions: a file the
public cannot download is a stated **F** with the dated reason, and a target this project's own
limits prevented assessing is **not graded** — stated, never silently dropped, and never
conflated with a publisher failure. That boundary is enforced by a status matrix rather than by
care: a certificate that will not verify, a `robots.txt` that says no, and this project's own
size ceiling are all **not graded**, because from one attempt none of them is distinguishable
from a problem on this end. An **A** means the implemented checks emitted nothing; it is
not the official CMS validator and not a certificate of validity.

## Why this shape

Two reasons, and the second one is the honest one.

**The public-interest reason.** Price transparency rules produced files, and files are not
transparency. A hospital can publish a technically conforming document that no one can act on,
and nothing in the rule distinguishes that from a good-faith publication. Grading the difference
is useful and nobody is doing it in the open.

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
spec-cited, and the project publishes its own errors. A commercial product has a structural
reason to make its data look more complete and more comparable than it is. An open project has
the opposite incentive, and that is the whole value proposition.

## What exists today, and what remains

Built:

- A standard-library streaming JSON reader with bounded problem samples and peak memory tied to
  one item rather than the whole file ([ADR 0002](docs/adr/0002-stdlib-only-streaming-core.md));
  the phase-0 measurements are in [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md). The
  largest file in the current cohort is 883,973,507 bytes and streams to completion.
- Five-field, multi-location `cms-hpt.txt` discovery, an identified conditional fetcher,
  content-addressed cache, and a v2 append-only registry that retains dated success and failure
  evidence.
- A deterministic five-dimension file assessment — retrievability, conformance, completeness,
  interpretability, freshness — with a source-cited finding catalog
  ([docs/how-we-grade.md](docs/how-we-grade.md)) and integrity-hashed persisted records
  ([ADR 0004](docs/adr/0004-separate-remote-scorecard-artifacts.md)).
- A DuckDB + partitioned-Parquet lakehouse with 13 documented models, executable data contracts
  at every layer boundary, exact raw text retention, `DECIMAL(38,10)` numerics, and idempotent
  content-addressed run identity ([docs/MODEL-DAG.md](docs/MODEL-DAG.md),
  [ADR 0003](docs/adr/0003-local-lakehouse-duckdb-parquet.md)). Five of the six cohort files are
  contracted through it; the sixth is a v2.0.0 file the v3-only pipeline correctly refuses.
- `robots.txt`, per-host pacing and `Retry-After` enforced in the fetcher rather than by an
  operator's habits (`src/mrf_honest/politeness.py`). robots is fetched before the first request
  and obeyed with no override flag; an unreachable `robots.txt` is a complete disallow per
  RFC 9309 section 2.3.1.4 and a 4xx means none exists per 2.3.1.3; a per-host minimum interval
  is held across a whole run and a `Crawl-delay` can only lengthen it; `Retry-After` on 429 and
  503 outranks this tool's own backoff. A robots disallow is **not graded**, never an F: it is a
  fact about this crawler's permission, not about whether the hospital published. Measured by a
  localhost-server suite in `tests/test_politeness.py`.
- A comparison layer (`mrf-honest compare`) that turns one attested collection run into a
  published comparison under a versioned, fingerprinted grade policy, refusing mixed scopes,
  unattested runs, and duplicate subjects ([docs/how-we-compare.md](docs/how-we-compare.md)).
- A dependency-free static site (`mrf-honest site`) with one indexable page per graded file,
  spec citations on every finding, verification provenance down to the content SHA-256, and a
  fail-closed coverage statement, deployed by a SHA-pinned Pages workflow that rebuilds only
  from committed data.

Still open:

- structural separation of dollar, percentage, and algorithm representations exists; the
  phase-4 small-cell suppression and uncertainty intervals do not, so **no price comparison is
  published anywhere**;
- hospital CSV and payer-MRF adapters (there is no payer-MRF pipeline yet; a `.zip`/CSV
  publication in the current cohort is recorded and excluded rather than mis-graded);
- a second, roster-sourced cohort. The first was purposive, which is right for a shakedown run
  and wrong for a rate; retrieval politeness is now in code (below), so the prerequisite is met;
- safe concurrent-writer coordination, supported warehouse migrations, and a full SIGKILL/fsync
  crash matrix; and
- the phase-5 dataset export, API, MCP server, and release process.

## Documents

| Document | What it covers |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | Why this project exists, what gaps it closes, when to build it |
| [docs/DATA-LANDSCAPE.md](docs/DATA-LANDSCAPE.md) | What MRFs actually are, the schemas, the scale, the known pitfalls |
| [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) | Phased build plan with decision points and stop conditions |
| [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md) | Measured phase-0 constraint study and the phase-1 streaming result |
| [docs/PHASE-2-FINDINGS.md](docs/PHASE-2-FINDINGS.md) | Real-file lakehouse acceptance, counts, storage, and limits |
| [docs/PHASE-3-FINDINGS.md](docs/PHASE-3-FINDINGS.md) | Fail-closed remote scorecard contract, verification, and limits |
| [docs/MODEL-DAG.md](docs/MODEL-DAG.md) | Model grains, lineage, contracts, and methodology-safe query |
| [docs/how-we-grade.md](docs/how-we-grade.md) | Assessment semantics and the source-cited finding catalog |
| [docs/how-we-compare.md](docs/how-we-compare.md) | The comparison boundary and the published file-grade policy |
| [docs/findings/](docs/findings/) | Written-up findings from published cohorts, with evidence |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Current position, observability declaration, metrics ledger |
| [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) | Responsible-tech declarations for the current scope |

## Standards Conformance

Per the portfolio's standards set. N/A rows carry a reason, and the judgment-call ones cite an
ADR in [docs/adr/](docs/adr/). No blank rows, no silent skips.

| Standard | State |
|---|---|
| Code Quality | Applies: `make verify` runs six gates — `ruff check` (security `S` rules, `max-complexity=10`), `ruff format --check`, `mypy --strict`, pytest with a branch-coverage floor of 85, `uv lock --check`, and `pip-audit --strict` over the exported lockfile. Current: 262 tests, 90.73% branch coverage, zero lint/format/type findings, lockfile in sync, zero known vulnerabilities (2026-08-15). Floors: Python >= 3.12 (`.python-version` pins 3.14), ruff >= 0.15, mypy >= 1.18, locked in `uv.lock`. Dev tooling is a PEP 735 `[dependency-groups]` group, so `uv sync` installs it and a published wheel never carries it. |
| Security & Supply-Chain | Applies: the streaming, inspection, discovery, fetch, registry, comparison, and site path is standard-library-only; DuckDB is an optional lakehouse dependency ([ADRs 0002-0003](docs/adr/)). The lockfile, ruff `S` gate, HTTPS/redirect validation, bounded downloads, and SHA-pinned CI actions reduce the current surface. Hosted CodeQL (Python and Actions) and a checksum-pinned full-history gitleaks scan run on push, PR, and weekly schedule (`.github/workflows/security.yml`). `make verify` runs `pip-audit --strict` against the whole exported lockfile — every extra and the dev group — with no ignore list, so the audit runs on a laptop and in CI rather than only in CI. The lockfile-drift gate is `uv lock --check`, not `uv sync --frozen`: measured on a deliberately drifted project under uv 0.12.1, `uv lock --check` and `uv sync --locked` exit 1 and `uv sync --frozen` exits 0, because `--frozen` installs from the lockfile without reading `pyproject.toml` and so cannot see the two disagree. |
| CI/CD | Applies: SHA-pinned workflows mirror `make verify` on Python 3.12 and 3.14, build distributions, and publish the site from committed data only, with a fail-closed render check. |
| Observability | Applies to the local batch shape plus a static published artifact: finalized run manifests and DuckDB `model_metric` rows retain counts, bytes, and wall time; the site is rebuilt from committed data with no availability objective declared. See [docs/ROADMAP.md](docs/ROADMAP.md). |
| Accessibility | Applies as of the site, and now gated. `.github/workflows/accessibility.yml` runs Lighthouse over **every** page the render produced — enumerated from the build, not typed into the workflow — and requires 1.0 on accessibility, best-practices and SEO, a declared floor above the standard's 0.90. `make verify` runs the parts that need no browser: a contrast assertion over every declared text/background pair in the design tokens, and a heading-order check on every generated page. Two real defects were found and fixed when the gate was first pointed at the live site (`heading-order` on the index; a 4.28:1 finding chip on every file page with a warning). The remaining open obligation is the manual screen-reader pass, stated in [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md). |
| Internationalization | Applies: the site and CLI are English-only by a recorded decision with its limits stated: [docs/I18N.md](docs/I18N.md). |
| AI Evaluation | N/A (no LLM or model component; the grading and comparison path is deterministic by design, a written engineering standard in [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)). |
| Quality & Metrics | Applies: metrics ledger in [docs/ROADMAP.md](docs/ROADMAP.md); every published number is measured or generated from committed data, never estimated. |
| Documentation | Applies: README, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff`, ADR log ([docs/adr/](docs/adr/)), findings log ([docs/findings/](docs/findings/)). |
| Responsible-Tech Framework | Applies: [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) (grades files, never organizations or care; dated appendices for the grade-bias review and the site's accessibility scope). |
| Performance | Applies as of the site. The same Lighthouse job asserts a performance floor and a resource budget in which every non-document resource type is zero: no scripts, no external stylesheets, no fonts, no images, no third parties. Measured 2026-08-15 across all nine pages: 1.0 performance, 12,197 bytes and one request on the heaviest page ([perf/baseline.json](perf/baseline.json)). The k6 latency rows of the performance standard are N/A with a reason recorded in the baseline: there is no server, only static files. |
| Release & Versioning | N/A (pre-publication as a package: no tags, no downstream consumers; the site is a continuously rebuilt artifact). [docs/adr/0001-release-versioning-na.md](docs/adr/0001-release-versioning-na.md). |

## Provenance

Personal open-source project, planned and built on personal time and equipment, unaffiliated with
any employer or client, past or present.

Built AI-assisted (Claude Code and OpenAI Codex). Every number in the docs and this README was
measured or generated from committed data, never invented; the maintainer reviews and owns every
line.

License: Apache-2.0.

[45 CFR § 180.50]: https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50
