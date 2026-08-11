"""POST /predict — batch slot prediction."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_bearer
from app.estimator import estimator
from .predict_request import PredictRequest
from .predict_response import PredictResponse

router = APIRouter()


@router.post(
    "/predict",
    response_model=PredictResponse,
    dependencies=[Depends(verify_bearer)],
)
async def predict(body: PredictRequest) -> PredictResponse:
    """Return ML-corrected kWh values for each slot."""
    if not estimator.is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not ready — training may be in progress",
        )
    raw_slots: list[dict[str, Any]] = [s.model_dump() for s in body.slots]
    corrections = await estimator.predict_batch(raw_slots)
    return PredictResponse(corrected_kwh=corrections)
