"""助手 schema。"""
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


def test_content_has_parent():
    assert "parent_content_id" in _cols("media_content")


def test_action_table():
    assert {"id", "persona_id", "conversation_ref", "action_type", "target_table",
            "target_id", "before_json", "after_json", "status", "reversible",
            "created_at"} <= _cols("media_assistant_action")


def test_message_table():
    assert {"id", "persona_id", "role", "content", "cost", "created_at"} <= _cols("media_assistant_message")
