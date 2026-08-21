"""媒体查类工具：scoped + 共享可见 + 不回全文。"""
import asyncio
import pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat

_SEEDED = False


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mart_db") / "t.db"
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
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('B','别人','y','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,script) "
                     "VALUES ('C1','A','我的内容','published','很长的转写正文……')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage) "
                     "VALUES ('C2','B','别人内容','idea')")
    # 打法库共享（persona_id 不同也应都可见）
    await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) VALUES ('P1','A','痛点法','proven')")
    await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) VALUES ('P2','B','悬念法','validating')")
    # 原料库：本人设 + 共享
    await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status) "
                     "VALUES ('M1','A','story','我的料','x','active')")
    await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status,scope) "
                     "VALUES ('M2','B','story','公司料','y','active','shared')")
    await db.commit()
    await db.close()
    _SEEDED = True


def test_list_contents_scoped_no_fulltext():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_contents", {}, "A")
        assert "我的内容" in out and "别人内容" not in out       # 只本人设
        assert "很长的转写正文" not in out                       # 不回全文
    asyncio.run(go())


def test_read_content_returns_fulltext():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("read_content", {"id": "C1"}, "A")
        assert "很长的转写正文" in out
    asyncio.run(go())


def test_list_playbooks_shows_shared_all():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_playbooks", {}, "A")
        assert "痛点法" in out and "悬念法" in out               # 共享全部
    asyncio.run(go())


def test_list_materials_includes_shared():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_materials", {}, "A")
        assert "我的料" in out and "公司料" in out               # 本人设 + shared
    asyncio.run(go())
