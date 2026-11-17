# CloseLoop

Multi-tenant **Task & Period-Close Planner** built with:

- **Backend** — FastAPI · async SQLAlchemy 2.0 (asyncpg) · PostgreSQL 16 · native WebSockets · JWT auth
- **Frontend** — React 18 · Vite · react-bootstrap · bootstrap-icons
- **Infra** — Docker Compose (one command to bring up the whole stack)

Implements the SRS v1.0 (June 2026):
- Four roles: **Platform Owner**, **Super Admin**, **Team Lead**, **Team Member**
- Three distinct role-based dashboards
- Strict tenant isolation (`tenant_id` on every business table, enforced in every query)
- Plan → Task → Sub-Task → Checkpoint hierarchy with status roll-up
- **Maker-Checker review**: Team Members complete checkpoints and submit a sub-task; the parent task's **Team Lead** approves or rejects (with note) — rejected items return to the member for rework
- Real-time updates via WebSockets (tenant + per-user rooms)
- Period-Close template (Invoices Mailbox · Pending Invoices · Approval Pending · PLEO Expenses)

---

## Quick start (Docker — recommended)

Prerequisites: **Docker Desktop** (or Docker Engine + Compose v2).

```bash
docker compose up --build
```

That's it. On first boot the backend container waits for Postgres, runs `alembic upgrade head` to create the schema, then runs `seed.py` (because `SEED_ON_BOOT=1` in `docker-compose.yml`) to insert demo data. `seed.py` is **idempotent** — it skips if the demo data already exists. Then:

| Service     | URL                                          |
|-------------|----------------------------------------------|
| Frontend    | http://localhost:8080                        |
| Backend API | http://localhost:8000/api/health             |
| Postgres    | `localhost:5433`  (user/pass: postgres/postgres, db: `closeloop`) |

> Leaving `SEED_ON_BOOT=1` is now safe across restarts (idempotent). To wipe & re-seed run `docker compose exec -e SEED_RESET=1 backend python seed.py`. To start from a clean DB, `docker compose down -v` then `up`.

### Schema migrations (Alembic)

Schema is managed by Alembic — see `backend/migrations/`. The container runs `alembic upgrade head` automatically at boot. To add a new migration after changing `app/models.py`:

```bash
docker compose exec backend alembic revision --autogenerate -m "your change"
docker compose exec backend alembic upgrade head
```

### Smoke test

```bash
docker compose exec backend python smoke_test.py
```
Exercises login for all roles, plan/task/subtask creation, the maker-checker flow, and tenant isolation.

### Demo accounts (printed in backend logs)

| Role           | Email                  | Password    |
|----------------|------------------------|-------------|
| Platform Owner | owner@closeloop.io     | owner123    |
| Super Admin    | admin@acme.com         | admin123    |
| **Team Lead** (reviewer) | lead@acme.com   | lead123     |
| Team Member    | member@acme.com        | member123   |
| Team Member    | member2@acme.com       | member123   |

### Common commands

```bash
docker compose up -d            # background
docker compose logs -f backend  # tail backend logs
docker compose down             # stop containers (keep data)
docker compose down -v          # stop AND wipe Postgres volume
docker compose build --no-cache # rebuild from scratch
```

---

## Demo flow (5 minutes)

1. Open http://localhost:8080 — log in as **admin@acme.com / admin123** (Super Admin dashboard, red theme). Open the seeded plan **"Period Close — June 2026"**, add a new Task and assign it to **Larry Lead**.
2. Log out, log in as **lead@acme.com / lead123** (Team Lead dashboard, blue theme). Open the same plan, expand the task you were just assigned, add a sub-task assigned to **Mia Member** with a few checkpoints.
3. Log out, log in as **member@acme.com / member123** (Team Member dashboard, green theme). Tick all checkpoints **Done** (sequential — out-of-order is rejected) → click **Submit to Team Lead**.
4. Log back in as **lead@acme.com** → the sub-task shows **PENDING_REVIEW** → **Approve** or **Reject with note**.
5. If rejected, log back in as the member → see the rejection note + alert → fix → resubmit.

Watch the **bell icon** for live notifications and the dashboards refreshing live via WebSocket.

---

## Architecture notes

```
PLATFORM_OWNER  ── manages tenants (no tenant_id)
   │
   └─ Tenant ──────────────────────────────────────────────
        │   SUPER_ADMIN ── creates Plans + Tasks; assigns each Task to a Team Lead
        │      │
        │      └─ TEAM_LEAD ── owns assigned Tasks; creates SubTasks
        │              │      and assigns them to Team Members; reviews submissions
        │              │
        │              └─ TEAM_MEMBER ── completes Checkpoints, submits SubTasks
        │
        ▼
   Plan ─► Task ─► SubTask ─► Checkpoint (─► Attachment)
   status rolls up: Checkpoint completion → SubTask status → Task status → Plan status
```

- **Tenant isolation** — every business model has a `tenant_id`. The `require_tenant` FastAPI dependency rejects requests where the JWT has no tenant, and every query filters by `tenant_id == me.tenant_id`.
- **Role boundaries** (enforced server-side in `app/routers/*` via `require_roles`):
  - `POST/PATCH/DELETE /api/plans` — **SUPER_ADMIN only**
  - `POST/PATCH/DELETE /api/tasks` — **SUPER_ADMIN only**; optional `assigned_to_id` must be a TEAM_LEAD
  - `POST/PATCH/DELETE /api/subtasks` — **TEAM_LEAD only**, and only on a task where `task.assigned_to_id == me.id`; `assigned_to_id` must be a TEAM_MEMBER
  - `POST /api/subtasks/{id}/submit-for-review` — the assignee OR the parent task's Team Lead
  - `POST /api/subtasks/{id}/review` — the parent task's Team Lead only (Super Admin cannot override)
  - `GET /api/plans` for a Team Lead returns only plans where they own ≥1 task
- **Maker-Checker** — `SubTask.reviewer_id` is hard-set to the parent task's Team Lead (`task.assigned_to_id`) at creation; client-supplied `reviewer_id` is ignored. `POST /api/subtasks/{id}/submit-for-review` requires all checkpoints completed; review with `POST /api/subtasks/{id}/review` body `{decision: APPROVE|REJECT, note?}`. Rejected sub-tasks flip back to `REJECTED` with the note shown to the member.
- **Status roll-up** — `app/rollup.py` recomputes Task status from its SubTasks and Plan status from its Tasks after every relevant mutation.
- **WebSockets** — connect to `ws://<host>/ws?token=<JWT>`. The server joins the socket to a **tenant room** (broadcasts: `plan:created`, `subtask:updated`, etc.) and a **user room** (per-user `notification` events). The React hook `useLiveRefresh` re-fetches on any event.
- **Sequential checkpoints** — `POST /api/checkpoints/{id}/complete` rejects out-of-order completion (any earlier checkpoint must be done first).

### Project structure

```
apill/
├── docker-compose.yml
├── backend/
│   ├── Dockerfile · entrypoint.sh · .dockerignore
│   ├── requirements.txt · .env · seed.py
│   └── app/
│       ├── main.py · config.py · database.py
│       ├── models.py · schemas.py · auth.py
│       ├── websocket.py · rollup.py
│       └── routers/  (auth, tenants, users, plans, tasks, subtasks, checkpoints, notifications)
└── frontend/
    ├── Dockerfile · nginx.conf · .dockerignore
    ├── package.json · vite.config.js · index.html
    └── src/
        ├── main.jsx · App.jsx · api.js · ws.js · auth.jsx · styles.css
        ├── components/  (Layout, Notifications, Bits, useLiveRefresh)
        └── pages/       (Login, SuperAdminDashboard, TeamLeadDashboard,
                          TeamMemberDashboard, PlanDetail, Users, Tenants)
```

---

## Run without Docker (local dev)

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt

# Make sure PostgreSQL is running locally and the DB exists:
#   createdb -U postgres closeloop
# Adjust backend/.env DATABASE_URL if needed.

python seed.py                     # creates schema + demo data
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173 (proxies /api + /ws to :8000)
```

---

## Key API endpoints

| Method | Path                                | Notes                                         |
|--------|-------------------------------------|-----------------------------------------------|
| POST   | `/api/auth/login`                   | `{email, password}` → user; sets httpOnly session cookies |
| POST   | `/api/auth/refresh`                 | Rotates the session using the refresh cookie  |
| POST   | `/api/auth/logout`                  | Revokes the session server-side, clears cookies |
| GET    | `/api/auth/me`                      | Current user                                  |
| GET / POST / PATCH / DELETE | `/api/tenants`         | Platform Owner only                           |
| GET / POST | `/api/users`                    | Tenant-scoped                                 |
| GET / POST / PATCH / DELETE | `/api/plans`           | Super Admin creates/edits/deletes. `apply-period-close-template` is also SA-only. |
| GET / POST / PATCH / DELETE | `/api/tasks`           | Super Admin only; `assigned_to_id` must be a TEAM_LEAD |
| POST / PATCH / DELETE | `/api/subtasks`              | Team Lead only, scoped to tasks they own; `assigned_to_id` must be a TEAM_MEMBER |
| POST   | `/api/subtasks/{id}/submit-for-review` | Assignee or parent task's Team Lead; all checkpoints must be done |
| POST   | `/api/subtasks/{id}/review`         | Parent task's Team Lead only — `{decision, note}` |
| POST   | `/api/checkpoints/{id}/complete`    | Sequential                                    |
| POST   | `/api/checkpoints/{id}/uncomplete`  |                                               |
| GET / POST | `/api/notifications`            | Plus `POST /api/notifications/mark-all-read`  |
| WS     | `/ws?token=<JWT>`                   | Live events                                   |

---

## Production checklist

- [ ] Change `JWT_SECRET` in `docker-compose.yml` to a long random string
- [ ] Set `SEED_ON_BOOT: "0"` once you no longer need the demo seed to attempt insertion on every boot
- [x] Schema is managed by Alembic (`backend/migrations/`); container runs `alembic upgrade head` at boot
- [ ] Put the frontend behind HTTPS (terminate TLS at a reverse proxy in front of nginx)
- [ ] Switch Postgres credentials, restrict the `5433` host port, use a managed DB
- [ ] Configure structured logging + observability
#   C l o s e L o o p 
#
 
 
