from app.services.media_context import (
    INJECTION_BUDGET, select_by_budget, render_brief_list,
    build_script_context, extract_json,
)


# ---------- 预算截断 ----------

def test_budget_has_hard_caps_for_every_slot():
    # 这些上限是"体系可以无限重"的前提，改动需同步改 spec §6
    assert INJECTION_BUDGET["trait"] == 8
    assert INJECTION_BUDGET["signature"] == 3
    assert INJECTION_BUDGET["playbook"] == 2
    assert INJECTION_BUDGET["material"] == 3
    assert INJECTION_BUDGET["lesson"] == 3
    assert INJECTION_BUDGET["audience"] == 1


def test_select_truncates_to_budget():
    items = [{"id": f"t{i}", "brief": f"b{i}", "confidence": 1} for i in range(20)]
    assert len(select_by_budget(items, "trait")) == 8


def test_select_keeps_highest_scores_first():
    items = [
        {"id": "low", "brief": "低", "confidence": 1},
        {"id": "high", "brief": "高", "confidence": 5},
        {"id": "mid", "brief": "中", "confidence": 3},
    ]
    got = select_by_budget(items, "signature")  # 上限 3，全保留但要排序
    assert [i["id"] for i in got] == ["high", "mid", "low"]


def test_select_under_budget_returns_all():
    items = [{"id": "a", "brief": "x", "confidence": 2}]
    assert len(select_by_budget(items, "trait")) == 1


def test_select_on_empty_returns_empty():
    assert select_by_budget([], "trait") == []


def test_select_unknown_slot_returns_empty():
    # 未定义预算的槽位不允许注入，防止有人偷偷加注入点绕过预算
    items = [{"id": "a", "brief": "x", "confidence": 2}]
    assert select_by_budget(items, "not_a_slot") == []


def test_select_handles_missing_score_key():
    items = [{"id": "a", "brief": "x"}, {"id": "b", "brief": "y", "confidence": 5}]
    got = select_by_budget(items, "trait")
    assert [i["id"] for i in got] == ["b", "a"]


def test_select_custom_score_key():
    items = [
        {"id": "a", "brief": "x", "hit_rate": 0.2},
        {"id": "b", "brief": "y", "hit_rate": 0.9},
    ]
    got = select_by_budget(items, "playbook", score_key="hit_rate")
    assert [i["id"] for i in got] == ["b", "a"]


# ---------- brief 渲染 ----------

def test_render_brief_list_uses_brief_not_detail():
    # 注入提示词的是 brief（≤30字），detail 要 AI 自己调工具读
    items = [{"id": "t1", "brief": "3秒进主题", "content": "这里是800字的完整方法论……"}]
    out = render_brief_list(items, "人设条目")
    assert "3秒进主题" in out
    assert "800字的完整方法论" not in out


def test_render_brief_falls_back_to_truncated_content():
    items = [{"id": "t1", "brief": "", "content": "x" * 200}]
    out = render_brief_list(items, "人设条目")
    assert len(out) < 120  # 没有 brief 时截断 content，不能整段灌进去


def test_render_brief_list_empty_returns_empty_string():
    assert render_brief_list([], "人设条目") == ""


# ---------- 上下文拼装 ----------

def _persona():
    return {"id": "p1", "name": "小雅", "one_liner": "陪职场妈妈喘口气",
            "current_phase": "冷启动"}


def test_build_script_context_includes_persona_identity():
    text, ids = build_script_context(_persona(), [])
    assert "小雅" in text
    assert "陪职场妈妈喘口气" in text


def test_build_script_context_returns_injected_ids():
    traits = [{"id": "t1", "brief": "语气像闺蜜", "dimension": "tone", "confidence": 5}]
    text, ids = build_script_context(_persona(), traits)
    assert ids == ["t1"]


def test_build_script_context_separates_signature_from_trait_budget():
    # signature 是记忆点，必须单独占槽，不能被普通条目挤掉
    traits = [{"id": f"t{i}", "brief": f"普通{i}", "dimension": "positioning",
               "confidence": 5} for i in range(20)]
    traits += [{"id": "sig1", "brief": "开场必说'姐妹们'", "dimension": "signature",
                "confidence": 1}]
    text, ids = build_script_context(_persona(), traits)
    assert "sig1" in ids          # 置信度最低但仍被注入
    assert "开场必说" in text


def test_build_script_context_total_never_exceeds_budget():
    traits = [{"id": f"t{i}", "brief": f"普通{i}", "dimension": "positioning",
               "confidence": 3} for i in range(50)]
    traits += [{"id": f"s{i}", "brief": f"记忆点{i}", "dimension": "signature",
                "confidence": 3} for i in range(50)]
    text, ids = build_script_context(_persona(), traits)
    assert len(ids) <= INJECTION_BUDGET["trait"] + INJECTION_BUDGET["signature"]


def test_build_script_context_ignores_archived_traits():
    traits = [
        {"id": "old", "brief": "过时的定位", "dimension": "positioning",
         "confidence": 5, "status": "archived"},
        {"id": "new", "brief": "现在的定位", "dimension": "positioning",
         "confidence": 3, "status": "active"},
    ]
    text, ids = build_script_context(_persona(), traits)
    assert ids == ["new"]
    assert "过时的定位" not in text


# ---------- JSON 解析 ----------

def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fenced_block():
    raw = 'AI 的废话\n```json\n{"a": 1}\n```\n收尾废话'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_from_fence_without_lang():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_array_when_expected():
    assert extract_json('[{"a": 1}]', expect="array") == [{"a": 1}]


def test_extract_json_array_from_surrounding_prose():
    raw = '这是我的推荐：\n[{"title": "谜题式选题"}]\n希望有帮助'
    assert extract_json(raw, expect="array") == [{"title": "谜题式选题"}]


def test_extract_json_allows_real_newlines_in_strings():
    # DeepSeek 常在字符串里直接输出真实换行，strict=False 必须开
    assert extract_json('{"script": "第一行\n第二行"}')["script"] == "第一行\n第二行"


def test_extract_json_failure_returns_empty_object():
    assert extract_json("完全不是 JSON 的一段话") == {}


def test_extract_json_failure_returns_empty_array_when_array_expected():
    assert extract_json("完全不是 JSON 的一段话", expect="array") == []


def test_extract_json_empty_input():
    assert extract_json("") == {}
