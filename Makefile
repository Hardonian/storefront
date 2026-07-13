# Standard service entrypoints
VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: install dev run lint test
install:
	$(PY) -m pip install -r requirements.txt
dev:
	$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000
lint:
	$(PY) -m ruff check . ; $(PY) -m black --check .
test:
	$(PY) -m pytest -q
