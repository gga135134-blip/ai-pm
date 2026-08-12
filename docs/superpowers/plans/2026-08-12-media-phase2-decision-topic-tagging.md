# 决策引擎 · AI 给话题打受众/锚点标 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把决策引擎的 `audience_hit`/`anchor_distance` 从"中文字面重叠猜测"升级成"AI 语义标注"，并把"已放弃生意"变成一道反向护栏。

**Architecture:** AI 在上游（推选题 + 一键补标）判断每个话题命中哪些受众 segment / 服务哪些锚点 / 往哪些已放弃方向飘，把 id 列表存到 `media_topic`。决策引擎（`media_decision.py`）保持**纯函数**，只消费存好的标：有标读标，没标回落现有字面重叠。

**Tech Stack:** Python 3.14 + FastAPI + aiosqlite + Jinja2 + vanilla JS。测试：pytest（无 pytest-asyncio，纯函数直测 / 异步用 `asyncio.run` / AI 真调走浏览器 live 验证）。

## Global Constraints

- 决策引擎 `media_decision.py` **只允许 stdlib**（本次新增 `import json`），**不引 AI/DB**。纯函数、可单测。
- 所有 AI 取值防御性兜底：DeepSeek 会返回错类型。用现有 `_txt`/`_clamp` + 本计划的 `_clean_ids`。
- 模板改动一律用 Edit/Write 工具，**禁用 PowerShell `-replace`**（会把中文搞成乱码）。
- **不把 SVG 图标（`{{ ic.icon(...) }}`）塞进 JS 字符串**——已知会让整个 `<script>` SyntaxError 崩。失败时用预存的 `btn.innerHTML` 还原。
- DB 迁移只用 `ALTER TABLE ... ADD COLUMN`（带默认值），加进 `MIGRATIONS` 列表（`init_db` 已 try/except 幂等）。
- 人拍板哲学：护栏只报告 + 扣分，**不硬拦**。
- 运行测试：`cd D:/GAGA-5-25/ai-pm && python -m pytest`。若假挂 → 残留 python 进程，`taskkill //F //IM python.exe` 后重跑。

---

## 文件结构

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `app/database.py` | schema + 迁移 | SCHEMA 的 `media_topic` 加 4 列；MIGRATIONS 加 4 条 |
| `app/services/media_decision.py` | 纯函数打分引擎 | 新 `dropped_drift` 因子、audience/anchor 三分支、`build_decision_context` 加 `dropped_anchors`、容错解析、WEIGHTS |
| `app/services/media_ai.py` | media AI 能力 | 新 `_clean_ids`/`_build_asset_menu`/`tag_topics`/`TAG_SYSTEM`；`recommend_topics` 折入打标；`RECOMMEND_SYSTEM` 加字段 |
| `app/api/media.py` | 路由 | 新 `POST /media/topics/tag`；rank 路由加载 dropped_anchors |
| `app/templates/media_topics.html` | 选题页 | 「🏷️ AI标注」按钮 + AJAX |
| `tests/test_media_decision.py` | 引擎纯函数测 | +8 测 |
| `tests/test_media_ai_tagging.py` | 打标 helper 测（新文件） | `_clean_ids` + `_build_asset_menu` |
| `tests/test_media_routes.py` | 路由测 | +1 tag 路由测 |

---

### Task 1: DB 加 4 列（media_topic）

**Files:**
- Modify: `app/database.py`（SCHEMA 的 `CREATE TABLE ... media_topic` 段，约 227-246；MIGRATIONS 列表末尾，约 479）
- Test: `tests/test_media_schema.py`

**Interfaces:**
- Produces: `media_topic` 表新增列 `audience_ids TEXT DEFAULT '[]'`、`anchor_ids TEXT DEFAULT '[]'`、`dropped_drift_ids TEXT DEFAULT '[]'`、`tagged INTEGER DEFAULT 0`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_schema.py` 末尾追加：

```python
def test_media_topic_has_tagging_columns():
    import asyncio
    from app.database import get_db, init_db

    async def check():
        await init_db()
        db = await get_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_topic)")
            cols = {r["name"] for r in await cur.fetchall()}
            return cols
        finally:
            await db.close()

    cols = asyncio.run(check())
    assert {"audience_ids", "anchor_ids", "dropped_drift_ids", "tagged"} <= cols
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_schema.py::test_media_topic_has_tagging_columns -v`
Expected: FAIL（列不存在）

- [ ] **Step 3: 改 SCHEMA**

在 `app/database.py` 的 `media_topic` 建表语句里，`related_trait_ids TEXT DEFAULT '[]',` 那行之后加：

```sql
    audience_ids TEXT DEFAULT '[]',
    anchor_ids TEXT DEFAULT '[]',
    dropped_drift_ids TEXT DEFAULT '[]',
    tagged INTEGER DEFAULT 0,
```

- [ ] **Step 4: 加迁移**

在 `MIGRATIONS` 列表末尾（`"ALTER TABLE media_material ADD COLUMN source TEXT DEFAULT ''",` 之后）加：

```python
    "ALTER TABLE media_topic ADD COLUMN audience_ids TEXT DEFAULT '[]'",
    "ALTER TABLE media_topic ADD COLUMN anchor_ids TEXT DEFAULT '[]'",
    "ALTER TABLE media_topic ADD COLUMN dropped_drift_ids TEXT DEFAULT '[]'",
    "ALTER TABLE media_topic ADD COLUMN tagged INTEGER DEFAULT 0",
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_media_schema.py::test_media_topic_has_tagging_columns -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/database.py tests/test_media_schema.py
git commit -m "feat(media): media_topic 加 audience_ids/anchor_ids/dropped_drift_ids/tagged 列"
```

---

### Task 2: `_clean_ids` 纯函数（AI 返回 id 过滤）

**Files:**
- Modify: `app/services/media_ai.py`（在 `_txt` 附近加新函数）
- Test: `tests/test_media_ai_tagging.py`（新文件）

**Interfaces:**
- Produces: `_clean_ids(raw, valid_set) -> list[str]` —— 只保留 `raw` 里"是字符串、在 `valid_set` 里、不重复"的 id，顺序保持。非 list 输入返回 `[]`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_ai_tagging.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_ai_tagging.py -v`
Expected: FAIL（`_clean_ids` 未定义 / ImportError）

- [ ] **Step 3: 写实现**

在 `app/services/media_ai.py` 的 `_txt` 函数之后加：

```python
def _clean_ids(raw, valid_set) -> list:
    """把 AI 返回的 id 列表过滤成"只保留合法 id"。防 AI 编造 id / 返回错类型。

    只保留：是字符串、在 valid_set 里、不重复的 id，顺序保持。非 list → []。
    """
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for x in raw:
        if isinstance(x, str) and x in valid_set and x not in seen:
            seen.add(x)
            out.append(x)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_ai_tagging.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_ai_tagging.py
git commit -m "feat(media): _clean_ids 过滤 AI 返回的资产 id"
```

---

### Task 3: 决策引擎 —— 语义标三分支 + dropped_drift 护栏

**Files:**
- Modify: `app/services/media_decision.py`（顶部加 `import json`；`WEIGHTS` 三套加 `dropped_drift`；`build_decision_context` 加 `dropped_anchors` 参数；`score_topic` 改 audience_hit/anchor_distance 分支 + 加 dropped_drift 因子 + neg 列 + 报告）
- Test: `tests/test_media_decision.py`

**Interfaces:**
- Consumes: 话题 dict 上的 `tagged`(int)、`audience_ids`/`anchor_ids`/`dropped_drift_ids`（list 或 JSON 字符串均可）。
- Produces: `build_decision_context(traits, audiences, anchors, materials, recent_contents, history_contents, dropped_anchors=None)` 多返回 `ctx["dropped_anchors"]`；`score_topic` 的 `factors` 多一个 `dropped_drift` 键；总分把 `dropped_drift` 计入负项。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_decision.py` 末尾追加：

```python
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
```

同时把测试顶部的 `_ctx` helper 改成支持 `dropped_anchors`（找到现有 `_ctx` 定义替换）：

```python
def _ctx(traits=None, audiences=None, anchors=None, materials=None,
         recent=None, history=None, dropped_anchors=None):
    return build_decision_context(
        traits or [], audiences or [], anchors or [],
        materials or [], recent or [], history or [],
        dropped_anchors or [])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_decision.py -v -k "tagged or dropped_drift or json_string or dropped_anchors"`
Expected: FAIL（新分支/参数未实现）

- [ ] **Step 3: 改 `build_decision_context`**

`app/services/media_decision.py` 顶部（`WEIGHTS` 之前）加：

```python
import json
```

替换 `build_decision_context` 整个函数：

```python
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
```

- [ ] **Step 4: 改 WEIGHTS（三套各加 dropped_drift）**

把 `WEIGHTS` 字典替换为（每套末尾加 `dropped_drift`）：

```python
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
```

- [ ] **Step 5: 加 helper + 改 score_topic**

在 `_clamp15` 函数之后加两个 helper：

```python
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
```

在 `score_topic` 里，`text = _topic_text(topic)` 之后、`factors = {}` 之前（或紧接其后）加映射与解析：

```python
    tagged = bool(topic.get("tagged"))
    aud_ids = _as_id_list(topic.get("audience_ids"))
    anc_ids = _as_id_list(topic.get("anchor_ids"))
    drift_ids = _as_id_list(topic.get("dropped_drift_ids"))
    aud_by_id = {a.get("id"): a for a in ctx["audiences"]}
    anc_by_id = {a.get("id"): a for a in ctx["anchors"]}
    anc_by_id.update({a.get("id"): a for a in ctx.get("dropped_anchors", [])})
```

**替换 audience_hit 整段**（现有 `# audience_hit：命中 segment...` 那块）：

```python
    # ── audience_hit：优先 AI 语义标，没标回落字面重叠 ──
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
```

**替换 anchor_distance 整段**：

```python
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
```

**在 dup_penalty 段之后、C 类之前**加 dropped_drift 因子：

```python
    # ── dropped_drift（减·护栏）：飘向已放弃的生意方向 ──
    if tagged and drift_ids:
        names = "、".join(anc_by_id[i].get("name", "") for i in drift_ids if i in anc_by_id) \
                or "已放弃方向"
        factors["dropped_drift"] = {
            "value": 1.0,
            "note": f"⚠️ 往已放弃方向'{names}'飘 —— 当初花大代价才验证它不行，真做想清楚"}
    else:
        factors["dropped_drift"] = {"value": 0.0, "note": "未飘向已放弃方向"}
```

**改归一化的 neg 列**（找到 `neg = ["risk", "fatigue", "dup_penalty"]`）：

```python
    neg = ["risk", "fatigue", "dup_penalty", "dropped_drift"]
```

**改报告的负项循环**（找到 `for k in ["risk", "fatigue", "dup_penalty"]:`）：

```python
    for k in ["risk", "fatigue", "dup_penalty", "dropped_drift"]:
        if factors[k]["value"] > 0:
            lines.append(f"－{factors[k]['note']}")
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_decision.py -v`
Expected: PASS（含新 8 测 + 原有测不回归）

- [ ] **Step 7: 提交**

```bash
git add app/services/media_decision.py tests/test_media_decision.py
git commit -m "feat(media): 决策引擎读 AI 语义标 + dropped_drift 护栏因子"
```

---

### Task 4: `_build_asset_menu` —— 拼资产菜单 + 合法 id 集

**Files:**
- Modify: `app/services/media_ai.py`
- Test: `tests/test_media_ai_tagging.py`

**Interfaces:**
- Consumes: DB 里 `media_audience`(active) / `media_anchor`(validating/proven/dropped)。
- Produces: `async _build_asset_menu(db, persona_id) -> dict`，含键：`menu`(str 提示词菜单文本)、`valid_aud`/`valid_anc`/`valid_dropped`(set[str])。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_ai_tagging.py` 追加：

```python
def test_build_asset_menu_lists_assets_and_valid_ids():
    import asyncio
    from app.database import get_db, init_db
    from app.services.media_ai import _build_asset_menu

    async def run():
        await init_db()
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_anchor WHERE persona_id='MENUP'")
            await db.execute("DELETE FROM media_audience WHERE persona_id='MENUP'")
            await db.execute("DELETE FROM media_persona WHERE id='MENUP'")
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('MENUP','测试人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_audience (id,persona_id,segment,anxiety,language,"
                "pay_willingness,status) VALUES "
                "('AUD1','MENUP','焦虑老板','落地难','能落地不',5,'active')")
            await db.execute(
                "INSERT INTO media_anchor (id,persona_id,name,value_prop,status) VALUES "
                "('ANC1','MENUP','训练营','教你落地','proven'),"
                "('ANCD','MENUP','微商带货','已放弃','dropped')")
            await db.commit()
            return await _build_asset_menu(db, "MENUP")
        finally:
            await db.close()

    menu = asyncio.run(run())
    assert "焦虑老板" in menu["menu"]
    assert "训练营" in menu["menu"]
    assert "微商带货" in menu["menu"]          # 已放弃方向也列（供护栏）
    assert menu["valid_aud"] == {"AUD1"}
    assert menu["valid_anc"] == {"ANC1"}       # dropped 不进可标集
    assert menu["valid_dropped"] == {"ANCD"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_ai_tagging.py::test_build_asset_menu_lists_assets_and_valid_ids -v`
Expected: FAIL（`_build_asset_menu` 未定义）

- [ ] **Step 3: 写实现**

在 `app/services/media_ai.py` 的 `_clean_ids` 之后加：

```python
async def _build_asset_menu(db, persona_id: str) -> dict:
    """拼给 AI 打标用的资产菜单（受众/可标锚点/已放弃方向三段），并返回合法 id 集。

    dropped 锚点单列进「已放弃方向」段，只供 dropped_drift 护栏反向查，不进可标集。
    """
    cur = await db.execute(
        "SELECT id,segment,anxiety,language,pay_willingness FROM media_audience "
        "WHERE persona_id=? AND status='active'", (persona_id,))
    auds = [dict(r) for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT id,name,value_prop,status FROM media_anchor "
        "WHERE persona_id=? AND status IN ('validating','proven')", (persona_id,))
    anchors = [dict(r) for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT id,name,value_prop FROM media_anchor "
        "WHERE persona_id=? AND status='dropped'", (persona_id,))
    dropped = [dict(r) for r in await cur.fetchall()]

    lines = []
    if auds:
        lines.append("【受众 segment】命中填进 audience_ids：")
        for a in auds:
            lines.append(f"- id={a['id']}｜{a['segment']}｜焦虑:{a['anxiety']}｜原话:{a['language']}")
    if anchors:
        lines.append("【生意锚点】服务填进 anchor_ids：")
        for a in anchors:
            lines.append(f"- id={a['id']}｜{a['name']}｜{a['value_prop']}")
    if dropped:
        lines.append("【⛔ 已放弃方向】话题若往这些方向飘才填进 dropped_drift_ids：")
        for a in dropped:
            lines.append(f"- id={a['id']}｜{a['name']}｜{a['value_prop']}")
    menu = "\n".join(lines) if lines else "（当前无受众/锚点资产可标，三个 id 列都留空）"

    return {
        "menu": menu,
        "valid_aud": {a["id"] for a in auds},
        "valid_anc": {a["id"] for a in anchors},
        "valid_dropped": {a["id"] for a in dropped},
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_ai_tagging.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_ai_tagging.py
git commit -m "feat(media): _build_asset_menu 拼资产菜单+合法 id 集"
```

---

### Task 5: `tag_topics` —— 一键补标 AI 能力

**Files:**
- Modify: `app/services/media_ai.py`（加 `TAG_SYSTEM` 常量 + `tag_topics` 函数）

**Interfaces:**
- Consumes: `_build_asset_menu`(Task 4)、`_clean_ids`(Task 2)、`ask_ai`/`extract_json`/`log_injection`（现有 import）、`media_topic.tagged` 列(Task 1)。
- Produces: `async tag_topics(db, persona_id, model="auto") -> dict`，返回 `{ok, count, cost, model, error}`。给 `status='pool' AND tagged=0` 的话题打标并 `UPDATE ... tagged=1`。

- [ ] **Step 1: 写实现（AI 能力无单测，靠 Task 7 路由测 + live 验证）**

在 `app/services/media_ai.py` 的 `_build_asset_menu` 之后加：

```python
TAG_SYSTEM = """你是自媒体选题的资产标注员。给每个话题标注它命中的受众/锚点。

铁律（必须全部满足）：
1. 只能从给定的资产 id 里选，绝不编造 id。
2. 真命中才填，不沾就留空数组 —— 不硬凑、不凑数。
3. dropped_drift_ids 只在话题明显往「已放弃方向」飘时才填该 id，默认空。
4. 每个话题都要在结果里出现，用它原样的 id 对应。
5. 只输出 JSON 数组，不要任何解释文字。

输出格式：
[{"id":"话题原样id","audience_ids":[],"anchor_ids":[],"dropped_drift_ids":[]}]"""


async def tag_topics(db, persona_id: str, model: str = "auto") -> dict:
    """给选题池里未打标(tagged=0)的话题批量打受众/锚点/护栏标。人拍板前只写标，不改状态。"""
    menu = await _build_asset_menu(db, persona_id)
    cur = await db.execute(
        "SELECT id,title,puzzle FROM media_topic "
        "WHERE persona_id=? AND status='pool' AND tagged=0", (persona_id,))
    topics = [dict(r) for r in await cur.fetchall()]
    if not topics:
        return {"ok": True, "count": 0, "cost": 0, "model": "", "error": ""}

    tlist = "\n".join(
        f"- id={t['id']}｜{t['title']}｜谜题:{t['puzzle']}" for t in topics)
    prompt = (f"资产菜单：\n{menu['menu']}\n\n"
              f"待标话题（{len(topics)} 个）：\n{tlist}\n\n"
              "请为每个话题标注命中的资产 id，按输出格式返回。")

    result = await ask_ai(prompt, model=model, task_type="media_topic",
                          system_prompt=TAG_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    items = extract_json(resp, expect="array")
    if not items:
        obj = extract_json(resp, expect="object")
        items = obj.get("topics") or obj.get("data") or []

    by_id = {t["id"]: t for t in topics}
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = _txt(it.get("id"))
        if tid not in by_id:
            continue  # 按 id 匹配，防错位；瞎编的 id 丢弃
        aud = _clean_ids(it.get("audience_ids"), menu["valid_aud"])
        anc = _clean_ids(it.get("anchor_ids"), menu["valid_anc"])
        drift = _clean_ids(it.get("dropped_drift_ids"), menu["valid_dropped"])
        await db.execute(
            "UPDATE media_topic SET audience_ids=?, anchor_ids=?, "
            "dropped_drift_ids=?, tagged=1 WHERE id=?",
            (json.dumps(aud, ensure_ascii=False), json.dumps(anc, ensure_ascii=False),
             json.dumps(drift, ensure_ascii=False), tid))
        count += 1
    await db.commit()

    all_ids = list(menu["valid_aud"] | menu["valid_anc"] | menu["valid_dropped"])
    await log_injection(db, "", "tag_topics", all_ids, result.get("tokens", 0))

    return {"ok": True, "count": count, "cost": result.get("cost", 0),
            "model": result.get("model", ""), "error": ""}
```

- [ ] **Step 2: 冒烟——import 不报错**

Run: `python -c "from app.services.media_ai import tag_topics, TAG_SYSTEM; print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 3: 提交**

```bash
git add app/services/media_ai.py
git commit -m "feat(media): tag_topics 一键补标 AI 能力 + TAG_SYSTEM"
```

---

### Task 6: 推选题折入打标（`recommend_topics` + `RECOMMEND_SYSTEM`）

**Files:**
- Modify: `app/services/media_ai.py`（`RECOMMEND_SYSTEM` 输出格式；`recommend_topics` 加菜单注入 + 存三 id 列）

**Interfaces:**
- Consumes: `_build_asset_menu`(Task 4)、`_clean_ids`(Task 2)、三 id 列(Task 1)。
- Produces: `recommend_topics` 推的新话题带 `audience_ids/anchor_ids/dropped_drift_ids` 且 `tagged=1`（同一次 AI 调用，零额外成本）。

- [ ] **Step 1: 改 `RECOMMEND_SYSTEM` 输出格式**

把 `RECOMMEND_SYSTEM` 里输出格式段（`输出格式：` 到结尾）替换为：

```python
输出格式：
[{"title":"选题","puzzle":"核心谜题","reason":"为什么值得做","angle":"切入角度","heat":3,"fit_score":4,"audience_ids":[],"anchor_ids":[],"dropped_drift_ids":[]}]
heat 和 fit_score 都是 1-5 的整数。
audience_ids/anchor_ids 只能从下方「资产菜单」给的 id 里选，真命中才填、不沾留空。
dropped_drift_ids 只在选题往「已放弃方向」飘时才填该 id，默认空。绝不编造 id。"""
```

- [ ] **Step 2: `recommend_topics` 注入菜单**

在 `recommend_topics` 里，`parts.append("请推荐 5 个新选题。")` **之前**加：

```python
    menu = await _build_asset_menu(db, persona_id)
    parts.append("资产菜单（给选题打受众/锚点标用）：\n" + menu["menu"])
```

- [ ] **Step 3: 存三 id 列**

把 `recommend_topics` 里的 INSERT 语句及其参数替换为：

```python
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score,"
            " related_trait_ids,audience_ids,anchor_ids,dropped_drift_ids,tagged) "
            "VALUES (?,?,?,?,'ai_rec',?,?,?,?,?,?,?,?,1)",
            (str(uuid.uuid4()), persona_id, title, puzzle,
             _txt(it.get("reason")), _txt(it.get("angle")),
             _clamp(it.get("heat"), 3), _clamp(it.get("fit_score"), 3),
             json.dumps(trait_ids, ensure_ascii=False),
             json.dumps(_clean_ids(it.get("audience_ids"), menu["valid_aud"]),
                        ensure_ascii=False),
             json.dumps(_clean_ids(it.get("anchor_ids"), menu["valid_anc"]),
                        ensure_ascii=False),
             json.dumps(_clean_ids(it.get("dropped_drift_ids"), menu["valid_dropped"]),
                        ensure_ascii=False)))
        count += 1
```

- [ ] **Step 4: 冒烟——import 不报错**

Run: `python -c "from app.services.media_ai import recommend_topics; print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 5: 回归——现有 media 测试不挂**

Run: `python -m pytest tests/test_media_decision.py tests/test_media_ai_tagging.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/services/media_ai.py
git commit -m "feat(media): 推选题同一次调用折入受众/锚点/护栏打标"
```

---

### Task 7: 路由 —— `/media/topics/tag` + rank 加载 dropped_anchors

**Files:**
- Modify: `app/api/media.py`（import 加 `tag_topics`；新 `topics_tag` 路由；`topics_rank` 加载 dropped_anchors 并传入）
- Test: `tests/test_media_routes.py`

**Interfaces:**
- Consumes: `tag_topics`(Task 5)、`build_decision_context` 的 `dropped_anchors` 参数(Task 3)。
- Produces: `POST /media/topics/tag` 返回 JSON `{ok,count,cost,model,error}`；rank 打分时 dropped 锚点进 ctx，护栏与状态兜底生效。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_routes.py` 的「决策引擎」段末尾追加：

```python
def test_topics_tag_no_persona_returns_ok_false():
    """无人设时 tag 路由不崩，返回 ok:false。"""
    async def wipe():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(wipe())

    r = _client().post("/media/topics/tag")
    assert r.status_code == 200
    assert r.json()["ok"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py::test_topics_tag_no_persona_returns_ok_false -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: import 加 tag_topics**

`app/api/media.py` 顶部（约 14 行）现有 `recommend_topics, write_script, ...` 的 import 里加入 `tag_topics`：

```python
    recommend_topics, tag_topics, write_script, generate_platform_copy, review_content,
```

- [ ] **Step 4: 加 tag 路由**

在 `topics_ai_recommend` 路由（约 783 行结束处）之后加：

```python
@router.post("/media/topics/tag")
async def topics_tag():
    """一键给选题池未打标话题补受众/锚点/护栏标。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        try:
            result = await tag_topics(db, pid)
        except Exception as e:
            log.exception("AI 标注失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)
```

- [ ] **Step 5: rank 路由加载 dropped_anchors**

在 `topics_rank` 里，加载 `anchors` 那段之后加加载 dropped：

```python
        cur = await db.execute(
            "SELECT * FROM media_anchor WHERE persona_id=? AND status='dropped'", (pid,))
        dropped_anchors = [dict(r) for r in await cur.fetchall()]
```

并把 `build_decision_context(...)` 调用改为传入：

```python
        ctx = build_decision_context(traits, audiences, anchors, materials,
                                     recent, history, dropped_anchors)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py::test_topics_tag_no_persona_returns_ok_false tests/test_media_routes.py::test_topics_rank_writes_scores -v`
Expected: PASS（新测 + rank 回归都过）

- [ ] **Step 7: 提交**

```bash
git add app/api/media.py tests/test_media_routes.py
git commit -m "feat(media): /media/topics/tag 路由 + rank 加载 dropped_anchors"
```

---

### Task 8: 选题页「🏷️ AI标注」按钮 + AJAX

**Files:**
- Modify: `app/templates/media_topics.html`（头部按钮区加按钮；`<script>` 加 `aiTag()`）

**Interfaces:**
- Consumes: `POST /media/topics/tag`(Task 7)。

- [ ] **Step 1: 加按钮**

在 `app/templates/media_topics.html` 的「🧮 决策排序」`<form>`（约 26-28 行）之后加：

```html
  <button onclick="aiTag()" id="tag-btn" class="btn" style="margin-left:8px">🏷️ AI标注</button>
```

- [ ] **Step 2: 加 AJAX 函数**

在 `<script>` 块里 `aiRecommend` 函数之后（`</script>` 之前）加。**注意：`btn.textContent` 改文字，失败时用预存 `orig`（含图标）还原——不把图标塞进 JS 字符串**：

```javascript
async function aiTag() {
  const btn = document.getElementById('tag-btn');
  const status = document.getElementById('ai-status');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = '标注中…';
  status.style.display = 'block';
  status.textContent = 'AI 正在给未标话题打受众/锚点标…';
  try {
    const r = await fetch('/media/topics/tag', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      status.textContent = '已标注 ' + d.count + ' 条，刷新中…';
      location.reload();
    } else {
      status.textContent = '失败：' + (d.error || '未知错误');
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  } catch (e) {
    status.textContent = '请求失败：' + e;
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}
```

- [ ] **Step 3: 校验模板 render 不崩**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('app/templates')).get_template('media_topics.html'); print('ok')"`
Expected: 打印 `ok`（模板语法可解析）

- [ ] **Step 4: 提交**

```bash
git add app/templates/media_topics.html
git commit -m "feat(media): 选题页加「AI标注」按钮+AJAX"
```

---

### Task 9: 浏览器 live 验证 + 全套回归

**Files:** 无（验证任务）

- [ ] **Step 1: 全套测试绿**

Run: `python -m pytest -q`
Expected: 全 PASS（约 188 基线 + 本计划新增 ~12）。若假挂 → `taskkill //F //IM python.exe` 重跑。

- [ ] **Step 2: 起本地 server**

用 `preview_start`（`.claude/launch.json` 的 dev server，或 `python run.py` → http://localhost:8000）。

- [ ] **Step 3: 登录（测试签名 cookie，不输密码）**

参照 `tests/test_media_routes.py` 顶部签名 cookie 做法，给浏览器注入 session cookie，或用已有登录态。

- [ ] **Step 4: 补标验证**

访问 `/media/topics`，点「🏷️ AI标注」→ 看提示"已标注 N 条"、页面刷新。用 `read_console_messages` 确认无 JS 报错、无 `<script>` 崩。

- [ ] **Step 5: 决策排序验证**

点「🧮 决策排序」→ 展开话题的「决策得分·看理由」，确认 audience_hit/anchor_distance 的说明变成"AI判定命中…/AI判定服务锚点…"（而非"命中…的焦虑"字面口径）。若人设有 dropped 锚点且某话题被标了 dropped_drift，确认报告里出现"⚠️ 往已放弃方向…飘"。

- [ ] **Step 6: DB 抽查**

`python` 连 DB 查一条被标话题：`SELECT tagged,audience_ids,anchor_ids,dropped_drift_ids FROM media_topic WHERE tagged=1 LIMIT 3` —— 确认 `tagged=1` 且 id 列是合法 JSON 数组。

- [ ] **Step 7: 截图交付**

`computer` 截图选题页（含标注后的决策报告），发给用户。

---

## 部署提示（用户自行执行）

本地 push 后，服务器：

```bash
cd /www/wwwroot/ai-pm && git pull && systemctl restart ai-pm
```

（重启自动跑 Task 1 的 4 条 ADD COLUMN 迁移；systemd 不是 pm2。）

---

## Self-Review 记录

- **Spec 覆盖**：§3 数据(Task1) / §4.1 helper(Task2,4) / §4.2 推选题折入(Task6) / §4.3 补标(Task5) / §5 路由(Task7)+按钮(Task8) / §6 引擎三分支+护栏(Task3) / §7 测试(各Task+Task9) / §8 落点全覆盖。
- **无占位符**：每个 code step 均有完整代码。
- **类型一致**：`_build_asset_menu` 返回键 `menu/valid_aud/valid_anc/valid_dropped` 在 Task4 定义、Task5/6 消费一致；`tag_topics(db,persona_id,model)`、`build_decision_context(...,dropped_anchors=None)` 签名前后一致；话题 id 列 `audience_ids/anchor_ids/dropped_drift_ids` + `tagged` 全程同名。
