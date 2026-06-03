"""Unit tests for Axle event client."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.battery_charge_calculator.axle_client import (
    AxleClient,
    AxleClientError,
)


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: Any = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self._raise_error = raise_error

    def raise_for_status(self) -> None:
        if self._raise_error is not None:
            raise self._raise_error

    async def json(self, content_type: str | None = None) -> Any:
        return self._payload


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSession:
    def __init__(self, contexts: list[_FakeRequestContext]) -> None:
        self._contexts = contexts
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: aiohttp.ClientTimeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self._contexts.pop(0)


@pytest.mark.asyncio
async def test_fetch_event_sends_expected_headers_and_returns_event() -> None:
    payload = {
        "start_time": "2026-05-28T10:00:00Z",
        "end_time": "2026-05-28T10:30:00Z",
        "import_export": "export",
        "updated_at": "2026-05-28T09:58:00Z",
    }
    session = _FakeSession([_FakeRequestContext(response=_FakeResponse(payload=payload))])
    client = AxleClient("secret-token")

    event = await client.async_fetch_event(session)

    assert event is not None
    assert event.start_time == payload["start_time"]
    assert event.end_time == payload["end_time"]
    assert event.control_intent == payload["import_export"]
    assert event.source_updated_at == payload["updated_at"]

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["headers"]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_fetch_event_handles_empty_null_and_missing_start_as_no_event() -> None:
    no_event_payloads = [None, {}, [], "", {"end_time": "2026-05-28T10:30:00Z"}]

    for payload in no_event_payloads:
        session = _FakeSession(
            [_FakeRequestContext(response=_FakeResponse(status=200, payload=payload))]
        )
        client = AxleClient("abc")
        event = await client.async_fetch_event(session)
        assert event is None


@pytest.mark.asyncio
async def test_fetch_event_retries_on_timeout_then_succeeds() -> None:
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    payload = {
        "start_time": "2026-05-28T10:00:00Z",
        "end_time": "2026-05-28T10:30:00Z",
    }
    session = _FakeSession(
        [
            _FakeRequestContext(error=asyncio.TimeoutError()),
            _FakeRequestContext(response=_FakeResponse(payload=payload)),
        ]
    )
    client = AxleClient("abc", sleep_fn=_sleep)

    with patch("custom_components.battery_charge_calculator.axle_client.random.uniform", return_value=0.0):
        event = await client.async_fetch_event(session)

    assert event is not None
    assert len(session.calls) == 2
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_fetch_event_error_redacts_token() -> None:
    token = "top-secret-token"
    session = _FakeSession(
        [
            _FakeRequestContext(
                response=_FakeResponse(
                    raise_error=aiohttp.ClientError(
                        f"Authorization: Bearer {token} request failed"
                    )
                )
            )
        ]
    )
    client = AxleClient(token, max_retries=0)

    with pytest.raises(AxleClientError) as err:
        await client.async_fetch_event(session)

    message = str(err.value)
    assert token not in message
    assert "Bearer ***REDACTED***" in message
