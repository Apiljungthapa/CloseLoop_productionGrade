"""Query gateway entry point.

Run from the repo root:
    PYTHONPATH=. uvicorn app.api:app --app-dir apps/query_gateway/src --port 8002
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.deps import get_services
from app.routes import health, query, sql_planner, workflow
from core.config import get_settings
from core.exceptions import GatewayError
from core.logging import setup_logging
from shared.utils.telemetry import new_request_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    get_services().retriever.rag.warm_up()
    logger.info("Query gateway ready")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Query Gateway", version="1.0.0", lifespan=lifespan)

    origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_request_id()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(f"[{request_id}] {request.method} {request.url.path} crashed")
            return JSONResponse({"detail": "Internal server error", "request_id": request_id}, 500)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} -> {response.status_code} "
            f"({(time.perf_counter() - started) * 1000:.0f} ms)"
        )
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, error: GatewayError):
        logger.error(f"{type(error).__name__}: {error.detail}")
        retry_after = getattr(error, "retry_after", 0)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return JSONResponse({"detail": error.public_message}, error.status_code, headers=headers)

    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(sql_planner.router)
    app.include_router(workflow.router)
    return app


app = create_app()
