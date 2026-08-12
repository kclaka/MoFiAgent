PYTHON ?= .venv/bin/python
UV ?= .venv/bin/uv
UV_CACHE_DIR ?= /tmp/mofiagent-uv-cache
LOCAL_DATABASE_DSN ?= postgresql://mofiagent:mofiagent@127.0.0.1:5432/mofiagent
RUN = env PYTHONPATH=src $(PYTHON)

.PHONY: install test unit lint format typecheck check serve migrate ingest db-up db-down

install:
	env UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) sync --all-extras --frozen

test: db-up migrate
	env PYTHONPATH=src MOFI_TEST_DATABASE_DSN=$(LOCAL_DATABASE_DSN) $(PYTHON) -m pytest --cov=mofiagent --cov-report=term-missing

unit:
	$(RUN) -m pytest tests/unit

lint:
	$(RUN) -m ruff check .
	$(RUN) -m ruff format --check .

format:
	$(RUN) -m ruff check --fix .
	$(RUN) -m ruff format .

typecheck:
	$(RUN) -m pyright

check: lint typecheck test

serve:
	$(RUN) -m mofiagent serve

migrate:
	env PYTHONPATH=src MOFI_DATABASE_DSN=$(LOCAL_DATABASE_DSN) $(PYTHON) -m mofiagent migrate

ingest:
	env PYTHONPATH=src MOFI_DATABASE_DSN=$(LOCAL_DATABASE_DSN) $(PYTHON) -m mofiagent ingest

db-up:
	docker compose up -d --wait timescaledb

db-down:
	docker compose down
