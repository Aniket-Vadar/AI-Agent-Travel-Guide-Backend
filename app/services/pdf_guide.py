"""PDF travel guide generator.

Uses Gemini to create a comprehensive, personalized travel plan and then
formats it into a professional PDF document using reportlab.
"""

from __future__ import annotations

import asyncio
import io
import json as json_mod
from datetime import datetime, UTC

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    HRFlowable,
)

from app.core.logging import get_logger

log = get_logger("pdf_guide")

GUIDE_PROMPT = """You are a professional travel planner. Create a comprehensive {days}-day travel guide for {city}, {country}.

User preferences: {preferences}

Create a detailed guide with this exact JSON structure:
{{
  "title": "Your Complete {days}-Day Guide to {city}",
  "introduction": "A 100-word introduction to the city",
  "daily_plans": [
    {{
      "day": 1,
      "theme": "Day theme like 'Heritage & History'",
      "morning": {{
        "place": "Place name",
        "description": "What to do, 50 words",
        "time": "8:00 AM - 11:00 AM",
        "tip": "One insider tip"
      }},
      "afternoon": {{
        "place": "Place name",
        "description": "What to do, 50 words",
        "time": "12:00 PM - 4:00 PM",
        "tip": "One insider tip"
      }},
      "evening": {{
        "place": "Place name",
        "description": "What to do, 50 words",
        "time": "5:00 PM - 9:00 PM",
        "tip": "One insider tip"
      }}
    }}
  ],
  "food_recommendations": [
    {{"name": "Restaurant/Dish name", "type": "Cuisine type", "must_try": "Signature dish", "area": "Location/area"}}
  ],
  "transport_tips": ["tip1", "tip2", "tip3"],
  "packing_list": ["item1", "item2", "item3", "item4", "item5"],
  "emergency_info": {{
    "police": "100",
    "ambulance": "108",
    "tourist_helpline": "1363"
  }},
  "budget_estimate": {{
    "accommodation_per_night": "₹1500-3000",
    "food_per_day": "₹500-1000",
    "transport_per_day": "₹200-500",
    "total_estimate": "₹15000-25000"
  }}
}}
"""

# Color palette matching the off-white frontend theme
BRAND_GOLD = colors.HexColor("#C9A96E")
BRAND_CHARCOAL = colors.HexColor("#2C2C2C")
BRAND_SAGE = colors.HexColor("#87A878")
BRAND_CREAM = colors.HexColor("#FAF9F6")
BRAND_WARM = colors.HexColor("#8B7355")


def _build_styles() -> dict[str, ParagraphStyle]:
    """Create custom paragraph styles for the PDF."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "CustomTitle",
            parent=base["Title"],
            fontSize=28,
            textColor=BRAND_CHARCOAL,
            spaceAfter=6 * mm,
            fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "CustomSubtitle",
            parent=base["Normal"],
            fontSize=14,
            textColor=BRAND_WARM,
            spaceAfter=4 * mm,
            fontName="Helvetica-Oblique",
        ),
        "heading": ParagraphStyle(
            "CustomHeading",
            parent=base["Heading1"],
            fontSize=18,
            textColor=BRAND_CHARCOAL,
            spaceBefore=8 * mm,
            spaceAfter=4 * mm,
            fontName="Helvetica-Bold",
        ),
        "subheading": ParagraphStyle(
            "CustomSubheading",
            parent=base["Heading2"],
            fontSize=14,
            textColor=BRAND_GOLD,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "CustomBody",
            parent=base["Normal"],
            fontSize=11,
            textColor=BRAND_CHARCOAL,
            leading=16,
            spaceAfter=3 * mm,
            fontName="Helvetica",
        ),
        "tip": ParagraphStyle(
            "CustomTip",
            parent=base["Normal"],
            fontSize=10,
            textColor=BRAND_SAGE,
            leading=14,
            leftIndent=10 * mm,
            fontName="Helvetica-Oblique",
        ),
        "footer": ParagraphStyle(
            "CustomFooter",
            parent=base["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#999999"),
            alignment=1,
        ),
    }


def _build_pdf(guide_data: dict) -> bytes:
    """Build the PDF document from structured guide data."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
    )
    styles = _build_styles()
    story = []

    # ── Cover Page ──
    story.append(Spacer(1, 40 * mm))
    story.append(Paragraph(guide_data.get("title", "Travel Guide"), styles["title"]))
    story.append(Spacer(1, 5 * mm))
    story.append(HRFlowable(width="60%", thickness=2, color=BRAND_GOLD))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        f"Generated on {datetime.now(UTC).strftime('%B %d, %Y')}",
        styles["subtitle"],
    ))
    story.append(Paragraph("Powered by Wayfarer AI Travel Platform", styles["subtitle"]))
    story.append(Spacer(1, 15 * mm))
    intro = guide_data.get("introduction", "")
    if intro:
        story.append(Paragraph(intro, styles["body"]))
    story.append(PageBreak())

    # ── Day-by-Day Plans ──
    story.append(Paragraph("📅 Day-by-Day Itinerary", styles["heading"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_GOLD))
    story.append(Spacer(1, 4 * mm))

    for day_plan in guide_data.get("daily_plans", []):
        day_num = day_plan.get("day", "?")
        theme = day_plan.get("theme", "")
        story.append(Paragraph(f"Day {day_num} — {theme}", styles["subheading"]))

        for period_name in ("morning", "afternoon", "evening"):
            period = day_plan.get(period_name, {})
            if not period:
                continue
            place = period.get("place", "")
            time_str = period.get("time", "")
            desc = period.get("description", "")
            tip = period.get("tip", "")

            story.append(Paragraph(
                f"<b>{period_name.capitalize()}</b> ({time_str}): <b>{place}</b>",
                styles["body"],
            ))
            if desc:
                story.append(Paragraph(desc, styles["body"]))
            if tip:
                story.append(Paragraph(f"💡 Tip: {tip}", styles["tip"]))

        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(width="40%", thickness=0.5, color=colors.HexColor("#E0D5C1")))
        story.append(Spacer(1, 2 * mm))

    story.append(PageBreak())

    # ── Food Recommendations ──
    food = guide_data.get("food_recommendations", [])
    if food:
        story.append(Paragraph("🍽️ Food Recommendations", styles["heading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=BRAND_GOLD))
        story.append(Spacer(1, 4 * mm))

        table_data = [["Name", "Type", "Must Try", "Area"]]
        for item in food:
            if isinstance(item, dict):
                table_data.append([
                    item.get("name", ""),
                    item.get("type", ""),
                    item.get("must_try", ""),
                    item.get("area", ""),
                ])

        if len(table_data) > 1:
            t = Table(table_data, colWidths=[1.6 * inch, 1.2 * inch, 1.5 * inch, 1.2 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_GOLD),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 1), (-1, -1), BRAND_CREAM),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0D5C1")),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)
            story.append(Spacer(1, 6 * mm))

    # ── Transport Tips ──
    transport = guide_data.get("transport_tips", [])
    if transport:
        story.append(Paragraph("🚗 Transport Tips", styles["heading"]))
        for tip in transport:
            story.append(Paragraph(f"• {tip}", styles["body"]))
        story.append(Spacer(1, 4 * mm))

    # ── Packing List ──
    packing = guide_data.get("packing_list", [])
    if packing:
        story.append(Paragraph("🎒 Packing List", styles["heading"]))
        for item in packing:
            story.append(Paragraph(f"☐ {item}", styles["body"]))
        story.append(Spacer(1, 4 * mm))

    # ── Budget Estimate ──
    budget = guide_data.get("budget_estimate", {})
    if budget:
        story.append(Paragraph("💰 Budget Estimate", styles["heading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=BRAND_GOLD))
        story.append(Spacer(1, 3 * mm))
        for key, val in budget.items():
            label = key.replace("_", " ").title()
            story.append(Paragraph(f"<b>{label}:</b> {val}", styles["body"]))
        story.append(Spacer(1, 4 * mm))

    # ── Emergency Info ──
    emergency = guide_data.get("emergency_info", {})
    if emergency:
        story.append(Paragraph("🆘 Emergency Contacts", styles["heading"]))
        for key, val in emergency.items():
            label = key.replace("_", " ").title()
            story.append(Paragraph(f"<b>{label}:</b> {val}", styles["body"]))

    # ── Footer ──
    story.append(Spacer(1, 15 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_GOLD))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "Generated by Wayfarer AI Travel Platform • wayfarer.ai • All information is AI-generated",
        styles["footer"],
    ))

    doc.build(story)
    return buf.getvalue()


async def generate_pdf(
    city: str,
    country: str,
    days: int = 3,
    preferences: str = "",
) -> bytes:
    """Generate a comprehensive PDF travel guide."""
    from langchain_core.messages import HumanMessage
    from app.agents.llm import ainvoke_text

    prompt = GUIDE_PROMPT.format(
        city=city, country=country, days=days,
        preferences=preferences or "general sightseeing, local food, culture"
    )

    raw = await ainvoke_text(
        [HumanMessage(content=prompt)],
        pro=True,
        temperature=0.7,
    )

    # Parse JSON response
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.startswith("json"):
            text = text[4:]
        if text.endswith("```"):
            text = text[:-3]
        guide_data = json_mod.loads(text.strip())
    except (json_mod.JSONDecodeError, ValueError):
        log.warning("pdf_guide_parse_failed", city=city, raw_length=len(raw))
        guide_data = {
            "title": f"Your {days}-Day Guide to {city}",
            "introduction": raw[:300] if raw else "Your travel guide.",
            "daily_plans": [],
            "food_recommendations": [],
            "transport_tips": [],
            "packing_list": [],
            "emergency_info": {},
            "budget_estimate": {},
        }

    # Build PDF in a thread (reportlab is CPU-bound)
    pdf_bytes = await asyncio.to_thread(_build_pdf, guide_data)
    log.info("pdf_generated", city=city, days=days, size_bytes=len(pdf_bytes))
    return pdf_bytes
