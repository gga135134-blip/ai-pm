"""复盘读工具：列/读 周期+阶段复盘。"""
import asyncio, pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rr_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_review_cycle WHERE persona_id='RA'")
        await db.execute("DELETE FROM media_phase_review WHERE persona_id='RA'")
        await db.execute("DELETE FROM media_persona WHERE id='RA'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('RA','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_review_cycle (id,persona_id,level,seq,patterns,advisory) "
                         "VALUES ('CY1','RA','L2',1,'[\"规律甲\"]','{\"tip\":\"建议甲\"}')")
        await db.execute("INSERT INTO media_phase_review (id,persona_id,seq,phase_reco,phase_reason) "
                         "VALUES ('PR1','RA',1,'advance','该进阶段了')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_list_and_read_cycle():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("list_cycles", {}, "RA")
        assert "CY1" in out
        out = await mat.dispatch_media_tool("read_cycle", {"id": "CY1"}, "RA")
        assert "规律甲" in out
    asyncio.run(go())


def test_list_and_read_phase():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("list_phase_reviews", {}, "RA")
        assert "PR1" in out
        out = await mat.dispatch_media_tool("read_phase_review", {"id": "PR1"}, "RA")
        assert "该进阶段了" in out or "advance" in out
    asyncio.run(go())
