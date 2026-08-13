"""L2 删除某一轮：删行 + 释放其 content_ids 回可复盘池。"""
import asyncio
import json
import uuid

from tests.media_helpers import make_db, seed_content
from app.services import media_review_cycle as rc


async def _seed_published(db, persona_id, cid, views, first=False):
    if first:
        await seed_content(db, persona_id=persona_id, content_id=cid, stage="published")
    else:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage) "
            "VALUES (?,?,?,?, 'published')", (cid, persona_id, "标题" + cid, "谜题"))
    aid = "ACC-" + cid
    await db.execute("INSERT INTO media_account (id,persona_id,platform,account_name) "
                     "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
    pid = "PUB-" + cid
    await db.execute("INSERT INTO media_publish (id,content_id,account_id,status) "
                     "VALUES (?,?,?, 'published')", (pid, cid, aid))
    await db.execute("INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
                     "shares,new_fans) VALUES (?,?,?,0,0,0,0)",
                     (str(uuid.uuid4()), pid, views))
    await db.commit()


def test_delete_cycle_removes_row_and_frees_contents():
    async def go():
        db = await make_db()
        try:
            await _seed_published(db, "P1", "c1", 1000, first=True)
            cid = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "period_end) VALUES (?,?,?,?,datetime('now'))",
                (cid, "P1", 1, json.dumps(["c1"])))
            await db.commit()
            # 删前：c1 已被这轮复盘，去重排除
            contents, _ = await rc.gather_cycle_contents(db, "P1")
            assert "c1" not in {c["id"] for c in contents}
            # 删除
            ok = await rc.delete_cycle(db, cid)
            assert ok is True
            assert await rc.get_cycle(db, cid) is None
            # 删后：c1 重新回到可复盘池
            contents2, prev = await rc.gather_cycle_contents(db, "P1")
            assert "c1" in {c["id"] for c in contents2}
            assert prev is None          # 已无往轮
        finally:
            await db.close()
    asyncio.run(go())


def test_delete_missing_cycle_returns_false():
    async def go():
        db = await make_db()
        try:
            assert await rc.delete_cycle(db, "nope") is False
        finally:
            await db.close()
    asyncio.run(go())
