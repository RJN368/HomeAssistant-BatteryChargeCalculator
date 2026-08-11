"""Response body model for prediction endpoint."""

from pydantic import BaseModel


class PredictResponse(BaseModel):
    corrected_kwh: list[float]
