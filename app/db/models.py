"""SQLAlchemy models.

Plans, chat sessions, city stories and audio narrations are persisted so that
generated content survives restarts and repeated requests are served instantly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class PlanRecord(Base):
    """A generated travel guide, stored whole so it can be re-served instantly."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    city: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str] = mapped_column(String(80), index=True)
    days: Mapped[int] = mapped_column(Integer, default=3)
    headline: Mapped[str] = mapped_column(String(300), default="")
    place_count: Mapped[int] = mapped_column(Integer, default=0)
    request_json: Mapped[dict] = mapped_column(JSON, default=dict)
    guide_json: Mapped[dict] = mapped_column(JSON, default=dict)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    langsmith_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (Index("ix_plans_city_country", "city", "country"),)


class ChatSessionRecord(Base):
    """Conversational intake state: the transcript plus the slots filled so far."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    history_json: Mapped[list] = mapped_column(JSON, default=list)
    slots_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ready_to_plan: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CityStory(Base):
    """An LLM-generated narrative about a city, cached so the AI only runs once per city."""

    __tablename__ = "city_stories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    city: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    overview: Mapped[str] = mapped_column(Text, default="")
    places_json: Mapped[list] = mapped_column(JSON, default=list)
    highlights: Mapped[list] = mapped_column(JSON, default=list)
    best_time: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    audio: Mapped[StoryAudio | None] = relationship(back_populates="story", uselist=False)

    __table_args__ = (Index("ix_stories_city_country", "city", "country", unique=True),)


class StoryAudio(Base):
    """MP3 audio narration of a city story, generated via gTTS and cached."""

    __tablename__ = "story_audio"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    story_id: Mapped[str] = mapped_column(ForeignKey("city_stories.id"), unique=True, index=True)
    audio_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    story: Mapped[CityStory] = relationship(back_populates="audio")
