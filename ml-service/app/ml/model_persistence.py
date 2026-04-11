"""Atomic model persistence for the BCC ML service.

Identical in logic to the HA integration's model_persistence.py, but the model
is stored at a fixed path inside the Docker data volume (/data/model.pkl) rather
than being parameterised by an HA config_dir.

This module has NO homeassistant imports.  It is pure Python + joblib + sklearn.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone

import joblib

from .model_trainer import TrainedModel, _RETRAIN_RMSE_TRIGGER

_LOGGER = logging.getLogger(__name__)

# D-4 (service edition): fixed path inside Docker named volume
_MODEL_PATH = os.environ.get("ML_SERVICE_DATA_DIR", "/data") + "/model.pkl"

# D-9: retrain if model is older than this many days
_MODEL_MAX_AGE_DAYS: float = 35.0


def get_model_path(config_dir: str | None = None) -> str:
    """Return the absolute path for the model pickle file.

    When ``config_dir`` is supplied (e.g. in tests) the file is placed at
    ``{config_dir}/model.pkl``.  Otherwise uses ``_MODEL_PATH``.
    """
    if config_dir is not None:
        return os.path.join(config_dir, "model.pkl")
    return _MODEL_PATH


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_model(model: TrainedModel, config_dir: str | None = None) -> None:
    """Atomically save a :class:`TrainedModel` to disk using joblib."""
    target_path = get_model_path(config_dir)
    dir_path = os.path.dirname(target_path)
    os.makedirs(dir_path, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        os.close(fd)
        joblib.dump(model, tmp_path, compress=3)
        os.replace(tmp_path, target_path)
        _LOGGER.debug("save_model: model saved to %s", target_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_model(config_dir: str | None = None) -> TrainedModel | None:
    """Load a :class:`TrainedModel` from disk, returning ``None`` on any failure."""
    path = get_model_path(config_dir)
    if not os.path.exists(path):
        _LOGGER.debug("load_model: no model file at %s", path)
        return None
    try:
        model = joblib.load(path)
        if not isinstance(model, TrainedModel):
            _LOGGER.warning(
                "load_model: unexpected type %s at %s — ignoring",
                type(model).__name__,
                path,
            )
            return None
        _LOGGER.debug("load_model: loaded model from %s", path)
        return model
    except Exception as exc:
        _LOGGER.warning("load_model: failed to load model from %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Retrain / staleness helpers
# ---------------------------------------------------------------------------


def needs_retrain(model: TrainedModel | None, config_dir: str | None = None) -> bool:
    """Return True if a new training run should be triggered."""
    if model is None:
        return True
    age_days = (datetime.now(timezone.utc) - model.trained_at).total_seconds() / 86400
    if age_days > _MODEL_MAX_AGE_DAYS:
        _LOGGER.debug(
            "needs_retrain: model age %.1f days exceeds limit %.1f",
            age_days,
            _MODEL_MAX_AGE_DAYS,
        )
        return True
    return False


def model_age_days(model: TrainedModel | None) -> float:
    """Return the age of the model in days, or ``inf`` if model is ``None``."""
    if model is None:
        return float("inf")
    return (datetime.now(timezone.utc) - model.trained_at).total_seconds() / 86400


def should_retrain(
    model: TrainedModel | None,
    current_rmse_7day: float | None = None,
) -> bool:
    """Return True if a retrain should be triggered.

    Checks age (> _MODEL_MAX_AGE_DAYS) and optionally a 7-day rolling RMSE
    ratio: retrain when ``current_rmse_7day > 1.5 × model.training_rmse``.
    """
    if model is None:
        return True
    if needs_retrain(model):
        return True
    if (
        current_rmse_7day is not None
        and model.training_rmse is not None
        and current_rmse_7day > 1.5 * model.training_rmse
    ):
        return True
    return False
