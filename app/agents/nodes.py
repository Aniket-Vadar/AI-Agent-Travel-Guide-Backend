"""The agent team.

Each function below is one node of the planning graph:

    researcher  -> finds the destination and every candidate place (Wikipedia + OSM)
    curator     -> picks and ranks the places worth a traveller's time
    storyteller -> writes the narrative for each place
    navigator   -> works out how to get there and how to move around
    editor      -> writes the front matter: overview, food, packing, safety
    planner     -> lays the places out across the days
    assembler   -> computes the budget and returns the finished guide

The researcher, navigator and assembler are deterministic; only the middle four
call Gemini. That split matters: facts and geometry come from data, and the model
is used for judgement and prose, which is what it is actually good at.
"""

from __future__ import annotations

import asyncio
import re
from math import ceil
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents import prompts
from app.agents.llm import ainvoke_structured
from app.agents.state import (
    CurationResult,
    FrontMatter,
    ItineraryResult,
    PlannerState,
    StoryBatch,
    TransportNarrative,
)
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.schemas.travel import (
    BudgetBreakdown,
    Destination,
    GeoPoint,
    ItineraryDay,
    ItineraryItem,
    Pace,
    Place,
    PlaceCategory,
    TransportPlan,
    TravelRequest,
    TravelStyle,
)
from app.services import wikipedia
from app.services.geo import geocode, haversine_km
from app.services.routing import gather_transport_context, human_duration, tourism_pois

log = get_logger("agents")

MAX_CANDIDATES = 45
ENRICH_LIMIT = 26
STORY_BATCH_SIZE = 3

PLACES_PER_PACE = {Pace.relaxed: 2.5, Pace.balanced: 3.5, Pace.packed: 5.0}

# Per-day costs by style, in the traveller's currency, used for the estimate panel.
STYLE_COSTS = {
    TravelStyle.backpacking: {"stay": 900, "food": 600, "local": 250},
    TravelStyle.budget: {"stay": 1800, "food": 900, "local": 400},
    TravelStyle.solo: {"stay": 2200, "food": 1100, "local": 450},
    TravelStyle.family: {"stay": 4500, "food": 2200, "local": 900},
    TravelStyle.comfort: {"stay": 4000, "food": 1800, "local": 700},
    TravelStyle.romantic: {"stay": 6000, "food": 2600, "local": 900},
    TravelStyle.luxury: {"stay": 14000, "food": 5000, "local": 2000},
}


def _normalise(name: str) -> str:
    """Key used to detect that Wikipedia and OSM are describing the same place."""
    cleaned = re.sub(r"\(.*?\)", "", name.lower())
    cleaned = re.sub(r"\b(the|of|at|in|a|an|shri|sri|saint|st)\b", " ", cleaned)
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# 1. Researcher - deterministic, no LLM
# ---------------------------------------------------------------------------


async def researcher_node(state: PlannerState) -> dict[str, Any]:
    """Resolve the destination, then gather every candidate place around it."""
    request: TravelRequest = state["request"]
    warnings: list[str] = []

    destination = await geocode(request.city, request.country)
    if destination is None:
        destination = await geocode(request.city)
    if destination is None or destination.coordinates is None:
        raise NotFoundError(
            f"Could not find '{request.city}, {request.country}' on the map. "
            "Check the spelling, or try a larger nearby city."
        )
    centre = destination.coordinates

    overview_task = wikipedia.city_overview(destination.name, destination.country)
    geo_task = wikipedia.geosearch(centre, radius_m=10_000, limit=50)
    poi_task = tourism_pois(centre, radius_m=12_000, limit=30)
    origin_task = (
        geocode(request.origin_city, request.country) if request.origin_city else _none()
    )

    overview, geo_hits, pois, origin = await asyncio.gather(
        overview_task, geo_task, poi_task, origin_task, return_exceptions=True
    )

    if isinstance(overview, BaseException):
        log.warning("city_overview_failed", error=str(overview))
        overview = None
    if isinstance(geo_hits, BaseException):
        log.warning("geosearch_failed", error=str(geo_hits))
        geo_hits = []
    if isinstance(pois, BaseException):
        log.warning("poi_lookup_failed", error=str(pois))
        pois = []
    if isinstance(origin, BaseException):
        origin = None

    if overview:
        destination.summary = overview.get("summary", "")
        destination.wikipedia_url = overview.get("url")
        destination.image_url = overview.get("image")
    else:
        warnings.append(
            f"Wikipedia has no clear article for {destination.name}; the overview leans on "
            "map data instead."
        )

    city_names = {destination.name.lower(), request.city.lower()}
    geo_hits = [h for h in (geo_hits or []) if h["title"].lower() not in city_names]
    candidates = _merge_candidates(geo_hits, pois or [], centre)
    if not candidates:
        warnings.append(
            "No well-documented attractions were found near this point. Try a larger city "
            "or a nearby district."
        )

    enriched = await wikipedia.enrich_titles(
        [c["wiki_title"] for c in candidates[:ENRICH_LIMIT] if c.get("wiki_title")]
    )
    for candidate in candidates:
        data = enriched.get(candidate.get("wiki_title") or "")
        if not data:
            continue
        candidate["summary"] = data.get("summary", "")
        candidate["image_url"] = data.get("image")
        candidate["wikipedia_url"] = data.get("url")
        candidate["description"] = data.get("description", "")

    # A place we could not describe at all is a weak recommendation; rank it last.
    candidates.sort(key=lambda c: (0 if c.get("summary") else 1, c["distance_km"]))
    candidates = candidates[:MAX_CANDIDATES]

    sources = [destination.wikipedia_url] if destination.wikipedia_url else []
    sources += [c["wikipedia_url"] for c in candidates if c.get("wikipedia_url")][:20]

    log.info(
        "research_complete",
        city=destination.name,
        candidates=len(candidates),
        described=sum(1 for c in candidates if c.get("summary")),
    )
    return {
        "destination": destination,
        "origin": origin,
        "candidates": candidates,
        "research": {
            "centre": {"lat": centre.lat, "lon": centre.lon},
            "candidate_count": len(candidates),
            "city_summary": destination.summary,
        },
        "sources": sources,
        "warnings": warnings,
    }


async def _none() -> None:
    return None


def _merge_candidates(
    geo_hits: list[dict], pois: list[dict], centre: GeoPoint
) -> list[dict[str, Any]]:
    """Fuse Wikipedia geosearch with OSM POIs, deduplicating by normalised name."""
    merged: dict[str, dict[str, Any]] = {}

    for hit in geo_hits:
        key = _normalise(hit["title"])
        if not key:
            continue
        merged[key] = {
            "name": hit["title"],
            "wiki_title": hit["title"],
            "lat": hit["lat"],
            "lon": hit["lon"],
            "distance_km": round(hit.get("distance_m", 0) / 1000, 2),
            "sources": ["wikipedia"],
            "osm_kind": "",
        }

    for poi in pois:
        key = _normalise(poi["name"])
        if not key:
            continue
        if key in merged:
            entry = merged[key]
            entry["osm_kind"] = entry["osm_kind"] or poi.get("osm_kind", "")
            if "openstreetmap" not in entry["sources"]:
                entry["sources"].append("openstreetmap")
            continue
        wiki_title = ""
        tag = poi.get("wikipedia", "")
        if ":" in tag:  # OSM stores "en:Article Title"
            wiki_title = tag.split(":", 1)[1]
        merged[key] = {
            "name": poi["name"],
            "wiki_title": wiki_title,
            "lat": poi["lat"],
            "lon": poi["lon"],
            "distance_km": round(poi.get("distance_km", 0), 2),
            "sources": ["openstreetmap"],
            "osm_kind": poi.get("osm_kind", ""),
        }

    out: list[dict[str, Any]] = []
    for index, entry in enumerate(merged.values(), start=1):
        entry["id"] = f"c{index}"
        point = GeoPoint(lat=entry["lat"], lon=entry["lon"])
        entry["distance_km"] = entry["distance_km"] or haversine_km(centre, point)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# 2. Curator
# ---------------------------------------------------------------------------


def _target_place_count(request: TravelRequest) -> int:
    per_day = PLACES_PER_PACE.get(request.pace, 3.5)
    return max(5, min(18, round(request.days * per_day) + 1))


async def curator_node(state: PlannerState) -> dict[str, Any]:
    request: TravelRequest = state["request"]
    destination: Destination = state["destination"]
    candidates: list[dict] = state.get("candidates", [])
    if not candidates:
        return {"places": [], "warnings": ["No candidate places were available to curate."]}

    wanted = _target_place_count(request)
    lines = []
    for c in candidates:
        bits = [f"[{c['id']}] {c['name']}", f"{c['distance_km']} km from centre"]
        if c.get("osm_kind"):
            bits.append(f"osm:{c['osm_kind']}")
        if c.get("description"):
            bits.append(c["description"])
        line = " | ".join(bits)
        if c.get("summary"):
            line += f"\n    {_truncate(c['summary'], 320)}"
        lines.append(line)

    interests = ", ".join(request.interests) if request.interests else "no stated preference"
    context = (
        f"DESTINATION: {destination.name}, {destination.country}\n"
        f"ABOUT: {_truncate(destination.summary, 700) or 'no article available'}\n"
        f"TRAVELLER: {request.travellers} person(s), {request.days} days, "
        f"{request.travel_style.value} style, {request.pace.value} pace\n"
        f"INTERESTS: {interests}\n"
        f"NOTES: {request.notes or 'none'}\n\n"
        f"SELECT THE BEST {wanted} PLACES FROM THESE CANDIDATES:\n" + "\n".join(lines)
    )

    result = await ainvoke_structured(
        CurationResult,
        [SystemMessage(content=prompts.CURATOR_AGENT), HumanMessage(content=context)],
        config=_child_config(state, "curator"),
        fallback=CurationResult(places=[]),
    )

    by_id = {c["id"]: c for c in candidates}
    by_name = {_normalise(c["name"]): c for c in candidates}
    places: list[Place] = []
    seen: set[str] = set()

    for curated in result.places:
        source = by_id.get(curated.candidate_id) or by_name.get(_normalise(curated.name))
        if source is None or source["id"] in seen:
            continue  # the model referenced something that is not in the candidate list
        seen.add(source["id"])
        places.append(
            Place(
                id=source["id"],
                name=source["name"],
                category=curated.category,
                tagline=curated.tagline,
                summary=_truncate(source.get("summary", ""), 600),
                why_visit=curated.why_visit,
                best_time=curated.best_time,
                suggested_duration_minutes=max(20, min(300, curated.suggested_duration_minutes)),
                entry_fee_note=curated.entry_fee_note,
                coordinates=GeoPoint(lat=source["lat"], lon=source["lon"]),
                distance_from_centre_km=source["distance_km"],
                wikipedia_url=source.get("wikipedia_url"),
                image_url=source.get("image_url"),
                tips=curated.tips[:4],
                rating_hint=curated.rating_hint,
                sources=source.get("sources", []),
            )
        )

    warnings: list[str] = []
    if not places:
        # Degrade to the raw research rather than returning an empty guide.
        places = _fallback_places(candidates[:wanted])
        warnings.append("The curator could not rank these places, so they are listed by proximity.")

    log.info("curation_complete", selected=len(places), requested=wanted)
    return {"places": places, "warnings": warnings}


def _fallback_places(candidates: list[dict]) -> list[Place]:
    return [
        Place(
            id=c["id"],
            name=c["name"],
            summary=_truncate(c.get("summary", ""), 600),
            category=PlaceCategory.other,
            coordinates=GeoPoint(lat=c["lat"], lon=c["lon"]),
            distance_from_centre_km=c["distance_km"],
            wikipedia_url=c.get("wikipedia_url"),
            image_url=c.get("image_url"),
            sources=c.get("sources", []),
        )
        for c in candidates
    ]


# ---------------------------------------------------------------------------
# 3. Storyteller
# ---------------------------------------------------------------------------


async def storyteller_node(state: PlannerState) -> dict[str, Any]:
    """Write a story per place, in small parallel batches.

    Batching keeps each generation short enough to stay vivid; running the batches
    concurrently keeps a 12-place guide inside a few seconds.
    """
    places: list[Place] = state.get("places", [])
    if not places:
        return {}
    destination: Destination = state["destination"]

    batches = [places[i : i + STORY_BATCH_SIZE] for i in range(0, len(places), STORY_BATCH_SIZE)]

    async def write(batch: list[Place]) -> StoryBatch:
        body = "\n\n".join(
            f"[{p.id}] {p.name} ({p.category.value})\n"
            f"Known facts: {_truncate(p.summary, 700) or 'none available'}\n"
            f"Why it was chosen: {p.why_visit or 'unstated'}"
            for p in batch
        )
        context = (
            f"CITY: {destination.name}, {destination.country}\n"
            f"CITY CONTEXT: {_truncate(destination.summary, 500)}\n\n"
            f"WRITE ONE STORY FOR EACH PLACE BELOW.\n\n{body}"
        )
        return await ainvoke_structured(
            StoryBatch,
            [SystemMessage(content=prompts.STORYTELLER_AGENT), HumanMessage(content=context)],
            pro=False,
            temperature=0.85,
            config=_child_config(state, "storyteller"),
            fallback=StoryBatch(stories=[]),
        )

    results = await asyncio.gather(*(write(b) for b in batches), return_exceptions=True)

    stories: dict[str, str] = {}
    for result in results:
        if isinstance(result, BaseException):
            log.warning("story_batch_failed", error=str(result))
            continue
        for story in result.stories:
            if story.story.strip():
                stories[story.place_id] = story.story.strip()

    updated = [p.model_copy(update={"story": stories.get(p.id, "")}) for p in places]
    missing = [p.name for p in updated if not p.story]
    warnings = (
        [f"Stories are missing for: {', '.join(missing[:3])}."] if len(missing) > len(places) // 2 else []
    )
    log.info("stories_complete", written=len(stories), total=len(places))
    return {"places": updated, "warnings": warnings}


# ---------------------------------------------------------------------------
# 4. Navigator
# ---------------------------------------------------------------------------


async def navigator_node(state: PlannerState) -> dict[str, Any]:
    request: TravelRequest = state["request"]
    destination: Destination = state["destination"]
    origin: Destination | None = state.get("origin")

    modes = ["drive", "bus", "train", "flight"]
    options, hubs = await gather_transport_context(
        origin.coordinates if origin and origin.coordinates else None,
        destination.coordinates,  # type: ignore[arg-type]
        request.currency,
        modes,
    )

    plan = TransportPlan(
        origin=origin.name if origin else (request.origin_city or ""),
        destination=destination.name,
        options=options,
        hubs=hubs,
    )

    option_lines = "\n".join(
        f"- {o.label}: {o.distance_km} km, {human_duration(o.duration_minutes)}, "
        f"about {o.estimated_cost} {o.currency}. {o.comfort_note}"
        for o in options
    ) or "- No intercity route was computed (no origin city given)."
    hub_lines = "\n".join(
        f"- {h.name} ({h.kind.replace('_', ' ')}), {h.distance_km} km from the centre"
        for h in hubs[:8]
    ) or "- No transport hubs were found in the map data."

    context = (
        f"TRAVELLER: {request.travellers} person(s), {request.travel_style.value} style, "
        f"budget {request.budget_total or 'unstated'} {request.currency}\n"
        f"FROM: {plan.origin or 'not specified'}\nTO: {destination.name}, {destination.country}\n\n"
        f"ROUTE OPTIONS:\n{option_lines}\n\nNEARBY HUBS:\n{hub_lines}\n\n"
        f"CITY CONTEXT: {_truncate(destination.summary, 400)}"
    )

    narrative = await ainvoke_structured(
        TransportNarrative,
        [SystemMessage(content=prompts.TRANSPORT_AGENT), HumanMessage(content=context)],
        config=_child_config(state, "navigator"),
        fallback=TransportNarrative(
            recommended_option=options[0].label if options else "",
            local_transport_advice="",
        ),
    )

    plan.recommended_option = narrative.recommended_option or (
        options[0].label if options else ""
    )
    plan.local_transport_advice = narrative.local_transport_advice
    if narrative.recommendation_reason:
        for option in plan.options:
            if option.label == plan.recommended_option:
                option.summary = f"{option.summary} {narrative.recommendation_reason}".strip()

    log.info("transport_complete", options=len(options), hubs=len(hubs))
    return {"transport": plan}


# ---------------------------------------------------------------------------
# 5. Editor - front matter
# ---------------------------------------------------------------------------


async def editor_node(state: PlannerState) -> dict[str, Any]:
    request: TravelRequest = state["request"]
    destination: Destination = state["destination"]
    places: list[Place] = state.get("places", [])

    context = (
        f"DESTINATION: {destination.name}, {destination.region}, {destination.country}\n"
        f"ABOUT: {_truncate(destination.summary, 900) or 'no article available'}\n"
        f"TRAVELLER: {request.travellers} person(s), {request.days} days, "
        f"{request.travel_style.value} style, month "
        f"{request.start_date.strftime('%B') if request.start_date else 'unspecified'}\n"
        f"INTERESTS: {', '.join(request.interests) or 'none stated'}\n"
        f"PLACES IN THE GUIDE: {', '.join(p.name for p in places[:14]) or 'none'}\n"
    )

    front = await ainvoke_structured(
        FrontMatter,
        [SystemMessage(content=prompts.GUIDE_AGENT), HumanMessage(content=context)],
        config=_child_config(state, "editor"),
        fallback=FrontMatter(
            headline=f"{destination.name}, {destination.country}",
            overview=_truncate(destination.summary, 600),
        ),
    )
    return {
        "front_matter": {
            "headline": front.headline or f"{destination.name}, {destination.country}",
            "overview": front.overview or _truncate(destination.summary, 600),
            "best_time_to_visit": front.best_time_to_visit,
            "local_food": front.local_food,
            "packing_list": front.packing_list,
            "safety_tips": front.safety_tips,
            "local_phrases": [
                {"phrase": p.phrase, "meaning": p.meaning} for p in front.local_phrases
            ],
        }
    }


# ---------------------------------------------------------------------------
# 6. Planner - day by day
# ---------------------------------------------------------------------------


async def planner_node(state: PlannerState) -> dict[str, Any]:
    request: TravelRequest = state["request"]
    destination: Destination = state["destination"]
    places: list[Place] = state.get("places", [])
    if not places:
        return {"itinerary": []}

    food = ", ".join(state.get("front_matter", {}).get("local_food", [])[:6]) or "local dishes"
    place_lines = "\n".join(
        f"[{p.id}] {p.name} ({p.category.value}) - {p.suggested_duration_minutes} min, "
        f"{p.distance_from_centre_km} km from centre, best {p.best_time or 'any time'}. "
        f"{_truncate(p.why_visit, 120)}"
        for p in places
    )
    arrival = state.get("transport", TransportPlan()).recommended_option or "your own arrangement"

    context = (
        f"CITY: {destination.name}, {destination.country}\n"
        f"DAYS: {request.days}\nPACE: {request.pace.value}\n"
        f"TRAVELLERS: {request.travellers}\nSTYLE: {request.travel_style.value}\n"
        f"START DATE: {request.start_date or 'unspecified'}\n"
        f"ARRIVING: {arrival}\n"
        f"LOCAL DISHES TO WORK IN: {food}\n\n"
        f"PLACES AVAILABLE:\n{place_lines}"
    )

    result = await ainvoke_structured(
        ItineraryResult,
        [SystemMessage(content=prompts.ITINERARY_AGENT), HumanMessage(content=context)],
        config=_child_config(state, "planner"),
        fallback=ItineraryResult(days=[]),
    )

    valid_ids = {p.id for p in places}
    days: list[ItineraryDay] = []
    for index, day in enumerate(result.days[: request.days], start=1):
        items = [
            item.model_copy(update={"place_id": item.place_id if item.place_id in valid_ids else None})
            for item in day.items
        ]
        days.append(day.model_copy(update={"day": index, "items": items}))

    if not days:
        days = _fallback_itinerary(places, request)
        log.info("itinerary_fallback_used")

    return {"itinerary": days}


def _fallback_itinerary(places: list[Place], request: TravelRequest) -> list[ItineraryDay]:
    """Deterministic day split, used when the planning model returns nothing.

    The places are spread across every requested day rather than packed into the
    first: with four places over two days that means two and two, not four and an
    empty afternoon. The pace still caps how much a single day can hold.
    """
    pace_cap = max(1, round(PLACES_PER_PACE.get(request.pace, 3.5)))
    even_split = ceil(len(places) / request.days) if places else 1
    per_day = max(1, min(pace_cap, even_split))
    days: list[ItineraryDay] = []
    clock = ["09:30", "11:30", "14:00", "16:00", "18:00"]
    for day_number in range(1, request.days + 1):
        chunk = places[(day_number - 1) * per_day : day_number * per_day]
        if not chunk:
            break
        days.append(
            ItineraryDay(
                day=day_number,
                title=f"Day {day_number} in and around the centre",
                theme="Sights closest together, in one loop",
                items=[
                    ItineraryItem(
                        time=clock[i % len(clock)],
                        title=place.name,
                        place_id=place.id,
                        description=place.why_visit or place.tagline,
                        duration_minutes=place.suggested_duration_minutes,
                    )
                    for i, place in enumerate(chunk)
                ],
            )
        )
    return days


# ---------------------------------------------------------------------------
# 7. Assembler - deterministic budget
# ---------------------------------------------------------------------------


def build_budget(request: TravelRequest, transport: TransportPlan, places: int) -> BudgetBreakdown:
    """A transparent arithmetic estimate, not a model guess."""
    costs = STYLE_COSTS.get(request.travel_style, STYLE_COSTS[TravelStyle.comfort])
    nights = max(1, request.days - 1)
    rooms = max(1, (request.travellers + 1) // 2)

    stay = costs["stay"] * nights * rooms
    food = costs["food"] * request.days * request.travellers
    local = costs["local"] * request.days * max(1, request.travellers // 2 or 1)
    attractions = places * 300 * request.travellers

    intercity = 0.0
    recommended = next(
        (o for o in transport.options if o.label == transport.recommended_option),
        transport.options[0] if transport.options else None,
    )
    if recommended and recommended.estimated_cost:
        # Both directions, per traveller for public transport, per vehicle for driving.
        multiplier = 1 if recommended.mode == "drive" else request.travellers
        intercity = recommended.estimated_cost * 2 * multiplier

    total = stay + food + local + attractions + intercity
    note = (
        f"Arithmetic estimate for {request.travellers} traveller(s) at a "
        f"{request.travel_style.value} standard, excluding shopping and visas."
    )
    if request.budget_total and total > request.budget_total:
        over = round(total - request.budget_total)
        note += (
            f" This runs about {over} {request.currency} over your stated budget - "
            "dropping to shared rooms or the overnight bus closes most of that gap."
        )

    return BudgetBreakdown(
        currency=request.currency,
        stay_per_night=float(costs["stay"] * rooms),
        food_per_day=float(costs["food"] * request.travellers),
        local_transport_per_day=float(costs["local"]),
        attractions_total=float(attractions),
        intercity_travel=float(round(intercity)),
        total_estimate=float(round(total)),
        notes=note,
    )


def _child_config(state: PlannerState, node: str) -> dict[str, Any]:
    """Nest each agent's LangSmith run under the parent graph run."""
    base = dict(state.get("run_config") or {})
    base["run_name"] = f"agent:{node}"
    base["tags"] = [*base.get("tags", []), node]
    return base
