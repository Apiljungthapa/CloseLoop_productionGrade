from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.deps import Services, get_services
from core import cache as cache_layer

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(services: Services = Depends(get_services)) -> JSONResponse:
    ok = services.retriever.rag.is_healthy()
    cache = cache_layer.get_cache()
    # The cache is optional: a Redis outage never makes the gateway "unavailable".
    return JSONResponse(
        {
            "status": "ready" if ok else "unavailable",
            "vector_store": ok,
            "cache": {"status": cache.status(), "layers": cache.stats()},
            "llm": services.generator.client.describe(),
        },
        200 if ok else 503,
    )
