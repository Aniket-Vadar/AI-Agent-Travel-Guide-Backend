"""Cross-cutting HTTP middleware: request ids, timing, and optional API-key auth."""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import get_logger, request_id_ctx

log = get_logger("http")

PUBLIC_PATHS = ("/health", "/api/v1/health", "/docs", "/redoc", "/openapi.json")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, log the outcome, and expose the duration."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_ctx.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time-ms"] = str(elapsed_ms)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=elapsed_ms,
        )
        return response


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Enforce `X-API-Key` on /api/v1 routes when an API key is configured."""

    async def dispatch(self, request: Request, call_next):
        if not settings.api_key:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(PUBLIC_PATHS):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if provided != settings.api_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": {
                        "code": "unauthorized",
                        "message": "A valid X-API-Key header is required.",
                        "request_id": request_id_ctx.get(),
                    }
                },
            )
        return await call_next(request)


def register_middleware(app: FastAPI) -> None:
    # Added last runs first, so the request-context wrapper goes on after auth.
    app.add_middleware(APIKeyMiddleware)
    app.add_middleware(RequestContextMiddleware)
