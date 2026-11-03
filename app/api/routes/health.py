"""Liveness, readiness and a cache admin endpoint."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

from app.core.config import settings
from app.db.session import ping
from app.schemas.common import Ack, HealthStatus
from app.services.cache import cache

router = APIRouter(tags=["ops"])

_STARTED_AT = time.time()


@router.get("/health", response_model=HealthStatus, summary="Service health")
async def health() -> HealthStatus:
    db_ok = await ping()
    return HealthStatus(
        status="ok" if db_ok else "degraded",
        environment=settings.app_env,
        llm_enabled=settings.llm_enabled,
        langsmith_tracing=os.environ.get("LANGSMITH_TRACING") == "true",
        database="ok" if db_ok else "unavailable",
        providers={
            "wikipedia": f"{settings.wikipedia_lang}.wikipedia.org",
            "geocoding": settings.nominatim_url,
            "routing": settings.osrm_url,
            "osm_overpass": settings.overpass_url,
            "llm": settings.gemini_model if settings.llm_enabled else "not configured",
        },
        uptime_seconds=round(time.time() - _STARTED_AT, 1),
    )


@router.get("/health/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready", response_model=Ack, summary="Readiness probe")
async def ready() -> Ack:
    db_ok = await ping()
    return Ack(
        ok=db_ok,
        message="ready" if db_ok else "database unavailable",
        data={"llm_enabled": settings.llm_enabled},
    )


@router.post("/admin/cache/clear", response_model=Ack, summary="Flush the provider cache")
async def clear_cache() -> Ack:
    stats = await cache.stats()
    await cache.clear()
    return Ack(message="Cache cleared.", data=stats)
