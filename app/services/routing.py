"""Road routing (OSRM) and public-transport infrastructure (OpenStreetMap Overpass).

`road_route` gives the drivable line between two points with turn-by-turn steps.
Long-distance bus and train options are *derived*: OSM knows where the stations
are, and the realistic duration of an intercity coach is estimated from the road
route rather than invented by the language model. That keeps the numbers
defensible even though no commercial timetable API is wired in.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.travel import GeoPoint, RouteOption, RouteStep, TransportHub
from app.services.cache import cached
from app.services.geo import haversine_km
from app.services.geo import search_nearby as geo_search_nearby
from app.services.http import post_json, safe_get_json

log = get_logger("routing")

# Rough intercity economics, used only for figures we explicitly label as estimates.
BUS_AVG_SPEED_KMPH = 45.0
TRAIN_AVG_SPEED_KMPH = 60.0
BUS_FARE_PER_KM = 1.6
TRAIN_FARE_PER_KM = 0.9
FLIGHT_BASE_FARE = 2800.0
FLIGHT_FARE_PER_KM = 4.2
CAR_COST_PER_KM = 11.0
OVERPASS_TIMEOUT_SECONDS = 70.0
# How long a request will wait for map furniture before moving on without it.
HUB_BUDGET_SECONDS = 10.0


def _decode_polyline(encoded: str, precision: int = 5) -> list[list[float]]:
    """Decode an encoded polyline into [[lat, lon], ...]."""
    coords: list[list[float]] = []
    index = lat = lng = 0
    factor = 10**precision
    while index < len(encoded):
        for is_lat in (True, False):
            result, shift = 0, 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        coords.append([round(lat / factor, 6), round(lng / factor, 6)])
    return coords


def human_duration(minutes: float) -> str:
    minutes = int(minutes)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


async def road_route(origin: GeoPoint, destination: GeoPoint) -> RouteOption | None:
    """Drivable route with steps and geometry, via the public OSRM server."""

    async def _fetch() -> dict | None:
        url = (
            f"{settings.osrm_url}/route/v1/driving/"
            f"{origin.lon},{origin.lat};{destination.lon},{destination.lat}"
        )
        return await safe_get_json(
            url,
            provider="osrm",
            params={"overview": "simplified", "steps": "true", "alternatives": "false"},
            default=None,
        )

    key = {
        "o": [round(origin.lat, 4), round(origin.lon, 4)],
        "d": [round(destination.lat, 4), round(destination.lon, 4)],
    }
    data = await cached("osrm", key, _fetch, ttl=6 * 3600)
    routes = (data or {}).get("routes") or []
    if not routes:
        return None

    route = routes[0]
    distance_km = round(route["distance"] / 1000, 1)
    duration_min = round(route["duration"] / 60)

    steps: list[RouteStep] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", [])[:60]:
            manoeuvre = step.get("maneuver", {}) or {}
            road = step.get("name") or "the road"
            kind = str(manoeuvre.get("type", "continue")).replace("_", " ").title()
            instruction = f"{kind} onto {road}"
            if manoeuvre.get("modifier"):
                instruction = f"{instruction} ({manoeuvre['modifier']})"
            steps.append(
                RouteStep(
                    instruction=instruction,
                    distance_km=round(step.get("distance", 0) / 1000, 2),
                    duration_minutes=round(step.get("duration", 0) / 60, 1),
                    mode="drive",
                )
            )

    geometry = _decode_polyline(route["geometry"]) if isinstance(route.get("geometry"), str) else []
    return RouteOption(
        mode="drive",
        label="By road (car / taxi)",
        summary=f"About {distance_km} km of driving, roughly {human_duration(duration_min)}.",
        distance_km=distance_km,
        duration_minutes=duration_min,
        estimated_cost=round(distance_km * CAR_COST_PER_KM),
        currency=settings.booking_currency,
        steps=steps,
        geometry=geometry,
        comfort_note="The most flexible option - you can stop wherever the view is good.",
    )


def derive_bus_option(distance_km: float, currency: str) -> RouteOption:
    """Estimate an intercity coach journey from the road distance."""
    duration = (distance_km / BUS_AVG_SPEED_KMPH) * 60 + 25  # boarding plus halts
    return RouteOption(
        mode="bus",
        label="By bus (intercity coach)",
        summary=(
            f"State and private operators cover the {round(distance_km)} km in about "
            f"{human_duration(duration)}, including halts."
        ),
        distance_km=round(distance_km, 1),
        duration_minutes=round(duration),
        estimated_cost=round(distance_km * BUS_FARE_PER_KM),
        currency=currency,
        comfort_note=(
            "An overnight sleeper is the best use of a long leg; pick an aisle seat "
            "if you sleep light."
        ),
        booking_available=True,
    )


def derive_train_option(distance_km: float, currency: str) -> RouteOption:
    duration = (distance_km / TRAIN_AVG_SPEED_KMPH) * 60 + 20
    return RouteOption(
        mode="train",
        label="By train",
        summary=f"Roughly {human_duration(duration)} on the rails for {round(distance_km)} km.",
        distance_km=round(distance_km, 1),
        duration_minutes=round(duration),
        estimated_cost=round(distance_km * TRAIN_FARE_PER_KM),
        currency=currency,
        comfort_note="Cheapest comfortable option on a long leg, and the scenery beats a motorway.",
        booking_available=True,
    )


def derive_flight_option(straight_km: float, currency: str) -> RouteOption:
    air_time = (straight_km / 750) * 60
    total = air_time + 150  # airport transfers, security, boarding
    return RouteOption(
        mode="flight",
        label="By air",
        summary=(
            f"About {human_duration(air_time)} in the air, close to {human_duration(total)} "
            "door to door once the airports are counted."
        ),
        distance_km=round(straight_km, 1),
        duration_minutes=round(total),
        estimated_cost=round(FLIGHT_BASE_FARE + straight_km * FLIGHT_FARE_PER_KM),
        currency=currency,
        comfort_note="Worth it beyond roughly 600 km, once airport overhead stops eating the saving.",
        booking_available=True,
    )


async def overpass(query: str) -> dict:
    """Run an Overpass QL query, falling back across mirrors.

    The public instance rate-limits hard (HTTP 429) and answers GET requests less
    reliably than POST, so we POST the query and move to the next mirror on any
    failure. Map furniture is enrichment: an empty result degrades the guide, it
    never breaks it.
    """
    for endpoint in settings.overpass_endpoints:
        try:
            # Overpass is slow by nature; it needs a longer budget than the other
            # providers, which is why the timeout is set here rather than globally.
            data = await post_json(
                endpoint,
                provider="overpass",
                data={"data": query},
                timeout=OVERPASS_TIMEOUT_SECONDS,
            )
            if isinstance(data, dict) and "elements" in data:
                return data
        except Exception as exc:
            log.info("overpass_mirror_failed", endpoint=endpoint, error=str(exc)[:120])
    log.warning("overpass_unavailable", mirrors=len(settings.overpass_endpoints))
    return {"elements": []}


# Nominatim queries that find each kind of hub, with the search span in km and the
# OSM category/type pairs that count as a real answer.
HUB_QUERIES = (
    ("bus station", "bus_station", 30.0, {("amenity", "bus_station")}),
    ("railway station", "railway_station", 30.0, {("railway", "station")}),
    ("airport", "airport", 70.0, {("aeroway", "aerodrome")}),
)


async def nearby_hubs(point: GeoPoint, limit: int = 12) -> list[TransportHub]:
    """Bus stations, railway stations and airports around a point.

    Backed by Nominatim rather than Overpass: the same question costs about a
    second here and up to a minute there, and the answer is the one a traveller
    needs on the page, not a complete extract of the map.
    """
    hubs: list[TransportHub] = []
    for query, kind, span_km, accepted in HUB_QUERIES:
        results = await geo_search_nearby(query, point, span_km=span_km, limit=4)
        for entry in results:
            name = entry.get("name") or ""
            pair = (entry.get("category"), entry.get("type"))
            if not name or pair not in accepted:
                continue
            coords = GeoPoint(lat=float(entry["lat"]), lon=float(entry["lon"]))
            hubs.append(
                TransportHub(
                    name=name,
                    kind=kind,  # type: ignore[arg-type]
                    coordinates=coords,
                    distance_km=haversine_km(point, coords),
                )
            )

    hubs.sort(key=lambda h: (h.distance_km or 9_999))
    # Keep the closest few per kind so one dense category cannot crowd out the rest.
    per_kind: dict[str, int] = {}
    selected: list[TransportHub] = []
    for hub in hubs:
        count = per_kind.get(hub.kind, 0)
        if count >= 4:
            continue
        per_kind[hub.kind] = count + 1
        selected.append(hub)
        if len(selected) >= limit:
            break
    return selected


POI_SELECTORS = (
    'node["tourism"~"attraction|museum|viewpoint|artwork|zoo|theme_park"]["name"]',
    'node["historic"~"monument|memorial|castle|fort|ruins|archaeological_site"]["name"]',
    'node["leisure"~"park|garden|beach_resort"]["name"]',
)


async def tourism_pois(
    point: GeoPoint, radius_m: int = 12_000, limit: int = 30
) -> list[dict[str, Any]]:
    """Attractions OSM knows about - complements Wikipedia's geosearch."""
    clauses = "".join(
        f"{selector}(around:{radius_m},{point.lat},{point.lon});" for selector in POI_SELECTORS
    )
    query = f"[out:json][timeout:40];({clauses});out body {limit * 2};"

    async def _fetch() -> dict:
        return await overpass(query)

    key = {"lat": round(point.lat, 3), "lon": round(point.lon, 3), "r": radius_m}
    data = await cached("overpass_poi", key, _fetch, ttl=7 * 24 * 3600)

    out: list[dict[str, Any]] = []
    for element in (data or {}).get("elements", []):
        tags = element.get("tags", {}) or {}
        name = tags.get("name")
        if not name or "lat" not in element:
            continue
        coords = GeoPoint(lat=element["lat"], lon=element["lon"])
        out.append(
            {
                "name": name,
                "lat": coords.lat,
                "lon": coords.lon,
                "distance_km": haversine_km(point, coords),
                "osm_kind": tags.get("tourism") or tags.get("historic") or tags.get("leisure") or "",
                "wikipedia": tags.get("wikipedia", ""),
            }
        )
    out.sort(key=lambda p: p["distance_km"])
    return out[:limit]


async def build_transport_options(
    origin: GeoPoint,
    destination: GeoPoint,
    currency: str,
    modes: list[str],
) -> list[RouteOption]:
    """Assemble every requested mode, using the real road route as the anchor."""
    straight_km = haversine_km(origin, destination)
    needs_road = "drive" in modes or "bus" in modes
    drive = await road_route(origin, destination) if needs_road else None
    road_km = drive.distance_km if drive else round(straight_km * 1.28, 1)

    options: list[RouteOption] = []
    if "drive" in modes and drive:
        options.append(drive)
    if "bus" in modes:
        options.append(derive_bus_option(road_km, currency))
    if "train" in modes:
        options.append(derive_train_option(road_km * 0.95, currency))
    if "flight" in modes and straight_km > 300:
        options.append(derive_flight_option(straight_km, currency))
    return options


async def gather_transport_context(
    origin: GeoPoint | None,
    destination: GeoPoint,
    currency: str,
    modes: list[str],
    *,
    hub_budget_seconds: float = HUB_BUDGET_SECONDS,
) -> tuple[list[RouteOption], list[TransportHub]]:
    """Routes and hubs in parallel - the two halves of the transport answer.

    Both are third-party lookups that can stall, so hubs get a wall-clock budget:
    if they miss it, the lookup keeps running in the background to populate the
    cache and this request returns without them, rather than holding the whole
    guide hostage to map furniture.
    """
    hub_task = asyncio.ensure_future(nearby_hubs(destination))
    route_task = (
        asyncio.ensure_future(build_transport_options(origin, destination, currency, modes))
        if origin is not None
        else None
    )

    options: list[RouteOption] = []
    if route_task is not None:
        try:
            options = await route_task
        except Exception as exc:
            log.warning("transport_options_failed", error=str(exc))

    hubs: list[TransportHub] = []
    try:
        hubs = await asyncio.wait_for(asyncio.shield(hub_task), timeout=hub_budget_seconds)
    except TimeoutError:
        log.info("hub_lookup_deferred", budget=hub_budget_seconds)
        _warm_in_background(hub_task)
    except Exception as exc:
        log.warning("transport_hubs_failed", error=str(exc))

    return options, hubs


# Keep a strong reference to background warm-ups so they are not garbage collected.
_BACKGROUND: set[asyncio.Task] = set()


def _warm_in_background(task: asyncio.Future) -> None:
    """Let a timed-out lookup finish so the next request hits a warm cache."""
    if not isinstance(task, asyncio.Task):
        return
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
