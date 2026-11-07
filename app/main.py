"""FastAPI application factory.

Serves the JSON API under /api/v1 and the travel site itself from /, so the whole
product is one process and one deployment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.config import FRONTEND_DIR, settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import register_middleware
from app.core.rate_limit import custom_rate_limit_handler, limiter
from app.core.tracing import configure_tracing
from app.db.session import dispose_db, init_db
from app.services.http import close_client
from slowapi.errors import RateLimitExceeded

configure_logging(settings.log_level, json_logs=settings.is_production)
log = get_logger("main")

DESCRIPTION = """
A team of AI agents plans your trip end to end.

* **Talk to it** - `POST /api/v1/chat` asks where you want to go until it knows enough.
* **Plan** - `POST /api/v1/plans` runs the whole agent graph: research, curation,
  storytelling, transport and a day-by-day itinerary.
* **Watch it work** - `POST /api/v1/plans/stream` streams each agent as it finishes.
* **Get there** - `POST /api/v1/routes` returns road, bus, train and flight options.
* **Book** - `POST /api/v1/bookings/search` then hold, pay and confirm.

Facts come from Wikipedia and OpenStreetMap; Gemini supplies judgement and prose.
Every agent run is traced to LangSmith when a key is configured.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    tracing_on = configure_tracing()
    await init_db()
    log.info(
        "startup",
        env=settings.app_env,
        llm=settings.gemini_model if settings.llm_enabled else "disabled",
        langsmith=tracing_on,
    )
    if not settings.llm_enabled:
        log.warning(
            "llm_not_configured",
            hint="Set GOOGLE_API_KEY in .env - planning and chat will fail until you do.",
        )
    try:
        yield
    finally:
        await close_client()
        await dispose_db()
        log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "AI Travel Planner", "email": settings.contact_email},
        license_info={"name": "MIT"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time-ms"],
    )
    register_middleware(app)
    register_exception_handlers(app)

    # Attach rate limiter & handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, custom_rate_limit_handler)

    app.include_router(api_router)
    app.include_router(health_router)  # unversioned /health for load balancers

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the site when the frontend directory is present."""
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        @app.get("/", include_in_schema=False)
        async def _docs_redirect():
            return RedirectResponse("/docs")

        return

    for name in ("css", "js", "assets"):
        directory = FRONTEND_DIR / name
        if directory.exists():
            app.mount(f"/{name}", StaticFiles(directory=directory), name=name)

    @app.get("/", include_in_schema=False)
    async def _index():
        return FileResponse(index)

    @app.get("/favicon.svg", include_in_schema=False)
    async def _favicon():
        icon = FRONTEND_DIR / "assets" / "favicon.svg"
        return FileResponse(icon) if icon.exists() else RedirectResponse("/")


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=not settings.is_production,
        log_level=settings.log_level.lower(),
    )
