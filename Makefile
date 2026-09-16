.PHONY: install test lint typecheck check

install:
	python -m pip install -e .

test:
	pytest -q

lint:
	ruff check .

typecheck:
	mypy src

check: test lint typecheck
