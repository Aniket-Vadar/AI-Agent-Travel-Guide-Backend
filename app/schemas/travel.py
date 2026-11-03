"""Request/response models for destination research, planning and guides."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TravelStyle(str, Enum):
    budget = "budget"
    comfort = "comfort"
    luxury = "luxury"
    backpacking = "backpacking"
    family = "family"
    solo = "solo"
    romantic = "romantic"


class Pace(str, Enum):
    relaxed = "relaxed"
    balanced = "balanced"
    packed = "packed"


class PlaceCategory(str, Enum):
    historical = "historical"
    religious = "religious"
    nature = "nature"
    beach = "beach"
    museum = "museum"
    viewpoint = "viewpoint"
    market = "market"
    food = "food"
    adventure = "adventure"
    architecture = "architecture"
    neighbourhood = "neighbourhood"
    other = "other"


class GeoPoint(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class Destination(BaseModel):
    """A resolved place on the map, enriched from Wikipedia."""

    name: str
    display_name: str = ""
    country: str = ""
    country_code: str = ""
    region: str = ""
    coordinates: GeoPoint | None = None
    timezone: str | None = None
    summary: str = ""
    wikipedia_url: str | None = None
    image_url: str | None = None
    population: int | None = None


class Place(BaseModel):
    """A single attraction inside a destination."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    category: PlaceCategory = PlaceCategory.other
    tagline: str = ""
    summary: str = ""
    story: str = ""
    why_visit: str = ""
    best_time: str = ""
    suggested_duration_minutes: int = 90
    entry_fee_note: str = ""
    coordinates: GeoPoint | None = None
    distance_from_centre_km: float | None = None
    wikipedia_url: str | None = None
    image_url: str | None = None
    tips: list[str] = Field(default_factory=list)
    rating_hint: float | None = Field(default=None, ge=0, le=5)
    sources: list[str] = Field(default_factory=list)


class RouteStep(BaseModel):
    instruction: str
    distance_km: float = 0.0
    duration_minutes: float = 0.0
    mode: str = "drive"


class RouteOption(BaseModel):
    """One way of getting from A to B."""

    mode: Literal["drive", "bus", "train", "flight", "walk", "cycle", "mixed"] = "drive"
    label: str = ""
    summary: str = ""
    distance_km: float = 0.0
    duration_minutes: float = 0.0
    estimated_cost: float | None = None
    currency: str = "INR"
    steps: list[RouteStep] = Field(default_factory=list)
    geometry: list[list[float]] = Field(
        default_factory=list, description="[[lat, lon], ...] polyline for the map."
    )
    operators: list[str] = Field(default_factory=list)
    comfort_note: str = ""
    booking_available: bool = False


class TransportHub(BaseModel):
    name: str
    kind: Literal["bus_station", "bus_stop", "railway_station", "airport", "ferry"] = "bus_station"
    coordinates: GeoPoint | None = None
    distance_km: float | None = None
    operator: str = ""


class TransportPlan(BaseModel):
    origin: str = ""
    destination: str = ""
    options: list[RouteOption] = Field(default_factory=list)
    hubs: list[TransportHub] = Field(default_factory=list)
    local_transport_advice: str = ""
    recommended_option: str = ""


class ItineraryItem(BaseModel):
    time: str = ""
    title: str
    place_id: str | None = None
    description: str = ""
    duration_minutes: int = 90
    travel_hint: str = ""
    cost_estimate: float | None = None


class ItineraryDay(BaseModel):
    day: int
    title: str = ""
    theme: str = ""
    items: list[ItineraryItem] = Field(default_factory=list)
    evening_note: str = ""
    estimated_cost: float | None = None


class BudgetBreakdown(BaseModel):
    currency: str = "INR"
    stay_per_night: float | None = None
    food_per_day: float | None = None
    local_transport_per_day: float | None = None
    attractions_total: float | None = None
    intercity_travel: float | None = None
    total_estimate: float | None = None
    notes: str = ""


class TravelRequest(BaseModel):
    """What the traveller asked for."""

    country: str = Field(..., min_length=2, max_length=80)
    city: str = Field(..., min_length=1, max_length=120)
    origin_city: str | None = Field(
        default=None, description="Where the trip starts, for intercity routing."
    )
    days: int = Field(default=3, ge=1, le=21)
    start_date: date | None = None
    travellers: int = Field(default=2, ge=1, le=20)
    travel_style: TravelStyle = TravelStyle.comfort
    pace: Pace = Pace.balanced
    interests: list[str] = Field(default_factory=list, max_length=12)
    budget_total: float | None = Field(default=None, ge=0)
    currency: str = "INR"
    language: str = "en"
    notes: str = ""

    @field_validator("country", "city")
    @classmethod
    def _clean(cls, v: str) -> str:
        return " ".join(v.strip().split())

    @field_validator("interests")
    @classmethod
    def _clean_interests(cls, v: list[str]) -> list[str]:
        return [i.strip().lower() for i in v if i and i.strip()][:12]


class TravelGuide(BaseModel):
    """The complete artefact the planner produces."""

    plan_id: str
    request: TravelRequest
    destination: Destination
    headline: str = ""
    overview: str = ""
    places: list[Place] = Field(default_factory=list)
    itinerary: list[ItineraryDay] = Field(default_factory=list)
    transport: TransportPlan = Field(default_factory=TransportPlan)
    budget: BudgetBreakdown = Field(default_factory=BudgetBreakdown)
    best_time_to_visit: str = ""
    local_food: list[str] = Field(default_factory=list)
    packing_list: list[str] = Field(default_factory=list)
    safety_tips: list[str] = Field(default_factory=list)
    local_phrases: list[dict[str, str]] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: str = ""
    trace: dict[str, Any] = Field(default_factory=dict)


class PlanSummary(BaseModel):
    plan_id: str
    city: str
    country: str
    days: int
    headline: str
    place_count: int
    created_at: str


class RouteRequest(BaseModel):
    origin: str = Field(..., min_length=2)
    destination: str = Field(..., min_length=2)
    modes: list[Literal["drive", "bus", "train", "flight"]] = Field(
        default_factory=lambda: ["drive", "bus"]
    )
    travellers: int = Field(default=1, ge=1, le=20)


class PlaceSearchRequest(BaseModel):
    city: str
    country: str = ""
    limit: int = Field(default=12, ge=1, le=40)
    interests: list[str] = Field(default_factory=list)
