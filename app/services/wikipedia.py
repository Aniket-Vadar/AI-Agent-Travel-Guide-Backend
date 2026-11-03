"""Wikipedia as the factual backbone of the guide.

We use the MediaWiki Action API plus the REST summary endpoint directly instead of
a sync wrapper library, because everything else in the request path is async and we
need geosearch (nearby articles), which is how the planner discovers attractions
that a language model alone would only half-remember.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.travel import GeoPoint
from app.services.cache import cached
from app.services.http import safe_get_json

log = get_logger("wikipedia")

# Nearby articles that are never worth showing a traveller.
_NOISE = re.compile(
    r"(list of|\d{4}.*(election|census|earthquake)|railway line|bus depot|"
    r"assembly constituency|municipal corporation|district of|talukas?|"
    r"airport code|postal index|road$|highway$|flyover|bridge \(|ward no|"
    r"(college|university|school|institute|hospital|polytechnic|campus)|"
    r"(junction|railway station|metro station|bus stand|bus terminal)|"
    r"(company|limited|ltd|industries|factory|refinery))",
    re.IGNORECASE,
)


def _api_url() -> str:
    return f"https://{settings.wikipedia_lang}.wikipedia.org/w/api.php"


def _rest_url() -> str:
    return f"https://{settings.wikipedia_lang}.wikipedia.org/api/rest_v1"


def is_noise(title: str) -> bool:
    return bool(_NOISE.search(title or ""))


async def page_summary(title: str) -> dict[str, Any] | None:
    """Intro paragraph, thumbnail and canonical URL for one article."""
    if not title:
        return None

    async def _fetch() -> dict | None:
        data = await safe_get_json(
            f"{_rest_url()}/page/summary/{title.replace(' ', '_')}",
            provider="wikipedia",
            default=None,
        )
        if not data or data.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found":
            return None
        coords = data.get("coordinates") or {}
        return {
            "title": data.get("title", title),
            "summary": data.get("extract", ""),
            "url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page"),
            "image": (data.get("originalimage") or data.get("thumbnail") or {}).get("source"),
            "lat": coords.get("lat"),
            "lon": coords.get("lon"),
            "description": data.get("description", ""),
        }

    return await cached("wiki_summary", title, _fetch)


async def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Full-text article search."""

    async def _fetch() -> list[dict]:
        data = await safe_get_json(
            _api_url(),
            provider="wikipedia",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
                "srprop": "snippet",
            },
            default={},
        )
        results = ((data or {}).get("query", {}) or {}).get("search", []) or []
        return [
            {
                "title": r["title"],
                "snippet": re.sub(r"<[^>]+>", "", r.get("snippet", "")),
            }
            for r in results
        ]

    return await cached("wiki_search", {"q": query, "n": limit}, _fetch) or []


async def geosearch(point: GeoPoint, radius_m: int = 15_000, limit: int = 40) -> list[dict[str, Any]]:
    """Articles whose subject sits near a coordinate — our attraction discovery."""

    async def _fetch() -> list[dict]:
        data = await safe_get_json(
            _api_url(),
            provider="wikipedia",
            params={
                "action": "query",
                "list": "geosearch",
                "gscoord": f"{point.lat}|{point.lon}",
                "gsradius": min(radius_m, 10_000),  # MediaWiki caps gsradius at 10km
                "gslimit": min(limit, 50),
                "format": "json",
            },
            default={},
        )
        items = ((data or {}).get("query", {}) or {}).get("geosearch", []) or []
        return [
            {
                "title": i["title"],
                "lat": i["lat"],
                "lon": i["lon"],
                "distance_m": i.get("dist", 0),
            }
            for i in items
            if not is_noise(i["title"])
        ]

    return await cached(
        "wiki_geo", {"lat": round(point.lat, 3), "lon": round(point.lon, 3), "r": radius_m, "n": limit}, _fetch
    ) or []


async def enrich_titles(titles: list[str], concurrency: int = 6) -> dict[str, dict[str, Any]]:
    """Fetch summaries for many titles at once, politely."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(title: str) -> tuple[str, dict | None]:
        async with semaphore:
            return title, await page_summary(title)

    pairs = await asyncio.gather(*(one(t) for t in titles), return_exceptions=True)
    out: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if isinstance(pair, Exception):
            continue
        title, data = pair
        if data:
            out[title] = data
    return out


async def city_overview(city: str, country: str) -> dict[str, Any] | None:
    """Best-effort article for the destination itself."""
    for candidate in (f"{city}, {country}" if country else city, city):
        data = await page_summary(candidate)
        if data and data.get("summary"):
            return data
    hits = await search(f"{city} {country} city".strip(), limit=3)
    for hit in hits:
        data = await page_summary(hit["title"])
        if data and data.get("summary"):
            return data
    return None
