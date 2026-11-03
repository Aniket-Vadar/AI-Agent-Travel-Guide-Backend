"""Ticket search and the hold / confirm / cancel booking flow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.booking import (
    Booking,
    BookingRequest,
    ConfirmBookingRequest,
    TripSearchRequest,
    TripSearchResponse,
)
from app.services import booking as booking_service

router = APIRouter(prefix="/bookings", tags=["booking"])


@router.post(
    "/search",
    response_model=TripSearchResponse,
    summary="Search buses, trains and flights",
    response_description="Available trips, sorted by departure time.",
)
async def search(
    request: TripSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> TripSearchResponse:
    return await booking_service.search_offers(session, request)


@router.post(
    "",
    response_model=Booking,
    status_code=201,
    summary="Hold seats on an offer",
    response_description="A booking in `held` state. Confirm it within 15 minutes.",
)
async def hold(
    request: BookingRequest,
    session: AsyncSession = Depends(get_session),
) -> Booking:
    return await booking_service.hold_booking(session, request)


@router.post(
    "/confirm",
    response_model=Booking,
    summary="Pay for and confirm a held booking",
)
async def confirm(
    request: ConfirmBookingRequest,
    session: AsyncSession = Depends(get_session),
) -> Booking:
    return await booking_service.confirm_booking(session, request.booking_id, request.payment_token)


@router.get("", response_model=list[Booking], summary="List bookings")
async def list_bookings(
    email: str | None = Query(default=None),
    plan_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[Booking]:
    return await booking_service.list_bookings(session, email=email, plan_id=plan_id, limit=limit)


@router.get("/reference/{reference}", response_model=Booking, summary="Look up by reference")
async def by_reference(
    reference: str,
    session: AsyncSession = Depends(get_session),
) -> Booking:
    return await booking_service.get_booking_by_reference(session, reference)


@router.get("/{booking_id}", response_model=Booking, summary="Fetch a booking")
async def get(booking_id: str, session: AsyncSession = Depends(get_session)) -> Booking:
    return await booking_service.get_booking(session, booking_id)


@router.delete("/{booking_id}", response_model=Booking, summary="Cancel a booking")
async def cancel(booking_id: str, session: AsyncSession = Depends(get_session)) -> Booking:
    """Cancels and returns the booking with the refund the policy allows."""
    return await booking_service.cancel_booking(session, booking_id)
