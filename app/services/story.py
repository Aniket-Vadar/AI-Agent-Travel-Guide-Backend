"""City story generation and caching service.

When a user requests a story for a city, we first check the database.
If the story exists, return it instantly. If not, generate it via Gemini LLM,
save it, and then optionally generate audio narration via gTTS.
"""

from __future__ import annotations

import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import BASE_DIR, settings
from app.core.logging import get_logger
from app.db.models import CityStory, StoryAudio

log = get_logger("story")

# Popular destinations shown on the homepage
POPULAR_DESTINATIONS: list[dict[str, Any]] = [
    {"city": "Pune", "country": "India", "tagline": "Where Heritage Meets Modern Spirit", "image": "https://images.unsplash.com/photo-1567157577867-05ccb1388e13?w=600&q=80", "emoji": "🏰"},
    {"city": "Jaipur", "country": "India", "tagline": "The Pink City of Royal Splendor", "image": "https://images.unsplash.com/photo-1477587458883-47145ed94245?w=600&q=80", "emoji": "👑"},
    {"city": "Goa", "country": "India", "tagline": "Sun, Sand, and Serenity", "image": "https://images.unsplash.com/photo-1512343879784-a960bf40e7f2?w=600&q=80", "emoji": "🏖️"},
    {"city": "Kerala", "country": "India", "tagline": "God's Own Country", "image": "https://images.unsplash.com/photo-1602216056096-3b40cc0c9944?w=600&q=80", "emoji": "🌿"},
    {"city": "Varanasi", "country": "India", "tagline": "The Spiritual Heart of India", "image": "https://images.unsplash.com/photo-1561361513-2d000a50f0dc?w=600&q=80", "emoji": "🕉️"},
    {"city": "Udaipur", "country": "India", "tagline": "The City of Lakes", "image": "https://images.unsplash.com/photo-1595658658481-d53d3f999875?w=600&q=80", "emoji": "🏞️"},
    {"city": "Manali", "country": "India", "tagline": "A Himalayan Paradise", "image": "https://images.unsplash.com/photo-1626621341517-bbf3d9990a23?w=600&q=80", "emoji": "🏔️"},
    {"city": "Mumbai", "country": "India", "tagline": "The City That Never Sleeps", "image": "https://images.unsplash.com/photo-1570168007204-dfb528c6958f?w=600&q=80", "emoji": "🌆"},
    {"city": "Kyoto", "country": "Japan", "tagline": "Ancient Temples and Zen Gardens", "image": "https://images.unsplash.com/photo-1493976040374-85c8e12f0c0e?w=600&q=80", "emoji": "🏯"},
    {"city": "Paris", "country": "France", "tagline": "The City of Light and Love", "image": "https://images.unsplash.com/photo-1502602898657-3e91760cbb34?w=600&q=80", "emoji": "🗼"},
    {"city": "Bali", "country": "Indonesia", "tagline": "Island of the Gods", "image": "https://images.unsplash.com/photo-1537996194471-e657df975ab4?w=600&q=80", "emoji": "🌺"},
    {"city": "Istanbul", "country": "Turkey", "tagline": "Where East Meets West", "image": "https://images.unsplash.com/photo-1524231757912-21f4fe3a7200?w=600&q=80", "emoji": "🕌"},
]


STORY_PROMPT = """You are a world-class travel storyteller. Write a captivating, immersive narrative about {city}, {country}.

Your story must include:

1. **Title**: A poetic, evocative title for the city story (not just the city name)
2. **Overview**: A 150-200 word narrative introduction that makes the reader feel like they're arriving in the city for the first time. Use vivid sensory details — sights, sounds, smells, textures.
3. **Top 5 Places**: For each of the top 5 must-visit places in the city, write:
   - **name**: The place name
   - **story**: A 100-150 word narrative story about this place — its history, what makes it special, what a visitor experiences there. Write it like a travel essay, not a guidebook.
   - **best_time**: Best time to visit
   - **tip**: One insider tip
4. **Cultural Highlights**: 4-5 unique cultural experiences (festivals, food traditions, local customs)
5. **Best Time to Visit**: When to visit and why

Write in a warm, engaging, first-person-plural style ("As you walk through..."). Make the reader want to book a flight immediately.

Respond in this exact JSON format:
{{
  "title": "string",
  "overview": "string",
  "places": [
    {{"name": "string", "story": "string", "best_time": "string", "tip": "string"}}
  ],
  "highlights": ["string", "string", "string", "string"],
  "best_time": "string"
}}
"""


async def get_story(session: AsyncSession, city: str, country: str = "") -> dict[str, Any] | None:
    """Fetch a cached story from the database."""
    city_lower = city.strip().lower()
    country_lower = country.strip().lower() if country else ""

    stmt = (
        select(CityStory)
        .options(selectinload(CityStory.audio))
        .where(CityStory.city == city_lower)
    )
    if country_lower:
        stmt = stmt.where(CityStory.country == country_lower)

    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        return None

    return {
        "id": record.id,
        "city": record.city,
        "country": record.country,
        "title": record.title,
        "overview": record.overview,
        "places": record.places_json or [],
        "highlights": record.highlights or [],
        "best_time": record.best_time,
        "image_url": record.image_url,
        "has_audio": record.audio is not None,
        "created_at": record.created_at.isoformat() if record.created_at else "",
    }


async def generate_story(session: AsyncSession, city: str, country: str = "") -> dict[str, Any]:
    """Generate a city story via Gemini LLM and cache it in the database."""
    import json as json_mod

    from app.agents.llm import ainvoke_text
    from app.services import wikipedia

    city_clean = city.strip()
    country_clean = country.strip() if country else ""
    city_lower = city_clean.lower()
    country_lower = country_clean.lower()

    # Check cache first
    existing = await get_story(session, city_lower, country_lower)
    if existing:
        return existing

    # Get Wikipedia context for better stories
    wiki_context = ""
    try:
        from app.services.geo import geocode
        dest = await geocode(city_clean, country_clean)
        if dest:
            overview = await wikipedia.city_overview(dest.name, dest.country)
            if overview:
                wiki_context = f"\n\nWikipedia context: {overview.get('summary', '')}"
                country_clean = country_clean or dest.country
                country_lower = country_clean.lower()
    except Exception:
        pass

    # Generate story via LLM
    prompt = STORY_PROMPT.format(city=city_clean, country=country_clean or "unknown")
    if wiki_context:
        prompt += wiki_context

    from langchain_core.messages import HumanMessage

    raw = await ainvoke_text(
        [HumanMessage(content=prompt)],
        pro=True,
        temperature=0.8,
    )

    # Parse JSON response
    try:
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.startswith("json"):
            text = text[4:]
        if text.endswith("```"):
            text = text[:-3]
        data = json_mod.loads(text.strip())
    except (json_mod.JSONDecodeError, ValueError):
        log.warning("story_parse_failed", city=city_clean, raw_length=len(raw))
        data = {
            "title": f"Discover {city_clean}",
            "overview": raw[:500] if raw else f"A beautiful city waiting to be explored.",
            "places": [],
            "highlights": [],
            "best_time": "Year-round",
        }

    # Find an image
    image_url = None
    for dest_info in POPULAR_DESTINATIONS:
        if dest_info["city"].lower() == city_lower:
            image_url = dest_info["image"]
            break

    # Save to database
    record = CityStory(
        city=city_lower,
        country=country_lower,
        title=data.get("title", f"Discover {city_clean}"),
        overview=data.get("overview", ""),
        places_json=data.get("places", []),
        highlights=data.get("highlights", []),
        best_time=data.get("best_time", ""),
        image_url=image_url,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    log.info("story_generated", city=city_lower, country=country_lower, story_id=record.id)

    return {
        "id": record.id,
        "city": record.city,
        "country": record.country,
        "title": record.title,
        "overview": record.overview,
        "places": record.places_json or [],
        "highlights": record.highlights or [],
        "best_time": record.best_time,
        "image_url": record.image_url,
        "has_audio": False,
        "created_at": record.created_at.isoformat() if record.created_at else "",
    }


AUDIO_DIR = BASE_DIR / "data" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


async def get_audio(session: AsyncSession, story_id: str) -> bytes | None:
    """Fetch cached audio from disk or database."""
    # 1. Fast disk cache check
    file_path = AUDIO_DIR / f"{story_id}.mp3"
    if file_path.exists():
        try:
            return file_path.read_bytes()
        except Exception:
            pass

    # 2. Database check
    try:
        stmt = select(StoryAudio).where(StoryAudio.story_id == story_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record and record.audio_data:
            # Also cache to disk for future fast access
            try:
                file_path.write_bytes(bytes(record.audio_data))
            except Exception:
                pass
            return bytes(record.audio_data)
    except Exception as e:
        log.warning("get_audio_db_failed", error=str(e), story_id=story_id)

    return None


async def generate_audio(session: AsyncSession, story_id: str) -> bytes:
    """Generate MP3 audio for a story via Google Voice API (or gTTS) and cache it."""
    import asyncio
    import base64
    import httpx
    from app.core.config import settings

    # Check cache
    existing = await get_audio(session, story_id)
    if existing:
        return existing

    # Get the story text
    story_record = await session.get(CityStory, story_id)
    if story_record is None:
        raise ValueError(f"Story {story_id} not found")

    # Build narration text
    parts = [story_record.title, ".", story_record.overview]
    for place in (story_record.places_json or []):
        if isinstance(place, dict):
            parts.append(f". Next, let's visit {place.get('name', '')}.")
            parts.append(place.get("story", ""))
    full_text = " ".join(parts)[:4500]  # Cap at reasonable length for TTS synthesis

    audio_bytes: bytes | None = None

    # Option A: Attempt Google Cloud Text-to-Speech API if key is present
    if settings.google_api_key:
        try:
            tts_url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={settings.google_api_key}"
            payload = {
                "input": {"text": full_text},
                "voice": {
                    "languageCode": "en-US",
                    "name": "en-US-Journey-F",
                    "ssmlGender": "FEMALE",
                },
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": 0.96,
                },
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(tts_url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    if "audioContent" in data:
                        audio_bytes = base64.b64decode(data["audioContent"])
                        log.info("google_voice_api_success", story_id=story_id, size=len(audio_bytes))
        except Exception as e:
            log.warning("google_cloud_tts_fallback", error=str(e))

    # Option B: Reliable Google Text-to-Speech (gTTS) fallback
    if not audio_bytes:
        def _make_gtts() -> bytes:
            from gtts import gTTS
            tts = gTTS(text=full_text, lang="en", slow=False)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            return buf.getvalue()

        audio_bytes = await asyncio.to_thread(_make_gtts)

    # 1. Save to local disk cache (immune to SQLite locks and memory limits)
    try:
        file_path = AUDIO_DIR / f"{story_id}.mp3"
        file_path.write_bytes(audio_bytes)
    except Exception as e:
        log.warning("disk_cache_failed", error=str(e))

    # 2. Persist to database safely
    try:
        # Wrap bytes explicitly to prevent SQLite/aiosqlite memoryview interface errors
        audio_record = StoryAudio(
            story_id=story_id,
            audio_data=bytes(audio_bytes),
            size_bytes=len(audio_bytes),
        )
        session.add(audio_record)
        await session.commit()
    except Exception as db_err:
        log.warning("audio_db_persist_warning", error=str(db_err), story_id=story_id)
        # Roll back failed transaction so session stays healthy
        await session.rollback()

    log.info("audio_generated", story_id=story_id, size_bytes=len(audio_bytes))
    return audio_bytes
