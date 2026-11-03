"""Planning endpoints: generate a guide, stream one, and manage saved plans."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import SessionFactory, get_session
from app.schemas.common import Ack, Page
from app.schemas.travel import PlanSummary, TravelGuide, TravelRequest
from app.services import planner

log = get_logger("api.plans")
router = APIRouter(prefix="/plans", tags=["planning"])


@router.post(
    "",
    response_model=TravelGuide,
    summary="Generate a complete travel guide",
    response_description="The finished guide, already saved and retrievable by plan_id.",
)
async def create_plan(
    request: TravelRequest,
    session: AsyncSession = Depends(get_session),
) -> TravelGuide:
    """Run the full agent team and return the guide in one response.

    Expect 20-40 seconds. For a responsive UI, use `/plans/stream` instead.
    """
    return await planner.generate_plan(request, session=session)


@router.post(
    "/stream",
    summary="Generate a guide, streaming each agent's progress",
    response_description="A text/event-stream of StreamEvent objects.",
)
async def stream_plan(request: TravelRequest) -> EventSourceResponse:
    """Server-sent events, one per completed agent, ending with the full guide.

    This handler opens its own database session: the request-scoped dependency
    would be closed the moment the response object is returned, long before the
    generator that needs it has finished producing events.
    """

    async def publisher():
        async with SessionFactory() as db:
            try:
                async for event in planner.stream_plan(request, session=db):
                    yield {"event": event.event, "data": event.model_dump_json()}
            except AppError as exc:
                yield {
                    "event": "error",
                    "data": json.dumps({"code": exc.code, "message": exc.message}),
                }
            except Exception as exc:
                log.exception("stream_failed", error=str(exc))
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"code": "internal_error", "message": "The planner stopped unexpectedly."}
                    ),
                }

    return EventSourceResponse(publisher(), ping=15_000)


@router.get("", response_model=Page[PlanSummary], summary="List saved plans")
async def list_plans(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[PlanSummary]:
    items, total = await planner.list_plans(session, limit=limit, offset=offset)
    return Page[PlanSummary](items=items, total=total, limit=limit, offset=offset)


@router.get("/{plan_id}", response_model=TravelGuide, summary="Fetch a saved plan")
async def get_plan(plan_id: str, session: AsyncSession = Depends(get_session)) -> TravelGuide:
    return await planner.get_plan(session, plan_id)


@router.delete("/{plan_id}", response_model=Ack, summary="Delete a saved plan")
async def delete_plan(plan_id: str, session: AsyncSession = Depends(get_session)) -> Ack:
    await planner.delete_plan(session, plan_id)
    return Ack(message=f"Plan {plan_id} deleted.")
