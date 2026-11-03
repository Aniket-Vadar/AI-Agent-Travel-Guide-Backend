"""Geocoding (OpenStreetMap Nominatim) and distance maths."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.travel import Destination, GeoPoint
from app.services.cache import cached
from app.services.http import safe_get_json

log = get_logger("geo")

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance in kilometres."""
    lat1, lon1, lat2, lon2 = map(radians, (a.lat, a.lon, b.lat, b.lon))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * asin(sqrt(h)), 3)


async def geocode(query: str, country: str = "") -> Destination | None:
    """Resolve a free-text place into coordinates and administrative context."""
    q = query.strip()
    if not q:
        return None

    async def _fetch() -> dict | None:
        params = {
            "q": f"{q}, {country}".strip(", ") if country else q,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "extratags": 1,
            "email": settings.contact_email,
        }
        data = await safe_get_json(
            f"{settings.nominatim_url}/search",
            provider="nominatim",
            params=params,
            default=[],
        )
        return data[0] if data else None

    raw = await cached("geocode", {"q": q, "c": country}, _fetch)
    if not raw:
        log.info("geocode_miss", query=q, country=country)
        return None

    address = raw.get("address", {}) or {}
    extratags = raw.get("extratags", {}) or {}
    population = extratags.get("population")
    name = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("state")
        or raw.get("name")
        or q
    )
    return Destination(
        name=name,
        display_name=raw.get("display_name", ""),
        country=address.get("country", country),
        country_code=(address.get("country_code") or "").upper(),
        region=address.get("state") or address.get("region") or "",
        coordinates=GeoPoint(lat=float(raw["lat"]), lon=float(raw["lon"])),
        population=int(population) if str(population or "").isdigit() else None,
    )


async def search_nearby(
    query: str, centre: GeoPoint, *, span_km: float = 30.0, limit: int = 5
) -> list[dict]:
    """Find named features of a kind near a point, inside a bounding box.

    Nominatim answers this in about a second, where the equivalent Overpass query
    regularly takes forty or times out, so this is what backs the transport-hub
    lookup on the request path.
    """
    # 1 degree of latitude is ~111 km; longitude shrinks with the cosine of latitude.
    d_lat = span_km / 111.0
    d_lon = span_km / max(1.0, 111.0 * cos(radians(centre.lat)))
    viewbox = (
        f"{centre.lon - d_lon:.4f},{centre.lat + d_lat:.4f},"
        f"{centre.lon + d_lon:.4f},{centre.lat - d_lat:.4f}"
    )

    async def _fetch() -> list[dict]:
        return await safe_get_json(
            f"{settings.nominatim_url}/search",
            provider="nominatim",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": limit,
                "bounded": 1,
                "viewbox": viewbox,
                "email": settings.contact_email,
            },
            default=[],
        )

    key = {"q": query, "box": viewbox, "n": limit}
    return await cached("nominatim_nearby", key, _fetch, ttl=7 * 24 * 3600) or []


async def reverse_geocode(point: GeoPoint) -> str:
    """Human-readable label for a coordinate. Empty string when unavailable."""

    async def _fetch() -> dict:
        return await safe_get_json(
            f"{settings.nominatim_url}/reverse",
            provider="nominatim",
            params={
                "lat": point.lat,
                "lon": point.lon,
                "format": "jsonv2",
                "email": settings.contact_email,
            },
            default={},
        )

    raw = await cached("reverse", {"lat": point.lat, "lon": point.lon}, _fetch)
    return (raw or {}).get("display_name", "")
