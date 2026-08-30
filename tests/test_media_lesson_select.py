"""教训/红线库的筛选与渲染（纯函数，无 DB 无 AI）。"""
from app.services.media_context import (
    select_redlines, select_lessons, render_lesson_block)


def _mk(kind, brief, trigger="", created="2026-08-01"):
    return {"id": brief, "kind": kind, "brief": brief,
            "trigger_context": trigger, "created_at": created}


def test_redlines_capped_at_two():
    """红线超过 2 条时只取前 2（created_at 升序）。"""
    reds = [_mk("redline", "红一", created="2026-08-01"),
            _mk("redline", "红二", created="2026-08-02"),
            _mk("redline", "红三", created="2026-08-03")]
    picked = select_redlines(reds)
    assert [r["brief"] for r in picked] == ["红一", "红二"]


def test_redlines_ignore_lesson_kind():
    """select_redlines 只认 kind='redline'，教训不混进来。"""
    items = [_mk("lesson", "教训甲"), _mk("redline", "红一")]
    assert [r["brief"] for r in select_redlines(items)] == ["红一"]


def test_lessons_ranked_by_trigger_overlap():
    """教训按 trigger_context 与选题文本的 bigram 重合度降序。"""
    items = [_mk("lesson", "不相干", trigger="做菜的时候"),
             _mk("lesson", "该命中", trigger="讲方法论类的内容")]
    picked = select_lessons(items, "四步方法论，让AI稳定做好一件事")
    assert picked[0]["brief"] == "该命中"


def test_lessons_capped_at_three():
    items = [_mk("lesson", f"教训{i}", trigger="讲方法论") for i in range(5)]
    assert len(select_lessons(items, "讲方法论")) == 3


def test_lesson_without_trigger_ranks_last():
    """trigger_context 为空视为 0 分，排在有 trigger 的之后（但不被排除）。"""
    items = [_mk("lesson", "没trigger", trigger=""),
             _mk("lesson", "有trigger", trigger="讲方法论")]
    picked = select_lessons(items, "讲方法论怎么落地")
    assert [x["brief"] for x in picked] == ["有trigger", "没trigger"]


def test_redlines_and_lessons_do_not_compete():
    """核心语义：红线与教训分槽，3 条高匹配教训不会挤掉任何红线。"""
    items = ([_mk("redline", f"红{i}") for i in range(2)]
             + [_mk("lesson", f"教{i}", trigger="讲方法论") for i in range(3)])
    reds = select_redlines(items)
    less = select_lessons(items, "讲方法论")
    assert len(reds) == 2 and len(less) == 3


def test_render_both_blocks():
    block = render_lesson_block(
        [_mk("redline", "不许编数据")],
        [_mk("lesson", "开头别铺垫")])
    assert "【红线（绝对不许违反）】" in block
    assert "- 不许编数据" in block
    assert "【教训（这次特别注意）】" in block
    assert "- 开头别铺垫" in block


def test_render_empty_returns_empty_string():
    """两者皆空返回空串，绝不产生一个只有标题的空块。"""
    assert render_lesson_block([], []) == ""


def test_render_skips_blank_brief():
    """brief 全空时那一块整块不渲染（不留裸标题）。"""
    assert render_lesson_block([_mk("redline", "")], []) == ""
