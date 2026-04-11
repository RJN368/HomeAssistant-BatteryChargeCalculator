"""FastAPI application factory."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from app.auth import ensure_api_key
from app.routes.configure import router as configure_router
from app.routes.health import router as health_router
from app.routes.predict import router as predict_router
from app.routes.retrain import router as retrain_router
from app.routes.status import router as status_router

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="BCC ML Service",
    description="Battery Charge Calculator — standalone ML inference and training service",
    version="1.0.0",
)

app.include_router(health_router)
app.include_router(configure_router)
app.include_router(predict_router)
app.include_router(retrain_router)
app.include_router(status_router)


@app.on_event("startup")
async def _startup() -> None:
    ensure_api_key()
