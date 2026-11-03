"""Conversational intake: the agent asks until it knows where you want to go."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.travel import TravelRequest


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)


class CollectedSlots(BaseModel):
    """What the intake agent has pinned down so far."""

    country: str | None = None
    city: str | None = None
    origin_city: str | None = None
    days: int | None = None
    travellers: int | None = None
    travel_style: str | None = None
    interests: list[str] = Field(default_factory=list)
    budget_total: float | None = None
    currency: str | None = None
    start_date: str | None = None
    notes: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    slots: CollectedSlots
    missing: list[str] = Field(default_factory=list)
    ready_to_plan: bool = False
    suggested_request: TravelRequest | None = None
    suggestions: list[str] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)
