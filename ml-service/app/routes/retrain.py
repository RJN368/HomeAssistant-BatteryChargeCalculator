"""POST /retrain — trigger a background training run."""

from fastapi import APIRouter, Depends

from app.auth import verify_bearer
from app.estimator import estimator

router = APIRouter()


@router.post("/retrain", dependencies=[Depends(verify_bearer)])
async def retrain() -> dict:
    """Trigger an asynchronous model retrain.  Returns immediately."""
    await estimator.trigger_retrain()
    return {"status": "accepted"}
