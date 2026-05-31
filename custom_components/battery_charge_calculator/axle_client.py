"""Axle VPP event API client."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

from . import const

_LOGGER = logging.getLogger(__name__)

_REDACTED_TOKEN = "***REDACTED***"
_BEARER_PATTERN = re.compile(r"(Bearer\s+)([^\s]+)", flags=re.IGNORECASE)


@dataclass(slots=True)
class AxleEvent:
    """Normalized event payload returned by Axle endpoint."""

    start_time: str
    end_time: str | None
    control_intent: str | None
    source_updated_at: str | None


class AxleClientError(RuntimeError):
    """Raised when Axle event retrieval fails after retries."""


class AxleClient:
    """Thin async client for the Axle home-assistant event endpoint."""

    def __init__(
        self,
        api_token: str,
        *,
        endpoint: str = const.AXLE_EVENT_ENDPOINT,
        request_timeout_seconds: int = const.DEFAULT_AXLE_REQUEST_TIMEOUT_SECONDS,
        max_retries: int = const.AXLE_MAX_RETRIES,
        retry_base_delay_seconds: float = const.AXLE_RETRY_BASE_DELAY_SECONDS,
        retry_jitter_seconds: float = const.AXLE_RETRY_JITTER_SECONDS,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_token = api_token
        self._endpoint = endpoint
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_jitter_seconds = retry_jitter_seconds
        self._sleep_fn = sleep_fn

    async def async_fetch_event(
        self, session: aiohttp.ClientSession
    ) -> AxleEvent | None:
        """Fetch the current/upcoming Axle event.

        Returns None for no-event payloads (empty/null/missing start_time).
        """
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Accept": const.AXLE_EVENT_ACCEPT_HEADER,
        }
        timeout = aiohttp.ClientTimeout(total=self._request_timeout_seconds)
        total_attempts = self._max_retries + 1

        for attempt in range(total_attempts):
            try:
                async with session.get(
                    self._endpoint,
                    headers=headers,
                    timeout=timeout,
                ) as response:
                    if response.status == 204:
                        return None

                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                    return _normalize_axle_event(payload)
            except Exception as err:
                retryable = _is_retryable_error(err)
                is_last_attempt = attempt >= total_attempts - 1
                if not retryable or is_last_attempt:
                    sanitized = _redact_sensitive(str(err), self._api_token)
                    raise AxleClientError(
                        f"Failed to fetch Axle event after {attempt + 1} attempt(s): {sanitized}"
                    ) from err

                delay_seconds = self._retry_backoff_delay(attempt)
                _LOGGER.debug(
                    "Axle event fetch failed (attempt %s/%s): %s; retrying in %.2fs",
                    attempt + 1,
                    total_attempts,
                    _redact_sensitive(str(err), self._api_token),
                    delay_seconds,
                )
                await self._sleep_fn(delay_seconds)

        return None

    def _retry_backoff_delay(self, attempt: int) -> float:
        """Return exponential backoff with jitter for a zero-based attempt."""
        base = self._retry_base_delay_seconds * (2**attempt)
        jitter = random.uniform(0.0, self._retry_jitter_seconds)
        return base + jitter


def _is_retryable_error(err: Exception) -> bool:
    if isinstance(err, asyncio.TimeoutError):
        return True

    if isinstance(err, aiohttp.ClientResponseError):
        return err.status == 429 or err.status >= 500

    return isinstance(err, aiohttp.ClientError)


def _normalize_axle_event(payload: Any) -> AxleEvent | None:
    """Normalize raw payload to AxleEvent or None for no-event responses."""
    if payload in (None, "", [], {}):
        return None

    if not isinstance(payload, dict):
        return None

    start_time = payload.get("start_time")
    if not start_time:
        return None

    return AxleEvent(
        start_time=str(start_time),
        end_time=_as_optional_str(payload.get("end_time")),
        control_intent=_as_optional_str(payload.get("import_export")),
        source_updated_at=_as_optional_str(payload.get("updated_at")),
    )


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _redact_sensitive(text: str, token: str) -> str:
    """Redact bearer values and raw token string from free-form text."""
    redacted = text
    if token:
        redacted = redacted.replace(token, _REDACTED_TOKEN)
    redacted = _BEARER_PATTERN.sub(rf"\1{_REDACTED_TOKEN}", redacted)
    return redacted
