"""核心 stage 工具：只落 pending 不执行，验 target 属人设。用 tmp-DB_PATH fixture(工具内部 get_db)。"""
import asyncio, pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("core_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id IN ('A','B')")
        await db.execute("DELETE FROM media_content WHERE persona_id IN ('A','B')")
        await db.execute("DELETE FROM media_persona WHERE id IN ('A','B')")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('A','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('B','别人','y','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                         "VALUES ('C1','A','内容甲','published',0)")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage) "
                         "VALUES ('C2','B','别人内容','published')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_mark_winner_stages_pending_not_execute():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("mark_winner", {"content_id": "C1"}, "A")
        assert "确认" in out
        db = await get_db()
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["is_winner"] == 0        # 没执行
        cur = await db.execute("SELECT status,action_type FROM media_assistant_action WHERE persona_id='A'")
        row = dict(await cur.fetchone())
        assert row["status"] == "pending" and row["action_type"] == "mark_winner"
        await db.close()
    asyncio.run(go())


def test_mark_winner_rejects_other_persona():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("mark_winner", {"content_id": "C2"}, "A")  # C2 属 B
        assert "找不到" in out or "不属于" in out
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action WHERE persona_id='A'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(go())
