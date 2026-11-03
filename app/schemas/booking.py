"""Ticket search and booking contracts.

The shipped provider is a deterministic simulator: it invents plausible inventory
so the whole flow is demonstrable end to end. Swapping in a real GDS/IRCTC/RedBus
adapter means implementing `BookingProvider` in app/services/booking.py — nothing
in the API layer has to change.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, field_validator


class TransportMode(str, Enum):
    bus = "bus"
    train = "train"
    flight = "flight"


class BookingStatus(str, Enum):
    held = "held"
    confirmed = "confirmed"
    cancelled = "cancelled"
    expired = "expired"


class SeatClass(str, Enum):
    seater = "seater"
    sleeper = "sleeper"
    ac_seater = "ac_seater"
    ac_sleeper = "ac_sleeper"
    economy = "economy"
    business = "business"


class TripSearchRequest(BaseModel):
    origin: str = Field(..., min_length=2, max_length=120)
    destination: str = Field(..., min_length=2, max_length=120)
    travel_date: date
    passengers: int = Field(default=1, ge=1, le=10)
    modes: list[TransportMode] = Field(default_factory=lambda: [TransportMode.bus])

    @field_validator("origin", "destination")
    @classmethod
    def _clean(cls, v: str) -> str:
        return " ".join(v.strip().split())


class TripOffer(BaseModel):
    offer_id: str
    mode: TransportMode
    operator: str
    vehicle_name: str = ""
    seat_class: SeatClass = SeatClass.seater
    origin: str
    destination: str
    departure: datetime
    arrival: datetime
    duration_minutes: int
    price_per_seat: float
    currency: str = "INR"
    seats_available: int
    amenities: list[str] = Field(default_factory=list)
    boarding_point: str = ""
    dropping_point: str = ""
    rating: float = Field(default=4.0, ge=0, le=5)
    cancellation_policy: str = ""


class TripSearchResponse(BaseModel):
    search_id: str
    query: TripSearchRequest
    offers: list[TripOffer]
    provider: str = "simulated"
    disclaimer: str = (
        "Inventory is simulated for demonstration. Connect a real provider adapter "
        "before taking live payments."
    )


class Passenger(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=80)
    age: int = Field(..., ge=0, le=120)
    gender: str = Field(default="unspecified", max_length=20)
    seat_preference: str = Field(default="any", max_length=20)


class BookingRequest(BaseModel):
    offer_id: str
    passengers: list[Passenger] = Field(..., min_length=1, max_length=10)
    contact_email: EmailStr
    contact_phone: str = Field(..., min_length=6, max_length=20)
    plan_id: str | None = None


class Booking(BaseModel):
    booking_id: str
    reference: str
    status: BookingStatus
    offer: TripOffer
    passengers: list[Passenger]
    contact_email: str
    contact_phone: str
    seats: list[str]
    total_amount: float
    currency: str
    plan_id: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    refund_amount: float | None = None


class ConfirmBookingRequest(BaseModel):
    booking_id: str
    payment_token: str = Field(
        default="demo-token",
        description="Opaque token from the payment gateway. The simulator accepts any "
        "non-empty value except 'fail'.",
    )
