"""
Health check utilities for external services.

This module provides health check functions for verifying the availability
and status of external services like the Health Docs provider API.
"""
from __future__ import annotations

import requests
from loguru import logger


from config import batch_config


def perform_source_api_health_check() -> bool:
    """
    Check health of the Health Docs provider API.

    Performs a health check request to the provider's ping endpoint.

    Returns:
        True if the API is healthy, False otherwise
    """
    health_check_url = f"{batch_config.SOURCE_BASE_URL.rstrip('/')}/ping"

    logger.info(f"Source API Health Check URL: {health_check_url}")

    try:
        response = requests.get(health_check_url)
        if response.status_code == 200:
            logger.success("Health Docs provider API is healthy")
            return True
        else:
            logger.error(
                f"Health Docs provider API unhealthy. "
                f"Status: {response.status_code}, Response: {response.text}"
            )
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Health Docs provider API health check error: {e}")
        return False
