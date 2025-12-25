from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 5123


def get_backend_host() -> str:
    return os.getenv("APP_BACKEND_HOST", DEFAULT_BACKEND_HOST)


def get_backend_port() -> int:
    raw_port = os.getenv("APP_BACKEND_PORT")
    if not raw_port:
        return DEFAULT_BACKEND_PORT
    try:
        return int(raw_port)
    except ValueError:
        logger.warning(
            "Invalid APP_BACKEND_PORT=%s, using default %s", raw_port, DEFAULT_BACKEND_PORT
        )
        return DEFAULT_BACKEND_PORT


def get_backend_base_url() -> str:
    base_url = os.getenv("APP_BACKEND_BASE_URL")
    if base_url:
        return base_url
    return f"http://{get_backend_host()}:{get_backend_port()}"
