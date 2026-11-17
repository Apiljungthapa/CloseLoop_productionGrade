# Runbook

## Setup
```bash
make install
cp .env.example .env     # GEMINI_API_KEY, QDRANT_CLUSTER_ENDPOINT, QDRANT_API_KEY
```
Python 3.10 is recommended (newer versions can fail to install torch).

## Run locally
```bash
make ingestion     # :8001
make gateway       # :8010
```
First start takes 30–60 s while the embedding model loads. Without `make`:
```bash
PYTHONPATH=. .venv/bin/uvicorn api:app --app-dir apps/ingestion/ProjectLoad/dataLoaderAPI/src --port 8001
PYTHONPATH=. .venv/bin/uvicorn app.api:app --app-dir apps/query_gateway/src --port 8010
```

## Docker
```bash
make docker          # builds both images and starts them from infra/compose/docker-compose.yml
```
Secrets come from `.env` at run time; they are not copied into images.

## Tests
`make test` (unit + integration), `make smoke` (needs both services running).

## Documents users upload (production)
The CloseLoop assistant lets each organization add its own documents (Close Bhai → paperclip). The CloseLoop
backend sends them to this repo's ingestion service; questions then search that organization's documents plus the
`shared` ones, never another organization's.

1. In `.env.prod` set `INGEST_API_AUTH_KEY` (random), `RABBITMQ_PASSWORD`, and keep `GEMINI_API_KEY`, `QDRANT_*`.
2. `docker compose --env-file .env.prod -f docker-compose.prod.yml --profile ingest up -d --build`
3. In CloseLoop's `.env.prod` set `CHATBOT_INGEST_ENABLED=true` and `CHATBOT_INGEST_API_KEY=<the same INGEST_API_AUTH_KEY>`,
   then recreate the CloseLoop `backend`. The paperclip only appears for users once this is on.

Qdrant chunks written **before tenants existed** have no `tenant_id` and are not searchable (search is fail-closed).
Re-ingest them with the CLI (they are then tagged `shared`), or tag them once:
`client.set_payload("rag_documents", payload={"tenant_id": "shared"}, points=<ids of the untagged points>)`.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `address already in use` | `lsof -i :8001` (or `:8010`) then stop that process; a background server may still be running (`pkill -f uvicorn`) |
| `/ready` returns 503 | Qdrant endpoint/key wrong or collection missing: check `.env` and that ingestion has run once |
| `/ask` returns 502 | `GEMINI_API_KEY` missing/invalid, quota hit, or no network |
| Answers always "couldn't find" | Nothing ingested yet, or `MIN_RETRIEVAL_SCORE` too high; try `/retrieve` to see raw scores |
| `ModuleNotFoundError: shared` | Start from the repo root with `PYTHONPATH=.` (the Makefile does this) |
| Search quality drops after changing the model | `EMBEDDING_MODEL` or `QDRANT_VECTOR_SIZE` differs between services, or documents were embedded with another model: re-ingest |

## Operations
- Rotate any key that was ever committed or pasted into chat; update `.env` and restart.
- Probes: liveness `/health`, readiness `/ready` on both services.
- Logs include a request id; search for it across both services.
- Scale by running more containers (one worker each). Both services are stateless apart from Qdrant.
