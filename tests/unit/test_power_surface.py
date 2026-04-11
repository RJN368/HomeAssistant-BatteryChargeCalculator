"""Tests for ML power-surface computation and serialisation.

These tests were written to diagnose the 'Surface data is missing' error in
``MLPowerSurfaceSensor``.  They cover every step of the pipeline that converts
a trained model into the surface data the sensor displays:

    train_power_model()
        → compute_power_surface()
            → TrainedModel.power_surface (dict)
                → _serialise_power_surface()
                    → get_status()["power_surface"]
                        → MLPowerSurfaceSensor._update_attributes()

Running with ``-s`` will print the exact exception if any step silently fails.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_model(
    n_samples: int = 3000,
    training_rmse: float = 0.73,
    with_octopus: bool = False,
) -> Any:
    """Return a TrainedModel whose estimator is a MagicMock.

    The mock estimator returns all-zero corrections so the surface will be
    all-zero kWh (correct — pure physics with no physics_calc = 0).
    """
    from app.ml.model_trainer import (
        FEATURE_COLUMNS,
        FEATURE_COLUMNS_WITH_OCTOPUS,
        TrainedModel,
    )

    feature_cols = FEATURE_COLUMNS_WITH_OCTOPUS if with_octopus else FEATURE_COLUMNS
    n_total = 16 * 52 * 48  # temps × weeks × slots

    mock_est = MagicMock()
    mock_est.predict.return_value = np.zeros(n_total)

    return TrainedModel(
        estimator=mock_est,
        model_type="hist_gbr",
        feature_columns=feature_cols,
        trained_at=datetime(2026, 4, 11, 18, 48, 0, tzinfo=timezone.utc),
        n_training_samples=n_samples,
        training_rmse=training_rmse,
        blend_weight=1.0,
        trained_with_octopus_feature=with_octopus,
        slot_residual_std=0.5,
    )


# ---------------------------------------------------------------------------
# 1. compute_power_surface — shape and structure
# ---------------------------------------------------------------------------


class TestComputePowerSurfaceShape:
    """compute_power_surface must return a correctly shaped dict."""

    def test_returns_dict(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert set(result.keys()) >= {"temps", "weeks", "z"}

    def test_temps_16_points(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert len(result["temps"]) == 16

    def test_temps_range_minus10_to_20(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        expected = [float(t) for t in range(-10, 22, 2)]
        assert result["temps"] == expected

    def test_weeks_52_entries(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert len(result["weeks"]) == 52

    def test_weeks_values(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert result["weeks"] == list(range(1, 53))

    def test_z_shape_52_rows_16_cols(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        z = result["z"]
        assert len(z) == 52, f"Expected 52 rows, got {len(z)}"
        for i, row in enumerate(z):
            assert len(row) == 16, f"Row {i}: expected 16 cols, got {len(row)}"

    def test_z_values_are_python_floats(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        for row in result["z"]:
            for val in row:
                assert isinstance(val, float), f"Expected float, got {type(val)}: {val}"

    def test_z_values_non_negative(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        for row in result["z"]:
            for val in row:
                assert val >= 0.0, f"Negative value {val} in surface"


# ---------------------------------------------------------------------------
# 2. compute_power_surface — physics_calc=None behaviour
# ---------------------------------------------------------------------------


class TestComputePowerSurfaceNoPhysics:
    """Behaviour when no physics calculator is configured (heating_type=none)."""

    def test_no_physics_calc_does_not_raise(self):
        """DIAGNOSTIC: if this raises, it IS the root cause of 'Surface data missing'."""
        from app.ml.model_trainer import compute_power_surface

        try:
            result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        except Exception as exc:
            pytest.fail(
                f"compute_power_surface raised when physics_calc=None:\n"
                f"  {type(exc).__name__}: {exc}"
            )

    def test_z_physics_is_none_without_physics_calc(self):
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert result.get("z_physics") is None

    def test_dict_is_non_empty(self):
        """The returned dict must be truthy so _serialise_power_surface works."""
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert result, (
            "compute_power_surface returned a falsy value — serialiser will return None"
        )

    def test_result_z_key_is_truthy(self):
        """power_surface['z'] must be truthy for _serialise_power_surface to pass it through."""
        from app.ml.model_trainer import compute_power_surface

        result = compute_power_surface(_make_minimal_model(), physics_calc=None)
        assert result.get("z"), "z key is falsy — _serialise_power_surface will drop it"

    def test_low_sample_count_zero_surface(self):
        """n_samples < 500 → blend_weight=0 → all corrections irrelevant → cells = 0."""
        from app.ml.model_trainer import compute_power_surface

        model = _make_minimal_model(n_samples=100)
        result = compute_power_surface(model, physics_calc=None)
        for row in result["z"]:
            for val in row:
                assert val == pytest.approx(0.0, abs=1e-6)

    def test_full_blend_weight_positive_correction(self):
        """blend_weight=1, correction=+0.5/slot → daily = 48×0.5 = 24.0 kWh."""
        from app.ml.model_trainer import compute_power_surface

        model = _make_minimal_model(n_samples=3000, training_rmse=1.0)
        # All corrections = +0.5 kWh/slot; cap = 2×1.0 = 2.0 so not capped
        model.estimator.predict.return_value = np.full(16 * 52 * 48, 0.5)
        result = compute_power_surface(model, physics_calc=None)
        for row in result["z"]:
            for val in row:
                assert val == pytest.approx(24.0, abs=0.01)


# ---------------------------------------------------------------------------
# 3. compute_power_surface — with physics calc
# ---------------------------------------------------------------------------


class TestComputePowerSurfaceWithPhysics:
    def test_z_physics_populated(self):
        from app.ml.model_trainer import compute_power_surface

        model = _make_minimal_model()
        mock_physics = MagicMock()
        mock_physics.from_temp_and_time.return_value = 0.1  # 0.1 kWh/slot
        result = compute_power_surface(model, physics_calc=mock_physics)
        assert result["z_physics"] is not None
        assert len(result["z_physics"]) == 52

    def test_z_physics_shape(self):
        from app.ml.model_trainer import compute_power_surface

        model = _make_minimal_model()
        mock_physics = MagicMock()
        mock_physics.from_temp_and_time.return_value = 0.1
        result = compute_power_surface(model, physics_calc=mock_physics)
        for row in result["z_physics"]:
            assert len(row) == 16

    def test_z_physics_values_sum_to_48_x_slot(self):
        """0.1 kWh/slot × 48 slots = 4.8 kWh/day."""
        from app.ml.model_trainer import compute_power_surface

        model = _make_minimal_model()
        mock_physics = MagicMock()
        mock_physics.from_temp_and_time.return_value = 0.1
        result = compute_power_surface(model, physics_calc=mock_physics)
        for row in result["z_physics"]:
            for val in row:
                assert val == pytest.approx(4.8, abs=0.01)


# ---------------------------------------------------------------------------
# 4. compute_power_surface — octopus feature column
# ---------------------------------------------------------------------------


class TestComputePowerSurfaceOctopus:
    def test_octopus_model_does_not_raise(self):
        from app.ml.model_trainer import (
            FEATURE_COLUMNS_WITH_OCTOPUS,
            compute_power_surface,
        )

        model = _make_minimal_model(with_octopus=True)
        model.estimator.predict.return_value = np.zeros(16 * 52 * 48)
        try:
            result = compute_power_surface(model, physics_calc=None)
        except Exception as exc:
            pytest.fail(
                f"compute_power_surface raised for octopus model:\n"
                f"  {type(exc).__name__}: {exc}"
            )
        assert len(result["z"]) == 52


# ---------------------------------------------------------------------------
# 5. _serialise_power_surface — all paths
# ---------------------------------------------------------------------------


class TestSerialisePowerSurface:
    """Tests for the private _serialise_power_surface helper in estimator.py."""

    def _get_fn(self):
        from app.estimator import _serialise_power_surface

        return _serialise_power_surface

    def test_none_model_returns_none(self):
        fn = self._get_fn()
        assert fn(None) is None

    def test_empty_power_surface_returns_none(self):
        """
        CRITICAL: model.power_surface = {} (default) must return None.
        This is intentional — the sensor should show 'unavailable' until surface is computed.
        But it means compute_power_surface MUST run successfully during training.
        """
        fn = self._get_fn()
        model = _make_minimal_model()
        model.power_surface = {}
        result = fn(model)
        assert result is None, (
            "Empty power_surface {} should return None — "
            "this confirms the sensor will show unavailable if compute_power_surface fails"
        )

    def test_populated_dict_returned_directly(self):
        fn = self._get_fn()
        model = _make_minimal_model()
        surface = {
            "temps": [float(t) for t in range(-10, 22, 2)],
            "weeks": list(range(1, 53)),
            "z": [[1.0] * 16 for _ in range(52)],
            "z_physics": None,
        }
        model.power_surface = surface
        result = fn(model)
        assert result is surface  # same object returned

    def test_populated_dict_has_z(self):
        fn = self._get_fn()
        model = _make_minimal_model()
        model.power_surface = {
            "temps": [0.0, 2.0],
            "weeks": [1, 2],
            "z": [[1.0, 2.0], [3.0, 4.0]],
            "z_physics": None,
        }
        result = fn(model)
        assert result is not None
        assert result["z"] == [[1.0, 2.0], [3.0, 4.0]]

    def test_dict_without_z_returns_none(self):
        """Surface dict with an empty z list is falsy at the ps.get('z') check."""
        fn = self._get_fn()
        model = _make_minimal_model()
        model.power_surface = {"temps": [], "weeks": [], "z": [], "z_physics": None}
        result = fn(model)
        # z = [] is falsy, so the guard `if isinstance(ps, dict) and ps.get("z"):` fails
        assert result is None


# ---------------------------------------------------------------------------
# 6. Full pipeline: compute_power_surface → assign → serialise
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    """Tests the exact sequence that _do_training executes."""

    def test_power_surface_assigned_and_serialised(self):
        """
        Simulates the _do_training sequence:
            trained_model.power_surface = compute_power_surface(model, physics_calc)
            result = _serialise_power_surface(model)
        After this, result must NOT be None.
        """
        from app.ml.model_trainer import compute_power_surface
        from app.estimator import _serialise_power_surface

        model = _make_minimal_model(n_samples=3000)

        # Step 1: compute surface
        surface = compute_power_surface(model, physics_calc=None)
        assert surface, "compute_power_surface returned empty/falsy"

        # Step 2: assign to model (as _do_training does)
        model.power_surface = surface

        # Step 3: serialise (as get_status does)
        result = _serialise_power_surface(model)
        assert result is not None, (
            "_serialise_power_surface returned None even though compute_power_surface succeeded.\n"
            f"power_surface type: {type(model.power_surface)}, value keys: {list(model.power_surface.keys()) if isinstance(model.power_surface, dict) else 'N/A'}"
        )

    def test_serialised_surface_has_correct_shape(self):
        from app.ml.model_trainer import compute_power_surface
        from app.estimator import _serialise_power_surface

        model = _make_minimal_model(n_samples=3000)
        model.power_surface = compute_power_surface(model, physics_calc=None)
        result = _serialise_power_surface(model)

        assert result is not None
        assert len(result["temps"]) == 16
        assert len(result["weeks"]) == 52
        assert len(result["z"]) == 52
        assert all(len(row) == 16 for row in result["z"])

    def test_get_status_includes_power_surface(self):
        """
        BccMlEstimator.get_status() must return a non-None power_surface
        when the model has a populated power_surface dict.
        """
        from app.ml.model_trainer import compute_power_surface
        from app.estimator import BccMlEstimator

        estimator = BccMlEstimator()
        model = _make_minimal_model(n_samples=3000)
        model.power_surface = compute_power_surface(model, physics_calc=None)

        # Inject the model directly (bypassing training)
        estimator._model = model
        estimator._state = "ready"

        status = estimator.get_status()
        ps = status.get("power_surface")
        assert ps is not None, (
            "get_status() returned power_surface=None even with populated model.\n"
            f"model.power_surface keys: {list(model.power_surface.keys())}"
        )
        assert len(ps["z"]) == 52
        assert len(ps["z"][0]) == 16

    def test_get_status_no_surface_returns_none(self):
        """If power_surface is empty (training failed), get_status returns None for it."""
        from app.estimator import BccMlEstimator

        estimator = BccMlEstimator()
        model = _make_minimal_model(n_samples=3000)
        model.power_surface = {}  # default — as if compute_power_surface was never called

        estimator._model = model
        estimator._state = "ready"

        status = estimator.get_status()
        assert status.get("power_surface") is None, (
            "Expected None for power_surface when model.power_surface={}, "
            f"got {status.get('power_surface')}"
        )


# ---------------------------------------------------------------------------
# 7. Sensor-side check: what the HA sensor sees
# ---------------------------------------------------------------------------


class TestSensorSurfaceCheck:
    """Mirrors the exact check in MLPowerSurfaceSensor._update_attributes."""

    def _surface_is_valid(self, surface: dict | None) -> bool:
        """Replicates: if not surface or not surface.get('z')."""
        return bool(surface and surface.get("z"))

    def test_none_surface_invalid(self):
        assert not self._surface_is_valid(None)

    def test_empty_dict_invalid(self):
        assert not self._surface_is_valid({})

    def test_z_empty_list_invalid(self):
        assert not self._surface_is_valid(
            {"temps": [], "weeks": [], "z": [], "z_physics": None}
        )

    def test_populated_surface_valid(self):
        from app.ml.model_trainer import compute_power_surface
        from app.estimator import _serialise_power_surface

        model = _make_minimal_model(n_samples=3000)
        model.power_surface = compute_power_surface(model, physics_calc=None)
        surface = _serialise_power_surface(model)
        assert self._surface_is_valid(surface), (
            f"Full pipeline produced an invalid surface.\nsurface={surface}"
        )


# ---------------------------------------------------------------------------
# 8. async_start migration: old model loaded from disk without power_surface
# ---------------------------------------------------------------------------


class TestAsyncStartMigration:
    """
    ROOT CAUSE TEST: this is the scenario that causes 'Surface data is missing'.

    When the Docker container starts and loads an old model.pkl (trained before
    compute_power_surface was added), model.power_surface == {}.
    async_start() must detect this and compute the surface before the service
    becomes ready, so get_status() immediately includes the surface.
    """

    @pytest.mark.asyncio
    async def test_async_start_computes_surface_for_old_model(self):
        """async_start must populate power_surface on an old surfaceless model."""
        from unittest.mock import AsyncMock, patch
        from app.estimator import BccMlEstimator

        old_model = _make_minimal_model(n_samples=3000)
        old_model.power_surface = {}  # as loaded from old model.pkl

        estimator = BccMlEstimator()

        with (
            patch("app.estimator.load_model", return_value=old_model),
            patch("app.estimator.needs_retrain", return_value=False),
            patch("app.estimator.save_model") as mock_save,
        ):
            await estimator.async_start()

        assert estimator._model is old_model
        assert estimator._state == "ready"
        assert old_model.power_surface, (
            "async_start did not compute power_surface for an old model.\n"
            "This is the root cause of 'Surface data is missing' on container restart."
        )
        assert old_model.power_surface.get("z"), "power_surface['z'] is empty"
        assert len(old_model.power_surface["z"]) == 52
        mock_save.assert_called_once_with(old_model)

    @pytest.mark.asyncio
    async def test_async_start_skips_surface_if_already_computed(self):
        """async_start must NOT recompute the surface if it is already populated."""
        from unittest.mock import patch
        from app.estimator import BccMlEstimator

        model = _make_minimal_model(n_samples=3000)
        from app.ml.model_trainer import compute_power_surface

        model.power_surface = compute_power_surface(model, physics_calc=None)
        original_surface = model.power_surface

        estimator = BccMlEstimator()

        with (
            patch("app.estimator.load_model", return_value=model),
            patch("app.estimator.needs_retrain", return_value=False),
            patch("app.estimator.save_model") as mock_save,
        ):
            await estimator.async_start()

        # Surface should not have been recomputed or re-saved
        assert model.power_surface is original_surface
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_status_has_surface_after_async_start_with_old_model(self):
        """get_status() must return surface data after async_start migrates old model."""
        from unittest.mock import patch
        from app.estimator import BccMlEstimator

        old_model = _make_minimal_model(n_samples=3000)
        old_model.power_surface = {}

        estimator = BccMlEstimator()

        with (
            patch("app.estimator.load_model", return_value=old_model),
            patch("app.estimator.needs_retrain", return_value=False),
            patch("app.estimator.save_model"),
        ):
            await estimator.async_start()

        status = estimator.get_status()
        ps = status.get("power_surface")
        assert ps is not None, (
            "get_status() returned power_surface=None after async_start migration.\n"
            "The HA sensor will show 'Surface data is missing' until a full retrain."
        )
        assert len(ps["z"]) == 52
        assert len(ps["z"][0]) == 16
