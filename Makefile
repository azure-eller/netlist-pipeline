PY := .venv/bin/python
export PYTHONPATH := src

.PHONY: up down migrate api worker lint type test e2e check demo eval build

up:            ## start Postgres + MinIO
	docker compose up -d postgres minio
	@until docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done

down:
	docker compose down

migrate:
	$(PY) -m pipeline.db

api:
	.venv/bin/uvicorn pipeline.api:app --reload --port 8000

worker:
	$(PY) -m pipeline.worker

lint:
	.venv/bin/ruff format --check src tests scripts && .venv/bin/ruff check src tests scripts

type:
	.venv/bin/mypy src

test:
	.venv/bin/pytest -q tests --ignore=tests/test_e2e.py

e2e: up migrate
	.venv/bin/pytest -q tests/test_e2e.py

check: lint type test e2e   ## nothing is done until this passes

build:
	docker compose build api

demo:
	scripts/demo.sh http://localhost:8000

eval:
	$(PY) scripts/eval.py
