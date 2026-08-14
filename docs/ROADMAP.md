# Roadmap, observability, and metrics ledger

The build plan itself lives in [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) (phases 0-5 with
stop conditions); this file carries the standards-facing declarations that sit alongside it.

## Current position

Phases 0 through 3 are implemented (`PHASE-0-FINDINGS.md`, `PHASE-2-FINDINGS.md`,
`PHASE-3-FINDINGS.md`, and `how-we-grade.md`). Phase 3 keeps local inspection separate from mutable remote evidence, then
combines them in an integrity-hashed `FileAssessment`: every terminal fetch outcome becomes a row,
publisher type and URL provenance are explicit, missing bodies leave four dimensions explicitly
`NOT_ASSESSED`, and comparison is refused across type/profile/provenance/policy/date scopes. No
real multi-publisher grade distribution or hosted scorecard surface is claimed yet.

## Observability

**Local batch shape; no service tier.** Each ingest is identified by pipeline version, publisher,
content digest, inspection `as_of`, and a fingerprint of the transformation boundary. That
fingerprint incorporates the inspection policy and finding catalog, so grading-semantic changes
also create a new run identity. The ingest archives the admitted source by content hash and writes
DuckDB `model_metric` rows containing source identity, status, row counts, rows scanned/produced,
bytes read/written, wall time, and DuckDB's system peak-buffer profiler signal. That profiler field
is not process RSS or a per-model memory delta.

A schema-v4 manifest is promoted in `prepared` state before the catalog commit and atomically
finalized to `success` afterward. Its body digest covers every immutable field except `status` and
the digest itself. Reuse validates that digest, manifest/catalog identity, and every source/Parquet
artifact before returning; tampered inspection, envelope, or metrics fail closed, while a valid
`prepared` manifest attached to a successful catalog run is recoverably finalized. Contract
failures roll back model rows, and handled promotion failures clean the run's deterministic
artifacts. This is not a claim of full crash durability: concurrent writers, historical warehouse
migrations, and a full SIGKILL/fsync matrix remain open.

The hosted surface is a static GitHub Pages site rebuilt exclusively from data committed to this
repository by a SHA-pinned workflow (`.github/workflows/pages.yml`); the build fails closed if
the rendered pages disagree with the committed comparison document. There is no scheduled
refresh, no alert destination, and no declared availability objective — the site is a published
artifact, not a service, and it never fetches anything at build time. Scheduled collection
remains out of scope until the `robots.txt` policy, per-host pacing, and `Retry-After` work
below; any future scheduled job must take a real service/job tier declaration before shipping.

## Metrics ledger

Per the Quality & Metrics standard's ledger shape. Every value below was measured, not
estimated; dates are when the number was last observed on this tree.

| Metric | Target | Measured by | Gate | Last measured |
|---|---|---|---|---|
| Branch coverage | >= 85% | `pytest --cov` (branch mode, `fail_under = 85`) | AUTO (`make verify`) | 89.78%, 226 tests passing, 2026-08-09 |
| Lint findings (ruff `E,F,I,B,S,C90,UP,RUF`, `max-complexity=10`) | 0 | `ruff check src tests` | AUTO (`make verify`) | 0, 2026-08-09 |
| `mypy --strict` errors | 0 | `mypy` over `src` | AUTO (`make verify`) | 0, 2026-08-09 |
| Streaming scan on the 64,828,148-byte reference file | RSS below file size; zero problems | `/usr/bin/time -l`, recorded in `PHASE-0-FINDINGS.md` | REVIEW (re-measure when `stream.py` changes) | 30,114 items, 0 problems, 9.25 s, 33,865,728-byte RSS (0.5224x), 26,231,240-byte peak footprint, 2026-08-09 |
| End-to-end lakehouse process memory | measured and disclosed; no cap claim | `/usr/bin/time -l`, recorded in `PHASE-2-FINDINGS.md` | REVIEW (re-measure on model/load changes) | 534,790,144-byte max RSS; 575,865,768-byte peak footprint, 2026-08-09 |
| Clean lakehouse wall time / verified warm reuse | measured and disclosed | instrumented CLI acceptance | REVIEW | 46.66 s / 0.34 s; reuse max RSS 63,668,224 bytes and peak footprint 34,980,368 bytes, 2026-08-09 |
| DuckDB settings and peak-buffer profiler signal | retain settings and do not relabel profiler data as RSS | manifest `duckdb` and `model_metrics` | REVIEW | configured 256MB / two threads; effective 244.1 MiB; `system_peak_buffer_memory_bytes` 512,212,992, not directly comparable, 2026-08-09 |
| Artifact integrity | every source and Parquet artifact | manifest SHA-256 + byte size, rechecked on reuse | AUTO (runtime + tests) | source archive + 13 of 13 Parquets verified; 117,287,726 bytes excluding manifest, 2026-08-09 |
| Lakehouse persistent/transient storage | measured by artifact inventory and spool sizes | clean CLI acceptance | REVIEW | DB 117,977,088; 13 Parquets 52,459,578; archive 64,828,148; nine spools 251,678,531 bytes, 2026-08-09 |
| Manifest body integrity and recovery | immutable body tampering rejected; prepared state recoverable after commit | deterministic integrity/transaction tests | AUTO (tests) | schema 4 digest passes; inspection/envelope/metrics tampering rejected; full SIGKILL/fsync matrix pending, 2026-08-09 |
| Base runtime dependency count | 0 (DuckDB remains an optional lakehouse extra, ADRs 0002-0003) | `pyproject.toml` `[project] dependencies` | REVIEW | 0, 2026-08-09 |
| Fabricated figures in docs | 0 | every published number traces to a run or a query | REVIEW (house rule, `docs/CONTEXT.md`) | 0 known, 2026-08-09 |

Planned ledger rows that only become meaningful later: multi-publisher grade distribution and
denominator-honesty checks (phases 3-4), suppression/uncertainty coverage (phase 4), and scheduled
job plus site availability (phase 5).

Before scheduled collection, retrieval also needs a `robots.txt` policy, per-host pacing, and
`Retry-After` handling. These are explicit scope limits, not untracked enhancements.
