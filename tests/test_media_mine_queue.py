"""批量挖矿候选队列：去重写入/分组/采纳落库/丢弃。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_mine_queue as q


async def _mkdb():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('BM','嘉','x','涨粉','active')")
    for cid, t in (("C1", "文案甲"), ("C2", "文案乙")):
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source) "
                         "VALUES (?,?,?, 'published','legacy_text')", (cid, "BM", t))
    await db.commit()
    return db


def test_enqueue_dedup_within_content():
    async def go():
        db = await _mkdb()
        n = await q.enqueue_candidates(db, "BM", "C1", "signature",
            [{"content": "你要知道", "brief": "", "evidence": "e", "reason": "r"},
             {"content": "你要知道", "brief": "", "evidence": "e2", "reason": "r2"}])
        assert n == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_mine_candidate")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())


def test_group_counts_across_contents():
    async def go():
        db = await _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "signature",
            [{"content": "你要知道", "evidence": "来自甲"}])
        await q.enqueue_candidates(db, "BM", "C2", "signature",
            [{"content": "你要知道", "evidence": "来自乙"}])
        grouped = await q.list_pending_grouped(db, "BM")
        sigs = grouped["signature"]
        assert len(sigs) == 1 and sigs[0]["count"] == 2
        assert len(sigs[0]["sources"]) == 2
        await db.close()
    asyncio.run(go())


def test_adopt_signature_writes_trait_and_marks_group():
    async def go():
        db = await _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "signature",
            [{"content": "你要知道", "evidence": "来自甲"}])
        await q.enqueue_candidates(db, "BM", "C2", "signature",
            [{"content": "你要知道", "evidence": "来自乙"}])
        grouped = await q.list_pending_grouped(db, "BM")
        rep = grouped["signature"][0]["rep_id"]
        n = await q.adopt_candidates(db, [rep])
        assert n == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait "
                               "WHERE persona_id='BM' AND dimension='signature'")
        assert (await cur.fetchone())["c"] == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_mine_candidate WHERE status='pending'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(go())


def test_adopt_material_and_playbook():
    async def go():
        db = await _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "material",
            [{"type": "story", "content": "踩过的坑", "brief": "坑", "evidence": "e", "reason": "r"}])
        await q.enqueue_candidates(db, "BM", "C1", "playbook",
            [{"name": "痛点自曝法", "structure": "抛→自曝→给法", "when_to_use": "焦虑", "evidence": "e", "similar_to": ""}])
        g = await q.list_pending_grouped(db, "BM")
        ids = [g["material"][0]["rep_id"], g["playbook"][0]["rep_id"]]
        await q.adopt_candidates(db, ids)
        cur = await db.execute("SELECT COUNT(*) c FROM media_material WHERE persona_id='BM'")
        assert (await cur.fetchone())["c"] == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_playbook WHERE name='痛点自曝法'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())


def test_discard_marks_group():
    async def go():
        db = await _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "signature", [{"content": "口水词"}])
        g = await q.list_pending_grouped(db, "BM")
        await q.discard_candidates(db, [g["signature"][0]["rep_id"]])
        g2 = await q.list_pending_grouped(db, "BM")
        assert g2["signature"] == []
        await db.close()
    asyncio.run(go())
