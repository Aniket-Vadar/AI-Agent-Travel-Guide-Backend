"""Post-setup verification.

Run this once GOOGLE_API_KEY is in your .env to confirm the whole system works:
every provider, the intake agent, the six-agent graph and the booking flow.

    python scripts/smoke.py                  # against a running server
    python scripts/smoke.py --city Kyoto --country Japan --origin Osaka

It exits non-zero if anything essential fails, so it also works as a CI or
post-deploy check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta

import httpx

# Windows consoles default to cp1252, which cannot encode the arrow and box
# characters used below; force UTF-8 rather than crashing on the first print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_COLOUR = sys.stdout.isatty()
GREEN, RED, YELLOW, DIM, RESET = (
    ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m") if _COLOUR else ("",) * 5
)

failures: list[str] = []


def ok(label: str, detail: str = "") -> None:
    print(f"  {GREEN}PASS{RESET}  {label}{f'  {DIM}{detail}{RESET}' if detail else ''}")


def fail(label: str, detail: str = "") -> None:
    failures.append(label)
    print(f"  {RED}FAIL{RESET}  {label}{f'  {DIM}{detail}{RESET}' if detail else ''}")


def skip(label: str, detail: str = "") -> None:
    print(f"  {YELLOW}SKIP{RESET}  {label}{f'  {DIM}{detail}{RESET}' if detail else ''}")


def section(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


async def check_health(client: httpx.AsyncClient) -> dict:
    section("Service")
    try:
        health = (await client.get("/health")).json()
    except Exception as exc:
        fail("server reachable", str(exc))
        print(f"\n{RED}Is the server running?{RESET}  make dev")
        sys.exit(1)

    ok("server reachable", f"env={health['environment']}")
    (ok if health["database"] == "ok" else fail)("database", health["database"])
    if health["llm_enabled"]:
        ok("Gemini configured", health["providers"]["llm"])
    else:
        fail("Gemini configured", "set GOOGLE_API_KEY in .env")
    (ok if health["langsmith_tracing"] else skip)(
        "LangSmith tracing", "set LANGSMITH_API_KEY to enable" if not health["langsmith_tracing"] else ""
    )
    return health


async def check_providers(client: httpx.AsyncClient, city: str, country: str, origin: str) -> None:
    section("Data providers")

    response = await client.get("/api/v1/destinations/search", params={"city": city, "country": country})
    if response.status_code == 200 and response.json().get("coordinates"):
        body = response.json()
        ok("geocoding (Nominatim)", f"{body['name']}, {body['country']}")
        (ok if body.get("summary") else fail)(
            "Wikipedia article", f"{len(body.get('summary') or '')} chars"
        )
    else:
        fail("geocoding (Nominatim)", response.text[:120])

    response = await client.post("/api/v1/places/search", json={"city": city, "country": country, "limit": 8})
    if response.status_code == 200 and response.json():
        places = response.json()
        described = sum(1 for p in places if p["summary"])
        ok("attraction discovery", f"{len(places)} places, {described} described")
    else:
        fail("attraction discovery", response.text[:120])

    response = await client.post(
        "/api/v1/routes", json={"origin": origin, "destination": city, "modes": ["drive", "bus", "train"]}
    )
    if response.status_code == 200 and response.json().get("options"):
        plan = response.json()
        drive = next((o for o in plan["options"] if o["mode"] == "drive"), None)
        ok("road routing (OSRM)", f"{drive['distance_km']} km" if drive else "derived only")
        (ok if plan.get("hubs") else skip)("transport hubs", f"{len(plan.get('hubs', []))} found")
    else:
        fail("road routing (OSRM)", response.text[:120])


async def check_agents(client: httpx.AsyncClient, city: str, country: str, origin: str, days: int) -> None:
    section("Agents")

    chat = await client.post("/api/v1/chat", json={"message": f"I want {days} days in {city}, {country}"})
    if chat.status_code == 200:
        body = chat.json()
        ok("intake agent", f"ready={body['ready_to_plan']} missing={body['missing'] or 'nothing'}")
        print(f"        {DIM}{body['reply'][:110]}{RESET}")
    else:
        fail("intake agent", chat.text[:160])
        return

    print(f"\n  {DIM}Running the full graph — this takes 20-40 seconds…{RESET}")
    request = {
        "country": country, "city": city, "origin_city": origin,
        "days": days, "travellers": 2, "travel_style": "comfort", "pace": "balanced",
        "interests": ["history", "food"],
    }

    steps: list[str] = []
    guide: dict | None = None
    try:
        async with client.stream("POST", "/api/v1/plans/stream", json=request, timeout=180) as response:
            if response.status_code != 200:
                fail("planning graph", f"HTTP {response.status_code}")
                return
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                    if event == "step":
                        steps.append(payload["step"])
                        print(f"        {DIM}{payload['step']:<12} {payload['message']}{RESET}")
                    elif event == "complete":
                        guide = payload["data"]["guide"]
                    elif event == "error":
                        fail("planning graph", payload.get("message", ""))
                        return
    except Exception as exc:
        fail("planning graph", str(exc)[:160])
        return

    expected = {"researcher", "curator", "storyteller", "navigator", "editor", "day_planner"}
    (ok if expected <= set(steps) else fail)("all six agents ran", ", ".join(steps))

    if not guide:
        fail("guide produced")
        return

    ok("guide produced", f"{guide['trace']['duration_ms'] / 1000:.1f}s")
    (ok if guide["places"] else fail)("places curated", f"{len(guide['places'])} places")
    stories = sum(1 for p in guide["places"] if p["story"])
    (ok if stories else fail)("stories written", f"{stories}/{len(guide['places'])}")
    (ok if guide["itinerary"] else fail)("itinerary built", f"{len(guide['itinerary'])} days")
    (ok if guide["transport"]["options"] else fail)(
        "transport options", f"{len(guide['transport']['options'])} modes"
    )
    (ok if guide["local_food"] else fail)("front matter", f"{len(guide['local_food'])} dishes")
    (ok if guide["budget"]["total_estimate"] else fail)(
        "budget", f"{guide['budget']['total_estimate']} {guide['budget']['currency']}"
    )

    if guide["warnings"]:
        for warning in guide["warnings"]:
            skip("warning", warning)

    print(f"\n  {DIM}Headline:{RESET} {guide['headline']}")
    if guide["places"] and guide["places"][0]["story"]:
        print(f"  {DIM}First story:{RESET} {guide['places'][0]['story'][:200]}…")

    fetched = await client.get(f"/api/v1/plans/{guide['plan_id']}")
    (ok if fetched.status_code == 200 else fail)("guide persisted", guide["plan_id"])


async def check_booking(client: httpx.AsyncClient, origin: str, city: str) -> None:
    section("Booking")
    travel_date = (date.today() + timedelta(days=14)).isoformat()

    search = await client.post(
        "/api/v1/bookings/search",
        json={"origin": origin, "destination": city, "travel_date": travel_date,
              "passengers": 1, "modes": ["bus", "train"]},
    )
    if search.status_code != 200 or not search.json().get("offers"):
        fail("trip search", search.text[:160])
        return
    offers = search.json()["offers"]
    ok("trip search", f"{len(offers)} offers")

    held = await client.post(
        "/api/v1/bookings",
        json={
            "offer_id": offers[0]["offer_id"],
            "passengers": [{"full_name": "Smoke Test", "age": 30}],
            "contact_email": "smoke@example.com",
            "contact_phone": "+910000000000",
        },
    )
    if held.status_code != 201:
        fail("seat hold", held.text[:160])
        return
    booking = held.json()
    ok("seat hold", f"{booking['reference']} seats {','.join(booking['seats'])}")

    confirmed = await client.post(
        "/api/v1/bookings/confirm",
        json={"booking_id": booking["booking_id"], "payment_token": "smoke"},
    )
    (ok if confirmed.status_code == 200 else fail)("confirmation", confirmed.json().get("status", ""))

    cancelled = await client.delete(f"/api/v1/bookings/{booking['booking_id']}")
    (ok if cancelled.status_code == 200 else fail)(
        "cancellation", f"refund {cancelled.json().get('refund_amount')}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--city", default="Jaipur")
    parser.add_argument("--country", default="India")
    parser.add_argument("--origin", default="Delhi")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--skip-agents", action="store_true", help="only check providers and booking")
    args = parser.parse_args()

    print(f"Wayfarer smoke test → {args.url}")

    async with httpx.AsyncClient(base_url=args.url, timeout=120) as client:
        health = await check_health(client)
        await check_providers(client, args.city, args.country, args.origin)
        if args.skip_agents:
            skip("agents", "--skip-agents")
        elif health["llm_enabled"]:
            await check_agents(client, args.city, args.country, args.origin, args.days)
        else:
            skip("agents", "no GOOGLE_API_KEY, so the graph cannot run")
        await check_booking(client, args.origin, args.city)

    print()
    if failures:
        print(f"{RED}{len(failures)} check(s) failed:{RESET} {', '.join(failures)}")
        return 1
    print(f"{GREEN}Everything works.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
