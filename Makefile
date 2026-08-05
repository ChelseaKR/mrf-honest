PYTHON ?= .venv/bin/python

.PHONY: verify lint typecheck test

verify: lint typecheck test

lint:
	$(PYTHON) -m ruff check src tests

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q
