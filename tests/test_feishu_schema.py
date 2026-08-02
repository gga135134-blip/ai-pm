import sqlite3
from app.database import SCHEMA, MIGRATIONS


def _cols(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def test_feishu_unmatched_table_and_missing_fields():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            db.execute(sql)
        except Exception:
            pass
    cur = db.cursor()
    assert {"id", "post_url", "title", "raw_metrics", "status",
            "created_at", "updated_at"} <= _cols(cur, "media_feishu_unmatched")
    assert "missing_fields" in _cols(cur, "media_metrics")
    db.close()
