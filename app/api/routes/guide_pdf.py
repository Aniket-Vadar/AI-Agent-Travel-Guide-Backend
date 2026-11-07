"""PDF travel guide download endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.services.pdf_guide import generate_pdf

log = get_logger("api.guide_pdf")
router = APIRouter(prefix="/guide", tags=["guide"])


class PDFRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=120)
    country: str = Field(default="India", max_length=80)
    days: int = Field(default=3, ge=1, le=21)
    preferences: str = Field(default="", max_length=500)


@router.post("/pdf", summary="Generate a downloadable PDF travel guide")
@limiter.limit(settings.rate_limit_pdf)
async def download_pdf_guide(request: Request, payload: PDFRequest):
    """Generate a comprehensive PDF travel guide and return it as a downloadable file."""
    pdf_bytes = await generate_pdf(
        city=payload.city,
        country=payload.country,
        days=payload.days,
        preferences=payload.preferences,
    )

    filename = f"{payload.city.replace(' ', '_')}_{payload.days}day_guide.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
