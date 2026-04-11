"""Service configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_dir: str = "/data"

    model_config = {"env_prefix": "ML_SERVICE_"}


settings = Settings()
