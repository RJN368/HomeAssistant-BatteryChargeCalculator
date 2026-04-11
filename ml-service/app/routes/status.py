"""GET /status — full model and training status."""

from fastapi import APIRouter, Depends

from app.auth import verify_bearer
from app.estimator import estimator

router = APIRouter()


@router.get("/status", dependencies=[Depends(verify_bearer)])
async def get_status() -> dict:
    """Return full estimator status including model diagnostics."""
    return estimator.get_status()
