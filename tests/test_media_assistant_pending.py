"""助手 pending：log status + cancel + list_pending。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.commit()
    return db


def test_log_pending_and_list():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "mark_winner", "media_content", "C1",
                                  after={"summary": "标爆款《X》", "content_id": "C1"}, status="pending")
        pend = await ma.list_pending(db, "A")
        assert len(pend) == 1 and pend[0]["id"] == aid and pend[0]["status"] == "pending"
        await db.close()
    asyncio.run(go())


def test_cancel():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "mark_winner", "media_content", "C1",
                                  after={"summary": "x"}, status="pending")
        assert await ma.cancel_action(db, aid) is True
        cur = await db.execute("SELECT status FROM media_assistant_action WHERE id=?", (aid,))
        assert (await cur.fetchone())["status"] == "cancelled"
        assert await ma.list_pending(db, "A") == []
        await db.close()
    asyncio.run(go())
