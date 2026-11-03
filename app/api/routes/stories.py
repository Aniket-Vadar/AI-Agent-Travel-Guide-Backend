"""Story API endpoints: city stories, audio narration, and popular destinations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.services.story import (
    POPULAR_DESTINATIONS,
    generate_audio,
    generate_story,
    get_audio,
    get_story,
)

log = get_logger("api.stories")
router = APIRouter(prefix="/stories", tags=["stories"])


@router.get("/popular", summary="Get popular destinations for the homepage")
async def popular_destinations():
    """Return the curated list of popular travel destinations."""
    return {"destinations": POPULAR_DESTINATIONS}


@router.get("/{city}", summary="Get or generate a city story")
async def get_city_story(
    city: str,
    country: str = Query(default="", description="Country name for more specific results"),
    session: AsyncSession = Depends(get_session),
):
    """Fetch a city story. If one doesn't exist yet, generate it via AI and cache it."""
    # Try cache first
    cached = await get_story(session, city, country)
    if cached:
        return {"story": cached, "cached": True}

    # Generate new story
    story = await generate_story(session, city, country)
    return {"story": story, "cached": False}


@router.get("/{city}/audio", summary="Get audio narration for a city story")
async def get_city_audio(
    city: str,
    country: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Return MP3 audio narration of the city story. Generates both story and audio if needed."""
    # Ensure story exists
    story = await get_story(session, city, country)
    if not story:
        story = await generate_story(session, city, country)

    story_id = story["id"]

    # Check for cached audio
    audio_bytes = await get_audio(session, story_id)
    if not audio_bytes:
        audio_bytes = await generate_audio(session, story_id)

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'inline; filename="{city}_story.mp3"',
            "Cache-Control": "public, max-age=86400",
        },
    )
