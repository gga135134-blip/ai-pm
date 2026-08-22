"""复盘跑工具：stage pending + apply 真跑。"""
import asyncio, pytest
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.commit()
    return db


def test_apply_run_l2_calls_runner(monkeypatch):
    called = {}
    async def fake_l2(db, persona_id, model="auto", force=False):
        called["l2"] = (persona_id, force)
        return {"ok": True}
    import app.services.media_review_cycle as mrc
    monkeypatch.setattr(mrc, "run_l2_cycle", fake_l2)

    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "run_l2", "media_review_cycle", "",
                                  after={"summary": "跑周期复盘"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        assert called["l2"] == ("A", True)
        cur = await db.execute("SELECT status,reversible FROM media_assistant_action WHERE id=?", (aid,))
        row = dict(await cur.fetchone())
        assert row["status"] == "applied" and row["reversible"] == 0
        assert await ma.revert_action(db, aid) is False
        await db.close()
    asyncio.run(go())
