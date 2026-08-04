"""media 二期测试基建：内存 DB + 假 AI。无 pytest-asyncio，异步用 asyncio.run。"""
import aiosqlite
from app.database import SCHEMA, MIGRATIONS


async def make_db():
    """内存 aiosqlite 连接，已应用 SCHEMA + MIGRATIONS，row_factory=Row。"""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()
    return db


def fake_ai(response, tokens=10, cost=0.0):
    """返回一个可替换 media_ai.ask_ai 的 async stub，固定返回 response。"""
    async def _stub(prompt, model="auto", task_type="", system_prompt="",
                    json_mode=False):
        return {"response": response, "model": model or "deepseek",
                "tokens": tokens, "cost": cost}
    return _stub


async def seed_content(db, *, persona_id="P1", content_id="C1",
                       title="AI如何真落地到企业", puzzle="为什么多数企业上AI三个月就放弃？",
                       stage="idea"):
    """插入一个人设 + 一条内容，返回 content_id。"""
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')",
        (persona_id, "嘉姐", "帮中小企业务实落地AI", "涨粉"))
    await db.execute(
        "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_reason) "
        "VALUES (?,?,?,?,?,?)",
        (content_id, persona_id, title, puzzle, stage, "受众常被AI焦虑营销割"))
    await db.commit()
    return content_id
