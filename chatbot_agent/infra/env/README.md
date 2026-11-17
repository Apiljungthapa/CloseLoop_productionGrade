# Environment

Both services read the repo-root `.env` (template: `/.env.example`). Real environment variables override it,
so in Docker/Kubernetes inject secrets as env vars or `env_file` and do not bake `.env` into images.

| Variable | Service | Purpose |
|---|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | gateway | Answer generation |
| `QDRANT_CLUSTER_ENDPOINT`, `QDRANT_API_KEY` | both | Vector store (shared) |
| `QDRANT_COLLECTION`, `QDRANT_VECTOR_SIZE`, `EMBEDDING_MODEL` | both | Must be identical in both services |
| `MIN_RETRIEVAL_SCORE` | gateway | Weak-match cutoff |
| `API_AUTH_KEY` | both | If set, clients send `X-API-Key` |
| `ALLOWED_ORIGINS` | both | CORS |
| `DB_ENABLED`, `DB_URL` | gateway | Relational DB route (off until implemented) |
