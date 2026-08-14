"""老文案炼化+打法库 schema。"""
import asyncio
from tests.media_helpers import make_db


def _cols(t):
    async def go():
        db = await make_db()
        try:
            cur = await db.execute(f"PRAGMA table_info({t})")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    return asyncio.run(go())


def test_media_content_has_is_winner():
    assert "is_winner" in _cols("media_content")


def test_media_playbook_columns():
    cols = _cols("media_playbook")
    assert {"id", "persona_id", "name", "structure", "when_to_use",
            "evidence", "source", "status", "created_at"} <= cols
