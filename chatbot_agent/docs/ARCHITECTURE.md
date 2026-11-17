# Architecture

## Overview

```
                    ┌────────────────────┐        ┌──────────────┐
 file ── /upload ──►│ Ingestion service  │──────► │   Qdrant     │
                    │ clean·chunk·embed  │ write  │ (vectors)    │
                    └────────────────────┘        └──────┬───────┘
                                                         │ read
 question ── /ask ─►┌────────────────────┐  search       │
                    │   Query gateway    │◄──────────────┘
                    │ plan·retrieve·     │──► Gemini (answer)
                    │ merge·answer·check │──► Relational DB (SQL API)
                    └────────────────────┘
```

The two services share only Qdrant and the settings in `.env`. They never import each other. Code used by
more than one app lives in `shared/`.

## Where things live

### Ingestion: `apps/ingestion/ProjectLoad/dataLoaderAPI/src`
| Path | Role |
|---|---|
| `api.py` | HTTP: `/upload`, `/documents`, `/health`, `/ready`; auth, CORS, request logging |
| `main.py` | `DataLoadOrchestrator`: wires loader → processors → chunkers → embeddings → Qdrant; batch CLI |
| `config/` | `config.py` (settings), `dev_config.yaml` (chunk sizes, model, Qdrant), `html_config/` (section tags) |
| `data_loader/` | Getting documents in: `local_loader.py` (uploads), `loader.py` (remote source API), parser, schema |
| `processors/` | HTML/XML → text, chunking decisions |
| `chunking/` | Section chunkers and the recursive fallback |
| `vector_store/qdrant_store.py` | Qdrant **writes** and document list/get/delete |
| `core/`, `utils/` | Ingestion settings, constants, file reader, source health check |

### Query gateway: `apps/query_gateway/src`
| Path | Role |
|---|---|
| `app/api.py` | App factory, middleware, error handlers. `app/deps.py` builds services once. `app/routes/` has `query.py` (`/ask`, `/retrieve`) and `health.py` |
| `core/` | `config.py` (settings), `logging.py`, `exceptions.py` (errors mapped to HTTP codes) |
| `domain/` | `schemas.py` (HTTP request/response), `models.py` (`QueryPlan`) |
| `planner/` | `intent_router.py` + `planner_rules.py` choose a route; `query_planner.py` builds the plan |
| `retrieval/` | `rag_retriever.py` (Qdrant), `db_retriever.py`, `hybrid_retriever.py` (runs the plan), `reranker.py` |
| `db/` | Text-to-SQL pipeline: `sql_planner` → `sql_validator` → `executor` → `result_normalizer` |
| `evidence/` | `merger.py` combines results, `validator.py` checks there is something to answer from |
| `answer/` | `prompt.py` (**edit to change answer style**), `gemini_client.py`, `generator.py`, `validator.py`, `repair_loop.py` |
| `tests/` | Unit tests for every package above |

### Shared: `shared/`
`schemas/` (`UserQuery`, `RetrievedItem`, `Answer`), `utils/` (text, vector, telemetry helpers), `constants/enums.py` (`Route`, `EvidenceSource`).

### Infra and tests
`infra/docker` (one Dockerfile per service), `infra/compose`, `infra/env`; `tests/integration` (gateway in-process with fakes), `tests/smoke` (live health), `tests/e2e` (upload then ask).

## Query flow (`POST /ask`)

1. **Plan**: `intent_router` picks `doc`, `db`, `hybrid` or `chitchat` from keyword rules. A route whose capability is disabled (the DB, today) falls back to `doc`.
2. **Retrieve**: `doc` embeds the question (model loaded once at startup), searches Qdrant (optionally filtered by `document_id`), then re-ranks: `0.75 × semantic + 0.25 × keyword overlap`, drops duplicates and anything below `MIN_RETRIEVAL_SCORE`.
3. **Merge**: evidence from all routes is combined and sorted.
4. **Guard**: no usable evidence → fixed "couldn't find it" answer; Gemini is not called, so it cannot guess.
5. **Answer**: the prompt template adapts to the question (direct vs explanation vs yes/no).
6. **Validate and repair**: empty answers or answers that talk about "the context" are retried once with the reason, then fall back to the safe message.

## Relational DB API

`db/` is switched off by default (`DB_ENABLED=false`, `SQL_PLANNER_API_ENABLED=false`,
`SQL_QUERY_EXECUTION_ENABLED=false`). When explicitly enabled, `POST /sql/plan` returns validated SQL as plain text;
`POST /sql/ask` plans a question, executes it in a PostgreSQL read-only transaction with a statement timeout, and asks
Gemini to answer using bounded query results. Both endpoints require `X-SQL-API-Key` and a tenant ID in the request.
The planner applies the input guardrail, checks that the tenant exists, uses a live catalog of tenant-owned public
tables, rejects unapproved tables/columns and unsafe functions, injects a tenant filter for every table, and caps
results. The executor repeats SQL validation and tenant scoping before execution. The current API key does not
authenticate the supplied tenant ID; do not expose this API publicly until trusted server-side authentication maps
callers to authorized tenants. Use a least-privilege read-only database role in production. Never rely on the LLM for
tenancy or SQL safety.

## Conventions

- Dependencies point inward: `app` → `retrieval`/`answer`/`planner` → `shared`. Retrieval and answer code know nothing about HTTP.
- Each app runs in its own process; their top-level package names (`core`, `config`) overlap on purpose, so run test suites per app (`make test`).
- Configuration only through environment/`.env`; no secrets in YAML.
- Qdrant settings (`QDRANT_COLLECTION`, `QDRANT_VECTOR_SIZE`, `EMBEDDING_MODEL`) must match in both services. Vectors are padded from 384 to 512 dims by both sides.

## Known limits

No rate limiting (use a gateway or proxy); routing is rule-based and can misroute unusual phrasing, so replace it
with an LLM or learned classifier once the DB route exists; the old Qdrant key was committed to git history before this
refactor and should be rotated.
