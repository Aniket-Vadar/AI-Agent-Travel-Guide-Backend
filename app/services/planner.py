"""Runs the agent graph and turns its final state into a stored TravelGuide."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import NODE_LABELS, NODE_PROGRESS, get_compiled_graph
from app.agents.nodes import build_budget
from app.agents.state import PlannerState
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.tracing import run_config
from app.db.models import PlanRecord
from app.schemas.common import StreamEvent
from app.schemas.travel import (
    Destination,
    PlanSummary,
    TransportPlan,
    TravelGuide,
    TravelRequest,
)

log = get_logger("planner")


def _initial_state(request: TravelRequest, plan_id: str, session_id: str | None) -> PlannerState:
    config = run_config(
        "travel-plan",
        tags=["plan", request.travel_style.value],
        metadata={
            "plan_id": plan_id,
            "city": request.city,
            "country": request.country,
            "days": request.days,
            "travellers": request.travellers,
            "session_id": session_id or "",
        },
        thread_id=session_id or plan_id,
    )
    return {
        "request": request,
        "sources": [],
        "warnings": [],
        "run_config": config,
    }


def _assemble(state: dict[str, Any], request: TravelRequest, plan_id: str, elapsed_ms: int) -> TravelGuide:
    destination: Destination = state["destination"]
    places = state.get("places", [])
    transport: TransportPlan = state.get("transport") or TransportPlan()
    front = state.get("front_matter", {}) or {}

    # Deduplicate sources while keeping the order they were discovered in.
    seen: set[str] = set()
    sources: list[str] = []
    for url in state.get("sources", []):
        if url and url not in seen:
            seen.add(url)
            sources.append(url)

    return TravelGuide(
        plan_id=plan_id,
        request=request,
        destination=destination,
        headline=front.get("headline") or f"{destination.name}, {destination.country}",
        overview=front.get("overview", ""),
        places=places,
        itinerary=state.get("itinerary", []),
        transport=transport,
        budget=build_budget(request, transport, len(places)),
        best_time_to_visit=front.get("best_time_to_visit", ""),
        local_food=front.get("local_food", []),
        packing_list=front.get("packing_list", []),
        safety_tips=front.get("safety_tips", []),
        local_phrases=front.get("local_phrases", []),
        sources=sources[:25],
        warnings=state.get("warnings", []),
        generated_at=datetime.now(UTC).isoformat(),
        trace={
            "duration_ms": elapsed_ms,
            "places_considered": state.get("research", {}).get("candidate_count", 0),
            "places_selected": len(places),
            "langsmith_project": settings.langsmith_project if settings.langsmith_tracing else None,
        },
    )


async def generate_plan(
    request: TravelRequest,
    *,
    session: AsyncSession | None = None,
    session_id: str | None = None,
) -> TravelGuide:
    """Run the full graph once and return the finished guide."""
    plan_id = uuid.uuid4().hex
    started = time.perf_counter()
    state = _initial_state(request, plan_id, session_id)

    graph = get_compiled_graph()
    final = await graph.ainvoke(state, config=state["run_config"])

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    guide = _assemble(final, request, plan_id, elapsed_ms)
    log.info(
        "plan_generated",
        plan_id=plan_id,
        city=request.city,
        places=len(guide.places),
        duration_ms=elapsed_ms,
    )
    if session is not None:
        await save_plan(session, guide, session_id=session_id, duration_ms=elapsed_ms)
    return guide


async def stream_plan(
    request: TravelRequest,
    *,
    session: AsyncSession | None = None,
    session_id: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run the graph, emitting one event per completed agent, then the guide."""
    plan_id = uuid.uuid4().hex
    started = time.perf_counter()
    state = _initial_state(request, plan_id, session_id)
    graph = get_compiled_graph()

    yield StreamEvent(
        event="started",
        step="start",
        message=f"Planning {request.days} days in {request.city}",
        progress=0.05,
        data={"plan_id": plan_id},
    )

    final: dict[str, Any] = {}
    async for update in graph.astream(state, config=state["run_config"], stream_mode="updates"):
        for node, payload in update.items():
            if not isinstance(payload, dict):
                continue
            final.update(payload)
            yield StreamEvent(
                event="step",
                step=node,
                message=NODE_LABELS.get(node, node),
                progress=NODE_PROGRESS.get(node, 0.5),
                data=_step_data(node, payload),
            )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    guide = _assemble(final, request, plan_id, elapsed_ms)
    if session is not None:
        await save_plan(session, guide, session_id=session_id, duration_ms=elapsed_ms)

    yield StreamEvent(
        event="complete",
        step="done",
        message="Your guide is ready",
        progress=1.0,
        data={"guide": guide.model_dump(mode="json")},
    )


def _step_data(node: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A small preview per step, so the UI can show progress that means something."""
    if node == "researcher":
        destination = payload.get("destination")
        return {
            "destination": destination.name if destination else "",
            "candidates": payload.get("research", {}).get("candidate_count", 0),
        }
    if node == "curator":
        return {"places": [p.name for p in payload.get("places", [])]}
    if node == "storyteller":
        return {"stories_written": sum(1 for p in payload.get("places", []) if p.story)}
    if node == "navigator":
        transport = payload.get("transport")
        return {"options": [o.label for o in transport.options] if transport else []}
    if node == "editor":
        return {"headline": payload.get("front_matter", {}).get("headline", "")}
    if node == "day_planner":
        return {"days": len(payload.get("itinerary", []))}
    return {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def save_plan(
    session: AsyncSession,
    guide: TravelGuide,
    *,
    session_id: str | None = None,
    duration_ms: int = 0,
) -> None:
    record = PlanRecord(
        id=guide.plan_id,
        city=guide.destination.name or guide.request.city,
        country=guide.destination.country or guide.request.country,
        days=guide.request.days,
        headline=guide.headline[:300],
        place_count=len(guide.places),
        request_json=guide.request.model_dump(mode="json"),
        guide_json=guide.model_dump(mode="json"),
        session_id=session_id,
        duration_ms=duration_ms,
    )
    session.add(record)
    await session.commit()


async def get_plan(session: AsyncSession, plan_id: str) -> TravelGuide:
    record = await session.get(PlanRecord, plan_id)
    if record is None:
        raise NotFoundError(f"No plan with id '{plan_id}'.")
    return TravelGuide.model_validate(record.guide_json)


async def list_plans(session: AsyncSession, limit: int = 20, offset: int = 0) -> tuple[list[PlanSummary], int]:
    total = await session.scalar(select(func.count()).select_from(PlanRecord)) or 0
    rows = await session.scalars(
        select(PlanRecord).order_by(PlanRecord.created_at.desc()).limit(limit).offset(offset)
    )
    summaries = [
        PlanSummary(
            plan_id=r.id,
            city=r.city,
            country=r.country,
            days=r.days,
            headline=r.headline,
            place_count=r.place_count,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
    return summaries, int(total)


async def delete_plan(session: AsyncSession, plan_id: str) -> None:
    record = await session.get(PlanRecord, plan_id)
    if record is None:
        raise NotFoundError(f"No plan with id '{plan_id}'.")
    await session.delete(record)
    await session.commit()
