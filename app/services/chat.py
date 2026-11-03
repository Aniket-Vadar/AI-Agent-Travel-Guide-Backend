"""Conversation state for the intake agent."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.intake import missing_slots, run_intake, to_travel_request
from app.core.logging import get_logger
from app.core.tracing import run_config
from app.db.models import ChatSessionRecord
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, CollectedSlots

log = get_logger("chat")

OPENER = (
    "Tell me where you are dreaming of going - a country, a city, or just a feeling - "
    "and I will build the trip around it."
)


async def _load_session(session: AsyncSession, session_id: str | None) -> ChatSessionRecord:
    if session_id:
        record = await session.get(ChatSessionRecord, session_id)
        if record is not None:
            return record
    record = ChatSessionRecord(
        id=session_id or uuid.uuid4().hex, history_json=[], slots_json={}
    )
    session.add(record)
    await session.flush()
    return record


async def handle_turn(session: AsyncSession, request: ChatRequest) -> ChatResponse:
    """Advance the conversation by one turn and persist the result."""
    record = await _load_session(session, request.session_id)

    stored = [ChatMessage.model_validate(m) for m in (record.history_json or [])]
    history = stored or request.history
    known = CollectedSlots.model_validate(record.slots_json or {})

    config = run_config(
        "intake-agent",
        tags=["intake"],
        metadata={"session_id": record.id},
        thread_id=record.id,
    )
    turn, merged = await run_intake(request.message, history, known, config=config)

    history = [
        *history,
        ChatMessage(role="user", content=request.message),
        ChatMessage(role="assistant", content=turn.reply),
    ][-40:]

    record.history_json = [m.model_dump() for m in history]
    record.slots_json = merged.model_dump()
    record.ready_to_plan = 1 if turn.ready_to_plan else 0
    await session.commit()

    suggested = to_travel_request(merged) if turn.ready_to_plan else None
    return ChatResponse(
        session_id=record.id,
        reply=turn.reply,
        slots=merged,
        missing=missing_slots(merged),
        ready_to_plan=turn.ready_to_plan,
        suggested_request=suggested,
        suggestions=turn.suggestions[:4],
        trace={"thread_id": record.id},
    )


async def get_session_state(session: AsyncSession, session_id: str) -> ChatResponse | None:
    record = await session.get(ChatSessionRecord, session_id)
    if record is None:
        return None
    slots = CollectedSlots.model_validate(record.slots_json or {})
    history = [ChatMessage.model_validate(m) for m in (record.history_json or [])]
    last_reply = next(
        (m.content for m in reversed(history) if m.role == "assistant"), OPENER
    )
    return ChatResponse(
        session_id=record.id,
        reply=last_reply,
        slots=slots,
        missing=missing_slots(slots),
        ready_to_plan=bool(record.ready_to_plan),
        suggested_request=to_travel_request(slots),
    )
