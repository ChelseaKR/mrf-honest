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

The dates are not uniform on purpose. Every AUTO row is re-measured by a `make verify` run
and carries the date of the last such run. The REVIEW rows carry the date of the
instrumented acceptance run that produced their figures, and that run is not repeated to
refresh a date; a REVIEW row moves when the thing it measures is re-measured. When the
README quotes a ledger figure, this table is the source and the README follows it.

| Metric | Target | Measured by | Gate | Last measured |
|---|---|---|---|---|
| Branch coverage | >= 85% | `pytest --cov` (branch mode, `fail_under = 85`) | AUTO (`make verify`) | 90.88%, 324 tests passing, 2026-08-15 |
| Lint findings (ruff `E,F,I,B,S,C90,UP,RUF`, `max-complexity=10`) | 0 | `ruff check src tests` | AUTO (`make verify`) | 0, 2026-08-15 |
| Formatting findings | 0 | `ruff format --check src tests` | AUTO (`make verify`) | 0, 2026-08-15 |
| `mypy --strict` errors | 0 | `mypy` over `src` | AUTO (`make verify`) | 0, 2026-08-15 |
| Lockfile drift | none | `uv lock --check` (**not** `uv sync --frozen`, which cannot see drift) | AUTO (`make verify`, CI `uv sync --locked`) | in sync, 2026-08-15 |
| Known vulnerabilities in the locked dependency set | 0, no ignore list | `pip-audit --strict --no-deps` over `uv export --all-extras` (116 pinned distributions) | AUTO (`make verify`) | 0, 2026-08-15 |
| Lighthouse accessibility, best-practices and SEO, every rendered page | 1.0 (a declared floor above the standard's 0.90) | `perf/score_lighthouse.py` over Lighthouse 12 reports for every HTML file the render produced | AUTO (`.github/workflows/accessibility.yml`) | 1.0 / 1.0 / 1.0 on all 9 pages, 2026-08-15 |
| Lighthouse performance, every rendered page | >= 0.95 absolute, and no worse than 10% off `perf/baseline.json` | same job | AUTO (`.github/workflows/accessibility.yml`) | 1.0 on all 9 pages, 2026-08-15 |
| Page weight and request count | 0 bytes of script, stylesheet, font, image and third party; 1 request; <= 60 KB document | `perf/resource-budget.json` asserted against Lighthouse's `resource-summary` audit (**not** `--budget-path`, which does not exist in Lighthouse 12) | AUTO (`.github/workflows/accessibility.yml`) | heaviest page 12,197 bytes in 1 request, 2026-08-15 |
| Design-token contrast, every declared text/background pair | >= 4.5:1 (WCAG 2.2 SC 1.4.3), no large-text exemptions claimed | `tests/test_site.py`, which also fails on a palette colour with no declared pair | AUTO (`make verify`) | 16 pairs, minimum 4.97:1, 2026-08-15 |
| Heading order, every generated page | no skipped level, exactly one h1 | `tests/test_site.py` | AUTO (`make verify`) | 0 violations, 2026-08-15 |
| robots.txt obeyed, no override path | a disallow or an unreadable robots.txt stops the fetch before any request for the file | `tests/test_politeness.py` against a real `http.server` on loopback, plus a signature assertion that no `ignore_robots`/`force` parameter exists | AUTO (`make verify`) | 22 cases, 0 failures, 2026-08-15 |
| Per-host interval and `Retry-After` | interval held across a run; `Crawl-delay` lengthens only; 429/503 `Retry-After` outranks local backoff | same suite | AUTO (`make verify`) | default floor 2.0 s; measured 3.0 s waited on a `Retry-After: 3` against a 100 s configured backoff, 2026-08-15 |
| Retrieval failures attributed to a publisher | only causes that are actually the publisher's; every local or ambiguous cause is not graded | `tests/test_scorecard.py` status matrix, which fails when a new `FetchStatus` is added without an explicit mapping | AUTO (`make verify`) | 11 of 11 statuses mapped; TLS verification moved from publisher failure to not graded, 2026-08-15 |
| Manual screen-reader pass | one dated walkthrough of the index and one file page | a person, recorded in `RESPONSIBLE-TECH-AUDITS.md` | REVIEW | **not performed**; the open obligation, stated rather than implied |
| Streaming scan on the 64,828,148-byte reference file | RSS below file size; zero problems | `/usr/bin/time -l`, recorded in `PHASE-0-FINDINGS.md` | REVIEW (re-measure when `stream.py` changes) | 30,114 items, 0 problems, 9.25 s, 33,865,728-byte RSS (0.5224x), 26,231,240-byte peak footprint, 2026-08-09 |
| End-to-end lakehouse process memory | measured and disclosed; no cap claim | `/usr/bin/time -l`, recorded in `PHASE-2-FINDINGS.md` | REVIEW (re-measure on model/load changes) | 534,790,144-byte max RSS; 575,865,768-byte peak footprint, 2026-08-09 |
| Clean lakehouse wall time / verified warm reuse | measured and disclosed | instrumented CLI acceptance | REVIEW | 46.66 s / 0.34 s; reuse max RSS 63,668,224 bytes and peak footprint 34,980,368 bytes, 2026-08-09 |
| DuckDB settings and peak-buffer profiler signal | retain settings and do not relabel profiler data as RSS | manifest `duckdb` and `model_metrics` | REVIEW | configured 256MB / two threads; effective 244.1 MiB; `system_peak_buffer_memory_bytes` 512,212,992, not directly comparable, 2026-08-09 |
| Artifact integrity | every source and Parquet artifact | manifest SHA-256 + byte size, rechecked on reuse | AUTO (runtime + tests) | source archive + 13 of 13 Parquets verified; 117,287,726 bytes excluding manifest, 2026-08-09 |
| Lakehouse persistent/transient storage | measured by artifact inventory and spool sizes | clean CLI acceptance | REVIEW | DB 117,977,088; 13 Parquets 52,459,578; archive 64,828,148; nine spools 251,678,531 bytes, 2026-08-09 |
| Manifest body integrity and recovery | immutable body tampering rejected; prepared state recoverable after commit | deterministic integrity/transaction tests | AUTO (tests) | schema 4 digest passes; inspection/envelope/metrics tampering rejected; full SIGKILL/fsync matrix pending, 2026-08-09 |
| Base runtime dependency count | 0 (DuckDB remains an optional lakehouse extra, ADRs 0002-0003) | `pyproject.toml` `[project] dependencies` | REVIEW | 0, 2026-08-15 |
| Fabricated figures in docs | 0 | every published number traces to a run or a query | REVIEW (house rule, `docs/CONTEXT.md`) | 0 known, 2026-08-09 |

Planned ledger rows that only become meaningful later: multi-publisher grade distribution and
denominator-honesty checks (phases 3-4), suppression/uncertainty coverage (phase 4), and scheduled
job plus site availability (phase 5).

Retrieval politeness is no longer an operator procedure. `src/mrf_honest/politeness.py` fetches
and obeys `robots.txt` before the first request with no override flag, holds a per-host minimum
interval across a whole run that a `Crawl-delay` can only lengthen, and honours `Retry-After` on
429 and 503 ahead of this tool's own backoff. Every decision and every wait is retained as
JSON-safe evidence. What that unblocks is a roster-sourced second cohort; what it does not by
itself authorise is a *scheduled* job, which still needs a service/job tier declaration before
it ships.
