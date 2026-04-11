"""Thin aiohttp HTTPS client for the BCC ML Service.

Replaces the in-process ``MLPowerEstimator``.  All ML work runs in the
external Docker service; this client is responsible only for:

  - Calling ``POST /configure`` on startup to send credentials/physics config.
  - Calling ``POST /predict`` with a batch of planning slots.
  - Calling ``POST /retrain`` when the coordinator requests a retrain.
  - Calling ``GET /status`` to populate ML diagnostic sensors.

TLS certificate pinning
-----------------------
If ``tls_fingerprint`` is provided (colon-delimited hex SHA-256), the client
uses ``aiohttp.Fingerprint`` to verify the server certificate against the
stored digest rather than the system CA bundle.  This lets self-signed certs
work securely over a LAN — no CA needed.

If ``tls_fingerprint`` is empty, the client falls back to standard OS CA
verification (suitable when the service has a real certificate).
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)


class MLServiceClient:
    """Async HTTPS client for the BCC ML Service REST API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        tls_fingerprint: str = "",
        config: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            base_url: Full base URL, e.g. ``https://192.168.1.50:8765``.
            api_key: Bearer token for authentication.
            tls_fingerprint: Optional SHA-256 fingerprint (colon-delimited hex)
                for certificate pinning.  If empty, uses OS CA bundle.
            config: Optional credentials/physics config to send via ``/configure``.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tls_fingerprint = tls_fingerprint
        self._config = config or {}

        self._ssl: Any = self._build_ssl_context(tls_fingerprint)
        self._headers = {"Authorization": f"Bearer {api_key}"}

        self._status: dict[str, Any] = {}
        self._is_ready: bool = False
        self._state: str = "not_connected"

    # ------------------------------------------------------------------
    # SSL context
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ssl_context(fingerprint: str) -> Any:
        """Return an ``aiohttp.Fingerprint`` or ``True`` (OS CA verification)."""
        fp = fingerprint.replace(":", "").strip()
        if fp:
            return aiohttp.Fingerprint(bytes.fromhex(fp))
        return True  # standard CA verification

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Send POST /configure and GET /status; raises on connection failure."""
        await self._configure()
        await self.async_refresh_status()

    async def _configure(self) -> None:
        """POST /configure — send credentials and physics settings to the service."""
        async with aiohttp.ClientSession(
            timeout=_TIMEOUT, headers=self._headers
        ) as session:
            resp = await session.post(
                f"{self._base_url}/configure",
                json=self._config,
                ssl=self._ssl,
            )
            resp.raise_for_status()
            data = await resp.json()
            _LOGGER.debug("ML service /configure response: %s", data)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    async def async_predict_batch(
        self,
        slots: list[dict[str, Any]],
    ) -> list[float]:
        """POST /predict with all planning slots; returns corrected kWh list.

        Falls back to returning physics_kwh values unchanged on any error.
        """
        fallback = [float(s.get("physics_kwh", 0.0)) for s in slots]
        try:
            async with aiohttp.ClientSession(
                timeout=_TIMEOUT, headers=self._headers
            ) as session:
                resp = await session.post(
                    f"{self._base_url}/predict",
                    json={"slots": slots},
                    ssl=self._ssl,
                )
                if resp.status == 503:
                    _LOGGER.debug("ML service: model not ready (503)")
                    return fallback
                resp.raise_for_status()
                data = await resp.json()
                return list(data["corrected_kwh"])
        except Exception as exc:
            _LOGGER.warning("ML service predict_batch failed: %s", exc)
            return fallback

    # ------------------------------------------------------------------
    # Retrain
    # ------------------------------------------------------------------

    async def async_trigger_retrain(self) -> None:
        """POST /retrain — fire and forget; logs on failure."""
        try:
            async with aiohttp.ClientSession(
                timeout=_TIMEOUT, headers=self._headers
            ) as session:
                resp = await session.post(
                    f"{self._base_url}/retrain",
                    ssl=self._ssl,
                )
                resp.raise_for_status()
                _LOGGER.info("ML service: retrain accepted")
        except Exception as exc:
            _LOGGER.warning("ML service trigger_retrain failed: %s", exc)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def async_refresh_status(self) -> None:
        """GET /status and cache the result.  Logs on failure; never raises."""
        try:
            async with aiohttp.ClientSession(
                timeout=_TIMEOUT, headers=self._headers
            ) as session:
                resp = await session.get(
                    f"{self._base_url}/status",
                    ssl=self._ssl,
                )
                resp.raise_for_status()
                self._status = await resp.json()
                self._is_ready = bool(self._status.get("is_ready", False))
                self._state = str(self._status.get("state", "unknown"))
        except Exception as exc:
            _LOGGER.warning("ML service refresh_status failed: %s", exc)
            self._is_ready = False
            self._state = "service_unreachable"

    def get_status(self) -> dict[str, Any]:
        """Return the last-fetched status dict (sync, cached)."""
        return self._status

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True when the service reports a trained model is available."""
        return self._is_ready

    @property
    def state(self) -> str:
        """Last-known service state string (e.g. 'ready', 'training')."""
        return self._state
