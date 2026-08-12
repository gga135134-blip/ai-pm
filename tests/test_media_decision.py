"""决策引擎纯函数测试：无 DB/AI/asyncio，直接调纯函数。"""
from app.services.media_decision import (
    _overlap, WEIGHTS, build_decision_context, score_topic, rank_pool,
)


def _ctx(traits=None, audiences=None, anchors=None, materials=None,
         recent=None, history=None, dropped_anchors=None):
    return build_decision_context(
        traits or [], audiences or [], anchors or [],
        materials or [], recent or [], history or [],
        dropped_anchors or [])


def test_overlap_identical_and_disjoint():
    assert _overlap("人工智能落地", "人工智能落地") == 1.0
    assert _overlap("人工智能", "养花种草") == 0.0
    v = _overlap("人工智能落地难", "人工智能很难")
    assert 0.0 < v < 1.0


def test_overlap_short_text_no_crash():
    assert _overlap("", "人工智能") == 0.0
    assert _overlap("a", "") == 0.0


def test_heat_normalized_full():
    t = {"title": "随便", "puzzle": "", "fit_score": 5, "heat": 5}
    res = score_topic(t, _ctx(), "涨粉")
    assert res["factors"]["heat"]["value"] == 1.0


def test_fit_grounded_positioning_weighted_more_than_consistency():
    """fit 从人设条目算：定位(0.65) + 一致(0.35)，不再看 AI 给的 fit_score。"""
    text = "中小企业AI落地避坑"
    t = {"title": text, "puzzle": "", "fit_score": 1, "heat": 3}  # fit_score=1 应被忽略
    # 只命中定位 → fit ≈ 0.65
    pos_only = score_topic(t, _ctx(traits=[
        {"dimension": "positioning", "content": text, "brief": ""}]), "涨粉")
    # 只命中一致(选题域) → fit ≈ 0.35
    con_only = score_topic(t, _ctx(traits=[
        {"dimension": "topics", "content": text, "brief": ""}]), "涨粉")
    assert pos_only["factors"]["fit"]["value"] > con_only["factors"]["fit"]["value"]
    assert abs(pos_only["factors"]["fit"]["value"] - 0.65) < 0.01
    assert abs(con_only["factors"]["fit"]["value"] - 0.35) < 0.01
    # 全命中定位+一致 → fit = 1.0，且忽略了 fit_score=1
    both = score_topic(t, _ctx(traits=[
        {"dimension": "positioning", "content": text, "brief": ""},
        {"dimension": "tone", "content": text, "brief": ""}]), "涨粉")
    assert abs(both["factors"]["fit"]["value"] - 1.0) < 0.01
    assert "定位" in both["report"]


def test_audience_hit_matches_and_weights_by_pay():
    t = {"title": "中小企业AI落地为什么这么难", "puzzle": "", "fit_score": 3, "heat": 3}
    ctx = _ctx(audiences=[
        {"segment": "焦虑的中小老板", "anxiety": "中小企业AI落地难",
         "language": "这玩意能落地不", "pay_willingness": 5}])
    res = score_topic(t, ctx, "涨粉")
    assert res["factors"]["audience_hit"]["value"] > 0
    assert "焦虑的中小老板" in res["report"]


def test_risk_hits_taboo_and_lowers_score():
    t = {"title": "教你三天涨粉十万的黑科技", "puzzle": "", "fit_score": 5, "heat": 5}
    ctx_clean = _ctx()
    ctx_risk = _ctx(traits=[
        {"dimension": "taboo", "content": "不吹涨粉黑科技", "brief": "涨粉黑科技"}])
    clean = score_topic(t, ctx_clean, "涨粉")
    risky = score_topic(t, ctx_risk, "涨粉")
    assert risky["factors"]["risk"]["value"] > 0
    assert risky["score"] < clean["score"]        # 红线拉低总分
    assert "红线" in risky["report"] or "⚠" in risky["report"]


def test_dup_flop_strong_prompt():
    t = {"title": "AI落地避坑指南", "puzzle": "", "fit_score": 3, "heat": 3}
    ctx = _ctx(history=[
        {"title": "AI落地避坑指南", "topic_fingerprint": "AI落地避坑",
         "outcome": "flop"}])
    res = score_topic(t, ctx, "涨粉")
    assert res["factors"]["dup_penalty"]["value"] > 0
    assert "flop" in res["report"] or "失败" in res["report"] or "做过" in res["report"]


def test_degraded_factors_marked_and_not_in_denominator():
    """C 类 evidence/playbook/gap 恒 0 且不进分母：报告标注；一个把所有 active 正项
    都打满、无负项的话题应拿满分 10.0——证明 C 类没被算进分母稀释总分。"""
    text = "中小企业AI落地避坑"
    t = {"title": text, "puzzle": "", "fit_score": 5, "heat": 5}
    ctx = _ctx(
        traits=[{"dimension": "positioning", "content": text, "brief": ""},
                {"dimension": "tone", "content": text, "brief": ""}],
        audiences=[{"segment": "老板", "anxiety": text, "language": "", "pay_willingness": 5}],
        anchors=[{"name": text, "value_prop": "", "status": "proven"}],
        materials=[{"brief": text, "title": ""}])
    res = score_topic(t, ctx, "涨粉")
    assert res["factors"]["playbook"]["value"] == 0
    assert res["factors"]["evidence"]["value"] == 0
    assert res["factors"]["gap"]["value"] == 0
    assert "未计" in res["report"]
    assert res["score"] == 10.0        # 正项全满、负项全 0 → 满分，说明 C 类未进分母


def test_weights_switch_by_phase():
    t = {"title": "蹭热点的话题", "puzzle": "", "fit_score": 1, "heat": 5}
    cold = score_topic(t, _ctx(), "冷启动")
    conv = score_topic(t, _ctx(), "转化")
    assert cold["score"] != conv["score"]


def test_rank_pool_sorts_desc():
    ctx = _ctx()
    topics = [
        {"id": "T1", "title": "低分", "puzzle": "", "fit_score": 1, "heat": 1},
        {"id": "T2", "title": "高分", "puzzle": "", "fit_score": 5, "heat": 5},
    ]
    ranked = rank_pool(topics, ctx, "涨粉")
    assert ranked[0]["id"] == "T2" and ranked[1]["id"] == "T1"
    assert ranked[0]["decision_score"] >= ranked[1]["decision_score"]
    assert isinstance(ranked[0]["decision_report"], str) and ranked[0]["decision_report"]


def test_audience_hit_tagged_uses_pay_willingness_not_overlap():
    """打了标的话题：audience_hit 用命中 segment 的付费意愿，不看字面重叠。"""
    seg = {"id": "S1", "segment": "焦虑老板", "anxiety": "完全不相关的焦虑词",
           "language": "毫不沾边", "pay_willingness": 5}
    t = {"title": "养花种草日常", "puzzle": "", "tagged": 1,
         "audience_ids": ["S1"], "anchor_ids": [], "dropped_drift_ids": []}
    res = score_topic(t, _ctx(audiences=[seg]), "涨粉")
    # 字面毫不相关，但因为 AI 标了命中 → 付费意愿5 → 5/5 = 1.0
    assert res["factors"]["audience_hit"]["value"] == 1.0
    assert "命中" in res["factors"]["audience_hit"]["note"]


def test_audience_hit_tagged_empty_scores_zero_no_fallback():
    """打了标但命中空 = AI 判定不沾受众 → 0，不回落字面重叠。"""
    seg = {"id": "S1", "segment": "焦虑老板", "anxiety": "中小企业AI落地难",
           "language": "能落地不", "pay_willingness": 5}
    t = {"title": "中小企业AI落地难题", "puzzle": "", "tagged": 1,
         "audience_ids": [], "anchor_ids": [], "dropped_drift_ids": []}
    res = score_topic(t, _ctx(audiences=[seg]), "涨粉")
    assert res["factors"]["audience_hit"]["value"] == 0.0


def test_audience_hit_untagged_falls_back_to_overlap():
    """没打标（tagged 缺省）→ 回落字面重叠，老行为不变。"""
    seg = {"id": "S1", "segment": "焦虑老板", "anxiety": "中小企业AI落地难",
           "language": "能落地不", "pay_willingness": 5}
    t = {"title": "中小企业AI落地难", "puzzle": ""}   # 无 tagged
    res = score_topic(t, _ctx(audiences=[seg]), "涨粉")
    assert res["factors"]["audience_hit"]["value"] > 0   # 字面重叠命中


def test_anchor_distance_tagged_status_weight():
    """打标锚点按状态给权重：proven 1.0 > validating 0.7。"""
    proven = {"id": "A1", "name": "训练营", "value_prop": "x", "status": "proven"}
    validating = {"id": "A2", "name": "咨询", "value_prop": "y", "status": "validating"}
    t = {"title": "无关", "puzzle": "", "tagged": 1, "audience_ids": [],
         "anchor_ids": ["A1", "A2"], "dropped_drift_ids": []}
    res = score_topic(t, _ctx(anchors=[proven, validating]), "转化")
    assert res["factors"]["anchor_distance"]["value"] == 1.0   # 取最高 proven


def test_dropped_drift_flags_and_penalizes():
    """飘向已放弃方向 → dropped_drift=1.0 + 报告红旗 + 拉低总分。"""
    dead = {"id": "D1", "name": "微商带货", "value_prop": "z", "status": "dropped"}
    t_drift = {"title": "无关", "puzzle": "", "tagged": 1, "audience_ids": [],
               "anchor_ids": [], "dropped_drift_ids": ["D1"]}
    t_clean = {"title": "无关", "puzzle": "", "tagged": 1, "audience_ids": [],
               "anchor_ids": [], "dropped_drift_ids": []}
    ctx = _ctx(dropped_anchors=[dead])
    r_drift = score_topic(t_drift, ctx, "转化")
    r_clean = score_topic(t_clean, ctx, "转化")
    assert r_drift["factors"]["dropped_drift"]["value"] == 1.0
    assert "已放弃方向" in r_drift["factors"]["dropped_drift"]["note"]
    assert "微商带货" in r_drift["report"]
    assert r_drift["score"] < r_clean["score"]       # 护栏拉低分


def test_dropped_drift_absent_when_clean():
    t = {"title": "无关", "puzzle": "", "tagged": 1, "audience_ids": [],
         "anchor_ids": [], "dropped_drift_ids": []}
    res = score_topic(t, _ctx(), "涨粉")
    assert res["factors"]["dropped_drift"]["value"] == 0.0


def test_id_columns_accept_json_string():
    """容错：id 列是 JSON 字符串（route 忘了解析）也能正确处理。"""
    import json as _json
    seg = {"id": "S1", "segment": "焦虑老板", "anxiety": "x", "language": "y",
           "pay_willingness": 4}
    t = {"title": "无关", "puzzle": "", "tagged": 1,
         "audience_ids": _json.dumps(["S1"]), "anchor_ids": "[]",
         "dropped_drift_ids": "[]"}
    res = score_topic(t, _ctx(audiences=[seg]), "涨粉")
    assert res["factors"]["audience_hit"]["value"] == 0.8   # 4/5


def test_build_context_carries_dropped_anchors():
    ctx = build_decision_context([], [], [], [], [], [], dropped_anchors=[{"id": "D1"}])
    assert ctx["dropped_anchors"] == [{"id": "D1"}]
