"""Unit tests for ml/model_trainer.py and ml/model_persistence.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from app.ml.model_trainer import (
    FEATURE_COLUMNS,
    TrainedModel,
    check_model_compatibility,
    compute_blend_weight,
    predict_correction,
    train_power_model,
)
from app.ml.model_persistence import (
    get_model_path,
    load_model,
    model_age_days,
    save_model,
    should_retrain,
)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def make_training_df(n: int, with_octopus: bool = False) -> pd.DataFrame:
    """Synthetic training DataFrame with all required columns."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    temps = rng.normal(8, 5, n)
    physics = np.clip(0.4 - temps * 0.015, 0.05, 2.0)
    df = pd.DataFrame(
        {
            "actual_kwh": np.abs(physics + rng.normal(0, 0.05, n)),
            "physics_kwh": np.abs(physics),
            "outdoor_temp_c": temps,
            "hour_sin": np.sin(2 * np.pi * idx.hour / 24),
            "hour_cos": np.cos(2 * np.pi * idx.hour / 24),
            "dow_sin": np.sin(2 * np.pi * idx.dayofweek / 7),
            "dow_cos": np.cos(2 * np.pi * idx.dayofweek / 7),
            "doy_sin": np.sin(2 * np.pi * idx.dayofyear / 365),
            "doy_cos": np.cos(2 * np.pi * idx.dayofyear / 365),
            "is_weekend": (idx.dayofweek >= 5).astype(float),
            "slot_index": (idx.hour * 2 + idx.minute // 30).astype(float),
            "temp_delta_1slot": rng.normal(0, 0.3, n),
            "temp_delta_24h": rng.normal(0, 1, n),
            "rolling_mean_6h": temps + rng.normal(0, 0.2, n),
            "physics_kwh_sq": np.abs(physics) ** 2,
        },
        index=idx,
    )
    if with_octopus:
        df["octopus_import_kwh"] = np.abs(rng.normal(0.3, 0.1, n))
    return df


# ---------------------------------------------------------------------------
# compute_blend_weight tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_clean, expected",
    [
        (0, 0.0),
        (500, 0.0),
        (1500, 0.5),
        (2500, 1.0),
        (9999, 1.0),
    ],
)
def test_compute_blend_weight_ramp(n_clean: int, expected: float) -> None:
    assert compute_blend_weight(n_clean) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# train_power_model tests
# ---------------------------------------------------------------------------


def test_train_returns_trained_model() -> None:
    df = make_training_df(600)
    result = train_power_model(df)
    assert isinstance(result, TrainedModel)


def test_train_small_dataset_uses_ridge() -> None:
    df = make_training_df(200)
    result = train_power_model(df)
    assert result.model_type == "ridge"


def test_train_large_dataset_uses_histgbr() -> None:
    df = make_training_df(600)
    result = train_power_model(df)
    assert result.model_type == "hist_gbr"


def test_trained_model_has_positive_rmse() -> None:
    df = make_training_df(600)
    result = train_power_model(df)
    assert result.training_rmse > 0


def test_trained_model_blend_weight_in_range() -> None:
    df = make_training_df(600)
    result = train_power_model(df)
    assert 0.0 <= result.blend_weight <= 1.0


def test_trained_with_octopus_flag() -> None:
    df = make_training_df(600, with_octopus=True)
    result = train_power_model(df)
    assert result.trained_with_octopus_feature is True


def test_trained_without_octopus_flag() -> None:
    df = make_training_df(600)
    result = train_power_model(df)
    assert result.trained_with_octopus_feature is False


def test_predict_correction_shape() -> None:
    df = make_training_df(600)
    model = train_power_model(df)

    n = 48
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-02-01", periods=n, freq="30min", tz="UTC")
    temps = rng.normal(8, 5, n)
    physics = np.clip(0.4 - temps * 0.015, 0.05, 2.0)
    features = pd.DataFrame(
        {
            "outdoor_temp_c": temps,
            "physics_kwh": np.abs(physics),
            "hour_sin": np.sin(2 * np.pi * idx.hour / 24),
            "hour_cos": np.cos(2 * np.pi * idx.hour / 24),
            "dow_sin": np.sin(2 * np.pi * idx.dayofweek / 7),
            "dow_cos": np.cos(2 * np.pi * idx.dayofweek / 7),
            "doy_sin": np.sin(2 * np.pi * idx.dayofyear / 365),
            "doy_cos": np.cos(2 * np.pi * idx.dayofyear / 365),
            "is_weekend": (idx.dayofweek >= 5).astype(float),
            "slot_index": (idx.hour * 2 + idx.minute // 30).astype(float),
            "temp_delta_1slot": rng.normal(0, 0.3, n),
            "temp_delta_24h": rng.normal(0, 1, n),
            "rolling_mean_6h": temps + rng.normal(0, 0.2, n),
            "physics_kwh_sq": np.abs(physics) ** 2,
        },
        index=idx,
    )

    corrections = predict_correction(model, features)
    assert len(corrections) == 48


def test_predict_correction_clamped() -> None:
    training_rmse = 0.1
    mock_estimator = MagicMock()
    mock_estimator.predict.return_value = np.array([100.0, -100.0, 0.05])

    model = TrainedModel(
        estimator=mock_estimator,
        model_type="ridge",
        feature_columns=FEATURE_COLUMNS,
        trained_at=datetime.now(tz=timezone.utc),
        n_training_samples=100,
        training_rmse=training_rmse,
        blend_weight=0.0,
        trained_with_octopus_feature=False,
        slot_residual_std=training_rmse,
    )

    idx = pd.date_range("2024-02-01", periods=3, freq="30min", tz="UTC")
    features = pd.DataFrame(
        {col: np.zeros(3) for col in FEATURE_COLUMNS},
        index=idx,
    )

    corrections = predict_correction(model, features)
    cap = 2.0 * training_rmse  # 0.2
    assert all(-cap <= float(v) <= cap for v in corrections)


# ---------------------------------------------------------------------------
# check_model_compatibility tests
# ---------------------------------------------------------------------------


def test_check_compatibility_same() -> None:
    df = make_training_df(200)
    model = train_power_model(df)
    assert check_model_compatibility(model, FEATURE_COLUMNS) is True


def test_check_compatibility_different() -> None:
    df = make_training_df(200)
    model = train_power_model(df)
    modified = list(FEATURE_COLUMNS)
    modified[0] = "nonexistent_feature"
    assert check_model_compatibility(model, modified) is False


# ===========================================================================
# model_persistence tests
# ===========================================================================


def test_save_and_load_roundtrip(tmp_path) -> None:
    df = make_training_df(200)
    model = train_power_model(df)
    config_dir = str(tmp_path)

    save_model(model, config_dir)
    loaded = load_model(config_dir)

    assert loaded is not None
    assert loaded.n_training_samples == model.n_training_samples
    assert loaded.model_type == model.model_type
    assert loaded.trained_with_octopus_feature == model.trained_with_octopus_feature


def test_load_missing_file_returns_none(tmp_path) -> None:
    result = load_model(str(tmp_path))
    assert result is None


def test_load_corrupt_file_returns_none(tmp_path) -> None:
    path = get_model_path(str(tmp_path))
    with open(path, "wb") as f:
        f.write(b"not a pickle")
    result = load_model(str(tmp_path))
    assert result is None


def test_model_age_days_fresh() -> None:
    trained_at = datetime.now(tz=timezone.utc) - timedelta(days=2)
    model = TrainedModel(
        estimator=MagicMock(),
        model_type="ridge",
        feature_columns=FEATURE_COLUMNS,
        trained_at=trained_at,
        n_training_samples=100,
        training_rmse=0.05,
        blend_weight=0.0,
        trained_with_octopus_feature=False,
        slot_residual_std=0.05,
    )
    assert model_age_days(model) == pytest.approx(2.0, abs=0.1)


def test_should_retrain_none_model() -> None:
    assert should_retrain(None) is True


def test_should_retrain_fresh_model() -> None:
    trained_at = datetime.now(tz=timezone.utc) - timedelta(days=5)
    model = TrainedModel(
        estimator=MagicMock(),
        model_type="ridge",
        feature_columns=FEATURE_COLUMNS,
        trained_at=trained_at,
        n_training_samples=100,
        training_rmse=0.05,
        blend_weight=0.0,
        trained_with_octopus_feature=False,
        slot_residual_std=0.05,
    )
    assert should_retrain(model) is False


def test_should_retrain_stale_model() -> None:
    trained_at = datetime.now(tz=timezone.utc) - timedelta(days=40)
    model = TrainedModel(
        estimator=MagicMock(),
        model_type="ridge",
        feature_columns=FEATURE_COLUMNS,
        trained_at=trained_at,
        n_training_samples=100,
        training_rmse=0.05,
        blend_weight=0.0,
        trained_with_octopus_feature=False,
        slot_residual_std=0.05,
    )
    assert should_retrain(model) is True


def test_should_retrain_rmse_trigger() -> None:
    trained_at = datetime.now(tz=timezone.utc) - timedelta(days=5)
    model = TrainedModel(
        estimator=MagicMock(),
        model_type="ridge",
        feature_columns=FEATURE_COLUMNS,
        trained_at=trained_at,
        n_training_samples=100,
        training_rmse=0.1,
        blend_weight=0.0,
        trained_with_octopus_feature=False,
        slot_residual_std=0.1,
    )
    # current_rmse_7day=0.16 > 1.5 × 0.1 = 0.15  →  retrain required
    assert should_retrain(model, current_rmse_7day=0.16) is True


def test_should_not_retrain_rmse_ok() -> None:
    trained_at = datetime.now(tz=timezone.utc) - timedelta(days=5)
    model = TrainedModel(
        estimator=MagicMock(),
        model_type="ridge",
        feature_columns=FEATURE_COLUMNS,
        trained_at=trained_at,
        n_training_samples=100,
        training_rmse=0.1,
        blend_weight=0.0,
        trained_with_octopus_feature=False,
        slot_residual_std=0.1,
    )
    # current_rmse_7day=0.14 < 1.5 × 0.1 = 0.15  →  no retrain
    assert should_retrain(model, current_rmse_7day=0.14) is False
