"""复盘助手读工具：按 COALESCE(published_at,created_at) 排序 + 估算标注。"""
import asyncio
import pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat

_SEEDED = False


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pub_db") / "t.db"
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
    # CPnull: 无真实发布时间，created_at 最新 → 兜底日 2026-08-14
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,created_at) "
                     "VALUES ('CPnull','A','没真日期','published','2026-08-14 09:00:00')")
    # CPmid: 真实发布 2026-07-15
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,published_at,created_at) "
                     "VALUES ('CPmid','A','七月那条','published','2026-07-15','2026-05-01 09:00:00')")
    # CPold: 真实发布 2026-06-01
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,published_at,created_at) "
                     "VALUES ('CPold','A','六月那条','published','2026-06-01','2026-05-01 09:00:00')")
    await db.commit()
    await db.close()
    _SEEDED = True


def test_list_contents_sorted_by_published_fallback():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_contents", {}, "A")
        # COALESCE 排序：08-14(兜底) > 07-15 > 06-01
        assert out.index("没真日期") < out.index("七月那条") < out.index("六月那条")
    asyncio.run(go())


def test_list_contents_marks_estimate():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_contents", {}, "A")
        lines = {l.split("]")[1].split("（")[0].strip(): l
                 for l in out.splitlines() if "]" in l}
        assert "（按录入时间估）" in lines["没真日期"]        # 无真日期→标估算
        assert "（按录入时间估）" not in lines["七月那条"]     # 有真日期→不标
        assert "2026-07-15" in lines["七月那条"]
    asyncio.run(go())


def test_read_content_returns_published_time():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("read_content", {"id": "CPmid"}, "A")
        assert "发布时间" in out and "2026-07-15" in out
    asyncio.run(go())
