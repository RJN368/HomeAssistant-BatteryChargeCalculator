"""API key authentication via Bearer token."""

from __future__ import annotations

import hmac
import os
import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_bearer = HTTPBearer()


def _api_key_path() -> str:
    return os.path.join(settings.data_dir, "api_key")


def ensure_api_key() -> None:
    """Create a random API key on first start and print it to stdout."""
    path = _api_key_path()
    if not os.path.exists(path):
        key = secrets.token_hex(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(key)
        print(  # noqa: T201
            f"\n[BCC ML Service] Generated API key: {key}\n"
            "Copy this into Home Assistant → Integrations → Battery Charge Calculator "
            "→ Configure → ML Service API Key\n"
        )


def _load_key() -> str:
    with open(_api_key_path()) as fh:
        return fh.read().strip()


def verify_bearer(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    """Raise 401 if the bearer token does not match the stored key."""
    stored = _load_key()
    if not hmac.compare_digest(creds.credentials.encode(), stored.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
