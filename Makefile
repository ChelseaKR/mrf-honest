PYTHON ?= .venv/bin/python
UV ?= uv

.PHONY: verify lint format typecheck test lock audit

verify: lint format typecheck test lock audit

lint:
	$(PYTHON) -m ruff check src tests

format:
	$(PYTHON) -m ruff format --check src tests

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q

# `uv lock --check` is the lockfile-drift gate, and the exact spelling matters.
# Measured on a deliberately drifted project (a dependency added to pyproject.toml, the
# lockfile left alone), uv 0.12.1:
#
#   uv lock --check    -> exit 1   (sees the drift)
#   uv sync --locked   -> exit 1   (sees the drift)
#   uv sync --frozen   -> exit 0   (does not)
#
# `--frozen` installs from the lockfile without consulting pyproject.toml at all, so it
# cannot observe the two disagreeing. A `uv sync --frozen` step is a real reproducibility
# guarantee and is not a drift check, and this repository's CI previously relied on it as
# though it were one. Note also that a bare `uv run` rewrites the lockfile in place when it
# is stale, so a drift gate must never be invoked through `uv run` -- it would repair the
# very condition it is there to report.
lock:
	$(UV) lock --check

# CQ-11. The audited surface is the *lockfile*, exported with every extra and the dev group,
# not whatever happens to be installed in someone's .venv -- so the answer is the same on a
# laptop and in CI. `--no-deps` is correct here because uv has already produced a complete
# pinned resolution; pip-audit only has to look each pin up.
#
# There is deliberately no ignore list and no `|| true`. The mute pattern is what turns an
# audit into decoration. `--strict` makes a dependency that could not be audited a failure
# rather than a silence, and the project itself is excluded from the export (it is not on
# PyPI, and under `--strict` an unauditable local package would otherwise fail the run for
# the wrong reason).
audit:
	@req="$$(mktemp -t mrf-honest-audit)"; \
	trap 'rm -f "$$req"' EXIT; \
	$(UV) export --frozen --no-emit-project --all-extras --no-hashes \
		--format requirements-txt -o "$$req" -q && \
	$(PYTHON) -m pip_audit --strict --no-deps -r "$$req" --progress-spinner off
