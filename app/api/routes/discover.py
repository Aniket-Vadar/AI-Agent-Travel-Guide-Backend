"""Fast, LLM-free lookups: what is here, and how do I get there.

These endpoints answer in well under a second because they touch only Wikipedia,
Nominatim, OSRM and Overpass. They power the search box and the route widget,
where a traveller wants an answer now, not a thirty-second agent run.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from app.core.errors import NotFoundError
from app.schemas.travel import (
    Destination,
    GeoPoint,
    Place,
    PlaceCategory,
    PlaceSearchRequest,
    RouteRequest,
    TransportPlan,
)
from app.services import wikipedia
from app.services.geo import geocode
from app.services.routing import gather_transport_context, tourism_pois

router = APIRouter(tags=["discovery"])

# OSM tags mapped onto our own categories, so the cards look right without an LLM.
OSM_CATEGORY = {
    "museum": PlaceCategory.museum,
    "viewpoint": PlaceCategory.viewpoint,
    "monument": PlaceCategory.historical,
    "memorial": PlaceCategory.historical,
    "castle": PlaceCategory.historical,
    "fort": PlaceCategory.historical,
    "ruins": PlaceCategory.historical,
    "archaeological_site": PlaceCategory.historical,
    "park": PlaceCategory.nature,
    "garden": PlaceCategory.nature,
    "beach_resort": PlaceCategory.beach,
    "zoo": PlaceCategory.nature,
    "theme_park": PlaceCategory.adventure,
    "attraction": PlaceCategory.other,
    "artwork": PlaceCategory.architecture,
}


@router.get("/destinations/search", response_model=Destination, summary="Resolve a destination")
async def search_destination(
    city: str = Query(..., min_length=1),
    country: str = Query(default=""),
) -> Destination:
    destination = await geocode(city, country)
    if destination is None:
        raise NotFoundError(f"Could not find '{city}'. Check the spelling or try a nearby city.")
    overview = await wikipedia.city_overview(destination.name, destination.country)
    if overview:
        destination.summary = overview.get("summary", "")
        destination.wikipedia_url = overview.get("url")
        destination.image_url = overview.get("image")
    return destination


@router.post("/places/search", response_model=list[Place], summary="Attractions near a city")
async def search_places(request: PlaceSearchRequest) -> list[Place]:
    destination = await geocode(request.city, request.country)
    if destination is None or destination.coordinates is None:
        raise NotFoundError(f"Could not find '{request.city}'.")
    centre = destination.coordinates

    geo_hits, pois = await asyncio.gather(
        wikipedia.geosearch(centre, radius_m=10_000, limit=40),
        tourism_pois(centre, radius_m=12_000, limit=30),
        return_exceptions=True,
    )
    geo_hits = [] if isinstance(geo_hits, BaseException) else geo_hits
    pois = [] if isinstance(pois, BaseException) else pois

    kind_by_name = {p["name"].lower(): p.get("osm_kind", "") for p in pois}
    # The article about the city itself is not an attraction in the city.
    city_names = {destination.name.lower(), request.city.strip().lower()}
    geo_hits = [h for h in geo_hits if h["title"].lower() not in city_names]
    titles = [hit["title"] for hit in geo_hits[: request.limit]]
    enriched = await wikipedia.enrich_titles(titles)

    places: list[Place] = []
    for index, hit in enumerate(geo_hits[: request.limit], start=1):
        data = enriched.get(hit["title"], {})
        kind = kind_by_name.get(hit["title"].lower(), "")
        places.append(
            Place(
                id=f"p{index}",
                name=hit["title"],
                category=OSM_CATEGORY.get(kind, PlaceCategory.other),
                summary=data.get("summary", ""),
                tagline=data.get("description", ""),
                coordinates=GeoPoint(lat=hit["lat"], lon=hit["lon"]),
                distance_from_centre_km=round(hit.get("distance_m", 0) / 1000, 2),
                wikipedia_url=data.get("url"),
                image_url=data.get("image"),
                sources=["wikipedia"],
            )
        )

    # Described places first; an entry with no article is a weak recommendation.
    places.sort(key=lambda p: (0 if p.summary else 1, p.distance_from_centre_km or 0))
    return places


@router.post("/routes", response_model=TransportPlan, summary="Routes between two places")
async def compute_routes(request: RouteRequest) -> TransportPlan:
    origin, destination = await asyncio.gather(
        geocode(request.origin), geocode(request.destination)
    )
    if origin is None or origin.coordinates is None:
        raise NotFoundError(f"Could not find the origin '{request.origin}'.")
    if destination is None or destination.coordinates is None:
        raise NotFoundError(f"Could not find the destination '{request.destination}'.")

    options, hubs = await gather_transport_context(
        origin.coordinates, destination.coordinates, "INR", list(request.modes)
    )
    cheapest = min(
        (o for o in options if o.estimated_cost is not None),
        key=lambda o: o.estimated_cost or 0,
        default=None,
    )
    return TransportPlan(
        origin=origin.name,
        destination=destination.name,
        options=options,
        hubs=hubs,
        recommended_option=cheapest.label if cheapest else "",
    )
