"""LangSmith wiring.

LangChain reads tracing configuration from environment variables at import time of
the callback manager, so we export them before any chain or graph is built.
Every run is tagged with the app version and environment, which makes the
LangSmith project filterable per deployment.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("tracing")

_configured = False


def configure_tracing() -> bool:
    """Export LangSmith env vars. Returns True when tracing is actually on."""
    global _configured
    if _configured:
        return os.environ.get("LANGSMITH_TRACING") == "true"

    enabled = settings.langsmith_tracing and bool(settings.langsmith_api_key.strip())
    if enabled:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"  # back-compat with older SDKs
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
        log.info("langsmith_enabled", project=settings.langsmith_project)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        if settings.langsmith_tracing:
            log.warning("langsmith_disabled", reason="LANGSMITH_API_KEY is empty")

    _configured = True
    return enabled


def run_config(
    name: str,
    *,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Build a LangChain `RunnableConfig` that groups a run in LangSmith."""
    meta = {
        "app_env": settings.app_env,
        "app_version": "1.0.0",
        "model": settings.gemini_model,
        **(metadata or {}),
    }
    if thread_id:
        meta["thread_id"] = thread_id
    return {
        "run_name": name,
        "tags": ["travel-planner", settings.app_env, *(tags or [])],
        "metadata": meta,
        "configurable": {"thread_id": thread_id or str(uuid.uuid4())},
        "recursion_limit": 25,
    }


@contextmanager
def trace_span(name: str, **metadata: Any) -> Iterator[None]:
    """Best-effort span. Silently no-ops when LangSmith is not configured."""
    if os.environ.get("LANGSMITH_TRACING") != "true":
        yield
        return
    try:
        from langsmith import tracing_context

        with tracing_context(enabled=True, tags=[name], metadata=metadata):
            yield
    except Exception:  # tracing must never break the request path
        yield
