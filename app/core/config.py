"""Application settings, loaded once from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BASE_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


class Settings(BaseSettings):
    """Every knob the service exposes. Values come from `.env` or the process env."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- Application ----------
    app_name: str = "AI Travel Planner"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "APP_PORT"))
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    api_key: str = ""

    # ---------- Gemini ----------
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_pro_model: str = "gemini-2.5-flash"
    gemini_temperature: float = 0.6
    gemini_max_output_tokens: int = 8192

    # ---------- LangSmith ----------
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "ai-travel-planner"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # ---------- Providers ----------
    wikipedia_lang: str = "en"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    osrm_url: str = "https://router.project-osrm.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Comma separated fallbacks; the primary instance rate-limits aggressively.
    overpass_mirrors: str = (
        "https://overpass.kumi.systems/api/interpreter,"
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    )
    contact_email: str = "you@example.com"
    # Nominatim rejects any User-Agent containing an email address, so the header
    # carries a project URL and the email travels in the documented `email` param.
    contact_url: str = "https://github.com/ai-travel-planner"
    http_timeout_seconds: float = 25.0

    # ---------- Persistence ----------
    database_url: str = "sqlite+aiosqlite:///./data/travel.db"
    cache_ttl_seconds: int = 86_400

    # ---------- Security & Rate Limiting ----------
    rate_limit_enabled: bool = True  # Set RATE_LIMIT_ENABLED=false in .env to disable
    rate_limit_chat: str = "15/minute"
    rate_limit_pdf: str = "6/minute"
    rate_limit_audio: str = "6/minute"
    rate_limit_global: str = "60/minute"

    # ---------- Supabase & Authentication ----------
    require_auth: bool = True  # When True, requests require a valid Supabase user token
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    # ---------- Booking ----------
    booking_provider: str = "simulated"
    booking_currency: str = "INR"

    # ---------- Derived ----------
    request_max_days: int = Field(default=21, description="Upper bound on trip length.")

    @field_validator("gemini_pro_model")
    @classmethod
    def _sanitize_pro_model(cls, v: str) -> str:
        # gemini-2.5-pro and 1.5 have been retired for new API keys by Google
        if not v or "2.5-pro" in v or "1.5" in v:
            return "gemini-2.5-flash"
        return v

    @field_validator("gemini_model")
    @classmethod
    def _sanitize_model(cls, v: str) -> str:
        if not v or "1.5" in v:
            return "gemini-2.5-flash"
        return v

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Seamless Supabase PostgreSQL support
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        """False when no Gemini key is present; the app then runs in degraded mode."""
        return bool(self.google_api_key.strip())

    @property
    def user_agent(self) -> str:
        # OSM services reject generic user agents; identify the app and a contact URL.
        return f"AITravelPlanner/1.0 (+{self.contact_url})"

    @property
    def overpass_endpoints(self) -> list[str]:
        mirrors = [m.strip() for m in self.overpass_mirrors.split(",") if m.strip()]
        return [self.overpass_url, *mirrors]

    @property
    def sqlite_path(self) -> Path | None:
        if not self.database_url.startswith("sqlite"):
            return None
        raw = self.database_url.split("///", 1)[-1]
        path = Path(raw)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
