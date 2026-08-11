"""Axle client exception type."""


class AxleClientError(RuntimeError):
    """Raised when Axle event retrieval fails after retries."""
