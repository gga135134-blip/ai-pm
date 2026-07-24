from app.services.media_metrics import normalize_metrics, METRIC_FIELDS


def test_all_five_fields_always_present():
    got = normalize_metrics({})
    assert set(got) == set(METRIC_FIELDS)
    assert all(v == 0 for v in got.values())


def test_plain_integers():
    got = normalize_metrics({"views": 1234, "likes": 56})
    assert got["views"] == 1234
    assert got["likes"] == 56


def test_string_integers():
    assert normalize_metrics({"views": "1234"})["views"] == 1234


def test_chinese_wan_unit():
    # 平台后台普遍显示"1.2万"而不是 12000
    assert normalize_metrics({"views": "1.2万"})["views"] == 12000
    assert normalize_metrics({"views": "3万"})["views"] == 30000


def test_chinese_yi_unit():
    assert normalize_metrics({"views": "1.5亿"})["views"] == 150000000


def test_k_and_w_suffix():
    assert normalize_metrics({"views": "3.5k"})["views"] == 3500
    assert normalize_metrics({"views": "12K"})["views"] == 12000
    assert normalize_metrics({"views": "2.4w"})["views"] == 24000


def test_comma_separated():
    assert normalize_metrics({"views": "1,234,567"})["views"] == 1234567


def test_plus_prefix_for_fans():
    assert normalize_metrics({"new_fans": "+128"})["new_fans"] == 128


def test_garbage_becomes_zero():
    assert normalize_metrics({"views": "暂无"})["views"] == 0
    assert normalize_metrics({"views": None})["views"] == 0
    assert normalize_metrics({"views": "--"})["views"] == 0


def test_negative_clamped_to_zero():
    # 播放量不可能为负；AI 看错负号不该污染数据
    assert normalize_metrics({"views": -5})["views"] == 0


def test_float_truncated():
    assert normalize_metrics({"views": 12.9})["views"] == 12


def test_unknown_keys_ignored():
    got = normalize_metrics({"views": 5, "完播率": "35%"})
    assert set(got) == set(METRIC_FIELDS)


def test_alias_keys_from_ai():
    # AI 有时用中文键名返回
    got = normalize_metrics({"播放": 100, "点赞": 20, "评论": 5,
                             "转发": 3, "涨粉": 8})
    assert got == {"views": 100, "likes": 20, "comments": 5,
                   "shares": 3, "new_fans": 8}
