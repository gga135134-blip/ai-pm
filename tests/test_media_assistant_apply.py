"""apply_action 各类型 + 撤销。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                     "VALUES ('C1','A','内容甲','published',0)")
    await db.commit()
    return db


def test_apply_mark_winner_and_revert():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "mark_winner", "media_content", "C1",
                                  after={"summary": "标爆款", "content_id": "C1"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["is_winner"] == 1
        assert await ma.revert_action(db, aid) is True          # 可撤
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["is_winner"] == 0
        await db.close()
    asyncio.run(go())


def test_apply_adopt_signature_and_revert():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "adopt_signature", "media_persona_trait", "",
                                  after={"summary": "口头禅", "content": "你要知道"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait "
                               "WHERE persona_id='A' AND dimension='signature'")
        assert (await cur.fetchone())["c"] == 1
        assert await ma.revert_action(db, aid) is True          # 删掉写入的记录
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait WHERE persona_id='A'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(go())


def test_apply_delete_content_irreversible():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "delete_content", "media_content", "C1",
                                  after={"summary": "删除《内容甲》"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        cur = await db.execute("SELECT COUNT(*) c FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["c"] == 0
        assert await ma.revert_action(db, aid) is False         # 不可撤
        await db.close()
    asyncio.run(go())
