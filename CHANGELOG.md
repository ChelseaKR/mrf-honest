# Changelog

All notable changes to this project are documented here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. The project is pre-release with
no version tags yet; until the first dated release (phase 5 of
[docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)), entries are grouped by date.

## [Unreleased]

### Added

- Portfolio standards conformance pass: `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CITATION.cff`, `CODEOWNERS`, ADR log (`docs/adr/`), `docs/I18N.md`, `docs/ROADMAP.md`
  (observability declaration and metrics ledger), `docs/RESPONSIBLE-TECH-AUDITS.md`,
  `.pre-commit-config.yaml`, `.python-version`, `uv.lock`.
- README: quickstart, Standards Conformance table, and an AI-assisted development disclosure.

### Changed

- README status corrected: it still said "planning only, no code yet" after phases 0 and 1 had
  landed. It now describes what is actually built.
- Dev toolchain floors raised to `ruff>=0.15` and `mypy>=1.18` (previously `0.6` / `1.11`; the
  installed tools, ruff 0.16.2 and mypy 2.3.0, already satisfied both, so nothing weakened).

### Removed

- Dangling `[project.scripts]` entry `mrf-honest = "mrf_honest.cli:main"`: no `cli` module exists
  yet, so the declared console script could never resolve. It returns when the CLI is built.

## 2026-08-04

### Added

- Phase 1: streaming JSON reader (`src/mrf_honest/stream.py`). Peak RSS on the 65 MB reference
  file drops from 506 MB (naive `json.load`) to 27 MB, measured; UTF-8 BOM handled rather than
  fatal; property-based tests via Hypothesis. The buffer-refill slice bug found on the way is
  written up in `docs/PHASE-0-FINDINGS.md`.
- Phase 0: measured constraint study (`docs/PHASE-0-FINDINGS.md`) and the `cms-hpt.txt` discovery
  module (`src/mrf_honest/discover.py`).
- Planning documents: `docs/CONTEXT.md`, `docs/DATA-LANDSCAPE.md`, `docs/IMPLEMENTATION-PLAN.md`.
- Tooling: `Makefile` with a `verify` gate (ruff, mypy strict, pytest with a branch-coverage
  floor of 85), Apache-2.0 `LICENSE`, `pyproject.toml`.
