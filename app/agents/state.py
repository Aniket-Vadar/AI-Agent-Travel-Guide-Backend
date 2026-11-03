"""Graph state and the structured-output contracts each agent must return."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

from app.schemas.travel import (
    Destination,
    ItineraryDay,
    Place,
    PlaceCategory,
    TransportPlan,
    TravelRequest,
)


class PlannerState(TypedDict, total=False):
    """What flows between the agents.

    `warnings` uses an additive reducer because the storytelling, transport and
    front-matter agents run in parallel and any of them may need to flag a gap.
    """

    request: TravelRequest
    destination: Destination
    origin: Destination | None
    candidates: list[dict[str, Any]]
    research: dict[str, Any]
    places: list[Place]
    transport: TransportPlan
    itinerary: list[ItineraryDay]
    front_matter: dict[str, Any]
    sources: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]
    run_config: dict[str, Any]


# --------------------------------------------------------------------------
# Structured outputs
# --------------------------------------------------------------------------


class CuratedPlace(BaseModel):
    candidate_id: str = Field(..., description="The id of the candidate you are selecting.")
    name: str
    category: PlaceCategory = PlaceCategory.other
    tagline: str = Field(default="", max_length=90)
    why_visit: str = ""
    best_time: str = ""
    suggested_duration_minutes: int = 90
    entry_fee_note: str = ""
    tips: list[str] = Field(default_factory=list, max_length=4)
    rating_hint: float | None = Field(default=None, ge=0, le=5)


class CurationResult(BaseModel):
    places: list[CuratedPlace] = Field(default_factory=list)
    rejected_reason: str = Field(
        default="", description="Why strong candidates were dropped, if any were."
    )


class PlaceStory(BaseModel):
    place_id: str
    story: str


class StoryBatch(BaseModel):
    stories: list[PlaceStory] = Field(default_factory=list)


class ItineraryResult(BaseModel):
    days: list[ItineraryDay] = Field(default_factory=list)


class LocalPhrase(BaseModel):
    phrase: str
    meaning: str


class FrontMatter(BaseModel):
    headline: str = ""
    overview: str = ""
    best_time_to_visit: str = ""
    local_food: list[str] = Field(default_factory=list, max_length=8)
    packing_list: list[str] = Field(default_factory=list, max_length=12)
    safety_tips: list[str] = Field(default_factory=list, max_length=6)
    local_phrases: list[LocalPhrase] = Field(default_factory=list, max_length=6)


class TransportNarrative(BaseModel):
    recommended_option: str = ""
    recommendation_reason: str = ""
    local_transport_advice: str = ""
