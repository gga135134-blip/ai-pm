"""L2 advisory 的归一化与采纳。核心是向后兼容旧的纯字符串格式。"""
import asyncio

from app.services.media_review_cycle import normalize_advisory_items
from app.services.media_lesson import list_lessons, create_lesson
from tests.media_helpers import make_db


def test_normalize_legacy_string_items():
    """旧数据是纯字符串数组，归一化成 dict，缺的键补空串。"""
    out = normalize_advisory_items(["开头别铺垫", "别讲太深"])
    assert out == [
        {"brief": "开头别铺垫", "trigger_context": "", "evidence": ""},
        {"brief": "别讲太深", "trigger_context": "", "evidence": ""},
    ]


def test_normalize_structured_items_pass_through():
    out = normalize_advisory_items(
        [{"brief": "开头别铺垫", "trigger_context": "口播", "evidence": "《X》"}])
    assert out[0]["trigger_context"] == "口播"
    assert out[0]["evidence"] == "《X》"


def test_normalize_fills_missing_keys():
    out = normalize_advisory_items([{"brief": "只有brief"}])
    assert out == [{"brief": "只有brief", "trigger_context": "", "evidence": ""}]


def test_normalize_drops_blank_and_junk():
    """空 brief、空字符串、非 dict 非 str 的垃圾一律丢掉，不报错。"""
    out = normalize_advisory_items(["", "  ", {"brief": ""}, 42, None, "留下"])
    assert out == [{"brief": "留下", "trigger_context": "", "evidence": ""}]


def test_normalize_handles_non_list():
    """AI 返回的不是数组时（None / dict / 字符串）返回空列表，不抛异常。"""
    assert normalize_advisory_items(None) == []
    assert normalize_advisory_items({"a": 1}) == []
    assert normalize_advisory_items("一句话") == []


def test_adopt_from_advisory_lands_in_lesson_table():
    """采纳后进 media_lesson，source 标 l2_advisory 以便追溯。"""
    async def run():
        db = await make_db()
        await db.execute(
            "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
            "VALUES ('P1','嘉姐','帮中小企业落地AI','涨粉','active')")
        await db.commit()
        await create_lesson(db, "P1", "lesson", "开头别铺垫",
                            trigger_context="口播", evidence="8/20 复盘",
                            source="l2_advisory")
        rows = await list_lessons(db, "P1")
        await db.close()
        return rows

    rows = asyncio.run(run())
    assert rows[0]["source"] == "l2_advisory"
    assert rows[0]["trigger_context"] == "口播"
