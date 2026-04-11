"""Service entrypoint: generate TLS cert/key if absent, then start uvicorn."""

from __future__ import annotations

import os

import uvicorn

from app.tls import ensure_certs

if __name__ == "__main__":
    if os.environ.get("DEBUG_PORT"):
        import debugpy

        debug_port = int(os.environ["DEBUG_PORT"])
        debugpy.listen(("0.0.0.0", debug_port))  # noqa: S104
        print(f"[BCC ML Service] debugpy waiting for attach on port {debug_port}...")
        debugpy.wait_for_client()
        print("[BCC ML Service] debugpy client attached")

    cert_path, key_path = ensure_certs()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8765,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
        log_level="debug",
    )
