"""One shared, retrying httpx client for every outbound provider call."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger

log = get_logger("http_client")

_client: httpx.AsyncClient | None = None

RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds, connect=10.0),
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json",
                "Accept-Language": settings.wikipedia_lang,
            },
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


@retry(
    retry=retry_if_exception_type(RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.6, min=0.6, max=4),
    reraise=True,
)
async def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    client = get_client()
    response = await client.request(method, url, **kwargs)
    response.raise_for_status()
    return response


async def get_json(url: str, *, provider: str = "upstream", **kwargs: Any) -> Any:
    """GET and decode JSON, converting every transport failure into UpstreamError."""
    try:
        response = await _request("GET", url, **kwargs)
        return response.json()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "upstream_status", provider=provider, url=url, status=exc.response.status_code
        )
        raise UpstreamError(
            f"{provider} returned HTTP {exc.response.status_code}.",
            details={"provider": provider, "status": exc.response.status_code},
        ) from exc
    except RETRYABLE as exc:
        log.warning("upstream_unreachable", provider=provider, url=url, error=str(exc))
        raise UpstreamError(f"{provider} is unreachable.", details={"provider": provider}) from exc
    except ValueError as exc:  # malformed JSON
        raise UpstreamError(f"{provider} returned a malformed response.") from exc


async def post_json(url: str, *, provider: str = "upstream", **kwargs: Any) -> Any:
    try:
        response = await _request("POST", url, **kwargs)
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise UpstreamError(
            f"{provider} returned HTTP {exc.response.status_code}.",
            details={"provider": provider},
        ) from exc
    except RETRYABLE as exc:
        raise UpstreamError(f"{provider} is unreachable.", details={"provider": provider}) from exc


async def safe_get_json(url: str, *, provider: str = "upstream", default: Any = None, **kwargs: Any) -> Any:
    """Like `get_json` but degrades to `default` — for enrichment that may fail."""
    try:
        return await get_json(url, provider=provider, **kwargs)
    except UpstreamError:
        return default
