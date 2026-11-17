"""CloseLoop API — FastAPI application entrypoint."""

from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import engine
from .auth import decode_token, ACCESS_COOKIE
from .websocket import manager
from .services.holiday_service import refresh_holiday_registry_safely
from .services.scheduler_service import initialize_scheduler, shutdown_scheduler
from .routers import auth as auth_router
from .routers import tenants as tenants_router
from .routers import users as users_router
from .routers import entity_groups as entity_groups_router
from .routers import entities as entities_router
from .routers import periods as periods_router
from .routers import calendar as calendar_router
from .routers import holidays as holidays_router
from .routers import close_packs as close_packs_router
from .routers import plans as plans_router
from .routers import tasks as tasks_router
from .routers import subtasks as subtasks_router
from .routers import carry_forwards as carry_forwards_router
from .routers import dashboards as dashboards_router
from .routers import reports as reports_router
from .routers import report_studio as report_studio_router
from .routers import time_entries as time_entries_router
from .routers import audit_logs as audit_logs_router
from .routers import notifications as notifications_router
from .routers import oauth as oauth_router
from .routers import integration_api as integration_api_router
from .routers import team as team_router
from .routers import hrms as hrms_router
from .routers import timesheets as timesheets_router
from .routers import chat as chat_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic (see migrations/). Run `alembic upgrade head`
    # before/at boot — entrypoint.sh does this automatically in Docker.

    # Fail fast rather than silently serving production traffic with a
    # placeholder JWT secret or non-HTTPS session cookies.
    settings.assert_production_ready()

    # Configured holidays must be in force before anything computes a due date
    # (the scheduler's first run below generates plans). Never raises.
    await refresh_holiday_registry_safely()

    # Startup: initialize background scheduler
    try:
        await initialize_scheduler()
        logger.info("Background scheduler initialized")
    except Exception as e:
        logger.error(f"Failed to initialize scheduler: {e}")

    yield

    # Shutdown: close scheduler and db connections
    try:
        await shutdown_scheduler()
        logger.info("Background scheduler shut down")
    except Exception as e:
        logger.error(f"Failed to shutdown scheduler: {e}")

    await engine.dispose()


app = FastAPI(title="CloseLoop API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers — order doesn't matter functionally but groups by concern
app.include_router(auth_router.router)
app.include_router(tenants_router.router)
app.include_router(users_router.router)
app.include_router(entity_groups_router.router)
app.include_router(entities_router.router)
app.include_router(periods_router.router)
app.include_router(calendar_router.router)
app.include_router(holidays_router.router)
app.include_router(close_packs_router.router)
app.include_router(plans_router.router)
app.include_router(tasks_router.router)
app.include_router(subtasks_router.router)
app.include_router(carry_forwards_router.router)
app.include_router(dashboards_router.router)
app.include_router(reports_router.router)
app.include_router(report_studio_router.router)
app.include_router(time_entries_router.router)
app.include_router(audit_logs_router.router)
app.include_router(notifications_router.router)
app.include_router(oauth_router.router)
app.include_router(integration_api_router.router)
app.include_router(team_router.router)
app.include_router(hrms_router.router)
app.include_router(timesheets_router.router)
app.include_router(chat_router.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "name": "CloseLoop API"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.cookies.get(ACCESS_COOKIE)
    if not token:
        await ws.close(code=4401)
        return
    try:
        payload = decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    if payload.get("type") == "client_credentials":
        # Client-credentials tokens are for the read-only integration API
        # only — never a human session channel, even though they decode
        # successfully with the same JWT secret.
        await ws.close(code=4401)
        return
    user_id = payload.get("sub")
    tenant_id = payload.get("tenantId")
    await manager.connect(ws, tenant_id, user_id)
    try:
        while True:
            # keep alive — client doesn't need to send anything
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws, tenant_id, user_id)
    except Exception:
        await manager.disconnect(ws, tenant_id, user_id)
