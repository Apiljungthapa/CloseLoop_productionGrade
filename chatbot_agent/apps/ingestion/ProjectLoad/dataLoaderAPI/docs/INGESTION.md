# Ingestion service

Write side of the system: turns uploaded files (or documents from a remote source API) into chunks with
embeddings stored in Qdrant. Question answering is handled by `apps/query_gateway`.

See the repo-level [architecture](../../../../../docs/ARCHITECTURE.md) and [API](../../../../../docs/API.md) docs.

## Pipeline
`/upload` → `data_loader/local_loader.py` (detect file type) → `processors/` (clean HTML/XML → text) →
`chunking/` (section chunks; recursive splitter fallback for long text) → `main.py` (embed with MiniLM,
pad to 512 dims) → `vector_store/qdrant_store.py` (upsert).

## Config
- Secrets and shared values: repo-root `.env` (Qdrant endpoint/key, `API_AUTH_KEY`, `ALLOWED_ORIGINS`).
- Optional service-local overrides: `src/config/dev.env` (git-ignored; template `dev.env.example`).
- Chunk sizes, embedding model, collection name: `src/config/dev_config.yaml`. Qdrant values there use `${ENV_VAR}` placeholders.
- HTML section rules: `src/config/html_config/health_docs.json`.

## Batch CLI
`PYTHONPATH=. python apps/ingestion/ProjectLoad/dataLoaderAPI/src/main.py --output output/chunks.jsonl`
pulls from the remote source configured by `SOURCE_BASE_URL`, `SOURCE_SEARCH_URL`, `SOURCE_DOWNLOAD_URL`.
Run it from `src/` if imports fail.
