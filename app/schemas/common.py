"""Small shared response shapes."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class HealthStatus(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    environment: str = "development"
    llm_enabled: bool = False
    langsmith_tracing: bool = False
    database: str = "unknown"
    providers: dict[str, str] = Field(default_factory=dict)
    uptime_seconds: float = 0.0


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Ack(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class StreamEvent(BaseModel):
    """One server-sent event emitted while the agent graph runs."""

    event: str
    step: str = ""
    message: str = ""
    progress: float = 0.0
    data: dict[str, Any] = Field(default_factory=dict)
