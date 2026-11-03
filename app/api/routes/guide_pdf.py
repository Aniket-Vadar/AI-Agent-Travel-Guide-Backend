"""PDF travel guide download endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.pdf_guide import generate_pdf

log = get_logger("api.guide_pdf")
router = APIRouter(prefix="/guide", tags=["guide"])


class PDFRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=120)
    country: str = Field(default="India", max_length=80)
    days: int = Field(default=3, ge=1, le=21)
    preferences: str = Field(default="", max_length=500)


@router.post("/pdf", summary="Generate a downloadable PDF travel guide")
async def download_pdf_guide(request: PDFRequest):
    """Generate a comprehensive PDF travel guide and return it as a downloadable file."""
    pdf_bytes = await generate_pdf(
        city=request.city,
        country=request.country,
        days=request.days,
        preferences=request.preferences,
    )

    filename = f"{request.city.replace(' ', '_')}_{request.days}day_guide.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
