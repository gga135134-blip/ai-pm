"""批量挖矿 schema。"""
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


def test_content_has_mined_markers():
    cols = _cols("media_content")
    assert "mined_signature_at" in cols and "mined_essence_at" in cols


def test_candidate_table_columns():
    cols = _cols("media_mine_candidate")
    assert {"id", "persona_id", "kind", "payload", "source_content_id",
            "dedup_key", "status", "created_at"} <= cols
