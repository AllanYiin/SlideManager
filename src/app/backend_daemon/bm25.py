from __future__ import annotations

import sqlite3


def upsert_fts_page(conn: sqlite3.Connection, page_id: int, norm_text: str) -> None:
    conn.execute("DELETE FROM fts_pages WHERE page_id = ?", (page_id,))
    conn.execute(
        "INSERT INTO fts_pages(page_id, norm_text) VALUES (?,?)",
        (page_id, norm_text),
    )
