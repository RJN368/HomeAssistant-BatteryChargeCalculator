"""Atomic model persistence for the BatteryChargeCalculator ML layer.

Handles safe serialisation and deserialisation of :class:`TrainedModel` objects
to the HA configuration directory (D-4), along with helpers that determine when
a retrain is needed (D-9).

File location rationale (D-4):
    The model file is stored at ``{config_dir}/battery_charge_calculator_model.pkl``
    rather than inside ``custom_components/``.  The ``custom_components/`` directory is
    wiped on every HACS upgrade, which would silently destroy the trained model and
    force a cold-start period.  The HA config directory persists across updates.

Atomic write strategy (D-4):
    1. :func:`tempfile.mkstemp` creates a temp file in the same directory as the
       target path (guaranteeing same filesystem, so the rename is atomic).
    2. :func:`joblib.dump` writes the serialised model to the temp file.
    3. :func:`os.replace` performs a POSIX atomic rename to the final path.

    The target file is therefore never in a partially-written state — safe against
    an HA restart occurring mid-save.

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

# D-4: filename stored in HA config dir
_MODEL_FILENAME = "battery_charge_calculator_model.pkl"

# D-9: retrain if model is older than this many days (monthly schedule + 5-day buffer)
_MODEL_MAX_AGE_DAYS: float = 35.0


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def get_model_path(config_dir: str) -> str:
    """Return the absolute path for the model pickle file.

    The file is stored in the HA configuration directory rather than inside
    ``custom_components/`` so that it survives HACS upgrades (D-4).

    Args:
        config_dir: The HA config directory, typically obtained from
            ``hass.config.config_dir``.

    Returns:
        Absolute path string:
        ``{config_dir}/battery_charge_calculator_model.pkl``.
    """
    return os.path.join(config_dir, _MODEL_FILENAME)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_model(model: TrainedModel, config_dir: str) -> None:
    """Atomically save a :class:`TrainedModel` to disk using joblib.

    Writes to a temporary file in the same directory as the target (ensuring
    same filesystem) before performing an atomic ``os.replace()`` rename.  The
    model file is never in a partially-written state, making this safe against
    an HA restart occurring mid-save (D-4).

    Args:
        model: :class:`TrainedModel` instance to serialise.
        config_dir: HA config directory from ``hass.config.config_dir``.

    Raises:
        OSError: if the directory is not writable or the atomic rename fails.
    """
    target_path = get_model_path(config_dir)
    dir_path = os.path.dirname(target_path)

    # mkstemp in the same directory as the target ensures same-filesystem rename
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        os.close(fd)  # joblib opens the file by path; close the OS fd first
        joblib.dump(model, tmp_path, compress=3)
        os.replace(tmp_path, target_path)  # POSIX atomic rename
        _LOGGER.debug("save_model: model saved to %s", target_path)
    except Exception:
        # Clean up the orphaned temp file before re-raising
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_model(config_dir: str) -> TrainedModel | None:
    """Load a :class:`TrainedModel` from disk, returning ``None`` on any failure.

    Returns ``None`` (never raises) when:

    * The file does not exist — normal at first run; triggers training.
    * The file is corrupt or unpicklable — triggers a retrain cycle.
    * The object loaded is not a :class:`TrainedModel` — version mismatch;
      triggers retrain.

    A ``WARNING`` is logged for unexpected failures (corrupt file, wrong type)
    but not for the normal "file not found" case.

    Args:
        config_dir: HA config directory from ``hass.config.config_dir``.

    Returns:
        The deserialised :class:`TrainedModel`, or ``None``.
    """
    path = get_model_path(config_dir)

    if not os.path.exists(path):
        _LOGGER.debug("load_model: no model file found at %s", path)
        return None

    try:
        obj = joblib.load(path)
    except Exception as exc:
        _LOGGER.warning(
            "load_model: failed to deserialise model from %s — will retrain. Error: %s",
            path,
            exc,
        )
        return None

    if not isinstance(obj, TrainedModel):
        _LOGGER.warning(
            "load_model: object at %s is %s, not TrainedModel — will retrain.",
            path,
            type(obj).__name__,
        )
        return None

    _LOGGER.debug(
        "load_model: loaded model (type=%s n=%d trained_at=%s)",
        obj.model_type,
        obj.n_training_samples,
        obj.trained_at.isoformat(),
    )
    return obj


# ---------------------------------------------------------------------------
# Age and retrain decision
# ---------------------------------------------------------------------------


def model_age_days(model: TrainedModel) -> float:
    """Return the number of days elapsed since the model was trained.

    Args:
        model: A :class:`TrainedModel` whose ``trained_at`` field is a
            timezone-aware UTC datetime.

    Returns:
        Fractional number of days since ``model.trained_at``.
    """
    now = datetime.now(tz=timezone.utc)
    delta = now - model.trained_at
    return delta.total_seconds() / 86400.0


def should_retrain(
    model: TrainedModel | None,
    current_rmse_7day: float | None = None,
) -> bool:
    """Return ``True`` if the model should be retrained.

    Retrain is triggered when any of the following conditions hold (D-9):

    1. *model* is ``None`` — no persisted model exists yet.
    2. The model is older than ``_MODEL_MAX_AGE_DAYS`` days (35) — monthly
       retrain schedule with a 5-day buffer.
    3. *current_rmse_7day* is provided **and** exceeds
       ``_RETRAIN_RMSE_TRIGGER × model.training_rmse`` (1.5×) — the rolling
       7-day RMSE health check has detected a structural change in consumption
       patterns (e.g. a new appliance or EV added).

    When *current_rmse_7day* is ``None`` the RMSE trigger is skipped, which
    is correct at startup before any 7-day rolling window has accumulated.

    Args:
        model: The currently loaded :class:`TrainedModel`, or ``None``.
        current_rmse_7day: Rolling 7-day RMSE computed on held-out recent
            samples, or ``None`` if not yet available.

    Returns:
        ``True`` if a retrain cycle should be initiated; ``False`` otherwise.
    """
    if model is None:
        _LOGGER.debug("should_retrain: no model — retrain required")
        return True

    age = model_age_days(model)
    if age > _MODEL_MAX_AGE_DAYS:
        _LOGGER.debug(
            "should_retrain: model is %.1f days old (limit %.1f) — retrain required",
            age,
            _MODEL_MAX_AGE_DAYS,
        )
        return True

    if current_rmse_7day is not None:
        threshold = _RETRAIN_RMSE_TRIGGER * model.training_rmse
        if current_rmse_7day > threshold:
            _LOGGER.debug(
                "should_retrain: 7-day RMSE %.4f > %.4f (%.1f× training RMSE) — retrain required",
                current_rmse_7day,
                threshold,
                _RETRAIN_RMSE_TRIGGER,
            )
            return True

    return False
