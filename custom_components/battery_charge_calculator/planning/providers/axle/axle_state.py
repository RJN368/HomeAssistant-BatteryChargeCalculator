"""Axle source/cache state manager used by the coordinator."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .... import const
from ....axle_windows import AxleWindow, normalize_windows
from .axle_client import AxleClient
from .axle_client_error import AxleClientError
from .axle_planning_adjustment_snapshot import AxlePlanningAdjustmentSnapshot
from .axle_state_snapshot import AxleStateSnapshot


class AxleStateManager:
    """Encapsulate Axle cache and source/overlap logic."""

    def __init__(self, *, config_entry, hass) -> None:
        self._config_entry = config_entry
        self._hass = hass
        self._cache: dict = {
            "windows": [],
            "windows_signature": (),
            "windows_changed": False,
            "last_success_utc": None,
            "last_error": None,
            "source_status": const.AXLE_SOURCE_STATUS_UNAVAILABLE,
            "is_active": False,
            "suppression_reason": None,
            "last_transition_reason": None,
            "planning_adjustment_active": False,
            "slot_adjustment_kwh_total": 0.0,
            "required_export_energy_next_24h_kwh": 0.0,
        }

    @property
    def cache(self) -> dict:
        return self._cache

    def windows_changed(self) -> bool:
        """Return whether window signature changed since last clear."""
        return bool(self._cache.get("windows_changed", False))

    def clear_windows_changed(self) -> None:
        """Reset the windows-changed marker after a re-plan trigger."""
        self._cache["windows_changed"] = False

    def is_enabled(self) -> bool:
        return bool(
            self._config_entry.options.get(
                const.AXLE_ENABLED,
                const.DEFAULT_AXLE_ENABLED,
            )
        )

    def cache_age_seconds(self, now_utc: datetime | None = None) -> float | None:
        """Return Axle cache age in seconds, or None when never fetched."""
        last_success = self._cache.get("last_success_utc")
        if last_success is None:
            return None

        now_value = now_utc or datetime.now(timezone.utc)
        if now_value.tzinfo is None:
            now_value = now_value.replace(tzinfo=timezone.utc)

        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=timezone.utc)

        return max(0.0, (now_value - last_success).total_seconds())

    def evaluate_source_status(self, now_utc: datetime | None = None) -> str:
        """Evaluate Axle source freshness from cache state."""
        age_seconds = self.cache_age_seconds(now_utc)
        if age_seconds is None:
            return const.AXLE_SOURCE_STATUS_UNAVAILABLE

        poll_seconds = int(
            self._config_entry.options.get(
                const.AXLE_POLL_INTERVAL_SECONDS,
                const.DEFAULT_AXLE_POLL_INTERVAL_SECONDS,
            )
        )
        fresh_limit = poll_seconds * const.AXLE_FRESHNESS_MULTIPLIER

        if age_seconds <= fresh_limit:
            return const.AXLE_SOURCE_STATUS_FRESH

        if age_seconds <= const.AXLE_STALE_MAX_AGE_SECONDS:
            return const.AXLE_SOURCE_STATUS_STALE

        return const.AXLE_SOURCE_STATUS_UNAVAILABLE

    def _fail_safe_mode(self) -> str:
        return self._config_entry.options.get(
            const.AXLE_FAIL_SAFE_MODE,
            const.DEFAULT_AXLE_FAIL_SAFE_MODE,
        )

    def sync_runtime_state(self, *, now_utc: datetime) -> AxleStateSnapshot:
        """Update derived runtime state flags and return a typed snapshot."""
        source_status = self.evaluate_source_status(now_utc)
        active_window = self.overlapping_window(now_utc)

        self._cache["source_status"] = source_status
        self._cache["is_active"] = active_window is not None

        if active_window is not None:
            self._cache["suppression_reason"] = const.AXLE_SUPPRESSION_REASON_ACTIVE_WINDOW
        elif (
            source_status == const.AXLE_SOURCE_STATUS_UNAVAILABLE
            and self._fail_safe_mode() == const.AXLE_FAIL_SAFE_MODE_CLOSED
        ):
            self._cache["suppression_reason"] = (
                const.AXLE_SUPPRESSION_REASON_SOURCE_UNAVAILABLE_CLOSED
            )
        else:
            self._cache["suppression_reason"] = None

        return self.snapshot(now_utc=now_utc)

    def _is_export_intent(self, control_intent: str | None) -> bool:
        if control_intent is None:
            return False
        return str(control_intent).strip().lower() == "export"

    def _overlap_hours(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        window: AxleWindow,
    ) -> float:
        overlap_start = max(slot_start, window.start)
        overlap_end = min(slot_end, window.end)
        overlap_seconds = (overlap_end - overlap_start).total_seconds()
        if overlap_seconds <= 0:
            return 0.0
        return overlap_seconds / 3600.0

    def slot_export_adjustment_kwh(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        inverter_size_kw: float,
    ) -> float:
        """Compute required export obligation for a slot as kWh."""
        adjustment = 0.0
        for window in self._cache.get("windows", []):
            if not self._is_export_intent(window.control_intent):
                continue
            overlap_hours = self._overlap_hours(
                slot_start=slot_start,
                slot_end=slot_end,
                window=window,
            )
            adjustment += inverter_size_kw * overlap_hours
        return adjustment

    def slot_forced_action(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
    ) -> str | None:
        """Return forced slot action when an export-intent overlap exists."""
        for window in self._cache.get("windows", []):
            if not self._is_export_intent(window.control_intent):
                continue
            if (
                self._overlap_hours(
                    slot_start=slot_start,
                    slot_end=slot_end,
                    window=window,
                )
                > 0
            ):
                return "export"
        return None

    def windows_signature(
        self, windows: list[AxleWindow]
    ) -> tuple[tuple[str, str, str], ...]:
        """Build normalized immutable signature for change detection."""
        return tuple(
            (
                window.start.astimezone(timezone.utc).isoformat(),
                window.end.astimezone(timezone.utc).isoformat(),
                str(window.control_intent or "").strip().lower(),
            )
            for window in windows
        )

    def set_windows(self, windows: list[AxleWindow]) -> bool:
        """Store normalized windows and mark signature changes."""
        signature = self.windows_signature(windows)
        previous_signature = self._cache.get("windows_signature", ())
        changed = signature != previous_signature

        self._cache["windows"] = windows
        self._cache["windows_signature"] = signature
        self._cache["windows_changed"] = bool(
            self._cache.get("windows_changed", False) or changed
        )
        return changed

    def redact_text(self, text: str) -> str:
        """Redact configured Axle token from free-form text defensively."""
        token = str(self._config_entry.options.get(const.AXLE_API_TOKEN, "")).strip()
        if token:
            return text.replace(token, "***REDACTED***")
        return text

    async def async_refresh_source_state(self, *, now_utc: datetime) -> None:
        """Refresh Axle source cache snapshot from upstream endpoint."""
        if not self.is_enabled():
            return

        token = str(self._config_entry.options.get(const.AXLE_API_TOKEN, "")).strip()
        if not token:
            self.set_windows([])
            self._cache["last_error"] = "Axle enabled but API token is not configured"
            self._cache["source_status"] = const.AXLE_SOURCE_STATUS_UNAVAILABLE
            return

        timeout_seconds = int(
            self._config_entry.options.get(
                const.AXLE_REQUEST_TIMEOUT_SECONDS,
                const.DEFAULT_AXLE_REQUEST_TIMEOUT_SECONDS,
            )
        )
        client = AxleClient(
            token,
            request_timeout_seconds=timeout_seconds,
        )
        session = async_get_clientsession(self._hass)

        try:
            event = await client.async_fetch_event(session)
        except AxleClientError as err:
            self._cache["last_error"] = self.redact_text(str(err))
            self._cache["source_status"] = self.evaluate_source_status(now_utc=now_utc)
            return

        windows = normalize_windows([event] if event is not None else [])
        self.set_windows(windows)
        self._cache["last_success_utc"] = now_utc
        self._cache["last_error"] = None
        self._cache["source_status"] = self.evaluate_source_status(now_utc=now_utc)

    def overlapping_window(self, now_utc: datetime) -> AxleWindow | None:
        """Return overlapping Axle window at now, using half-open [start, end)."""
        for window in self._cache.get("windows", []):
            if window.start <= now_utc < window.end:
                return window
        return None

    def set_planning_adjustments(self, slot_adjustment_kwh_total: float) -> None:
        """Store derived planning adjustment diagnostics."""
        rounded = round(slot_adjustment_kwh_total, 4)
        self._cache["slot_adjustment_kwh_total"] = rounded
        self._cache["required_export_energy_next_24h_kwh"] = rounded
        self._cache["planning_adjustment_active"] = rounded > 0

    def snapshot(self, *, now_utc: datetime | None = None) -> AxleStateSnapshot:
        """Return a typed snapshot for consumers that avoid raw dict access."""
        effective_now = now_utc or datetime.now(timezone.utc)
        active_window = self.overlapping_window(effective_now)
        return AxleStateSnapshot(
            source_status=self._cache.get(
                "source_status",
                const.AXLE_SOURCE_STATUS_UNAVAILABLE,
            ),
            is_active=bool(self._cache.get("is_active", False)),
            suppression_reason=self._cache.get("suppression_reason"),
            last_transition_reason=self._cache.get("last_transition_reason"),
            last_error=self._cache.get("last_error"),
            active_window_start=active_window.start.isoformat()
            if active_window is not None
            else None,
            active_window_end=active_window.end.isoformat()
            if active_window is not None
            else None,
            cache_age_seconds=self.cache_age_seconds(now_utc=effective_now),
            planning=AxlePlanningAdjustmentSnapshot(
                active=bool(self._cache.get("planning_adjustment_active", False)),
                slot_adjustment_kwh_total=float(
                    self._cache.get("slot_adjustment_kwh_total", 0.0)
                ),
                required_export_energy_next_24h_kwh=float(
                    self._cache.get("required_export_energy_next_24h_kwh", 0.0)
                ),
            ),
        )
