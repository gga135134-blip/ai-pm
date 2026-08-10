# 决策引擎 V1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给选题池话题可解释地打分排序，输出决策报告，让人只需拍板选哪个。纯计算无 AI。

**Architecture:** 新建纯计算模块 `app/services/media_decision.py`（`_overlap` 中文 2-gram 重叠 + `WEIGHTS` 阶段预设 + `build_decision_context` + `score_topic` + `rank_pool`）。选题页加「决策排序」按钮 → `POST /media/topics/rank` 查资产、算全池、写 `decision_score`/`decision_report`（两列已存，零 migration）→ 按分排序展示。缺数据源的因子（打法/历史数据/缺口）优雅降级贡献 0 并在报告标注。

**Tech Stack:** Python（纯函数，无 asyncio/DB/AI 依赖）+ FastAPI 路由 + Jinja2。测试 pytest（纯函数直测 + TestClient 路由测）。

## Global Constraints

- **纯计算无 AI**：`media_decision.py` 不 import ask_ai / 不调模型 / 不碰 DB；所有输入靠调用方查好传入。全部可单测。
- **零 migration**：`media_topic` 已有 `decision_score REAL` + `decision_report TEXT`；不加表不加列。
- **诚实不编造**：C 类降级因子（evidence/playbook/gap）在报告里明确标"⚙️ 未计"，绝不假装打分。
- **不动 AI 推选题（topics_ai_recommend）、不动写稿、不动 finalize。**
- 因子归一到 0–1；总分 = Σ(signed_value×weight)/Σ|weight|，clamp[0,1] ×10 → 0–10 分。C 类权重为 0，自然不进分母。
- 权重按 `persona.current_phase` 取；未知阶段回落 `"涨粉"`。
- 改模板用 Edit/Write；Jinja 无 tojson；TemplateResponse 三参数（现有 `_tpl`）；模板 dict 键别用 items/keys/values/get。
- 跑测试：`python -m pytest tests/ -q -p no:cacheprovider`（别用 PowerShell 管道 Select-Object，会假挂；若 pytest 卡住先 `taskkill //F //IM python.exe` 清残留进程再跑）。
- 现有全套基线 **177 passed**，每个 Task 完成后应仍全绿。

---

### Task 1: `media_decision.py` 纯计算核心 + 单元测试

**Files:**
- Create: `app/services/media_decision.py`
- Test: `tests/test_media_decision.py`（新建）

**Interfaces:**
- Produces（后续 Task 依赖）：
  - `WEIGHTS: dict[str, dict[str, float]]`（键：冷启动/涨粉/转化）
  - `def _overlap(a: str, b: str) -> float`（0..1）
  - `def build_decision_context(traits, audiences, anchors, materials, recent_contents, history_contents) -> dict`
  - `def score_topic(topic: dict, ctx: dict, phase: str) -> dict` → `{"score": float, "report": str, "factors": dict}`
  - `def rank_pool(topics: list[dict], ctx: dict, phase: str) -> list[dict]`（每个话题 dict 附加 `decision_score`/`decision_report`，按 score 降序）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_decision.py`：

```python
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
    # 部分重叠介于 0 和 1
    v = _overlap("人工智能落地难", "人工智能很难")
    assert 0.0 < v < 1.0


def test_overlap_short_text_no_crash():
    assert _overlap("", "人工智能") == 0.0
    assert _overlap("a", "") == 0.0


def test_fit_and_heat_normalized_full():
    t = {"title": "随便", "puzzle": "", "fit_score": 5, "heat": 5}
    res = score_topic(t, _ctx(), "涨粉")
    assert res["factors"]["fit"]["value"] == 1.0
    assert res["factors"]["heat"]["value"] == 1.0


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
    """C 类 evidence/playbook/gap 恒 0 且不进分母：报告标注，且总分只由 active 因子决定。"""
    t = {"title": "随便话题", "puzzle": "", "fit_score": 5, "heat": 5}
    res = score_topic(t, _ctx(), "涨粉")
    assert res["factors"]["playbook"]["value"] == 0
    assert res["factors"]["evidence"]["value"] == 0
    assert res["factors"]["gap"]["value"] == 0
    # 报告含"未计"标注
    assert "未计" in res["report"]
    # 纯 fit=heat=1、无风险 → 归一后应接近满分（说明 C 类没拖低）
    assert res["score"] >= 9.0


def test_weights_switch_by_phase():
    # heat 高 fit 低的话题：冷启动(重heat)应高于转化(轻heat重anchor)
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_decision.py -q -p no:cacheprovider`
Expected: FAIL（`ModuleNotFoundError: app.services.media_decision`）

- [ ] **Step 3: 写实现**

新建 `app/services/media_decision.py`：

```python
"""决策引擎 V1：给选题池话题可解释打分。纯计算，无 AI/无 DB。
spec: docs/superpowers/specs/2026-08-11-media-phase2-decision-engine-design.md"""


# 权重按 persona.current_phase 三套预设（初值靠经验，L2 复盘迭代）。
# evidence/playbook/gap 当前 0（数据源未建），建好改这里即可。
WEIGHTS = {
    "冷启动": {"fit": 2, "heat": 3, "audience_hit": 2, "anchor_distance": 1,
              "material_ready": 2, "risk": 3, "fatigue": 1, "dup_penalty": 2,
              "evidence": 0, "playbook": 0, "gap": 0},
    "涨粉":   {"fit": 3, "heat": 2, "audience_hit": 3, "anchor_distance": 1,
              "material_ready": 2, "risk": 3, "fatigue": 2, "dup_penalty": 2,
              "evidence": 0, "playbook": 0, "gap": 0},
    "转化":   {"fit": 2, "heat": 1, "audience_hit": 3, "anchor_distance": 3,
              "material_ready": 2, "risk": 3, "fatigue": 2, "dup_penalty": 2,
              "evidence": 0, "playbook": 0, "gap": 0},
}


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


def build_decision_context(traits, audiences, anchors, materials,
                           recent_contents, history_contents) -> dict:
    """把该人设的资产打包成打分上下文（纯数据）。调用方从 DB 查好传入。"""
    return {
        "traits": list(traits or []),
        "audiences": list(audiences or []),
        "anchors": list(anchors or []),
        "materials": list(materials or []),
        "recent_contents": list(recent_contents or []),
        "history_contents": list(history_contents or []),
    }


def _topic_text(topic: dict) -> str:
    return ((topic.get("title") or "") + " " + (topic.get("puzzle") or "")).strip()


def score_topic(topic: dict, ctx: dict, phase: str) -> dict:
    """给一个话题打分。返回 {"score"(0-10), "report", "factors"}。纯函数。"""
    W = WEIGHTS.get(phase, WEIGHTS["涨粉"])
    text = _topic_text(topic)
    factors = {}

    # ── A 类：已存字段 ──
    fit = (_clamp15(topic.get("fit_score", 3)) - 1) / 4
    heat = (_clamp15(topic.get("heat", 3)) - 1) / 4
    factors["fit"] = {"value": fit, "note": f"契合人设 {'★' * _clamp15(topic.get('fit_score', 3))}"}
    factors["heat"] = {"value": heat, "note": f"热度 {'★' * _clamp15(topic.get('heat', 3))}"}

    # ── B 类：纯计算重叠 ──
    # audience_hit：命中 segment 焦虑/原话，按付费意愿加权
    best_aud, best_seg = 0.0, None
    for a in ctx["audiences"]:
        ov = _overlap(text, (a.get("anxiety") or "") + " " + (a.get("language") or ""))
        hit = ov * (_clamp15(a.get("pay_willingness", 3)) / 5)
        if hit > best_aud:
            best_aud, best_seg = hit, a
    factors["audience_hit"] = {
        "value": best_aud,
        "note": (f"命中'{best_seg.get('segment', '')}'的焦虑，付费意愿"
                 f"{'★' * _clamp15(best_seg.get('pay_willingness', 3))}") if best_seg and best_aud > 0
                else "未明显命中受众"}

    # anchor_distance：贴近某锚点（越贴越高）
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

    # ── C 类：缺数据源，降级 ──
    factors["evidence"] = {"value": 0.0, "note": "⚙️ 历史数据不足，此项未计"}
    factors["playbook"] = {"value": 0.0, "note": "⚙️ 打法库未建，此项未计"}
    factors["gap"] = {"value": 0.0, "note": "⚙️ 内容缺口分析待补，此项未计"}

    # ── 归一化：正项加、减项减，除以 active 权重和 ──
    pos = ["fit", "heat", "audience_hit", "anchor_distance", "material_ready"]
    neg = ["risk", "fatigue", "dup_penalty"]
    numer = sum(factors[k]["value"] * W[k] for k in pos) \
        - sum(factors[k]["value"] * W[k] for k in neg)
    denom = sum(W[k] for k in pos) + sum(W[k] for k in neg)
    score01 = (numer / denom) if denom else 0.0
    score01 = max(0.0, min(1.0, score01))
    score = round(score01 * 10, 1)

    # ── 报告 ──
    lines = [f"决策得分 {score}/10（{phase}期权重）"]
    for k in ["fit", "heat", "audience_hit", "anchor_distance", "material_ready"]:
        lines.append(f"＋{factors[k]['note']}")
    for k in ["risk", "fatigue", "dup_penalty"]:
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_decision.py -q -p no:cacheprovider`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_decision.py tests/test_media_decision.py
git commit -m "feat(media): 决策引擎纯计算核心——11项打分+阶段权重+优雅降级"
```

---

### Task 2: `/media/topics/rank` 路由 + 选题页按钮 + 报告展示

**Files:**
- Modify: `app/api/media.py`（import `media_decision`；加 `/media/topics/rank` 路由）
- Modify: `app/templates/media_topics.html`（头部加「决策排序」按钮；话题卡显示分数 + 报告 details）
- Test: `tests/test_media_routes.py`（追加 1 路由测）

**Interfaces:**
- Consumes：`build_decision_context` / `rank_pool`（Task 1）、`_first_persona_id`（现有）
- Produces：`POST /media/topics/rank`（写 decision_score/report，重定向 /media/topics）

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_routes.py` 末尾追加：

```python
# ─────────────── 决策引擎 ───────────────

def test_topics_rank_writes_scores():
    _only_active_persona()

    async def seed():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_topic WHERE persona_id='RTP2'")
            await db.execute("INSERT INTO media_audience "
                "(id,persona_id,segment,anxiety,language,pay_willingness,status) VALUES "
                "('SG1','RTP2','焦虑老板','中小企业AI落地难','能落地不',5,'active')")
            await db.execute("INSERT INTO media_topic "
                "(id,persona_id,title,puzzle,fit_score,heat,status) VALUES "
                "('TP_HI','RTP2','中小企业AI落地为什么这么难','',5,5,'pool'),"
                "('TP_LO','RTP2','随便一个不相关话题','',1,1,'pool')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    r = _client().post("/media/topics/rank", follow_redirects=False)
    assert r.status_code == 302

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT id,decision_score,decision_report FROM media_topic "
                "WHERE persona_id='RTP2' ORDER BY decision_score DESC")
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    rows = asyncio.run(check())
    assert rows[0]["id"] == "TP_HI"                 # 高契合+命中受众 排前
    assert rows[0]["decision_score"] > rows[1]["decision_score"]
    assert "决策得分" in rows[0]["decision_report"]
    assert "未计" in rows[0]["decision_report"]      # C 类降级标注在
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k topics_rank`
Expected: FAIL（404）

- [ ] **Step 3a: media.py import + 路由**

`app/api/media.py` 顶部加 import（放在其它 services import 附近）：

```python
from app.services.media_decision import build_decision_context, rank_pool
```

在 `topics_ai_recommend` 路由之后加：

```python
@router.post("/media/topics/rank")
async def topics_rank():
    """一键给选题池全部 pool 话题打分，写 decision_score + decision_report。纯计算。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return RedirectResponse("/media/topics", status_code=302)
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        prow = await cur.fetchone()
        phase = (prow["current_phase"] if prow else "") or "涨粉"

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active'", (pid,))
        traits = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_audience WHERE persona_id=? AND status='active'", (pid,))
        audiences = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_anchor WHERE persona_id=? AND status!='dropped' "
            "AND status!='archived'", (pid,))
        anchors = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_material WHERE persona_id=? AND status='active'", (pid,))
        materials = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT title, brief FROM media_content WHERE persona_id=? "
            "ORDER BY created_at DESC LIMIT 5", (pid,))
        recent = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT title, topic_fingerprint, outcome FROM media_content WHERE persona_id=?", (pid,))
        history = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_topic WHERE persona_id=? AND status='pool'", (pid,))
        topics = [dict(r) for r in await cur.fetchall()]

        ctx = build_decision_context(traits, audiences, anchors, materials, recent, history)
        ranked = rank_pool(topics, ctx, phase)
        for t in ranked:
            await db.execute(
                "UPDATE media_topic SET decision_score=?, decision_report=? WHERE id=?",
                (t["decision_score"], t["decision_report"], t["id"]))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)
```

- [ ] **Step 3b: 选题页按钮 + 报告展示**

`app/templates/media_topics.html` 头部（约 line 25 `AI 推选题` 按钮后）加：

```html
  <form method="post" action="/media/topics/rank" style="display:inline">
    <button class="btn" style="margin-left:8px">🧮 决策排序</button>
  </form>
```

话题卡里（约 line 47 `<div class="stars">契合…热度…</div>` 之后）加分数 + 报告：

```html
    {% if t.decision_score and t.decision_score > 0 %}
    <details style="margin-top:8px">
      <summary style="font-size:12.5px; color:var(--accent); cursor:pointer; list-style:none">🧮 决策得分 {{ t.decision_score }}/10 · 看理由</summary>
      <div style="font-size:11.5px; color:var(--ink-2); white-space:pre-wrap; margin-top:6px; background:var(--panel-2); border-radius:6px; padding:8px 10px">{{ t.decision_report }}</div>
    </details>
    {% endif %}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k topics_rank`
Expected: PASS；再跑整文件 `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider` 全绿。

- [ ] **Step 5: 提交**

```bash
git add app/api/media.py app/templates/media_topics.html tests/test_media_routes.py
git commit -m "feat(media): 选题页决策排序按钮——一键打分写报告按分排序"
```

---

### Task 3: 收尾 —— 全套回归 + 浏览器验证 + 部署 + 记忆

**Files:** 无代码改动（controller 执行）。

- [ ] **Step 1: 全套测试**

Run: `python -m pytest tests/ -q -p no:cacheprovider`
Expected: 全绿（177 基线 + Task1(9) + Task2(1) = 187，以实际为准）。

- [ ] **Step 2: 真浏览器验（controller 做）**

`preview_start {name:"ai-pm"}` → 测试同款签名 cookie 登录 → `/media/topics`：
1. `read_console_messages {onlyErrors:true}` 无错。
2. 页面有「🧮 决策排序」按钮；点击后（或直接 POST /media/topics/rank）话题卡出现「决策得分 X/10 · 看理由」，展开报告含逐因子说明 + "未计"标注。
3. 报告 `<details>` 展开正常（就一个 details，无复杂 JS，低风险）。

（本地 DB 无真话题时，先直接插一条 pool 话题验渲染，或在生产验收。）

- [ ] **Step 3: push 交给用户**

不自动 push 服务器。汇报：本地已 commit，按流程 `git push` → 服务器 `git pull && systemctl restart ai-pm`（零 migration）。真机验收：选题页点「决策排序」→ 看话题按分排序 + 展开决策报告。

- [ ] **Step 4: 更新记忆**

`C:\Users\62572\.claude\projects\D--GAGA-5-25\memory\project_aipm.md` 末尾追加本块完成记录。

---

## Self-Review

**Spec coverage：**
- spec §三 A 类（fit/heat 已存字段）→ Task1 score_topic + test_fit_and_heat ✅
- spec §三 B 类（audience_hit/anchor/material/risk/fatigue/dup 关键词重叠）→ Task1 + test_audience/risk/dup ✅
- spec §三 C 类（evidence/playbook/gap 降级标注不进分母）→ Task1 + test_degraded ✅
- spec §四 归一化 + 阶段权重 → Task1 归一段 + test_weights_switch ✅
- spec §五 media_decision 接口 → Task1 ✅；路由 → Task2 ✅；模板按钮+报告 → Task2 3b ✅
- spec §六 边界（空池/空资产/降级除零/短文本）→ test_overlap_short + degraded + rank_pool 空隐含 ✅
- spec §七 测试 1-9 → Task1(8 纯函数) + Task2(1 路由) ✅
- spec §二 锁死：纯计算无AI（media_decision 无 import ask_ai/无 db）、诚实（"未计"标注测）、零migration（用现有列）✅

**Placeholder scan：** 无 TBD/TODO；每 code step 完整代码。✅

**Type consistency：**
- `score_topic` 返回 `{score, report, factors}`；`factors[name] = {value, note}`；`rank_pool` 附加 `decision_score/decision_report` —— Task1 定义、Task1 测试、Task2 路由（读 t["decision_score"]/t["decision_report"]）、Task2 模板（t.decision_score/t.decision_report）全一致。
- `build_decision_context` 6 参数（traits/audiences/anchors/materials/recent_contents/history_contents）—— Task1 定义与 Task2 路由调用一致。
- WEIGHTS 键"冷启动/涨粉/转化"与 persona.current_phase 值域一致（人设创建默认"冷启动"，new-phase 用中文阶段名）。⚠️ 实现注意：若生产 persona.current_phase 是自定义阶段名（如"AI落地期"），会回落"涨粉"——可接受（spec §权重按阶段，未知回落）。
