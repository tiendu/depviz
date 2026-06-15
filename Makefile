VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: venv install test lint typecheck check clean

venv:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip

install: venv
	$(PIP) install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

check: test lint typecheck

clean:
	rm -rf $(VENV)
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	rm -rf build dist *.egg-info src/.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
