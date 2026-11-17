# API

If `API_AUTH_KEY` is set, send `X-API-Key: <key>` on every call except `/health` and `/ready`.
Every response has an `X-Request-ID` header. Interactive docs: `/docs` on each service.

## Ingestion service (`:8001`)

Every call except `/health` and `/ready` must also send **`X-Tenant-Id: <tenant>`** (letters, digits, `_`, `-`;
`shared` is reserved). The service acts only for that tenant: it lists, reads, deletes and tracks only that
tenant's documents and jobs, and tags what it stores with it. A document or job that belongs to another
tenant answers `404`. Optional `X-User-Id` records who uploaded (the app uses it to limit who may delete).
Only the CloseLoop backend calls this service, with the signed-in user's own tenant; browsers never do.

| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/health` | | `{"status":"ok"}` |
| GET | `/ready` | | `200` if Qdrant and the job store are reachable, else `503` |
| POST | `/upload` | multipart `file` (size ≤ `MAX_UPLOAD_MB`); `?wait=true` processes inline | `202 {job_id, filename, status, status_url}`; with `wait`: `{filename, total_chunks, chunks[]}` |
| POST | `/upload/batch` | multipart `files` (many) | `202 {batch_id, accepted, rejected[{filename,status,reason}], jobs[]}` |
| GET | `/jobs/{job_id}` | | `{status: queued\|processing\|retrying\|done\|failed, chunks, error, ...}` |
| GET | `/batches/{batch_id}` | `?include_jobs=true` | `{total, counts, finished, jobs[]}` |
| GET | `/documents` | | `{total_documents, documents[{document_id,title,file_format,uploaded_by,chunk_count}]}` |
| GET | `/documents/{id}` | `?include_text=false` | `{document_id, total_chunks, chunks[]}`; `404` if unknown |
| DELETE | `/documents/{id}` | | `{document_id, deleted:true}`; `404` if unknown |

A tenant's document is stored as `<tenant>__<file name>`, so two companies can upload the same file name, and
uploading a file again replaces the old version. Documents ingested by the CLI (`main.py`) have no tenant and are
stored as `shared`: every tenant's questions can use them, none can list or delete them here.

Errors: `400` missing/invalid `X-Tenant-Id` or empty file, `413` too large, `415` unsupported extension,
`422` processing failed, `503` queue unavailable.

## Query gateway (`:8010`)

### `POST /ask`
```json
{"question": "How long is the refund window?", "top_k": 5, "document_id": null}
```
```json
{"question": "...", "answer": "You have 30 days.", "route": "doc",
 "sources": [{"document_id": "policy", "title": "Policy", "section": null, "score": 0.59, "source": "document"}]}
```
`route` is `doc`, `db`, `hybrid` or `chitchat`. `question` ≤ 2000 chars, `top_k` 1–20.

### `POST /retrieve`
```json
{"query": "refund", "top_k": 5, "document_id": null, "min_score": null}
```
Returns `{query, total_matches, results[{text, score, semantic_score, keyword_score, document_id, title, section, chunk_id}]}`. No LLM call.

### `POST /sql/plan` and `POST /sql/ask`
These SQL endpoints are disabled by default. Enable `DB_ENABLED`,
`SQL_PLANNER_API_ENABLED`, and set `SQL_PLANNER_API_KEY`; `/sql/ask` additionally
requires `SQL_QUERY_EXECUTION_ENABLED=true`. They use the dedicated
`X-SQL-API-Key` header.

Both accept a tenant ID and natural-language question:
```json
{"question": "How many tasks are overdue?", "tenant_id": "YOUR_TENANT_ID", "max_rows": 50}
```

`/sql/plan` returns validated, tenant-scoped SQL as `text/plain` and does not
execute it. `/sql/ask` executes the query in a read-only PostgreSQL transaction
and returns a natural-language answer using bounded result data:
```json
{"question": "...", "answer": "...", "sql": "SELECT ...", "row_count": 1,
 "result_data_truncated": false}
```

Both endpoints run input guardrails, restrict tables and columns, enforce
tenant filters, and cap rows. `/sql/ask` also applies `DB_QUERY_TIMEOUT_MS`.
The provided tenant ID is not authenticated by the shared API key; do not
expose these endpoints publicly until trusted authentication maps each caller
to an authorized tenant. Use a least-privilege read-only database role.

### Errors
`401` bad key, `422` validation, `502` Gemini unavailable, `503` Qdrant unavailable, `501` disabled capability, `500` unexpected (body includes `request_id`).
