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
        for t in ("一", "二"):
            await add_draft(db, cid, t)
        rows = await list_drafts(db, cid)
        await db.close()
        return [r["text"] for r in rows]

    assert asyncio.run(run()) == ["二", "一"]


def test_keeps_only_latest_two():
    """写第 3 版时最老那版要被删掉——用户 2026-09-01 把上限从 3 调成 2
    （三版全列出来加上脚本框，页面太长：「看得我头都晕了」）。"""
    async def run():
        db, cid = await _mk()
        for i in range(1, 6):
            await add_draft(db, cid, f"第{i}版")
        rows = await list_drafts(db, cid)
        await db.close()
        return [r["text"] for r in rows]

    texts = asyncio.run(run())
    assert len(texts) == KEEP == 2
    assert texts == ["第5版", "第4版"]


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

    assert asyncio.run(run()) == ["v4", "v3"]


def test_backfill_from_legacy_ai_draft():
    """草稿历史表 2026-09-01 才建，之前的草稿只在 media_content.ai_draft 里。
    回填一版，新界面才看得见——否则老草稿在页面上凭空消失。"""
    async def run():
        db, cid = await _mk()
        await db.execute(
            "UPDATE media_content SET ai_draft=? WHERE id=?", ("老草稿正文", cid))
        await db.commit()
        before = await list_drafts(db, cid)
        await add_draft(db, cid, "老草稿正文", model="（本次之前写的）")
        after = await list_drafts(db, cid)
        await db.close()
        return before, after

    before, after = asyncio.run(run())
    assert before == []
    assert [r["text"] for r in after] == ["老草稿正文"]
    assert after[0]["model"] == "（本次之前写的）"


def test_backfill_when_history_has_other_versions():
    """回填的判断依据是「内容比对」，不是「表空不空」。

    第一版写成「表为空才回填」是错的：表里只要已有任意一条（哪怕是别的版本），
    ai_draft 里那版就永远浮不上来——页面上看着就是「助手明明写了，脚本页却没有」。
    """
    async def run():
        db, cid = await _mk()
        await add_draft(db, cid, "页面写的那版", model="deepseek")
        await db.execute(
            "UPDATE media_content SET ai_draft=? WHERE id=?", ("助手写的那版", cid))
        await db.commit()

        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id=?", (cid,))
        cur_draft = (await cur.fetchone())["ai_draft"].strip()
        drafts = await list_drafts(db, cid)
        # 复刻路由里的回填判断
        if cur_draft and not any(d["text"].strip() == cur_draft for d in drafts):
            await add_draft(db, cid, cur_draft, model="（本次之前写的）")
        out = await list_drafts(db, cid)
        await db.close()
        return [r["text"] for r in out]

    assert asyncio.run(run()) == ["助手写的那版", "页面写的那版"]


def test_backfill_skips_when_already_recorded():
    """ai_draft 跟历史表里某版一样时别重复补，否则每刷一次页面就多一条。"""
    async def run():
        db, cid = await _mk()
        await add_draft(db, cid, "同一版", model="claude")
        await db.execute(
            "UPDATE media_content SET ai_draft=? WHERE id=?", ("同一版", cid))
        await db.commit()

        drafts = await list_drafts(db, cid)
        cur_draft = "同一版"
        if cur_draft and not any(d["text"].strip() == cur_draft for d in drafts):
            await add_draft(db, cid, cur_draft)
        out = await list_drafts(db, cid)
        await db.close()
        return out

    assert len(asyncio.run(run())) == 1


def test_created_at_is_local_time_not_utc():
    """列的 DEFAULT 是 CURRENT_TIMESTAMP（UTC），东八区会显示成早 8 小时。
    add_draft 必须显式写本地时间。"""
    import datetime as _dt

    async def run():
        db, cid = await _mk()
        await add_draft(db, cid, "看时间")
        rows = await list_drafts(db, cid)
        await db.close()
        return rows[0]["created_at"]

    got = _dt.datetime.strptime(asyncio.run(run()), "%Y-%m-%d %H:%M:%S")
    # 跟本地时钟差不超过 2 分钟；如果误存成 UTC，东八区会差 8 小时
    assert abs((_dt.datetime.now() - got).total_seconds()) < 120
