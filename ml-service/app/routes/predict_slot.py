"""Request slot model for prediction endpoint."""

from pydantic import BaseModel


class PredictSlot(BaseModel):
    slot_time: str
    temp_c: float | None = None
    physics_kwh: float
