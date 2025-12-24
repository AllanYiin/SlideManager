from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    conn.executescript(schema_sql)
    conn.commit()


def executemany(conn: sqlite3.Connection, sql: str, rows: Iterable[Iterable[Any]]) -> None:
    conn.executemany(sql, rows)


def now_epoch() -> int:
    import time

    return int(time.time())
