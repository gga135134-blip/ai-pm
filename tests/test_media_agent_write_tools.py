"""媒体改草稿工具：落库 + 记 applied 日志。"""
import asyncio
import pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat

_SEEDED = False


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("matw_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


async def _seed_once():
    global _SEEDED
    if _SEEDED:
        return
    db = await get_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','企业AI落地','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,script) "
                     "VALUES ('C1','A','数据安全','published','讲了员工泄密，下一期聊怎么防')")
    await db.commit()
    await db.close()
    _SEEDED = True


def test_create_topic_writes_pool_and_logs():
    async def go():
        await _seed_once()
        db = await get_db()
        out = await mat.dispatch_media_tool("create_topic",
                {"title": "企业AI数据安全", "puzzle": "怎么既用AI又不泄密"}, "A")
        cur = await db.execute("SELECT COUNT(*) c FROM media_topic WHERE persona_id='A' AND source='assistant'")
        assert (await cur.fetchone())["c"] == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action "
                               "WHERE action_type='create_topic' AND status='applied'")
        assert (await cur.fetchone())["c"] == 1
        assert "企业AI数据安全" in out
        await db.close()
    asyncio.run(go())


def test_write_next_creates_content_with_parent(monkeypatch):
    async def fake_ai(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": '{"title":"数据安全下一集","puzzle":"怎么防","reason":"承接上期"}',
                "model": "x", "tokens": 1, "cost": 0}
    monkeypatch.setattr(mat, "ask_ai", fake_ai)

    async def go():
        await _seed_once()
        db = await get_db()
        out = await mat.dispatch_media_tool("write_next", {"from_content_id": "C1"}, "A")
        cur = await db.execute("SELECT id,parent_content_id,stage,idea_source FROM media_content "
                               "WHERE persona_id='A' AND parent_content_id='C1'")
        row = dict(await cur.fetchone())
        assert row["stage"] == "idea" and row["idea_source"] == "assistant"
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action WHERE action_type='write_next'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())
