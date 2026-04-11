"""POST /configure — provide credentials and physics settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import verify_bearer
from app.estimator import estimator

router = APIRouter()


class ConfigureRequest(BaseModel):
    # GivEnergy credentials
    givenergy_api_key: str = ""
    givenergy_inverter_serial: str = ""
    # Octopus credentials (optional)
    octopus_api_key: str = ""
    octopus_account_id: str = ""
    octopus_mpan: str = ""
    octopus_meter_serial: str = ""
    # Consumption source: "givenergy" or "octopus"
    consumption_source: str = "givenergy"
    # Training lookback window (days)
    training_lookback_days: int = 90
    # Location for Open-Meteo temperature history
    latitude: float = 51.5
    longitude: float = -0.1
    # Physics parameters
    heating_type: str = "none"
    cop: float = 3.0
    heat_loss_w_per_k: float = 100.0
    indoor_temp_c: float = 20.0
    heating_flow_temp_c: float = 45.0
    known_points: Any = None
    base_load_profile: Any = None


@router.post("/configure", dependencies=[Depends(verify_bearer)])
async def configure(body: ConfigureRequest) -> dict:
    """Store credentials and physics config; triggers training if model is absent."""
    estimator.configure(body.model_dump())
    await estimator.async_start()
    return {"status": "ok", "state": estimator.get_status()["state"]}
