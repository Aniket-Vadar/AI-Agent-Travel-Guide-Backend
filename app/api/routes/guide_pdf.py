"""PDF travel guide download endpoint."""

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.auth import require_auth_user
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.services.pdf_guide import generate_pdf

log = get_logger("api.guide_pdf")
router = APIRouter(prefix="/guide", tags=["guide"])


class PDFRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=120)
    country: str | None = Field(default="India", max_length=80)
    days: int = Field(default=3, ge=1, le=21)
    preferences: str | None = Field(default="", max_length=500)


@router.post("/pdf", summary="Generate a downloadable PDF travel guide")
@limiter.limit(settings.rate_limit_pdf)
async def download_pdf_guide(
    request: Request,
    payload: PDFRequest = Body(...),
    _user: dict = Depends(require_auth_user),
):
    """Generate a comprehensive PDF travel guide and return it as a downloadable file."""
    target_city = payload.city.strip()
    target_country = (payload.country or "India").strip()
    target_days = payload.days or 3
    target_pref = payload.preferences or ""

    pdf_bytes = await generate_pdf(
        city=target_city,
        country=target_country,
        days=target_days,
        preferences=target_pref,
    )

    filename = f"{target_city.replace(' ', '_')}_{target_days}day_guide.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
