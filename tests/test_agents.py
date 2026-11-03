"""The agent graph, exercised end to end with the model and providers stubbed.

This is the test that matters most: it proves the fan-out runs, that every agent's
output lands in the right part of the guide, and that a model which returns
nonsense (a place that was never a candidate, an itinerary of the wrong length)
cannot corrupt the result.
"""

from __future__ import annotations

import pytest

from app.agents.state import (
    CuratedPlace,
    CurationResult,
    FrontMatter,
    ItineraryResult,
    LocalPhrase,
    PlaceStory,
    StoryBatch,
    TransportNarrative,
)
from app.schemas.travel import (
    ItineraryDay,
    ItineraryItem,
    PlaceCategory,
    RouteOption,
    TransportHub,
    TravelRequest,
)
from app.services import planner as planner_service

pytestmark = pytest.mark.anyio

CANDIDATE_TITLES = ["Hawa Mahal", "Amber Fort", "Jantar Mantar", "City Palace"]


@pytest.fixture
def stub_providers(monkeypatch, stub_geocode):
    """Replace every network call the researcher and navigator make."""

    async def fake_city_overview(city, country):
        return {
            "title": city,
            "summary": f"{city} is the capital of Rajasthan, founded in 1727.",
            "url": f"https://en.wikipedia.org/wiki/{city}",
            "image": "https://example.org/city.jpg",
        }

    async def fake_geosearch(point, radius_m=10_000, limit=50):
        return [
            {"title": title, "lat": 26.92 + i / 100, "lon": 75.82 + i / 100, "distance_m": 900 * (i + 1)}
            for i, title in enumerate(CANDIDATE_TITLES)
        ]

    async def fake_enrich_titles(titles, concurrency=6):
        return {
            title: {
                "summary": f"{title} was built in the eighteenth century.",
                "image": f"https://example.org/{title.replace(' ', '_')}.jpg",
                "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "description": "historic site",
            }
            for title in titles
        }

    async def fake_tourism_pois(point, radius_m=12_000, limit=30):
        return [
            {
                "name": "Panna Meena ka Kund",
                "lat": 26.99,
                "lon": 75.85,
                "distance_km": 9.4,
                "osm_kind": "attraction",
                "wikipedia": "",
            }
        ]

    async def fake_transport(origin, destination, currency, modes, **kwargs):
        return (
            [
                RouteOption(
                    mode="drive", label="By road (car / taxi)", distance_km=280,
                    duration_minutes=300, estimated_cost=3000, currency=currency,
                ),
                RouteOption(
                    mode="bus", label="By bus (intercity coach)", distance_km=280,
                    duration_minutes=400, estimated_cost=450, currency=currency,
                    booking_available=True,
                ),
            ],
            [TransportHub(name="Sindhi Camp Bus Stand", kind="bus_station", distance_km=1.2)],
        )

    monkeypatch.setattr("app.agents.nodes.wikipedia.city_overview", fake_city_overview)
    monkeypatch.setattr("app.agents.nodes.wikipedia.geosearch", fake_geosearch)
    monkeypatch.setattr("app.agents.nodes.wikipedia.enrich_titles", fake_enrich_titles)
    monkeypatch.setattr("app.agents.nodes.tourism_pois", fake_tourism_pois)
    monkeypatch.setattr("app.agents.nodes.gather_transport_context", fake_transport)


def make_llm_stub(overrides: dict | None = None):
    """Return a fake `ainvoke_structured` that answers each agent plausibly."""
    overrides = overrides or {}

    async def fake(schema, messages, **kwargs):
        if schema.__name__ in overrides:
            return overrides[schema.__name__]

        if schema is CurationResult:
            return CurationResult(
                places=[
                    CuratedPlace(
                        candidate_id=f"c{i + 1}",
                        name=title,
                        category=PlaceCategory.historical,
                        tagline="Pink sandstone and old air",
                        why_visit="You can still see the city through its windows.",
                        best_time="Early morning, before the coaches arrive",
                        suggested_duration_minutes=90,
                        tips=["Enter from the side street", "Skip the paid guide"],
                    )
                    for i, title in enumerate(CANDIDATE_TITLES)
                ]
            )
        if schema is StoryBatch:
            return StoryBatch(
                stories=[PlaceStory(place_id=f"c{i + 1}", story=f"A story about {t}.")
                         for i, t in enumerate(CANDIDATE_TITLES)]
            )
        if schema is TransportNarrative:
            return TransportNarrative(
                recommended_option="By bus (intercity coach)",
                recommendation_reason="It costs a tenth of the taxi.",
                local_transport_advice="Autos are everywhere; agree the fare first.",
            )
        if schema is FrontMatter:
            return FrontMatter(
                headline="Jaipur in four days, without the crowds",
                overview="A planned city of pink stucco and astronomy.",
                best_time_to_visit="November to February",
                local_food=["Dal baati churma", "Pyaaz kachori"],
                packing_list=["Sun hat", "Slip-on shoes"],
                safety_tips=["Agree auto fares up front"],
                local_phrases=[LocalPhrase(phrase="Kitne ka hai?", meaning="How much is it?")],
            )
        if schema is ItineraryResult:
            return ItineraryResult(
                days=[
                    ItineraryDay(
                        day=1, title="The old city on foot", theme="Walkable centre",
                        items=[
                            ItineraryItem(time="09:30", title="Hawa Mahal", place_id="c1",
                                          description="Start before the light gets hard."),
                            ItineraryItem(time="12:00", title="Lunch", place_id=None,
                                          description="Pyaaz kachori near the bazaar."),
                        ],
                    ),
                    ItineraryDay(day=2, title="Amber and the hills", items=[
                        ItineraryItem(time="08:00", title="Amber Fort", place_id="c2"),
                    ]),
                ]
            )
        raise AssertionError(f"unexpected schema: {schema.__name__}")

    return fake


@pytest.fixture
def stub_llm(monkeypatch):
    def install(overrides: dict | None = None):
        monkeypatch.setattr("app.agents.nodes.ainvoke_structured", make_llm_stub(overrides))
    return install


def a_request(**overrides) -> TravelRequest:
    base = {"country": "India", "city": "Jaipur", "origin_city": "Delhi", "days": 2, "travellers": 2}
    return TravelRequest(**{**base, **overrides})


class TestPlanningGraph:
    async def test_full_run_produces_a_complete_guide(self, stub_providers, stub_llm):
        stub_llm()
        guide = await planner_service.generate_plan(a_request())

        assert guide.plan_id
        assert guide.destination.name == "Jaipur"
        assert guide.headline == "Jaipur in four days, without the crowds"
        assert len(guide.places) == len(CANDIDATE_TITLES)
        assert all(p.story for p in guide.places), "every place should get a story"
        assert guide.itinerary and guide.itinerary[0].items
        assert guide.transport.recommended_option == "By bus (intercity coach)"
        assert guide.transport.local_transport_advice
        assert guide.budget.total_estimate > 0
        assert guide.local_food and guide.packing_list and guide.safety_tips
        assert guide.sources, "Wikipedia URLs should be carried through as sources"
        assert guide.trace["places_selected"] == len(CANDIDATE_TITLES)

    async def test_curator_cannot_invent_places(self, stub_providers, stub_llm):
        """A hallucinated candidate id is dropped rather than rendered."""
        stub_llm({
            "CurationResult": CurationResult(
                places=[
                    CuratedPlace(candidate_id="c1", name="Hawa Mahal"),
                    CuratedPlace(candidate_id="c999", name="The Invisible Palace"),
                ]
            )
        })
        guide = await planner_service.generate_plan(a_request())

        names = [p.name for p in guide.places]
        assert "Hawa Mahal" in names
        assert "The Invisible Palace" not in names

    async def test_itinerary_is_trimmed_to_the_requested_days(self, stub_providers, stub_llm):
        stub_llm()  # the stub returns two days
        guide = await planner_service.generate_plan(a_request(days=1))
        assert len(guide.itinerary) == 1
        assert guide.itinerary[0].day == 1

    async def test_itinerary_place_links_are_validated(self, stub_providers, stub_llm):
        stub_llm({
            "ItineraryResult": ItineraryResult(
                days=[ItineraryDay(day=1, title="Day one", items=[
                    ItineraryItem(time="09:00", title="Somewhere", place_id="c404"),
                ])]
            )
        })
        guide = await planner_service.generate_plan(a_request(days=1))
        # A dangling reference is blanked, so the UI never links to a missing card.
        assert guide.itinerary[0].items[0].place_id is None

    async def test_empty_curation_falls_back_to_proximity(self, stub_providers, stub_llm):
        stub_llm({"CurationResult": CurationResult(places=[])})
        guide = await planner_service.generate_plan(a_request())

        assert guide.places, "the guide should still list the researched places"
        assert any("proximity" in w for w in guide.warnings)

    async def test_empty_itinerary_falls_back_to_a_deterministic_split(self, stub_providers, stub_llm):
        stub_llm({"ItineraryResult": ItineraryResult(days=[])})
        guide = await planner_service.generate_plan(a_request(days=2))

        assert len(guide.itinerary) == 2
        assert all(day.items for day in guide.itinerary)


class TestStreaming:
    async def test_every_agent_reports_progress_in_order(self, stub_providers, stub_llm):
        stub_llm()
        events = [event async for event in planner_service.stream_plan(a_request())]

        assert events[0].event == "started"
        assert events[-1].event == "complete"
        assert events[-1].data["guide"]["plan_id"]

        steps = [e.step for e in events if e.event == "step"]
        assert steps[0] == "researcher"
        assert steps[1] == "curator"
        assert set(steps) >= {"researcher", "curator", "storyteller", "navigator", "editor", "day_planner"}
        assert steps[-1] == "day_planner"

        progress = [e.progress for e in events]
        assert progress == sorted(progress), "progress should never go backwards"


class TestPersistence:
    async def test_a_generated_plan_can_be_fetched_over_the_api(
        self, client, stub_providers, stub_llm
    ):
        stub_llm()
        created = await client.post("/api/v1/plans", json=a_request().model_dump(mode="json"))
        assert created.status_code == 200
        plan_id = created.json()["plan_id"]

        fetched = await client.get(f"/api/v1/plans/{plan_id}")
        assert fetched.status_code == 200
        assert fetched.json()["headline"] == created.json()["headline"]

        listed = await client.get("/api/v1/plans?limit=5")
        assert listed.json()["total"] >= 1
        assert any(item["plan_id"] == plan_id for item in listed.json()["items"])

        deleted = await client.delete(f"/api/v1/plans/{plan_id}")
        assert deleted.status_code == 200
        assert (await client.get(f"/api/v1/plans/{plan_id}")).status_code == 404
