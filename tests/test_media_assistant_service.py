"""助手留痕 + 撤销。"""
import asyncio, json
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.commit()
    return db


def test_log_and_list():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "create_topic", "media_topic", "T1",
                                  after={"title": "选题甲"})
        acts = await ma.list_actions(db, "A")
        assert len(acts) == 1 and acts[0]["id"] == aid and acts[0]["status"] == "applied"
        await db.close()
    asyncio.run(go())


def test_revert_create_deletes_row():
    async def go():
        db = await _seed()
        await db.execute("INSERT INTO media_topic (id,persona_id,title,source,status) "
                         "VALUES ('T1','A','选题甲','assistant','pool')")
        await db.commit()
        aid = await ma.log_action(db, "A", "create_topic", "media_topic", "T1",
                                  after={"title": "选题甲"})
        ok = await ma.revert_action(db, aid)
        assert ok
        cur = await db.execute("SELECT COUNT(*) c FROM media_topic WHERE id='T1'")
        assert (await cur.fetchone())["c"] == 0
        cur = await db.execute("SELECT status FROM media_assistant_action WHERE id=?", (aid,))
        assert (await cur.fetchone())["status"] == "reverted"
        await db.close()
    asyncio.run(go())


def test_revert_draft_restores_ai_draft():
    async def go():
        db = await _seed()
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,ai_draft) "
                         "VALUES ('C1','A','标题','idea','新草稿')")
        await db.commit()
        aid = await ma.log_action(db, "A", "draft_script", "media_content", "C1",
                                  before={"ai_draft": "旧草稿"}, after={"ai_draft": "新草稿"})
        await ma.revert_action(db, aid)
        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["ai_draft"] == "旧草稿"
        await db.close()
    asyncio.run(go())
