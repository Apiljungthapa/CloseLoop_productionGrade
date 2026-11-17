# CloseLoop Chatbot Agent — Architecture (v0.1, initial plan)

Natural-language assistant for CloseLoop. Users ask things like *"What's overdue for my team this period?"* or *"How does carry-forward work?"* and get an answer grounded in **their own tenant's data** and the product docs.

## Goals

| Goal | How |
|---|---|
| Correct answers | Every answer is built from validated evidence (SQL rows or documents), then re-checked |
| Tenant + role safe | `tenant_id` and role scope are injected by the system, never by the LLM |
| Read-only | The bot can never create, edit or delete anything |
| Auditable | Each step is logged with the question, SQL, sources and verdict |

## Flow

```
User
  ▼
Query Gateway ──► Query Understanding ──► Query Planner
                                              │
                         ┌────────────────────┼────────────────────┐
                         ▼                    ▼                    ▼
                        RAG                  DB                  BOTH (parallel)
                         │                    │
                 Retriever → Reranker   Schema Retriever → SQL Planner
                         │              → SQL Generator → SQL Validator
                         ▼              → Executor → DB Validator
                  Doc Validator                │
                         └────────────┬────────┘
                                      ▼
                               Evidence Merger
                                      ▼
                               Answer Generator
                                      ▼
                               Answer Validator
                                 │          │
                               PASS        FAIL
                                 ▼          ▼
                                END       REPAIR (max 2 loops → back to the failing step)
```

## Components

### 1. Query Gateway
- **Does:** Entry point. Authenticates the request using the existing CloseLoop session cookie and builds the user context `{user_id, tenant_id, role, entity scope}`.
- **Solves:** Identity comes from the JWT, never from the chat text, so a user can't claim to be someone else or ask about another tenant.
- **Also:** Rate limit per user, max input length, basic prompt-injection screening.

### 2. Query Understanding
- **Does:** Extracts **intent** (status lookup, report, how-to, deadline…), **entities** (pack, task, person, entity/company), **time** ("this period", "last month", BS or AD dates) and merges in the user context.
- **Solves:** Turns vague text into a structured request. Resolves CloseLoop terms (period, pack, carry-forward) and Nepali BS dates using `app/calendar`.

### 3. Query Planner
- **Does:** Picks the route: **RAG** (how-to, policy, concepts), **DB** (live numbers and status), or **BOTH**.
- **Solves:** Avoids calling the database for doc questions and guessing from docs for data questions. BOTH runs the two branches in parallel.
- **Also:** Asks a clarifying question when the request is ambiguous, and refuses out-of-scope requests.

### RAG branch
| Step | Does | Solves |
|---|---|---|
| **Retriever** | Hybrid search (vector + keyword) over help docs, `README`/runbooks, pack and template descriptions | Finds candidate passages |
| **Reranker** | Re-scores candidates against the question and keeps the top 3–5 | Removes noisy matches |
| **Doc Validator** | Drops passages that are irrelevant, stale, or from another tenant | Only trusted, in-scope text reaches the answer |

### DB branch
| Step | Does | Solves |
|---|---|---|
| **Schema Retriever** | Gives the LLM only the relevant tables and columns from a **curated allowlist** (tasks, sub_tasks, plans, periods, entities, time_entries, carry_forward_*) | Smaller prompt, fewer wrong joins, no access to secrets (users' password hashes, tokens, credentials) |
| **SQL Planner** | Outlines tables, joins, filters and aggregation in plain steps | Better SQL on multi-table questions |
| **SQL Generator** | Writes a single `SELECT` from the plan | |
| **SQL Validator** | Parses the SQL (AST). Rejects anything that is not a `SELECT`, uses non-allowlisted tables, or lacks the tenant filter. **Injects** `tenant_id = :tenant` and role scope (a Team Member only sees own tasks; a Team Lead only their tasks) | Blocks cross-tenant leaks and privilege escalation even if the LLM misbehaves |
| **Executor** | Runs on a **read-only DB role** with statement timeout (~5 s) and row cap (e.g. 200) | Cannot write or hang the database |
| **DB Validator** | Checks for errors, empty or suspicious results (e.g. zero rows where a filter looks wrong) | Catches bad SQL that ran "successfully" |

### 4. Evidence Merger
- **Does:** Combines validated rows and passages into one evidence bundle, with a source ID on every item (table/rows or doc + section). Resolves conflicts, preferring live DB data over docs.
- **Solves:** The answer generator sees one clean, cited source of truth.

### 5. Answer Generator
- **Does:** Writes the answer **only from the evidence**, in plain language, with short tables or lists and source references. Says "I don't know" when evidence is missing.
- **Solves:** Prevents invented numbers or features.

### 6. Answer Validator
- **Does:** Checks that every claim and number is supported by the evidence, that nothing out-of-scope is leaked, and that the question was actually answered.
- **PASS** → return to user. **FAIL** → Repair.

### 7. Repair
- **Does:** Routes back to the step that failed (re-retrieve, regenerate SQL with the validator's error message, or rewrite the answer). **Maximum 2 attempts**, then returns a safe fallback ("I couldn't verify this — try rephrasing").
- **Solves:** Self-correction without infinite loops or runaway cost.

## Production Guardrails (must-haves)

1. **Tenant isolation is enforced in code**, not in the prompt (SQL Validator injection + read-only role).
2. **Read-only**: a dedicated Postgres user with `SELECT` on allowlisted tables only. No actions or write tools in v1.
3. **Role scoping** reuses CloseLoop's existing access rules (`services/access_service.py`) so the bot never shows more than the UI would.
4. **Audit log** per request: user, tenant, route, SQL, sources, validator verdicts, latency, token cost (reuse `audit_service`).
5. **Limits**: query timeout, row cap, max repair loops, rate limit, token budget.
6. **Prompt-injection safe**: document and task text (comments, descriptions) is treated as data, never as instructions.
7. **Evaluation**: a fixed test set of questions per role, with expected SQL or answers, run in CI before each prompt or model change.

## Suggested Implementation

- **Service:** new FastAPI router `routers/chat.py` plus `services/chatbot/` (one module per component above), orchestrated as a state graph so Repair loops are explicit.
- **Streaming:** answers stream over the existing WebSocket/SSE channel.
- **Vector store:** `pgvector` in the existing Postgres (no new infra).
- **Frontend:** a chat panel in `Layout.jsx`, visible to all roles.

## Phases

| Phase | Scope |
|---|---|
| **1 (MVP)** | Gateway, Understanding, Planner, DB branch with validators, Answer Generator + Validator, read-only |
| **2** | RAG branch (docs), BOTH route, Repair loop |
| **3** | Evaluation suite, caching, thumbs up/down feedback, proactive suggestions |

## Out of Scope (v1)

Write actions (create/assign/approve tasks), file/attachment analysis, cross-tenant analytics.
