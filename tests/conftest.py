"""Shared fixtures.

The environment is configured *before* `app` is imported, because settings are
read once into a module-level singleton. Tests run against a throwaway SQLite
file and never reach the network: every provider call is stubbed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "wayfarer_test.db"
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DB.as_posix()}",
        "GOOGLE_API_KEY": "",  # the LLM is stubbed in the tests that need it
        "LANGSMITH_TRACING": "false",
        "API_KEY": "",
        "CACHE_TTL_SECONDS": "1",
    }
)

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.db.session import dispose_db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.travel import Destination, GeoPoint  # noqa: E402
from app.services.cache import cache  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def fresh_database():
    """One schema for the whole run, cleared between tests."""
    await init_db()
    await cache.clear()
    yield
    await cache.clear()


@pytest.fixture(scope="session", autouse=True)
def cleanup_database_file():
    yield
    TEST_DB.unlink(missing_ok=True)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await dispose_db()


@pytest.fixture
def stub_geocode(monkeypatch):
    """Resolve any city to a fixed point, so nothing touches Nominatim."""
    points = {
        "delhi": (28.6139, 77.2090),
        "jaipur": (26.9124, 75.7873),
        "agra": (27.1767, 78.0081),
    }

    async def fake_geocode(query: str, country: str = "") -> Destination | None:
        lat, lon = points.get(query.strip().lower(), (26.9124, 75.7873))
        return Destination(
            name=query.strip().title(),
            display_name=f"{query.title()}, India",
            country=country or "India",
            country_code="IN",
            region="Rajasthan",
            coordinates=GeoPoint(lat=lat, lon=lon),
        )

    for module in ("app.services.booking", "app.services.geo", "app.api.routes.discover", "app.agents.nodes"):
        monkeypatch.setattr(f"{module}.geocode", fake_geocode, raising=False)
    return fake_geocode
