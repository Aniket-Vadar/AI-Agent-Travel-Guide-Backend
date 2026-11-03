"""End-to-end API tests over ASGI, with every provider stubbed."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.anyio


class TestHealth:
    async def test_health_reports_providers(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert "wikipedia" in body["providers"]

    async def test_liveness_probe(self, client):
        assert (await client.get("/health/live")).json() == {"status": "alive"}

    async def test_request_id_is_echoed(self, client):
        response = await client.get("/health", headers={"X-Request-ID": "abc123"})
        assert response.headers["X-Request-ID"] == "abc123"
        assert "X-Response-Time-ms" in response.headers


class TestErrorHandling:
    async def test_unknown_plan_returns_a_clean_envelope(self, client):
        response = await client.get("/api/v1/plans/does-not-exist")
        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "not_found"
        assert error["request_id"]

    async def test_invalid_body_is_rejected_with_details(self, client):
        response = await client.post("/api/v1/plans", json={"city": "Jaipur"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    async def test_day_count_is_bounded(self, client):
        response = await client.post(
            "/api/v1/plans", json={"city": "Jaipur", "country": "India", "days": 90}
        )
        assert response.status_code == 422


class TestBookingFlow:
    def _search_body(self, **overrides) -> dict:
        body = {
            "origin": "Delhi",
            "destination": "Jaipur",
            "travel_date": (date.today() + timedelta(days=10)).isoformat(),
            "passengers": 2,
            "modes": ["bus"],
        }
        body.update(overrides)
        return body

    async def test_search_returns_bookable_offers(self, client, stub_geocode):
        response = await client.post("/api/v1/bookings/search", json=self._search_body())
        assert response.status_code == 200
        body = response.json()

        assert body["offers"], "the simulator should always produce some inventory"
        assert "simulated" in body["disclaimer"].lower()
        for offer in body["offers"]:
            assert offer["mode"] == "bus"
            assert offer["seats_available"] >= 2  # filtered to what the party needs
            assert offer["price_per_seat"] > 0

    async def test_offers_are_stable_for_the_same_query(self, client, stub_geocode):
        body = self._search_body()
        first = (await client.post("/api/v1/bookings/search", json=body)).json()
        second = (await client.post("/api/v1/bookings/search", json=body)).json()

        assert [o["price_per_seat"] for o in first["offers"]] == [
            o["price_per_seat"] for o in second["offers"]
        ]
        assert [o["departure"] for o in first["offers"]] == [
            o["departure"] for o in second["offers"]
        ]

    async def test_past_dates_are_rejected(self, client, stub_geocode):
        response = await client.post(
            "/api/v1/bookings/search",
            json=self._search_body(travel_date=(date.today() - timedelta(days=1)).isoformat()),
        )
        assert response.status_code == 422

    async def test_same_origin_and_destination_is_rejected(self, client, stub_geocode):
        response = await client.post(
            "/api/v1/bookings/search", json=self._search_body(destination="Delhi")
        )
        assert response.status_code == 422

    async def _first_offer(self, client) -> dict:
        response = await client.post("/api/v1/bookings/search", json=self._search_body())
        return response.json()["offers"][0]

    async def test_hold_then_confirm_issues_a_ticket(self, client, stub_geocode):
        offer = await self._first_offer(client)

        held = await client.post(
            "/api/v1/bookings",
            json={
                "offer_id": offer["offer_id"],
                "passengers": [
                    {"full_name": "Asha Rao", "age": 31},
                    {"full_name": "Vikram Rao", "age": 34},
                ],
                "contact_email": "asha@example.com",
                "contact_phone": "+919876543210",
            },
        )
        assert held.status_code == 201
        booking = held.json()
        assert booking["status"] == "held"
        assert len(booking["seats"]) == 2
        assert booking["total_amount"] == offer["price_per_seat"] * 2
        assert booking["reference"].startswith("TRV")
        assert booking["expires_at"] is not None

        confirmed = await client.post(
            "/api/v1/bookings/confirm",
            json={"booking_id": booking["booking_id"], "payment_token": "tok_visa"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"
        assert confirmed.json()["confirmed_at"] is not None

    async def test_declined_payment_leaves_the_booking_held(self, client, stub_geocode):
        offer = await self._first_offer(client)
        booking = (await client.post(
            "/api/v1/bookings",
            json={
                "offer_id": offer["offer_id"],
                "passengers": [{"full_name": "Asha Rao", "age": 31}],
                "contact_email": "asha@example.com",
                "contact_phone": "+919876543210",
            },
        )).json()

        declined = await client.post(
            "/api/v1/bookings/confirm",
            json={"booking_id": booking["booking_id"], "payment_token": "fail"},
        )
        assert declined.status_code == 409

        still_held = await client.get(f"/api/v1/bookings/{booking['booking_id']}")
        assert still_held.json()["status"] == "held"

    async def test_lookup_by_reference_and_cancellation_refunds(self, client, stub_geocode):
        offer = await self._first_offer(client)
        booking = (await client.post(
            "/api/v1/bookings",
            json={
                "offer_id": offer["offer_id"],
                "passengers": [{"full_name": "Asha Rao", "age": 31}],
                "contact_email": "asha@example.com",
                "contact_phone": "+919876543210",
            },
        )).json()

        by_reference = await client.get(f"/api/v1/bookings/reference/{booking['reference']}")
        assert by_reference.json()["booking_id"] == booking["booking_id"]

        cancelled = await client.delete(f"/api/v1/bookings/{booking['booking_id']}")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        # Cancelling a hold costs nothing, so the whole amount is released.
        assert cancelled.json()["refund_amount"] == booking["total_amount"]

    async def test_booking_an_unknown_offer_is_a_404(self, client):
        response = await client.post(
            "/api/v1/bookings",
            json={
                "offer_id": "nope",
                "passengers": [{"full_name": "Asha Rao", "age": 31}],
                "contact_email": "asha@example.com",
                "contact_phone": "+919876543210",
            },
        )
        assert response.status_code == 404

    async def test_bookings_can_be_listed_by_email(self, client, stub_geocode):
        offer = await self._first_offer(client)
        await client.post(
            "/api/v1/bookings",
            json={
                "offer_id": offer["offer_id"],
                "passengers": [{"full_name": "Asha Rao", "age": 31}],
                "contact_email": "listme@example.com",
                "contact_phone": "+919876543210",
            },
        )
        listed = await client.get("/api/v1/bookings?email=listme@example.com")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["contact_email"] == "listme@example.com"


class TestStaticSite:
    async def test_the_site_is_served_at_the_root(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        assert "Wayfarer" in response.text

    async def test_openapi_document_is_published(self, client):
        schema = (await client.get("/openapi.json")).json()
        assert "/api/v1/plans" in schema["paths"]
        assert "/api/v1/bookings/search" in schema["paths"]
