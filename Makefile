PY := .venv/bin/python
export PYTHONPATH := src

.PHONY: up down migrate api worker judge lint type test e2e check demo eval golden train-judge build

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

judge:          ## versioned judge service; JUDGE_VERSION=v2 loads the artifact from S3
	.venv/bin/uvicorn pipeline.judge_api:app --port 8100

golden:         ## run a judge against the golden set (JUDGE_URL optional)
	$(PY) scripts/golden.py $(if $(JUDGE_URL),--judge-url $(JUDGE_URL),)

train-judge:    ## distill the rule judge into models/judge-v2.joblib and upload it
	$(PY) scripts/train_judge.py

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
