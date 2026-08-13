"""L2 纯计算：聚合/中位/门槛/去重。"""
import asyncio
import json
import uuid
from tests.media_helpers import make_db, seed_content
from app.services import media_review_cycle as rc


def test_agg_takes_max_across_platforms():
    c = {"platforms": [
        {"views": 100, "likes": 5, "comments": 1, "shares": 0, "new_fans": 2},
        {"views": 300, "likes": 2, "comments": 4, "shares": 1, "new_fans": 1},
    ]}
    a = rc._agg(c)
    assert a["views"] == 300 and a["likes"] == 5 and a["comments"] == 4


def test_median_odd_even():
    assert rc._median([9000, 1000, 5000]) == 5000
    assert rc._median([2, 4, 6, 8]) == 5
    assert rc._median([]) == 0


def test_summarize_marks_hit_and_flop():
    def mk(cid, v):
        return {"id": cid, "platforms": [{"views": v, "likes": 0,
                "comments": 0, "shares": 0, "new_fans": 0}]}
    contents = [mk("a", 1000), mk("b", 1000), mk("c", 5000), mk("d", 100)]
    s = rc.summarize_metrics(contents)
    assert s["content_count"] == 4
    assert s["median"]["views"] == 1000
    assert "c" in s["hit_content_ids"]      # 5000 >= 1.5*1000
    assert "d" in s["flop_content_ids"]     # 100 <= 0.5*1000
    assert s["hit_count"] == 1 and s["flop_count"] == 1


def _insert_published_with_metrics(db, persona_id, content_id, views):
    async def go():
        await seed_content(db, persona_id=persona_id, content_id=content_id,
                           stage="published")
        aid = "ACC-" + content_id
        await db.execute(
            "INSERT INTO media_account (id,persona_id,platform,account_name) "
            "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
        pid = "PUB-" + content_id
        await db.execute(
            "INSERT INTO media_publish (id,content_id,account_id,status) "
            "VALUES (?,?,?, 'published')", (pid, content_id, aid))
        await db.execute(
            "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
            "shares,new_fans) VALUES (?,?,?,0,0,0,0)",
            (str(uuid.uuid4()), pid, views))
        await db.commit()
    return go()


def test_gather_excludes_already_reviewed():
    async def go():
        db = await make_db()
        try:
            # seed_content 会插 persona；第一条用 seed_content 建 persona
            await _insert_published_with_metrics(db, "P1", "c1", 1000)
            await _insert_published_with_metrics_no_persona(db, "P1", "c2", 2000)
            # 往轮已纳入 c1
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "period_end) VALUES (?,?,?,?,datetime('now'))",
                (str(uuid.uuid4()), "P1", 1, json.dumps(["c1"])))
            await db.commit()
            contents, prev = await rc.gather_cycle_contents(db, "P1")
            ids = {c["id"] for c in contents}
            assert ids == {"c2"}          # c1 已复盘被排除
            assert prev is not None and prev["seq"] == 1
        finally:
            await db.close()
    asyncio.run(go())


async def _insert_published_with_metrics_no_persona(db, persona_id, content_id, views):
    """persona 已存在时只插内容+发布+数据。"""
    import uuid as _u
    await db.execute(
        "INSERT INTO media_content (id,persona_id,title,puzzle,stage) "
        "VALUES (?,?,?,?, 'published')",
        (content_id, persona_id, "标题" + content_id, "谜题"))
    aid = "ACC-" + content_id
    await db.execute(
        "INSERT INTO media_account (id,persona_id,platform,account_name) "
        "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
    pid = "PUB-" + content_id
    await db.execute(
        "INSERT INTO media_publish (id,content_id,account_id,status) "
        "VALUES (?,?,?, 'published')", (pid, content_id, aid))
    await db.execute(
        "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
        "shares,new_fans) VALUES (?,?,?,0,0,0,0)", (str(_u.uuid4()), pid, views))
    await db.commit()
