# Roadmap, observability, and metrics ledger

The build plan itself lives in [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) (phases 0-5 with
stop conditions); this file carries the standards-facing declarations that sit alongside it.

## Current position

Phases 0 and 1 are done and measured (`PHASE-0-FINDINGS.md`). Phase 2 (the Parquet + DuckDB
lakehouse) is next. Target for a defensible v0.1 is phases 0 through 3.

## Observability

**Not yet tiered.** There is no runtime service, hosted surface, or scheduled job: phases 0-1
are local library code exercised by tests and by the maintainer at a prompt. Per the
observability standard's deployment-shape tiers, the repo has nothing to observe today.

Committed follow-up, not a silent skip: the phase-2 pipeline (local lakehouse builds) records
cost signals per model (bytes scanned, rows produced, wall time) as data in the repo, and the
phase-5 published site/API takes a real tier declaration before it ships.

## Metrics ledger

Per the Quality & Metrics standard's ledger shape. Every value below was measured, not
estimated; dates are when the number was last observed on this tree.

| Metric | Target | Measured by | Gate | Last measured |
|---|---|---|---|---|
| Branch coverage | >= 85% | `pytest --cov` (branch mode, `fail_under = 85`) | AUTO (`make verify`) | 93.48%, 33 tests passing, 2026-08-07 |
| Lint findings (ruff `E,F,I,B,S,C90,UP,RUF`, `max-complexity=10`) | 0 | `ruff check src tests` | AUTO (`make verify`) | 0, 2026-08-07 |
| `mypy --strict` errors | 0 | `mypy` over `src` | AUTO (`make verify`) | 0, 2026-08-07 |
| Streaming peak RSS on the 65 MB reference file | below file size | measured run recorded in `PHASE-0-FINDINGS.md` | REVIEW (re-measure when `stream.py` changes) | 27 MB (0.42x), 2026-08-04 |
| Runtime dependency count | 0 (stdlib-only core, ADR 0002) | `pyproject.toml` `[project] dependencies` | REVIEW | 0, 2026-08-07 |
| Fabricated figures in docs | 0 | every published number traces to a run or a query | REVIEW (house rule, `docs/CONTEXT.md`) | 0 known, 2026-08-07 |

Planned ledger rows that only become measurable in later phases: per-model cost signals
(phase 2), grade distribution and denominator honesty checks (phases 3-4), site availability
(phase 5).
