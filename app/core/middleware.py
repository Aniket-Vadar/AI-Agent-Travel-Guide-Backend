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


class RateLimitAndAbuseMiddleware(BaseHTTPMiddleware):
    """Enforce real IP abuse protection and payload boundaries.

    Controlled by `settings.rate_limit_enabled` (toggleable in .env).
    """

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Allow health and docs paths freely
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/health"):
            return await call_next(request)

        from app.core.rate_limit import abuse_tracker, get_real_ip

        ip = get_real_ip(request)

        # 1. Check if IP is currently jailed for bursting
        if abuse_tracker.is_jailed(ip):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": "temporarily_blocked",
                        "message": "Too many requests detected from your IP. Temporary cooldown in place.",
                        "ip": ip,
                    }
                },
                headers={"Retry-After": "600"},
            )

        # 2. Check payload size limit (max 64 KB)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 65536:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "error": {
                        "code": "payload_too_large",
                        "message": "Request payload exceeds allowed limit (64 KB).",
                    }
                },
            )

        # 3. Record hit in sliding window
        if not abuse_tracker.record_and_check(ip):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Burst request limit exceeded. Temporary cooldown engaged.",
                        "ip": ip,
                    }
                },
                headers={"Retry-After": "600"},
            )

        return await call_next(request)


def register_middleware(app: FastAPI) -> None:
    # Added last runs first, so the request-context wrapper goes on after auth.
    app.add_middleware(RateLimitAndAbuseMiddleware)
    app.add_middleware(APIKeyMiddleware)
    app.add_middleware(RequestContextMiddleware)
