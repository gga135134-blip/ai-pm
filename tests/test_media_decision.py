"""决策引擎纯函数测试：无 DB/AI/asyncio，直接调纯函数。"""
from app.services.media_decision import (
    _overlap, WEIGHTS, build_decision_context, score_topic, rank_pool,
)


def _ctx(traits=None, audiences=None, anchors=None, materials=None,
         recent=None, history=None):
    return build_decision_context(
        traits or [], audiences or [], anchors or [],
        materials or [], recent or [], history or [])


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
