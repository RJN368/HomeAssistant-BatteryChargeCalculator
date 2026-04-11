"""ML sub-package for battery_charge_calculator.

All training and inference is now handled by the standalone BCC ML Service
(see ml-service/ at the repository root).  This package contains only the
thin aiohttp client used to communicate with that service.

  ml/ml_service_client.py — MLServiceClient (HTTPS REST client)
"""
