"""media 打标 helper 的纯函数/异步测试。"""
from app.services.media_ai import _clean_ids


def test_clean_ids_keeps_valid_drops_bogus():
    valid = {"A", "B", "C"}
    assert _clean_ids(["A", "X", "B"], valid) == ["A", "B"]


def test_clean_ids_dedup_and_order():
    assert _clean_ids(["B", "B", "A"], {"A", "B"}) == ["B", "A"]


def test_clean_ids_non_list_returns_empty():
    assert _clean_ids(None, {"A"}) == []
    assert _clean_ids("A", {"A"}) == []      # 字符串不是 list
    assert _clean_ids([1, 2, {"x": 1}], {"A"}) == []   # 非字符串元素丢弃
