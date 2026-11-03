"""The conversational intake endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.session import get_session
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import chat as chat_service

router = APIRouter(prefix="/chat", tags=["conversation"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Talk to the intake agent",
    response_description=(
        "The agent's reply plus everything it has worked out so far. When "
        "`ready_to_plan` is true, post `suggested_request` to /plans."
    ),
)
async def send_message(
    request: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    return await chat_service.handle_turn(session, request)


@router.get("/{session_id}", response_model=ChatResponse, summary="Resume a conversation")
async def get_conversation(
    session_id: str,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    state = await chat_service.get_session_state(session, session_id)
    if state is None:
        raise NotFoundError(f"No conversation with id '{session_id}'.")
    return state
