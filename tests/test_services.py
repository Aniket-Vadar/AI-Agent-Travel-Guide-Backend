"""Unit tests for the deterministic pieces: geometry, budgets, slot filling."""

from __future__ import annotations

import pytest

from app.agents.intake import _merge_slots, missing_slots, to_travel_request
from app.agents.nodes import _merge_candidates, _normalise, build_budget
from app.schemas.chat import CollectedSlots
from app.schemas.travel import GeoPoint, TransportPlan, TravelRequest, TravelStyle
from app.services.geo import haversine_km
from app.services.routing import _decode_polyline, derive_bus_option, human_duration


class TestGeometry:
    def test_haversine_matches_known_distance(self):
        delhi = GeoPoint(lat=28.6139, lon=77.2090)
        jaipur = GeoPoint(lat=26.9124, lon=75.7873)
        # The straight-line distance is about 240 km.
        assert 235 < haversine_km(delhi, jaipur) < 245

    def test_haversine_is_zero_for_the_same_point(self):
        point = GeoPoint(lat=10.0, lon=20.0)
        assert haversine_km(point, point) == 0.0

    def test_polyline_decodes_to_known_coordinates(self):
        # The canonical example from Google's polyline specification.
        decoded = _decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        assert decoded == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]

    def test_polyline_handles_empty_input(self):
        assert _decode_polyline("") == []

    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [(0, "0m"), (45, "45m"), (60, "1h"), (135, "2h 15m"), (1440, "24h")],
    )
    def test_human_duration(self, minutes, expected):
        assert human_duration(minutes) == expected


class TestTransportEstimates:
    def test_bus_option_scales_with_distance(self):
        short = derive_bus_option(100, "INR")
        long = derive_bus_option(600, "INR")
        assert long.duration_minutes > short.duration_minutes
        assert long.estimated_cost > short.estimated_cost
        assert short.booking_available is True

    def test_bus_option_includes_boarding_overhead(self):
        option = derive_bus_option(45, "INR")  # one hour of driving
        assert option.duration_minutes > 60


class TestBudget:
    def _request(self, **overrides) -> TravelRequest:
        base = {"country": "India", "city": "Jaipur", "days": 3, "travellers": 2}
        return TravelRequest(**{**base, **overrides})

    def test_luxury_costs_more_than_backpacking(self):
        transport = TransportPlan()
        luxury = build_budget(self._request(travel_style=TravelStyle.luxury), transport, 6)
        cheap = build_budget(self._request(travel_style=TravelStyle.backpacking), transport, 6)
        assert luxury.total_estimate > cheap.total_estimate * 3

    def test_budget_warns_when_over_the_stated_limit(self):
        budget = build_budget(
            self._request(travel_style=TravelStyle.luxury, budget_total=1000), TransportPlan(), 8
        )
        assert "over your stated budget" in budget.notes

    def test_more_travellers_costs_more(self):
        one = build_budget(self._request(travellers=1), TransportPlan(), 5)
        four = build_budget(self._request(travellers=4), TransportPlan(), 5)
        assert four.total_estimate > one.total_estimate


class TestCandidateMerging:
    def test_normalise_collapses_punctuation_and_filler(self):
        assert _normalise("The Amber Fort (Jaipur)") == _normalise("Amber Fort")

    def test_wikipedia_and_osm_entries_for_one_place_merge(self):
        centre = GeoPoint(lat=26.91, lon=75.78)
        geo_hits = [{"title": "Hawa Mahal", "lat": 26.92, "lon": 75.82, "distance_m": 1200}]
        pois = [{"name": "Hawa Mahal", "lat": 26.92, "lon": 75.82, "distance_km": 1.2, "osm_kind": "attraction"}]

        merged = _merge_candidates(geo_hits, pois, centre)

        assert len(merged) == 1
        assert set(merged[0]["sources"]) == {"wikipedia", "openstreetmap"}
        assert merged[0]["osm_kind"] == "attraction"

    def test_osm_only_places_are_kept_with_their_wikipedia_tag(self):
        centre = GeoPoint(lat=26.91, lon=75.78)
        pois = [
            {
                "name": "Panna Meena ka Kund",
                "lat": 26.99,
                "lon": 75.85,
                "distance_km": 9.4,
                "osm_kind": "attraction",
                "wikipedia": "en:Panna Meena ka Kund",
            }
        ]
        merged = _merge_candidates([], pois, centre)
        assert merged[0]["wiki_title"] == "Panna Meena ka Kund"
        assert merged[0]["id"] == "c1"


class TestIntakeSlots:
    def test_new_values_win_but_known_values_survive(self):
        known = CollectedSlots(country="India", city="Jaipur", days=3)
        fresh = CollectedSlots(days=5, interests=["forts"])

        merged = _merge_slots(known, fresh)

        assert merged.country == "India"   # not blanked by the empty field
        assert merged.days == 5            # overwritten by the newer value
        assert merged.interests == ["forts"]

    def test_interests_accumulate_without_duplicates(self):
        known = CollectedSlots(interests=["forts", "food"])
        merged = _merge_slots(known, CollectedSlots(interests=["Food", "photography"]))
        assert merged.interests == ["forts", "food", "photography"]

    def test_missing_slots_lists_only_the_required_ones(self):
        assert missing_slots(CollectedSlots(country="India")) == ["city", "days"]
        assert missing_slots(CollectedSlots(country="India", city="Jaipur", days=3)) == []

    def test_incomplete_slots_produce_no_request(self):
        assert to_travel_request(CollectedSlots(country="India")) is None

    def test_complete_slots_produce_a_valid_request(self):
        slots = CollectedSlots(
            country="India", city="Jaipur", days=4, travellers=2,
            travel_style="luxury", start_date="2026-11-03", interests=["forts"],
        )
        request = to_travel_request(slots)
        assert request.city == "Jaipur"
        assert request.travel_style == TravelStyle.luxury
        assert request.start_date.isoformat() == "2026-11-03"

    def test_unknown_style_and_bad_date_fall_back_instead_of_raising(self):
        slots = CollectedSlots(country="India", city="Jaipur", days=2, travel_style="yolo", start_date="not-a-date")
        request = to_travel_request(slots)
        assert request.travel_style == TravelStyle.comfort
        assert request.start_date is None

    def test_days_are_clamped_to_the_supported_range(self):
        request = to_travel_request(CollectedSlots(country="India", city="Goa", days=400))
        assert request.days == 21
