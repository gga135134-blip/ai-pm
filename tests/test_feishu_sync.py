from app.services.media_feishu_sync import norm_title, map_feishu_row

FIELD_MAP = {
    "fields": {
        "post_url": "视频链接", "title": "标题", "views": "播放量",
        "likes": "点赞", "comments": "评论", "shares": "转发",
        # 故意不映射 new_fans，模拟飞书拿不到涨粉
    }
}


def test_norm_title_strips_punct_space_emoji():
    assert norm_title(" 二胎妈妈的『时间黑洞』🕳️ ") == norm_title("二胎妈妈的时间黑洞")


def test_map_row_extracts_and_normalizes():
    row = {"视频链接": "http://x", "标题": "标题A", "播放量": "1.2万",
           "点赞": 350, "评论": 20, "转发": 5}
    out = map_feishu_row(row, FIELD_MAP)
    assert out["post_url"] == "http://x"
    assert out["title"] == "标题A"
    assert out["metrics"]["views"] == 12000
    assert out["metrics"]["likes"] == 350
    assert out["metrics"]["new_fans"] == 0  # 未映射


def test_map_row_missing_fields_flags_new_fans():
    row = {"视频链接": "http://x", "播放量": 100}
    out = map_feishu_row(row, FIELD_MAP)
    assert "new_fans" in out["missing_fields"]  # 飞书没给的标出来
    assert "views" not in out["missing_fields"]


def test_map_row_no_url_no_title_returns_none():
    assert map_feishu_row({"播放量": 100}, FIELD_MAP) is None


import asyncio, base64, json as _json, itsdangerous
import app.api.auth as auth
import app.database as d


def _run(coro):
    return asyncio.run(coro)  # 每次新事件循环；DB 连接在 coro 内建，安全


async def _seed_publish(db, post_url, title):
    import uuid
    pid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_persona (id,name) VALUES (?,?)", (pid, "P"))
    cid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_content (id,persona_id,title) VALUES (?,?,?)",
                     (cid, pid, title))
    aid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_account (id,persona_id,platform) VALUES (?,?,?)",
                     (aid, pid, "douyin"))
    pubid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_publish (id,content_id,account_id,post_url) "
                     "VALUES (?,?,?,?)", (pubid, cid, aid, post_url))
    await db.commit()
    return pubid


def test_sync_matches_by_url_and_writes_metrics():
    from app.services.media_feishu_sync import sync_from_feishu

    async def go():
        db = await d.get_db()
        pubid = await _seed_publish(db, "http://vid/1", "标题甲")
        records = [{"fields": {"视频链接": "http://vid/1", "标题": "标题甲",
                               "播放量": "1.5万", "点赞": 200}}]
        # 注入 field_map 到 settings
        import app.api.settings as st
        orig_map = st.load_settings().get("feishu_media_map")
        cfg = st.load_settings(); cfg["feishu_media_map"] = {
            "fields": {"post_url": "视频链接", "title": "标题",
                       "views": "播放量", "likes": "点赞"}}
        st.save_settings(cfg)
        rep = await sync_from_feishu(db, records=records)
        assert rep["ok"] and rep["synced"] == 1
        row = await (await db.execute(
            "SELECT views,collected_by,missing_fields FROM media_metrics "
            "WHERE publish_id=?", (pubid,))).fetchone()
        assert row["views"] == 15000
        assert row["collected_by"] == "feishu"
        assert "new_fans" in _json.loads(row["missing_fields"])
        # cleanup
        for t in ["media_metrics","media_publish","media_account",
                  "media_content","media_persona","media_feishu_unmatched"]:
            await db.execute(f"DELETE FROM {t}")
        await db.commit()
        s = st.load_settings()
        if orig_map is None:
            s.pop("feishu_media_map", None)
        else:
            s["feishu_media_map"] = orig_map
        st.save_settings(s)
        await db.close()
    _run(go())


def test_sync_unmatched_goes_to_table():
    from app.services.media_feishu_sync import sync_from_feishu

    async def go():
        db = await d.get_db()
        records = [{"fields": {"视频链接": "http://orphan", "标题": "野生视频",
                               "播放量": 999}}]
        import app.api.settings as st
        orig_map = st.load_settings().get("feishu_media_map")
        cfg = st.load_settings(); cfg["feishu_media_map"] = {
            "fields": {"post_url": "视频链接", "title": "标题", "views": "播放量"}}
        st.save_settings(cfg)
        rep = await sync_from_feishu(db, records=records)
        assert rep["unmatched"] == 1
        n = (await (await db.execute(
            "SELECT COUNT(*) c FROM media_feishu_unmatched WHERE status='pending'"
        )).fetchone())["c"]
        assert n == 1
        await db.execute("DELETE FROM media_feishu_unmatched"); await db.commit()
        s = st.load_settings()
        if orig_map is None:
            s.pop("feishu_media_map", None)
        else:
            s["feishu_media_map"] = orig_map
        st.save_settings(s)
        await db.close()
    _run(go())
