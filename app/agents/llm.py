"""Gemini model factory.

Two tiers are used on purpose: the fast model does extraction, classification and
short-form writing (most of the graph), while the pro model handles the long
narrative work where quality is actually visible to the traveller. Every model is
built with retries and a bounded output size so a single bad generation cannot
stall a request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import LLMUnavailableError
from app.core.logging import get_logger
from app.core.tracing import configure_tracing

log = get_logger("llm")

T = TypeVar("T", bound=BaseModel)

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-2.0-flash-lite",
]


def _is_quota_error(exc: Exception) -> bool:
    err_str = str(exc).lower()
    return any(k in err_str for k in ("429", "resourceexhausted", "quota", "rate limit", "rate_limit"))


@lru_cache(maxsize=16)
def get_llm(
    *,
    model_override: str | None = None,
    pro: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Return a cached Gemini chat model. Raises when no API key is configured."""
    if not settings.llm_enabled:
        raise LLMUnavailableError(
            "GOOGLE_API_KEY is not set, so the planning agents cannot run. "
            "Add a key from https://aistudio.google.com/apikey to your .env file."
        )
    configure_tracing()

    from langchain_google_genai import ChatGoogleGenerativeAI

    if model_override:
        model_name = model_override
    else:
        model_name = settings.gemini_pro_model if pro else settings.gemini_model

    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=settings.google_api_key,
        temperature=settings.gemini_temperature if temperature is None else temperature,
        max_output_tokens=max_tokens or settings.gemini_max_output_tokens,
        max_retries=2,
        timeout=35,
        convert_system_message_to_human=False,
    )


def structured(
    schema: type[T],
    *,
    model_override: str | None = None,
    pro: bool = False,
    temperature: float | None = None,
):
    """A runnable that returns a validated Pydantic object instead of free text."""
    return get_llm(
        model_override=model_override, pro=pro, temperature=temperature
    ).with_structured_output(schema)


async def ainvoke_structured(
    schema: type[T],
    messages: list[Any],
    *,
    pro: bool = False,
    temperature: float | None = None,
    config: dict | None = None,
    fallback: T | None = None,
) -> T:
    """Invoke a structured call, falling back rather than failing the whole plan.

    A single node returning nothing is recoverable - the graph degrades to the
    data it already has from Wikipedia and OSM. Losing the entire request because
    one generation tripped a safety filter or 429 rate limit is not.
    """
    primary_model = settings.gemini_pro_model if pro else settings.gemini_model
    try:
        result = await structured(schema, pro=pro, temperature=temperature).ainvoke(
            messages, config=config or {}
        )
        if result is None:
            raise ValueError("model returned no structured output")
        return result  # type: ignore[return-value]
    except LLMUnavailableError:
        raise
    except Exception as exc:
        log.warning(
            "structured_call_attempt_failed",
            model=primary_model,
            schema=schema.__name__,
            error=str(exc),
        )

        # Failover to secondary models if 429 quota or pro error occurs
        if _is_quota_error(exc) or pro:
            for fallback_model in FALLBACK_MODELS:
                if fallback_model.lower() == primary_model.lower():
                    continue
                try:
                    log.info(
                        "failing_over_to_model",
                        schema=schema.__name__,
                        target_model=fallback_model,
                    )
                    result = await structured(
                        schema, model_override=fallback_model, temperature=temperature
                    ).ainvoke(messages, config=config or {})
                    if result is not None:
                        log.info(
                            "failover_model_succeeded",
                            schema=schema.__name__,
                            model=fallback_model,
                        )
                        return result  # type: ignore[return-value]
                except Exception as inner_exc:
                    log.warning(
                        "failover_model_failed",
                        model=fallback_model,
                        error=str(inner_exc),
                    )
                    continue

        if fallback is not None:
            log.info("using_structured_fallback", schema=schema.__name__)
            return fallback
        raise


async def ainvoke_text(
    messages: list[Any],
    *,
    pro: bool = False,
    temperature: float | None = None,
    config: dict | None = None,
    fallback: str = "",
) -> str:
    primary_model = settings.gemini_pro_model if pro else settings.gemini_model
    try:
        response = await get_llm(pro=pro, temperature=temperature).ainvoke(
            messages, config=config or {}
        )
        content = response.content
        if isinstance(content, list):  # Gemini can return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content).strip()
    except LLMUnavailableError:
        raise
    except Exception as exc:
        log.warning("text_call_attempt_failed", model=primary_model, error=str(exc))

        # Failover to secondary models if 429 quota or pro error occurs
        if _is_quota_error(exc) or pro:
            for fallback_model in FALLBACK_MODELS:
                if fallback_model.lower() == primary_model.lower():
                    continue
                try:
                    log.info("failing_over_text_to_model", target_model=fallback_model)
                    response = await get_llm(
                        model_override=fallback_model, temperature=temperature
                    ).ainvoke(messages, config=config or {})
                    content = response.content
                    if isinstance(content, list):
                        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                    text_res = str(content).strip()
                    if text_res:
                        log.info("failover_text_succeeded", model=fallback_model)
                        return text_res
                except Exception as inner_exc:
                    log.warning(
                        "failover_text_model_failed",
                        model=fallback_model,
                        error=str(inner_exc),
                    )
                    continue

        if fallback:
            log.info("using_text_fallback")
            return fallback
        raise
