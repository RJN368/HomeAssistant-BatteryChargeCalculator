"""Request body model for prediction endpoint."""

from pydantic import BaseModel

from .predict_slot import PredictSlot


class PredictRequest(BaseModel):
    slots: list[PredictSlot]
