from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.job_manager import JobManager
from app.backend_daemon.logging_utils import setup_logging

logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[3]
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def run_worker(root: Path = ROOT_DIR) -> None:
    log_dir = root / ".slidemanager" / "logs"
    setup_logging(log_dir)
    db_path = root / ".slidemanager" / "index.sqlite"
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception("Failed to load schema.sql: %s", exc)
        raise
    JobManager(db_path=db_path, schema_sql=schema_sql, event_bus=EventBus())
    logger.info("Backend daemon worker started.")
    await asyncio.Event().wait()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Backend daemon worker stopped by user.")
    except Exception as exc:
        setup_logging(Path.cwd() / ".slidemanager" / "logs")
        logger.exception("Backend daemon worker crashed: %s", exc)
        raise


if __name__ == "__main__":
    main()
