"""老文案整理 schema。"""
import asyncio
from tests.media_helpers import make_db


def test_content_has_summary():
    async def go():
        db = await make_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_content)")
            cols = {r["name"] for r in await cur.fetchall()}
            assert "summary" in cols
        finally:
            await db.close()
    asyncio.run(go())
