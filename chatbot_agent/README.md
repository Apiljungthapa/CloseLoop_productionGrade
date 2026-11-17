# CloseLoop_Agent

A document-first AI system with a clean split between **ingestion** (write side) and **query** (read side).

| | Ingestion service | Query gateway |
|---|---|---|
| Does | upload → clean → chunk → embed → Qdrant; manage documents | plan → retrieve → merge evidence → Gemini answer → validate |
| Port | 8001 | 8010 |
| Code | `apps/ingestion/ProjectLoad/dataLoaderAPI/src` | `apps/query_gateway/src` |

## Layout

```text
.
├─ apps/
│  ├─ ingestion/ProjectLoad/dataLoaderAPI/   src/ (api, main, config, core, data_loader, processors,
│  │                                          chunking, vector_store, utils), tests/, docs/
│  └─ query_gateway/                          src/ (app, core, domain, planner, retrieval, db,
│                                             evidence, answer, tests), requirements.txt
├─ shared/        schemas, utils, constants used by more than one app
├─ infra/         docker/, compose/, env/
├─ tests/         integration/, e2e/, smoke/
├─ docs/          ARCHITECTURE.md, API.md, RUNBOOK.md
├─ .env.example   requirements.txt   requirements-dev.txt   Makefile
```

## Quick start

For Windows, install GNU Make and Docker Desktop, then run these commands in
PowerShell from the repository root. Keep the SQL backup in the repository root;
it is ignored by Git because it may contain private data.

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# Edit .env: set GEMINI_API_KEY, Qdrant values, and a strong POSTGRES_PASSWORD
make up                      # starts services; imports the SQL dump on first DB initialization
```

Open Adminer at http://localhost:8080. Log in with system `PostgreSQL`, server
`postgres`, database `closeloop`, user `closeloop`, and the `POSTGRES_PASSWORD`
from `.env`. The PostgreSQL dump is imported only when its data volume is first
created; subsequent `make up` runs preserve the database. `make down` stops
services without deleting database data.

The SQL planner API is disabled by default. For local testing, set
`DB_ENABLED=true`, `SQL_PLANNER_API_ENABLED=true`, and a strong
`SQL_PLANNER_API_KEY` in `.env`, then run `docker compose up -d --build`.
`POST http://localhost:8010/sql/plan` accepts a `question`, required
`tenant_id`, and optional `max_rows` (1–200), and returns validated SQL as
plain text without executing it.

To also enable read-only SQL execution and a natural-language answer, set
`SQL_QUERY_EXECUTION_ENABLED=true`. Then call
`POST http://localhost:8010/sql/ask` with the same JSON body and
`X-SQL-API-Key`. It returns the original question, grounded natural-language
answer, generated SQL, row count, and whether result data had to be truncated
before sending it to Gemini. PostgreSQL enforces a read-only transaction and a
statement timeout; generated SQL is revalidated and tenant-scoped immediately
before execution. Configure `DB_URL` with a least-privilege read-only database
role in production. The locally imported `closeloop` owner account is not a
production read-only role.

Before SQL generation, the planner runs its input guardrail, confirms the tenant
ID exists, limits queries to approved non-sensitive columns, adds a tenant
filter to every table, and caps result size. The gateway itself trusts the
`tenant_id` it is given and a shared API key does not stop a caller from choosing
another tenant ID, so **never expose it to browsers**. In production only the
CloseLoop backend calls it, and the backend picks the tenant from the logged-in
user (see [Production](#production-integrated-with-closeloop)).

PowerShell example (replace the tenant ID with one from your local database;
run from a trusted local shell, not browser JavaScript):

```powershell
$keyLine = Get-Content .env | Where-Object { $_ -match '^SQL_PLANNER_API_KEY=' } | Select-Object -First 1
$apiKey = $keyLine -replace '^SQL_PLANNER_API_KEY=', ''
$body = @{ question = 'How many tasks are overdue?'; tenant_id = 'YOUR_TENANT_ID'; max_rows = 50 } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://localhost:8010/sql/ask' -Method Post -Headers @{ 'X-SQL-API-Key' = $apiKey } -ContentType 'application/json' -Body $body
```

## Production (integrated with CloseLoop)

```text
browser ──cookie──► CloseLoop backend  /api/chat/stream ──X-SQL-API-Key + X-API-Key + tenant_id──► query_gateway ──► chatbot_ro (read-only) ──► CloseLoop Postgres
                    (authenticates the user,                                                       (private, no host ports)    └─► Qdrant (documents)
                     tenant = the user's own)
```

* **Tenant comes from the login, never the request.** The backend reads `tenant_id` from the
  signed-in user's database row and ignores any tenant in the request body. Every signed-in user
  of a tenant, whatever their role, can ask about that tenant's data; the Platform Owner (no
  tenant) cannot. The shared keys (`SQL_PLANNER_API_KEY`, `API_AUTH_KEY`) live only in server env.
* **What the browser sees is whitelisted.** Generated SQL, routing internals and traces stay on the server.
* **Two walls for the database.** The gateway validates and tenant-scopes every query; the DB role
  `chatbot_ro` (`infra/db/readonly_role.sql`) is read-only with column-level grants, so password
  hashes, tokens, client secrets, storage paths and `audit_logs` cannot be read even by raw SQL.
  Re-run that script after a migration adds tables/columns the chatbot should see.
* **Conversation memory is per user** (the backend derives the gateway session id from the user).

Run (the CloseLoop prod stack must be up first; this joins its `closeloop-prod_default` network):

```bash
# 1. once: create the read-only role, then put its URL in .env.prod as DB_URL
docker exec -i closeloop-prod-db-1 sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v ro_password="<strong password>" -f -' < infra/db/readonly_role.sql
# DB_URL=postgresql://chatbot_ro:<strong password>@db:5432/closeloop

# 2. gateway + redis (nothing is published to the host)
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

`.env.prod` needs `DB_URL`, `SQL_PLANNER_API_KEY`, `API_AUTH_KEY`, `REDIS_PASSWORD`, `GEMINI_API_KEY`,
`QDRANT_*` and `EMBEDDING_MODEL=gemini-embedding-001`. In the CloseLoop repo's `.env.prod` set
`CHATBOT_ENABLED=true`, `CHATBOT_SQL_API_KEY` (= `SQL_PLANNER_API_KEY`) and `CHATBOT_API_KEY`
(= `API_AUTH_KEY`), then recreate its backend and frontend.

Only the query side runs in production. Document ingestion (upload → Qdrant) is a separate
write-side service; stored vectors must be made with the same embedder the gateway queries with.

Docs: [Architecture](docs/ARCHITECTURE.md) · [API](docs/API.md) · [Runbook](docs/RUNBOOK.md)

## Status

Working: ingestion, document retrieval, Gemini answers, intent routing,
tenant-scoped read-only SQL question-answering, and trusted per-user tenant
authentication when run behind the CloseLoop backend (see Production). The
gateway on its own is not an authentication boundary.
