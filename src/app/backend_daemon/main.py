from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI

from app.backend_daemon.api import router
from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.job_manager import JobManager
from app.backend_daemon.logging_utils import setup_logging

logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[3]
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 5123


def create_app(db_path: Path, schema_sql: str) -> FastAPI:
    app = FastAPI()
    bus = EventBus()
    mgr = JobManager(db_path=db_path, schema_sql=schema_sql, event_bus=bus)

    app.state.bus = bus
    app.state.mgr = mgr
    app.include_router(router)

    return app


def build_app(root: Path = ROOT_DIR) -> FastAPI:
    log_dir = root / ".slidemanager" / "logs"
    setup_logging(log_dir)
    db_path = root / ".slidemanager" / "index.sqlite"
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception("Failed to load schema.sql: %s", exc)
        raise
    return create_app(db_path, schema_sql)


def get_backend_host() -> str:
    return os.getenv("APP_BACKEND_HOST", DEFAULT_BACKEND_HOST)


def get_backend_port() -> int:
    raw_port = os.getenv("APP_BACKEND_PORT", str(DEFAULT_BACKEND_PORT))
    try:
        return int(raw_port)
    except ValueError:
        logger.warning(
            "Invalid APP_BACKEND_PORT=%s, using default %s", raw_port, DEFAULT_BACKEND_PORT
        )
        return DEFAULT_BACKEND_PORT


app = build_app()


if __name__ == "__main__":
    try:
        app = build_app(ROOT_DIR)

        import uvicorn

        uvicorn.run(
            app, host=get_backend_host(), port=get_backend_port(), log_level="info"
        )
    except Exception as exc:
        setup_logging(Path.cwd() / ".slidemanager" / "logs")
        logger.exception("Backend daemon crashed: %s", exc)
        raise
