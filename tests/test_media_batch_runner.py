"""后台跑器：起任务/已跑拒绝/进度/完成。"""
import asyncio, pytest
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_batch as mb


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("batch_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_content WHERE persona_id='BP'")
        await db.execute("DELETE FROM media_persona WHERE id='BP'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('BP','嘉','x','涨粉','active')")
        for cid in ("B1", "B2"):
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                             "VALUES (?, 'BP',?, 'published','legacy_text','正文')", (cid, cid))
        await db.commit(); await db.close()
    asyncio.run(go())


def test_start_guard_and_progress(monkeypatch):
    _seed()
    async def fake_org(script, model="auto"):
        return {"ok": True, "summary": "s", "formatted": "f", "cost": 0, "model": "x"}
    monkeypatch.setattr(mb, "organize_content", fake_org)

    async def go():
        assert mb.get_status("BP") is None or mb.get_status("BP").get("running") is not True
        started = mb.start_batch("BP", "organize", ["B1", "B2"])
        assert started is True
        # 立刻再起：应被拒（同人设已有任务在跑）
        assert mb.start_batch("BP", "organize", ["B1"]) is False
        # 等它跑完
        for _ in range(60):
            st = mb.get_status("BP")
            if st and not st["running"]:
                break
            await asyncio.sleep(0.05)
        st = mb.get_status("BP")
        assert st["done"] == 2 and st["running"] is False and st["op"] == "organize"
    asyncio.run(go())

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT summary FROM media_content WHERE id='B1'")
        assert (await cur.fetchone())["summary"] == "s"
        await db.close()
    asyncio.run(chk())
