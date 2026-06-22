SHELL := /bin/bash

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(PYTHON) -m pip
DEPVIZ := $(VENV)/bin/depviz
INSTALL_STAMP := $(VENV)/.depviz-installed

BOOTSTRAP_PYTHON ?= python3
ARGS ?=

.PHONY: help venv install run test test-hardening test-compatibility test-failure-injection test-security lint format format-check typecheck check check-release clean

help:
	@printf '%s\n' \
		'make install      Create or repair .venv and install development dependencies' \
		'make run          Run depviz; pass arguments with ARGS="..."' \
		'make test         Run the test suite' \
		'make test-hardening Run deterministic and corruption regression tests' \
		'make test-compatibility Run package-manager compatibility tests' \
		'make test-failure-injection Run crash and concurrency tests' \
		'make test-security Run trust-boundary and integrity tests' \
		'make lint         Run Ruff linting' \
		'make format       Format source and tests with Ruff' \
		'make format-check Verify Ruff formatting without changes' \
		'make typecheck    Run strict type checking' \
		'make check        Run tests, linting, and type checking' \
		'make check-release Run the complete release-hardening gate' \
		'make clean        Remove the virtual environment and generated artifacts'

venv:
	@if [ ! -x "$(PYTHON)" ] || ! "$(PYTHON)" -m pip --version >/dev/null 2>&1; then \
		printf '%s\n' "Creating or repairing $(VENV)"; \
		rm -rf "$(VENV)"; \
		"$(BOOTSTRAP_PYTHON)" -m venv "$(VENV)"; \
		if ! "$(PYTHON)" -m pip --version >/dev/null 2>&1; then \
			"$(PYTHON)" -m ensurepip --upgrade; \
		fi; \
		"$(PYTHON)" -m pip install --upgrade pip; \
	fi


install: venv
	@if [ ! -f "$(INSTALL_STAMP)" ] \
		|| [ ! -x "$(DEPVIZ)" ] \
		|| [ pyproject.toml -nt "$(INSTALL_STAMP)" ]; then \
		printf '%s\n' "Installing depviz development environment"; \
		$(PIP) install -e ".[dev]"; \
		touch "$(INSTALL_STAMP)"; \
	fi

run: install
	$(DEPVIZ) $(ARGS)

test: install
	$(PYTHON) -m pytest

test-hardening: install
	$(PYTHON) -m pytest -m hardening

test-compatibility: install
	$(PYTHON) -m pytest -m compatibility

test-failure-injection: install
	$(PYTHON) -m pytest -m failure_injection

test-security: install
	$(PYTHON) -m pytest -m security

lint: install
	$(PYTHON) -m ruff check .

format: install
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

format-check: install
	$(PYTHON) -m ruff format --check .


typecheck: install
	$(PYTHON) -m mypy src

check: test lint format-check typecheck

check-release: lint format-check typecheck
	$(PYTHON) -m pytest -m "not network"

clean:
	rm -rf "$(VENV)"
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	rm -rf build dist *.egg-info src/*.egg-info
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
