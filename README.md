# mrf-honest

**Status: working local hospital-JSON path through phase 2 and most of phase 3.** The repository
can discover and retrieve CMS hospital MRFs, inspect one without loading it into memory, and build
an idempotent DuckDB + partitioned-Parquet snapshot with executable contracts. It does not yet
publish cross-hospital comparisons, a site, or an API. The original memory constraint and parser
measurements remain in [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md); the lakehouse
acceptance evidence is in [docs/PHASE-2-FINDINGS.md](docs/PHASE-2-FINDINGS.md).

The final clean acceptance used a 64,828,148-byte University of Cincinnati Medical Center file:
streaming alone parsed 30,114 items with zero problems in 9.25 seconds at 32.30 MiB maximum RSS;
the full 13-model lakehouse completed in 46.66 seconds at 510.02 MiB maximum RSS. A verified warm
reuse re-hashed the archived source and all 13 Parquets in 0.34 seconds.

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
uv sync --extra dev
make verify

# Inspect a local CMS hospital JSON v3 file. Findings are observations, not a compliance ruling.
uv run mrf-honest inspect prices.json --as-of 2026-08-09 --format json

# Build a contracted local snapshot. DuckDB is supplied by the dev or lakehouse extra.
uv run mrf-honest ingest prices.json \
  --publisher-id example-health \
  --warehouse warehouse \
  --as-of 2026-08-09 \
  --format json
```

The CLI also provides `discover`, `fetch`, `profile`, and `explain`. Retrieval requires an
identifying contact string, caches decoded content by SHA-256, validates HTTPS redirects, applies
size limits and retry backoff, and records discovery attempts—including failures—in append-only
JSONL evidence.

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

## What exists today, and what remains

Built:

- A standard-library streaming JSON reader with bounded problem samples, strict delimiters,
  invalid-UTF-8 evidence, UTF-8 BOM tolerance, and peak memory tied to one item rather than the
  whole file ([ADR 0002](docs/adr/0002-stdlib-only-streaming-core.md)).
- `cms-hpt.txt` discovery, an identified conditional fetcher, content-addressed cache, and an
  append-only registry that retains dated success and failure evidence.
- A deterministic five-dimension file inspection: retrievability, conformance, completeness,
  interpretability, and freshness. Local inspection explicitly reports retrievability as
  `NOT_ASSESSED`; there is no composite score or organization ranking.
- A DuckDB + Parquet `hospital-json-v2` lakehouse with 13 exported raw, staging, intermediate,
  finding, and mart models; exact raw item/modifier text and SHA-256; `DECIMAL(38,10)` typed
  numerics; and all source codes retained as ordered `codes_json` rather than choosing a synthetic
  primary code ([model DAG](docs/MODEL-DAG.md)).
- Stable source-scoped row IDs; an immutable content-addressed source archive; run identity over
  pipeline version, publisher, content, inspection `as_of`, and a transformation fingerprint;
  the inspection policy/catalog fingerprint participates in that identity. Schema-v4 manifests
  add a digest over every immutable manifest field while retaining recoverable `prepared` →
  `success` finalization ([ADR 0003](docs/adr/0003-local-lakehouse-duckdb-parquet.md)).
- Modifier definitions, payer-plan mappings, and charge references remain separate typed grains.
  Setting-aware resolution exposes exact, canonical, unresolved, and setting-mismatch context,
  including the selected and candidate settings, instead of silently dropping a failed join.
  Disjoint inpatient/outpatient definitions may share a canonical code; overlapping applicable
  definitions fail their contract.
- The real acceptance file's 11 modifier definitions all omitted optional `setting`; synthetic
  regressions, not that one-file run, cover mismatch reporting, disjoint definitions, and overlap
  rejection.
- Structural separation of dollar, percentage, and algorithm representations. Only stated dollar
  observations reach the comparison-ready mart, and methodology remains an explicit segment.

Still open:

- combine remote retrieval evidence with local inspection into a published per-file scorecard;
- exercise and document a real multi-publisher query rather than only providing its safe shape;
- add hospital CSV and payer-MRF adapters (there is no payer-MRF pipeline yet);
- add `robots.txt` policy, per-host pacing, and `Retry-After` handling before broad scheduled
  retrieval;
- add safe concurrent-writer coordination and supported historical warehouse migrations, and test
  crash durability with a full SIGKILL/fsync matrix; and
- implement phase-4 suppression and uncertainty before publishing comparisons, then the phase-5
  dataset, static site, API, and release process.

## Documents

| Document | What it covers |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | Why this project exists, what gaps it closes, when to build it |
| [docs/DATA-LANDSCAPE.md](docs/DATA-LANDSCAPE.md) | What MRFs actually are, the schemas, the scale, the known pitfalls |
| [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) | Phased build plan with decision points and stop conditions |
| [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md) | Measured phase-0 constraint study and the phase-1 streaming result |
| [docs/PHASE-2-FINDINGS.md](docs/PHASE-2-FINDINGS.md) | Real-file lakehouse acceptance, counts, storage, and limits |
| [docs/MODEL-DAG.md](docs/MODEL-DAG.md) | Model grains, lineage, contracts, and methodology-safe query |
| [docs/how-we-grade.md](docs/how-we-grade.md) | Scorecard semantics and source-cited finding catalog |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Current position, observability declaration, metrics ledger |
| [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) | Responsible-tech declarations for the current scope |

## Standards Conformance

Per the portfolio's standards set. N/A rows carry a reason, and the judgment-call ones cite an
ADR in [docs/adr/](docs/adr/). No blank rows, no silent skips.

| Standard | State |
|---|---|
| Code Quality | Applies: `make verify` runs ruff (security `S` rules, `max-complexity=10`), `mypy --strict`, and pytest with a branch-coverage floor of 85. Current: 153 tests, 91.00% branch coverage, zero lint/type errors (2026-08-09). Floors: Python >= 3.12 (`.python-version` pins 3.14), ruff >= 0.15, mypy >= 1.18, locked in `uv.lock`. |
| Security & Supply-Chain | Applies: the streaming, inspection, discovery, fetch, and registry path is standard-library-only; DuckDB is an optional lakehouse dependency ([ADRs 0002-0003](docs/adr/)). The lockfile, ruff `S` gate, HTTPS/redirect validation, bounded downloads, and SHA-pinned CI actions reduce the current surface. Hosted SAST/secret/dependency scanners remain a phase-5 gap. |
| CI/CD | Applies: a SHA-pinned GitHub Actions workflow mirrors `make verify` on Python 3.12 and 3.14 and builds distributions. The repository has no configured remote, so no hosted run is claimed. |
| Observability | Applies to the local batch shape: finalized run manifests and DuckDB `model_metric` rows retain status, counts, rows scanned/produced, bytes, wall time, and DuckDB peak-buffer signals. No service availability claim applies. See [docs/ROADMAP.md](docs/ROADMAP.md). |
| Accessibility | N/A (no human-facing HTML). The phase-5 static site brings this into scope before it ships. |
| Internationalization | Applies narrowly: the operator CLI and documentation are English-only; structured JSON keys and CMS source fields are stable. The public-site decision is still required before phase 5: [docs/I18N.md](docs/I18N.md). |
| AI Evaluation | N/A (no LLM or model component; the grading and comparison path is deterministic by design, a written engineering standard in [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)). |
| Quality & Metrics | Applies: metrics ledger in [docs/ROADMAP.md](docs/ROADMAP.md); every published number is measured, never estimated. |
| Documentation | Applies: README, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff`, ADR log ([docs/adr/](docs/adr/)). |
| Responsible-Tech Framework | Applies: [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) (grades files, never organizations or care). |
| Release & Versioning | N/A (pre-publication, not consumed downstream): no tags, no consumers, no release process until phase 5. [docs/adr/0001-release-versioning-na.md](docs/adr/0001-release-versioning-na.md). |

## Provenance

Personal open-source project, planned and built on personal time and equipment, unaffiliated with
any employer or client, past or present.

Built AI-assisted (Claude Code and OpenAI Codex). Every number in the docs and this README was
measured, not generated; the maintainer reviews and owns every line.

License: Apache-2.0.
