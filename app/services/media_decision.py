"""决策引擎 V1：给选题池话题可解释打分。纯计算，无 AI/无 DB。
spec: docs/superpowers/specs/2026-08-11-media-phase2-decision-engine-design.md"""

import json


# 权重按 persona.current_phase 三套预设（初值靠经验，L2 复盘迭代）。
# evidence/playbook/gap 当前 0（数据源未建），建好改这里即可。
# 用户看重定位/受众/变现/一致 → fit(定位+一致)/audience_hit/anchor_distance 权重整体抬高，
# 热度/原料相对让位。
WEIGHTS = {
    "冷启动": {"fit": 3, "heat": 3, "audience_hit": 3, "anchor_distance": 2,
              "material_ready": 1, "risk": 3, "fatigue": 1, "dup_penalty": 2,
              "dropped_drift": 2,
              "evidence": 0, "playbook": 0, "gap": 0},
    "涨粉":   {"fit": 4, "heat": 1, "audience_hit": 4, "anchor_distance": 2,
              "material_ready": 2, "risk": 3, "fatigue": 2, "dup_penalty": 2,
              "dropped_drift": 2,
              "evidence": 0, "playbook": 0, "gap": 0},
    "转化":   {"fit": 3, "heat": 1, "audience_hit": 4, "anchor_distance": 4,
              "material_ready": 2, "risk": 3, "fatigue": 2, "dup_penalty": 2,
              "dropped_drift": 3,
              "evidence": 0, "playbook": 0, "gap": 0},
}

# fit 分两半：定位(权重高) + 一致(权重低)。定位看"射程"，一致看"一贯人设/调性"。
FIT_POSITIONING_WEIGHT = 0.65
FIT_CONSISTENCY_WEIGHT = 0.35
FIT_POSITIONING_DIMS = ("positioning", "differentiator")
FIT_CONSISTENCY_DIMS = ("topics", "tone", "signature")


def _bigrams(s: str) -> set:
    s = "".join((s or "").split())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a: str, b: str) -> float:
    """两段中文文本的 2-gram 字符集 Jaccard 重叠，0..1。纯函数。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    union = len(ba | bb)
    return inter / union if union else 0.0


def _clamp15(v) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 3
    return v if 1 <= v <= 5 else 3


_ANCHOR_STATUS_W = {"proven": 1.0, "validating": 0.7, "dropped": 0.3}


def _as_id_list(v) -> list:
    """话题上的 id 列可能是 list 或 JSON 字符串，都转成 list。容错。"""
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    if isinstance(v, str) and v.strip():
        try:
            j = json.loads(v)
            return [x for x in j if isinstance(x, str)] if isinstance(j, list) else []
        except (ValueError, TypeError):
            return []
    return []


def build_decision_context(traits, audiences, anchors, materials,
                           recent_contents, history_contents,
                           dropped_anchors=None) -> dict:
    """把该人设的资产打包成打分上下文（纯数据）。调用方从 DB 查好传入。

    dropped_anchors：已放弃(dropped)的锚点，单独传——供 anchor 状态兜底 +
    dropped_drift 护栏查名字。正常 anchors 里不含 dropped。
    """
    return {
        "traits": list(traits or []),
        "audiences": list(audiences or []),
        "anchors": list(anchors or []),
        "materials": list(materials or []),
        "recent_contents": list(recent_contents or []),
        "history_contents": list(history_contents or []),
        "dropped_anchors": list(dropped_anchors or []),
    }


def _topic_text(topic: dict) -> str:
    return ((topic.get("title") or "") + " " + (topic.get("puzzle") or "")).strip()


def score_topic(topic: dict, ctx: dict, phase: str) -> dict:
    """给一个话题打分。返回 {"score"(0-10), "report", "factors"}。纯函数。"""
    W = WEIGHTS.get(phase, WEIGHTS["涨粉"])
    text = _topic_text(topic)
    factors = {}

    tagged = bool(topic.get("tagged"))
    aud_ids = _as_id_list(topic.get("audience_ids"))
    anc_ids = _as_id_list(topic.get("anchor_ids"))
    drift_ids = _as_id_list(topic.get("dropped_drift_ids"))
    aud_by_id = {a.get("id"): a for a in ctx["audiences"]}
    anc_by_id = {a.get("id"): a for a in ctx["anchors"]}
    anc_by_id.update({a.get("id"): a for a in ctx.get("dropped_anchors", [])})

    # ── 热度：已存字段（AI 生成时给，暂无真实热度源，见 spec/记忆软肋条）──
    heat = (_clamp15(topic.get("heat", 3)) - 1) / 4
    factors["heat"] = {"value": heat, "note": f"热度 {'★' * _clamp15(topic.get('heat', 3))}"}

    # ── fit：从人设条目算 = 定位(0.65) + 一致(0.35)。不用 AI 给的 fit_score。──
    def _best_trait_overlap(dims):
        best, hit = 0.0, None
        for tr in ctx["traits"]:
            if tr.get("dimension") in dims:
                ov = _overlap(text, (tr.get("content") or "") + " " + (tr.get("brief") or ""))
                if ov > best:
                    best, hit = ov, tr
        return best, hit

    positioning, pos_hit = _best_trait_overlap(FIT_POSITIONING_DIMS)
    consistency, con_hit = _best_trait_overlap(FIT_CONSISTENCY_DIMS)
    fit = FIT_POSITIONING_WEIGHT * positioning + FIT_CONSISTENCY_WEIGHT * consistency
    fit_bits = []
    if pos_hit and positioning > 0:
        fit_bits.append(f"契合定位'{pos_hit.get('brief') or pos_hit.get('content', '')}'")
    if con_hit and consistency > 0:
        fit_bits.append(f"延续一贯'{con_hit.get('brief') or con_hit.get('content', '')}'")
    factors["fit"] = {"value": fit,
                      "note": "，".join(fit_bits) if fit_bits else "与定位/一贯人设关联弱"}

    # ── B 类：受众/锚点/护栏。tagged 时读 AI 语义标，未标回落字面重叠 ──
    # ── audience_hit：优先 AI 语义标，没标回落字面重叠 ──
    # 注意：tagged 命中给满额资产权重，未标回落给 overlap×权重（overlap≤1 通常更小）。
    # 故混合池（部分老话题未补标）里未标话题会系统性偏低——用「先全池标注再排序」的
    # 工作流规避（AI标注按钮一次补全 tagged=0 全部，recommend 产出即 tagged=1）。
    if tagged:
        hits = [aud_by_id[i] for i in aud_ids if i in aud_by_id]
        if hits:
            best_seg = max(hits, key=lambda a: _clamp15(a.get("pay_willingness", 3)))
            pw = _clamp15(best_seg.get("pay_willingness", 3))
            factors["audience_hit"] = {
                "value": pw / 5,
                "note": f"AI判定命中'{best_seg.get('segment', '')}'，付费意愿{'★' * pw}"}
        else:
            factors["audience_hit"] = {"value": 0.0, "note": "AI判定未明显命中受众"}
    else:
        best_aud, best_seg = 0.0, None
        for a in ctx["audiences"]:
            ov = _overlap(text, (a.get("anxiety") or "") + " " + (a.get("language") or ""))
            hit = ov * (_clamp15(a.get("pay_willingness", 3)) / 5)
            if hit > best_aud:
                best_aud, best_seg = hit, a
        factors["audience_hit"] = {
            "value": best_aud,
            "note": (f"命中'{best_seg.get('segment', '')}'的焦虑，付费意愿"
                     f"{'★' * _clamp15(best_seg.get('pay_willingness', 3))}")
                    if best_seg and best_aud > 0 else "未明显命中受众"}

    # ── anchor_distance：优先 AI 语义标（按锚点状态给权重），没标回落重叠 ──
    if tagged:
        hits = [anc_by_id[i] for i in anc_ids if i in anc_by_id]
        if hits:
            best_anchor = max(hits, key=lambda a: _ANCHOR_STATUS_W.get(a.get("status"), 0.5))
            factors["anchor_distance"] = {
                "value": _ANCHOR_STATUS_W.get(best_anchor.get("status"), 0.5),
                "note": f"AI判定服务锚点'{best_anchor.get('name', '')}'（{best_anchor.get('status', '')}）"}
        else:
            factors["anchor_distance"] = {"value": 0.0, "note": "AI判定离生意锚点较远"}
    else:
        best_anc, best_anchor = 0.0, None
        for a in ctx["anchors"]:
            ov = _overlap(text, (a.get("name") or "") + " " + (a.get("value_prop") or ""))
            if ov > best_anc:
                best_anc, best_anchor = ov, a
        factors["anchor_distance"] = {
            "value": best_anc,
            "note": f"贴近锚点'{best_anchor.get('name', '')}'" if best_anchor and best_anc > 0
                    else "离生意锚点较远"}

    # material_ready：有现成原料可用
    best_mat, best_material = 0.0, None
    for m in ctx["materials"]:
        ov = _overlap(text, (m.get("brief") or "") + " " + (m.get("title") or ""))
        if ov > best_mat:
            best_mat, best_material = ov, m
    factors["material_ready"] = {
        "value": best_mat,
        "note": f"有现成原料'{best_material.get('brief') or best_material.get('title', '')}'可用"
                if best_material and best_mat > 0 else "无现成原料，开工成本高"}

    # risk（减）：撞红线
    best_risk, risk_hit = 0.0, None
    for t in ctx["traits"]:
        if t.get("dimension") == "taboo":
            ov = _overlap(text, (t.get("content") or "") + " " + (t.get("brief") or ""))
            if ov > best_risk:
                best_risk, risk_hit = ov, t
    factors["risk"] = {
        "value": best_risk,
        "note": f"⚠️ 撞红线'{risk_hit.get('brief') or risk_hit.get('content', '')}'"
                if risk_hit and best_risk > 0 else "无红线风险"}

    # fatigue（减）：近期同方向扎堆
    rc = ctx["recent_contents"]
    fatigue = (sum(_overlap(text, (c.get("title") or "") + " " + (c.get("brief") or ""))
                   for c in rc) / len(rc)) if rc else 0.0
    factors["fatigue"] = {"value": fatigue,
                          "note": "近期同方向偏多" if fatigue > 0.2 else "近期不重复"}

    # dup_penalty（减）：撞历史内容
    best_dup, dup_hit = 0.0, None
    for c in ctx["history_contents"]:
        ov = _overlap(text, (c.get("title") or "") + " " + (c.get("topic_fingerprint") or ""))
        if ov > best_dup:
            best_dup, dup_hit = ov, c
    dup_note = "无历史重复"
    if dup_hit and best_dup > 0:
        oc = dup_hit.get("outcome") or ""
        if oc == "flop":
            dup_note = f"⚠️ 此方向做过且 flop（'{dup_hit.get('title', '')}'），换角度再说明"
        elif oc == "hit":
            dup_note = f"该方向曾爆过（'{dup_hit.get('title', '')}'），可换新角度重做"
        else:
            dup_note = f"与历史内容'{dup_hit.get('title', '')}'相近"
    factors["dup_penalty"] = {"value": best_dup, "note": dup_note}

    # ── dropped_drift（减·护栏）：飘向已放弃的生意方向 ──
    if tagged and drift_ids:
        names = "、".join(anc_by_id[i].get("name", "") for i in drift_ids if i in anc_by_id) \
                or "已放弃方向"
        factors["dropped_drift"] = {
            "value": 1.0,
            "note": f"⚠️ 往已放弃方向'{names}'飘 —— 当初花大代价才验证它不行，真做想清楚"}
    else:
        factors["dropped_drift"] = {"value": 0.0, "note": "未飘向已放弃方向"}

    # ── C 类：缺数据源，降级 ──
    factors["evidence"] = {"value": 0.0, "note": "⚙️ 历史数据不足，此项未计"}
    factors["playbook"] = {"value": 0.0, "note": "⚙️ 打法库未建，此项未计"}
    factors["gap"] = {"value": 0.0, "note": "⚙️ 内容缺口分析待补，此项未计"}

    # ── 归一化：正项均值 − 负项均值。正负各自归一，避免负项权重挤进正项分母
    #    导致满分话题也上不去（C 类权重为 0，天然不进任何分母）。
    #    注意：负项常驻分母——即使某负项 value=0（如未触发的 dropped_drift）其权重
    #    仍占 neg_w，这是有意的（与 risk/fatigue/dup 一致），只影响绝对分不影响同质排序。──
    pos = ["fit", "heat", "audience_hit", "anchor_distance", "material_ready"]
    neg = ["risk", "fatigue", "dup_penalty", "dropped_drift"]
    pos_w = sum(W[k] for k in pos)
    neg_w = sum(W[k] for k in neg)
    pos_part = (sum(factors[k]["value"] * W[k] for k in pos) / pos_w) if pos_w else 0.0
    neg_part = (sum(factors[k]["value"] * W[k] for k in neg) / neg_w) if neg_w else 0.0
    score01 = max(0.0, min(1.0, pos_part - neg_part))
    score = round(score01 * 10, 1)

    # ── 报告 ──
    lines = [f"决策得分 {score}/10（{phase}期权重）"]
    for k in ["fit", "heat", "audience_hit", "anchor_distance", "material_ready"]:
        lines.append(f"＋{factors[k]['note']}")
    for k in ["risk", "fatigue", "dup_penalty", "dropped_drift"]:
        if factors[k]["value"] > 0:
            lines.append(f"－{factors[k]['note']}")
    for k in ["evidence", "playbook", "gap"]:
        lines.append(factors[k]["note"])
    report = "\n".join(lines)

    return {"score": score, "report": report, "factors": factors}


def rank_pool(topics: list, ctx: dict, phase: str) -> list:
    """给一批话题打分，附加 decision_score/decision_report，按分降序。"""
    out = []
    for t in topics:
        r = score_topic(t, ctx, phase)
        t = dict(t)
        t["decision_score"] = r["score"]
        t["decision_report"] = r["report"]
        out.append(t)
    out.sort(key=lambda x: x["decision_score"], reverse=True)
    return out
