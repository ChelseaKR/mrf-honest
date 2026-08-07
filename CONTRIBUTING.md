# Contributing

mrf-honest is a solo-maintained, pre-publication project, but the workflow below is the same one
a second contributor would use.

## Setup

```sh
uv sync --extra dev                                    # Python 3.14 (.python-version); creates .venv from uv.lock
pre-commit install && pre-commit install --hook-type pre-push
```

The dev toolchain is floor-pinned in `pyproject.toml` (`ruff>=0.15`, `mypy>=1.18`) and locked in
`uv.lock`. Use `uv sync`, not a hand-rolled `pip install`, so your tool versions match the gate.

## Before committing

```sh
make verify
```

`make verify` runs the full gate: `ruff check` (including the security `S` rules and
`max-complexity=10`), `mypy --strict`, and `pytest` with a branch-coverage floor of 85. If it
passes locally, the pre-push hook (which runs the same commands) will pass too. Do not lower any
floor to make a change pass; fix the change.

## House rules

- Standard library only in `src/` for the streaming path: a dependency that hides memory
  behaviour defeats the point of measuring it (`docs/adr/0002-stdlib-only-streaming-core.md`).
- Every number that appears in a doc or README is measured, never estimated. If you did not run
  it, do not write it.
- Architecture decisions that are expensive to reverse get an ADR in `docs/adr/`
  (`docs/adr/0000-record-architecture-decisions.md`).
- Changes land with a `CHANGELOG.md` entry under `[Unreleased]`.
