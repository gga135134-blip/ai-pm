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


def test_clamp_rating_handles_ai_garbage():
    from app.services.media_ai import _clamp
    assert _clamp(3, 3) == 3
    assert _clamp(0, 3) == 1        # AI 给 0 → 夹到 1
    assert _clamp(10, 3) == 5       # AI 给 10 → 夹到 5
    assert _clamp("4", 3) == 4      # AI 给字符串
    assert _clamp(None, 3) == 3     # AI 没给
    assert _clamp("很高", 3) == 3   # AI 给中文


# ---------- 二期 🅐：证据/角度/原料注入拼装 ----------

from app.services.media_context import (
    render_evidence_block, render_angle_block,
    select_materials, render_material_block,
)


def test_render_evidence_lists_items():
    ev = [{"item": "我去年帮一家做鞋的落地了客服AI", "item_type": "experience"},
          {"item": "转化率从2%到5%", "item_type": "data"}]
    text = render_evidence_block(ev)
    assert "真实素材" in text
    assert "做鞋" in text and "2%到5%" in text


def test_render_evidence_empty():
    assert render_evidence_block([]) == ""


def test_render_angle_block():
    text = render_angle_block("从我踩过的坑切入", "第一人称踩坑最可信")
    assert "从我踩过的坑切入" in text
    assert "第一人称踩坑最可信" in text


def test_render_angle_empty():
    assert render_angle_block("", "任何理由") == ""


def test_select_materials_prefers_unused_and_caps():
    mats = [{"id": f"m{i}", "brief": f"料{i}", "use_count": i} for i in range(10)]
    got = select_materials(mats)
    assert len(got) == 3  # INJECTION_BUDGET['material']
    assert [m["id"] for m in got] == ["m0", "m1", "m2"]  # use_count 升序


def test_render_material_block_uses_brief():
    mats = [{"id": "m1", "brief": "做鞋厂客服AI案例", "use_count": 0}]
    text = render_material_block(mats)
    assert "可复用原料" in text and "做鞋厂" in text


def test_build_script_context_injects_current_phase_and_permanent_only():
    persona = {"name": "嘉姐", "one_liner": "务实落地AI", "current_phase": "AI落地期"}
    traits = [
        {"id": "a", "dimension": "positioning", "brief": "帮中小企业落地AI",
         "status": "active", "phase_tag": "AI落地期", "confidence": 5},
        {"id": "b", "dimension": "taboo", "brief": "不编造本人经历",
         "status": "active", "phase_tag": "", "confidence": 5},           # 永久
        {"id": "c", "dimension": "positioning", "brief": "教你月入十万",
         "status": "active", "phase_tag": "旧带货期", "confidence": 5},   # 别的阶段
    ]
    text, ids = build_script_context(persona, traits)
    assert "帮中小企业落地AI" in text      # 当前阶段
    assert "不编造本人经历" in text        # 永久
    assert "教你月入十万" not in text      # 别的阶段被挡
    assert "a" in ids and "b" in ids and "c" not in ids
