# CloseLoop_productionGrade

The production-grade code of **CloseLoop** and its assistant **Close Bhai**, in one repository.

| Folder | What it is |
| --- | --- |
| [`CloseLoop/`](CloseLoop) | The CloseLoop application: period-close task management. FastAPI backend (`backend/`), React frontend (`frontend/`), PostgreSQL, Alembic migrations, Docker Compose files for dev and production. |
| [`chatbot_agent/`](chatbot_agent) | Close Bhai, the chatbot: a query gateway (tenant-scoped, read-only SQL planner plus document search) and a document-ingestion service. |

How they fit together: CloseLoop's backend is the only caller of the Close Bhai gateway. It takes the tenant from the signed-in user's own database row and adds the shared keys, so a browser never talks to the gateway. Report requests typed into the chat (for example "generate the overdue report") are answered inside CloseLoop by Report Studio, not by the language model.

## Running it

Each folder has its own README and Compose files:

- `CloseLoop/README.md`, `CloseLoop/RUNNING.txt`: local and production setup
- `chatbot_agent/README.md`: gateway and ingestion setup

Copy the `*.example` env files (`CloseLoop/.env.prod.example`, `CloseLoop/backend/.env.example`, `chatbot_agent/.env.example`) and fill in your own values.

## What is deliberately not in this repository

- Real `.env` / `.env.prod` files, API keys and passwords. Only the `*.example` templates are committed.
- Production database backups and data manifests.
- `node_modules`, build output and caches.

Never commit real secrets. If a key is ever pushed by mistake, rotate it: deleting the file afterwards does not remove it from git history.
