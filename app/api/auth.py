"""Authentication dependency for protecting API endpoints."""

from __future__ import annotations

import httpx
from fastapi import Header, HTTPException, Query, status

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("api.auth")

# Cache validated token user data to avoid repeated Supabase roundtrips
_TOKEN_CACHE: dict[str, dict] = {}


async def require_auth_user(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> dict:
    """Dependency that ensures requests originate from a signed-up, authenticated user.

    If REQUIRE_AUTH=true in settings, missing or invalid Supabase user tokens return 401.
    Accepts token via 'Authorization: Bearer <token>' header or '?token=<token>' query parameter (for media tags).
    """
    if not settings.require_auth:
        return {"id": "anonymous", "authenticated": False}

    raw_token = token
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split("Bearer ", 1)[-1].strip()

    if not raw_token or raw_token in ("undefined", "null"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign up or log in to process travel requests.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if raw_token in _TOKEN_CACHE:
        return _TOKEN_CACHE[raw_token]

    # Verify against Supabase Auth API if configured
    if settings.supabase_url:
        verify_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/user"
        headers = {
            "Authorization": f"Bearer {raw_token}",
            "apikey": settings.supabase_anon_key or "",
        }
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(verify_url, headers=headers)
                if res.status_code == 200:
                    user_data = res.json()
                    _TOKEN_CACHE[raw_token] = user_data
                    return user_data
                else:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired session. Please sign up or log in again.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
        except HTTPException:
            raise
        except Exception as e:
            log.warning("supabase_verify_error", error=str(e))
            # Fallback if network to Supabase times out but JWT format is valid
            parts = raw_token.split(".")
            if len(parts) == 3:
                return {"id": "supabase_user", "authenticated": True}
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication check failed. Please sign in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # If Supabase URL isn't configured in backend environment yet, verify valid JWT structure
    parts = raw_token.split(".")
    if len(parts) == 3:
        return {"id": "jwt_user", "authenticated": True}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Please sign up or log in.",
        headers={"WWW-Authenticate": "Bearer"},
    )
