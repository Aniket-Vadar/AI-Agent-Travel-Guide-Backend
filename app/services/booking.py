"""Ticket search and booking.

The provider shipped here is a *simulator*, and it is honest about that: the
search response carries a disclaimer and every offer is generated from a seed
derived from the route and date, so the same query returns the same inventory
and a quoted price never changes under the traveller's feet.

What is real is the shape of the flow - search, hold, pay, confirm, cancel with a
refund rule - and the geography, because departure times and fares are derived
from the actual road distance between the two cities. Replacing the simulator
with RedBus, IRCTC, Amadeus or any GDS means implementing `search_offers` against
that API; the routes, the database and the UI stay as they are.
"""

from __future__ import annotations

import hashlib
import random
import string
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import BookingError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import BookingRecord, OfferRecord, new_id
from app.schemas.booking import (
    Booking,
    BookingRequest,
    BookingStatus,
    Passenger,
    SeatClass,
    TransportMode,
    TripOffer,
    TripSearchRequest,
    TripSearchResponse,
)
from app.services.geo import geocode, haversine_km

log = get_logger("booking")

HOLD_MINUTES = 15
FULL_REFUND_HOURS = 48
HALF_REFUND_HOURS = 12

BUS_OPERATORS = [
    ("VRL Travels", "Volvo B11R Multi-Axle"),
    ("SRS Travels", "Scania Metrolink"),
    ("Orange Tours", "Mercedes-Benz Sleeper"),
    ("State Transport", "Semi-Deluxe Seater"),
    ("Neeta Travels", "Volvo 9600 Sleeper"),
    ("Zingbus", "Bharat Benz A/C Seater"),
]
TRAIN_OPERATORS = [
    ("Indian Railways", "Shatabdi Express"),
    ("Indian Railways", "Rajdhani Express"),
    ("Indian Railways", "Vande Bharat Express"),
    ("Indian Railways", "Intercity Express"),
]
AIRLINES = [
    ("IndiGo", "A320neo"),
    ("Air India", "A321"),
    ("Akasa Air", "B737 MAX"),
    ("SpiceJet", "B737-800"),
]

BUS_AMENITIES = [
    "Charging point",
    "Blanket",
    "Reading light",
    "Water bottle",
    "Live tracking",
    "Onboard washroom",
    "Wi-Fi",
]
TRAIN_AMENITIES = ["Meals included", "Bedroll", "Charging point", "Pantry car"]
FLIGHT_AMENITIES = ["Cabin bag 7kg", "Web check-in", "Paid meals", "Seat selection"]

CANCELLATION_POLICY = (
    f"Free cancellation up to {FULL_REFUND_HOURS}h before departure, 50% refund up to "
    f"{HALF_REFUND_HOURS}h before, no refund after that."
)


def _seed(query: TripSearchRequest, mode: TransportMode) -> int:
    """Stable across processes - `hash()` is salted per interpreter, sha1 is not."""
    key = f"{query.origin.lower()}|{query.destination.lower()}|{query.travel_date}|{mode.value}"
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)


def _reference() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "TRV" + "".join(random.choices(alphabet, k=7))


async def _route_distance_km(origin: str, destination: str) -> float:
    """Real geography where we can get it, a sane default where we cannot."""
    start = await geocode(origin)
    end = await geocode(destination)
    if start and end and start.coordinates and end.coordinates:
        return round(haversine_km(start.coordinates, end.coordinates) * 1.28, 1)
    return 420.0


def _seat_label(mode: TransportMode, index: int) -> str:
    if mode == TransportMode.flight:
        row = index // 6 + 3
        return f"{row}{'ABCDEF'[index % 6]}"
    row = index // 4 + 1
    return f"{row}{'ABCD'[index % 4]}"


def _build_bus_offers(rng: random.Random, query: TripSearchRequest, distance_km: float) -> list[TripOffer]:
    offers: list[TripOffer] = []
    hours = distance_km / 45.0
    for _ in range(rng.randint(4, 6)):
        operator, vehicle = rng.choice(BUS_OPERATORS)
        overnight = rng.random() < 0.5 and hours > 5
        depart_hour = rng.choice([20, 21, 22, 23]) if overnight else rng.choice([6, 7, 9, 11, 14, 16])
        departure = datetime.combine(
            query.travel_date, time(depart_hour, rng.choice([0, 15, 30, 45])), tzinfo=UTC
        )
        duration = int(hours * 60 * rng.uniform(0.94, 1.18)) + rng.randint(10, 40)
        seat_class = (
            SeatClass.ac_sleeper
            if overnight
            else rng.choice([SeatClass.seater, SeatClass.ac_seater, SeatClass.sleeper])
        )
        multiplier = {
            SeatClass.seater: 1.0,
            SeatClass.ac_seater: 1.35,
            SeatClass.sleeper: 1.5,
            SeatClass.ac_sleeper: 1.85,
        }[seat_class]
        offers.append(
            TripOffer(
                offer_id=new_id()[:20],
                mode=TransportMode.bus,
                operator=operator,
                vehicle_name=vehicle,
                seat_class=seat_class,
                origin=query.origin,
                destination=query.destination,
                departure=departure,
                arrival=departure + timedelta(minutes=duration),
                duration_minutes=duration,
                price_per_seat=round(distance_km * 1.6 * multiplier / 10) * 10,
                currency=settings.booking_currency,
                seats_available=rng.randint(3, 34),
                amenities=rng.sample(BUS_AMENITIES, k=rng.randint(3, 5)),
                boarding_point=f"{query.origin} Central Bus Stand",
                dropping_point=f"{query.destination} Bus Terminal",
                rating=round(rng.uniform(3.4, 4.8), 1),
                cancellation_policy=CANCELLATION_POLICY,
            )
        )
    return offers


def _build_train_offers(rng: random.Random, query: TripSearchRequest, distance_km: float) -> list[TripOffer]:
    offers: list[TripOffer] = []
    hours = distance_km / 60.0
    for _ in range(rng.randint(2, 4)):
        operator, name = rng.choice(TRAIN_OPERATORS)
        depart_hour = rng.choice([5, 6, 8, 15, 17, 22])
        departure = datetime.combine(
            query.travel_date, time(depart_hour, rng.choice([5, 10, 25, 40])), tzinfo=UTC
        )
        duration = int(hours * 60 * rng.uniform(0.9, 1.15))
        seat_class = rng.choice([SeatClass.seater, SeatClass.ac_seater, SeatClass.ac_sleeper])
        multiplier = {SeatClass.seater: 1.0, SeatClass.ac_seater: 2.1, SeatClass.ac_sleeper: 2.8}[seat_class]
        offers.append(
            TripOffer(
                offer_id=new_id()[:20],
                mode=TransportMode.train,
                operator=operator,
                vehicle_name=f"{name} {rng.randint(12000, 12999)}",
                seat_class=seat_class,
                origin=query.origin,
                destination=query.destination,
                departure=departure,
                arrival=departure + timedelta(minutes=duration),
                duration_minutes=duration,
                price_per_seat=round(distance_km * 0.9 * multiplier / 5) * 5,
                currency=settings.booking_currency,
                seats_available=rng.randint(0, 120),
                amenities=rng.sample(TRAIN_AMENITIES, k=rng.randint(2, 4)),
                boarding_point=f"{query.origin} Junction",
                dropping_point=f"{query.destination} Junction",
                rating=round(rng.uniform(3.6, 4.6), 1),
                cancellation_policy=CANCELLATION_POLICY,
            )
        )
    return offers


def _build_flight_offers(rng: random.Random, query: TripSearchRequest, distance_km: float) -> list[TripOffer]:
    if distance_km < 250:
        return []
    offers: list[TripOffer] = []
    for _ in range(rng.randint(2, 4)):
        airline, aircraft = rng.choice(AIRLINES)
        depart_hour = rng.choice([6, 8, 11, 14, 18, 21])
        departure = datetime.combine(
            query.travel_date, time(depart_hour, rng.choice([0, 20, 35, 50])), tzinfo=UTC
        )
        duration = int((distance_km / 750) * 60) + rng.randint(15, 35)
        seat_class = rng.choice([SeatClass.economy, SeatClass.economy, SeatClass.business])
        multiplier = 1.0 if seat_class == SeatClass.economy else 3.2
        offers.append(
            TripOffer(
                offer_id=new_id()[:20],
                mode=TransportMode.flight,
                operator=airline,
                vehicle_name=f"{aircraft} - {airline[:2].upper()}{rng.randint(100, 999)}",
                seat_class=seat_class,
                origin=query.origin,
                destination=query.destination,
                departure=departure,
                arrival=departure + timedelta(minutes=duration),
                duration_minutes=duration,
                price_per_seat=round((2800 + distance_km * 4.2) * multiplier / 50) * 50,
                currency=settings.booking_currency,
                seats_available=rng.randint(1, 40),
                amenities=rng.sample(FLIGHT_AMENITIES, k=rng.randint(2, 4)),
                boarding_point=f"{query.origin} Airport",
                dropping_point=f"{query.destination} Airport",
                rating=round(rng.uniform(3.5, 4.7), 1),
                cancellation_policy="Airline fare rules apply; cancellation fee deducted from the fare.",
            )
        )
    return offers


async def search_offers(session: AsyncSession, query: TripSearchRequest) -> TripSearchResponse:
    """Find, persist and return the trips available for a route and date."""
    if query.travel_date < date.today():
        raise ValidationError("Travel date is in the past.")
    if query.origin.strip().lower() == query.destination.strip().lower():
        raise ValidationError("Origin and destination must be different.")

    distance_km = await _route_distance_km(query.origin, query.destination)
    search_id = new_id()[:20]
    builders = {
        TransportMode.bus: _build_bus_offers,
        TransportMode.train: _build_train_offers,
        TransportMode.flight: _build_flight_offers,
    }

    offers: list[TripOffer] = []
    for mode in query.modes:
        rng = random.Random(_seed(query, mode))
        offers.extend(builders[mode](rng, query, distance_km))

    offers = [o for o in offers if o.seats_available >= query.passengers]
    offers.sort(key=lambda o: o.departure)

    for offer in offers:
        session.add(
            OfferRecord(
                id=offer.offer_id,
                search_id=search_id,
                mode=offer.mode.value,
                origin=offer.origin,
                destination=offer.destination,
                departure=offer.departure,
                price_per_seat=offer.price_per_seat,
                seats_available=offer.seats_available,
                payload_json=offer.model_dump(mode="json"),
            )
        )
    await session.commit()

    log.info(
        "offers_generated",
        search_id=search_id,
        count=len(offers),
        distance_km=distance_km,
        modes=[m.value for m in query.modes],
    )
    return TripSearchResponse(search_id=search_id, query=query, offers=offers, provider=settings.booking_provider)


def _to_booking(record: BookingRecord, offer: TripOffer) -> Booking:
    return Booking(
        booking_id=record.id,
        reference=record.reference,
        status=BookingStatus(record.status),
        offer=offer,
        passengers=[Passenger(**p) for p in record.passengers_json],
        contact_email=record.contact_email,
        contact_phone=record.contact_phone,
        seats=list(record.seats_json or []),
        total_amount=record.total_amount,
        currency=record.currency,
        plan_id=record.plan_id,
        created_at=record.created_at,
        expires_at=record.expires_at,
        confirmed_at=record.confirmed_at,
        cancelled_at=record.cancelled_at,
        refund_amount=record.refund_amount,
    )


async def _load(session: AsyncSession, booking_id: str) -> tuple[BookingRecord, OfferRecord]:
    record = await session.get(BookingRecord, booking_id)
    if record is None:
        raise NotFoundError(f"No booking with id '{booking_id}'.")
    offer_record = await session.get(OfferRecord, record.offer_id)
    if offer_record is None:
        raise NotFoundError("The offer behind this booking no longer exists.")
    return record, offer_record


async def hold_booking(session: AsyncSession, request: BookingRequest) -> Booking:
    """Reserve seats for a short window, before payment."""
    offer_record = await session.get(OfferRecord, request.offer_id)
    if offer_record is None:
        raise NotFoundError(
            "That offer has expired. Search again to get current availability."
        )
    offer = TripOffer.model_validate(offer_record.payload_json)

    seats_taken = await session.scalar(
        select(func.count())
        .select_from(BookingRecord)
        .where(
            BookingRecord.offer_id == request.offer_id,
            BookingRecord.status.in_([BookingStatus.held.value, BookingStatus.confirmed.value]),
        )
    ) or 0
    wanted = len(request.passengers)
    if seats_taken + wanted > offer.seats_available:
        raise BookingError(
            f"Only {max(0, offer.seats_available - seats_taken)} seat(s) left on this service.",
            details={"offer_id": request.offer_id, "requested": wanted},
        )

    now = datetime.now(UTC)
    seats = [_seat_label(offer.mode, seats_taken + i) for i in range(wanted)]
    record = BookingRecord(
        id=new_id(),
        reference=_reference(),
        status=BookingStatus.held.value,
        offer_id=offer.offer_id,
        plan_id=request.plan_id,
        contact_email=str(request.contact_email),
        contact_phone=request.contact_phone,
        passengers_json=[p.model_dump() for p in request.passengers],
        seats_json=seats,
        total_amount=round(offer.price_per_seat * wanted, 2),
        currency=offer.currency,
        created_at=now,
        expires_at=now + timedelta(minutes=HOLD_MINUTES),
    )
    session.add(record)
    await session.commit()
    log.info("booking_held", reference=record.reference, seats=len(seats))
    return _to_booking(record, offer)


async def confirm_booking(session: AsyncSession, booking_id: str, payment_token: str) -> Booking:
    record, offer_record = await _load(session, booking_id)
    offer = TripOffer.model_validate(offer_record.payload_json)
    now = datetime.now(UTC)

    if record.status == BookingStatus.confirmed.value:
        return _to_booking(record, offer)  # confirming twice is not an error
    if record.status == BookingStatus.cancelled.value:
        raise BookingError("This booking was cancelled and cannot be confirmed.")
    if record.expires_at and _aware(record.expires_at) < now:
        record.status = BookingStatus.expired.value
        await session.commit()
        raise BookingError("The seat hold expired. Please search and book again.")
    if not payment_token or payment_token == "fail":
        raise BookingError("Payment was declined by the gateway.")

    record.status = BookingStatus.confirmed.value
    record.confirmed_at = now
    await session.commit()
    log.info("booking_confirmed", reference=record.reference, amount=record.total_amount)
    return _to_booking(record, offer)


async def cancel_booking(session: AsyncSession, booking_id: str) -> Booking:
    record, offer_record = await _load(session, booking_id)
    offer = TripOffer.model_validate(offer_record.payload_json)
    if record.status == BookingStatus.cancelled.value:
        return _to_booking(record, offer)

    now = datetime.now(UTC)
    hours_to_departure = (_aware(offer.departure) - now).total_seconds() / 3600
    if record.status != BookingStatus.confirmed.value:
        refund = record.total_amount  # nothing was charged on a hold
    elif hours_to_departure >= FULL_REFUND_HOURS:
        refund = record.total_amount
    elif hours_to_departure >= HALF_REFUND_HOURS:
        refund = round(record.total_amount * 0.5, 2)
    else:
        refund = 0.0

    record.status = BookingStatus.cancelled.value
    record.cancelled_at = now
    record.refund_amount = refund
    await session.commit()
    log.info("booking_cancelled", reference=record.reference, refund=refund)
    return _to_booking(record, offer)


def _aware(value: datetime) -> datetime:
    """SQLite gives naive datetimes back; treat those as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def get_booking(session: AsyncSession, booking_id: str) -> Booking:
    record, offer_record = await _load(session, booking_id)
    return _to_booking(record, TripOffer.model_validate(offer_record.payload_json))


async def get_booking_by_reference(session: AsyncSession, reference: str) -> Booking:
    record = await session.scalar(
        select(BookingRecord).where(BookingRecord.reference == reference.upper())
    )
    if record is None:
        raise NotFoundError(f"No booking with reference '{reference}'.")
    return await get_booking(session, record.id)


async def list_bookings(
    session: AsyncSession, *, email: str | None = None, plan_id: str | None = None, limit: int = 50
) -> list[Booking]:
    stmt = select(BookingRecord).order_by(BookingRecord.created_at.desc()).limit(limit)
    if email:
        stmt = stmt.where(BookingRecord.contact_email == email)
    if plan_id:
        stmt = stmt.where(BookingRecord.plan_id == plan_id)
    records = list(await session.scalars(stmt))

    out: list[Booking] = []
    for record in records:
        offer_record = await session.get(OfferRecord, record.offer_id)
        if offer_record:
            out.append(_to_booking(record, TripOffer.model_validate(offer_record.payload_json)))
    return out
