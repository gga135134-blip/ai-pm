"""草稿历史：只留最新三版，老的自动删。"""
import asyncio

from app.services.media_draft import add_draft, list_drafts, KEEP
from tests.media_helpers import make_db, seed_content


async def _mk():
    db = await make_db()
    cid = await seed_content(db)
    return db, cid


def test_add_then_list():
    async def run():
        db, cid = await _mk()
        await add_draft(db, cid, "第一版", model="claude", cost=0.01)
        rows = await list_drafts(db, cid)
        await db.close()
        return rows

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["text"] == "第一版"
    assert rows[0]["model"] == "claude"


def test_newest_first():
    async def run():
        db, cid = await _mk()
        for t in ("一", "二", "三"):
            await add_draft(db, cid, t)
        rows = await list_drafts(db, cid)
        await db.close()
        return [r["text"] for r in rows]

    assert asyncio.run(run()) == ["三", "二", "一"]


def test_keeps_only_latest_three():
    """写第 4 版时最老那版要被删掉——这是用户明确要求的上限。"""
    async def run():
        db, cid = await _mk()
        for i in range(1, 6):
            await add_draft(db, cid, f"第{i}版")
        rows = await list_drafts(db, cid)
        await db.close()
        return [r["text"] for r in rows]

    texts = asyncio.run(run())
    assert len(texts) == KEEP == 3
    assert texts == ["第5版", "第4版", "第3版"]


def test_prune_is_per_content():
    """裁剪只影响自己这条内容，别把别条的草稿删了。"""
    async def run():
        db, cid = await _mk()
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,stage) "
            "VALUES ('C2','P1','另一条','idea')")
        await db.commit()
        await add_draft(db, "C2", "别条的草稿")
        for i in range(5):
            await add_draft(db, cid, f"第{i}版")
        other = await list_drafts(db, "C2")
        await db.close()
        return other

    other = asyncio.run(run())
    assert [r["text"] for r in other] == ["别条的草稿"]


def test_blank_text_not_recorded():
    async def run():
        db, cid = await _mk()
        did = await add_draft(db, cid, "   ")
        rows = await list_drafts(db, cid)
        await db.close()
        return did, rows

    did, rows = asyncio.run(run())
    assert did == "" and rows == []


def test_same_second_writes_keep_order():
    """同一秒连写多版时 created_at 一样，靠 rowid 兜底定序，
    否则裁剪会随机丢版本、列表顺序也会飘。"""
    async def run():
        db, cid = await _mk()
        for i in range(1, 5):
            await add_draft(db, cid, f"v{i}")
        rows = await list_drafts(db, cid)
        await db.close()
        return [r["text"] for r in rows]

    assert asyncio.run(run()) == ["v4", "v3", "v2"]
