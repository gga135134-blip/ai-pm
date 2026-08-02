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
