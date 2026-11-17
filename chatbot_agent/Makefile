PY := .venv/bin/python
export PYTHONPATH := .

.PHONY: install ingestion gateway test test-ingestion test-gateway test-integration smoke docker up down
install:
	python3.10 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ingestion:
	.venv/bin/uvicorn api:app --app-dir apps/ingestion/ProjectLoad/dataLoaderAPI/src --port 8001
gateway:
	.venv/bin/uvicorn app.api:app --app-dir apps/query_gateway/src --port 8010
# Suites run separately: each app has its own top-level package names (core, config, ...)
test: test-ingestion test-gateway test-integration
test-ingestion:
	$(PY) -m pytest apps/ingestion/ProjectLoad/dataLoaderAPI/tests -q
test-gateway:
	$(PY) -m pytest apps/query_gateway/src/tests -q
test-integration:
	$(PY) -m pytest tests/integration -q
smoke:
	$(PY) -m pytest tests/smoke tests/e2e -q
up:
	docker compose up --build
down:
	docker compose down
