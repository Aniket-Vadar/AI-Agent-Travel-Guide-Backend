"""The intake agent: the conversation that happens before any planning.

It runs as a slot-filling loop rather than a free-form chat. Each turn the model
returns both its reply and the full set of slots it believes are true, which the
service merges over what it already knew. That makes the conversation resumable
and the hand-off to the planner a pure data transfer - no prompt archaeology at
the end to work out where the traveller actually wants to go.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.llm import ainvoke_structured
from app.core.logging import get_logger
from app.schemas.chat import ChatMessage, CollectedSlots
from app.schemas.travel import Pace, TravelRequest, TravelStyle

log = get_logger("intake")

REQUIRED_SLOTS = ("country", "city", "days")

VALID_STYLES = {s.value for s in TravelStyle}


class IntakeTurn(BaseModel):
    """What the intake agent returns on every turn."""

    reply: str = Field(..., description="What to say to the traveller. Under 70 words.")
    slots: CollectedSlots = Field(default_factory=CollectedSlots)
    ready_to_plan: bool = False
    suggestions: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Two to four short tappable replies, 1-4 words each.",
    )


def _merge_slots(known: CollectedSlots, fresh: CollectedSlots) -> CollectedSlots:
    """New non-empty values win; previously known values are never blanked out."""
    merged = known.model_dump()
    for key, value in fresh.model_dump().items():
        if value in (None, "", []):
            continue
        if key == "interests":
            existing = list(merged.get("interests") or [])
            for interest in value:
                cleaned = interest.strip().lower()
                if cleaned and cleaned not in existing:
                    existing.append(cleaned)
            merged["interests"] = existing[:12]
        else:
            merged[key] = value
    return CollectedSlots(**merged)


def missing_slots(slots: CollectedSlots) -> list[str]:
    return [name for name in REQUIRED_SLOTS if not getattr(slots, name, None)]


def to_travel_request(slots: CollectedSlots) -> TravelRequest | None:
    """Turn collected slots into a validated planning request, or None if incomplete."""
    if missing_slots(slots):
        return None

    style = (slots.travel_style or "").strip().lower().replace(" ", "_")
    start: date | None = None
    if slots.start_date:
        try:
            start = date.fromisoformat(slots.start_date[:10])
        except ValueError:
            start = None

    return TravelRequest(
        country=slots.country or "",
        city=slots.city or "",
        origin_city=slots.origin_city,
        days=max(1, min(21, int(slots.days or 3))),
        start_date=start,
        travellers=max(1, min(20, int(slots.travellers or 2))),
        travel_style=TravelStyle(style) if style in VALID_STYLES else TravelStyle.comfort,
        pace=Pace.balanced,
        interests=slots.interests or [],
        budget_total=slots.budget_total,
        currency=slots.currency or "INR",
        notes=slots.notes or "",
    )


async def run_intake(
    message: str,
    history: list[ChatMessage],
    known: CollectedSlots,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[IntakeTurn, CollectedSlots]:
    """One conversational turn. Returns the turn and the merged slot state."""
    transcript: list[Any] = [SystemMessage(content=prompts.INTAKE_AGENT)]
    for entry in history[-12:]:
        if entry.role == "user":
            transcript.append(HumanMessage(content=entry.content))
        elif entry.role == "assistant":
            transcript.append(AIMessage(content=entry.content))

    known_json = known.model_dump(exclude_none=True)
    transcript.append(
        HumanMessage(
            content=(
                f"ALREADY KNOWN: {known_json or 'nothing yet'}\n"
                f"STILL MISSING: {missing_slots(known) or 'nothing'}\n\n"
                f"TRAVELLER SAYS: {message}"
            )
        )
    )

    turn = await ainvoke_structured(
        IntakeTurn,
        transcript,
        temperature=0.5,
        config=config,
        fallback=IntakeTurn(
            reply=(
                "I did not quite catch that. Which country and city are you thinking of, "
                "and roughly how many days do you have?"
            )
        ),
    )

    merged = _merge_slots(known, turn.slots)
    # The model is not the authority on readiness; the slots are.
    turn.ready_to_plan = not missing_slots(merged)
    log.info(
        "intake_turn",
        ready=turn.ready_to_plan,
        missing=missing_slots(merged),
        city=merged.city,
    )
    return turn, merged
