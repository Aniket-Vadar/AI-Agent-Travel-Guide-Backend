"""The versioned API surface."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import chat, discover, guide_pdf, health, plans, stories

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(plans.router)
api_router.include_router(discover.router)
api_router.include_router(stories.router)
api_router.include_router(guide_pdf.router)
