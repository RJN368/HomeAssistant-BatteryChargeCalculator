import unittest
from unittest.mock import AsyncMock
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)

import asyncio


class DummyEntry:
    entry_id = "dummy"
    options = {
        "octopus_api_key": "key",
        "octopus_account_number": "acc",
        "givenergy_api_serial_number": "serial",
        "givenergy_api_token": "token",
    }


class DummyHass:
    config = type("config", (), {"time_zone": "UTC"})

    def __init__(self):
        self.states = AsyncMock()
        self.services = AsyncMock()
        self.async_create_task = AsyncMock()


class TestCoordinatorDebounce(unittest.IsolatedAsyncioTestCase):
    async def test_async_refresh_reentrancy(self):
        # Setup
        hass = DummyHass()
        entry = DummyEntry()
        coordinator = BatteryChargeCoordinator(hass, entry)

        # Patch _debounced_refresh.async_lock to raise RuntimeError
        class DummyLock:
            async def __aenter__(self):
                raise RuntimeError("Debouncer lock is not re-entrant")

            async def __aexit__(self, exc_type, exc, tb):
                pass

        coordinator._debounced_refresh = type(
            "Debounced", (), {"async_lock": DummyLock()}
        )()
        # Should handle RuntimeError gracefully
        with self.assertRaises(RuntimeError):
            await coordinator.async_refresh()


if __name__ == "__main__":
    unittest.main()
