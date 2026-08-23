# 打法库🅒接决策引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让决策引擎给选题打分时，把"这条选题有没有一条已验证的打法可套"计入分数（AI 打标 → 纯打分器读标）。

**Architecture:** 扩展现有 `tag_topics` 让 AI 给每个话题挑一条最贴的打法写进 `media_topic.playbook_id`；纯函数 `score_topic` 读该标，按打法 status 加权（proven 1.0 / validating 0.6），**命中才计入归一化分母**（没匹配的选题分数不受影响）。与现有"受众/锚点"因子完全同构。

**Tech Stack:** Python 3、FastAPI、aiosqlite、pytest（纯函数测试 + tmp-DB_PATH 模块 fixture 的异步测试）。

## Global Constraints

- 数据库 SQLite，迁移走 `MIGRATIONS` 幂等 ALTER（`app/database.py`），重启自动跑。测试用临时 DB_PATH，不碰真实 `data/aipm.db`。
- 媒体工具测试铁律：函数用 `get_db()` 则测试用 tmp-DB_PATH 模块 fixture（见 `tests/test_media_ai_tagging.py`），**永不用 make_db 内存私连**，**永不往 `database.py` 加测试钩子**。
- LLM 可控值（args）绝不拼进 SQL；只硬编码常量拼。清洗后的 id 才写库。
- 一个选题只挂**一条**打法（`playbook_id` 单值不是数组），守"只注一条"注意力纪律。
- 全程 TDD：每步 RED→GREEN→commit。运行测试命令统一 `python -m pytest`。
- 打法状态权重：`proven=1.0`、`validating=0.6`、没命中=0。阶段权重：冷启动 1 / 涨粉 2 / 转化 2。
- 不做打法来源维度（对标/拆解别人）——本轮 legacy_mine 一视同仁。

---

### Task 1: DB 迁移 — `media_topic.playbook_id` 列

**Files:**
- Modify: `app/database.py`（`MIGRATIONS` 列表末尾，`"...media_content ADD COLUMN summary..."` 那条之后、闭合 `]` 之前）
- Test: `tests/test_media_playbook_decision.py`（新建）

**Interfaces:**
- Produces: `media_topic` 表新增列 `playbook_id TEXT DEFAULT ''`。Task 3（写标）、Task 4（SELECT *）依赖此列存在。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_playbook_decision.py`：

```python
"""打法库🅒接决策引擎：迁移列 + 打分器 + 打标 集成测试。"""
import asyncio

import pytest

from app.database import get_db, init_db
import app.database as _db_mod


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    """异步 DB 测试隔离到临时库，不污染真实 aipm.db。"""
    tmp = tmp_path_factory.mktemp("media_pb_decision_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_media_topic_has_playbook_id_column():
    async def run():
        db = await get_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_topic)")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    cols = asyncio.run(run())
    assert "playbook_id" in cols
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_playbook_decision.py::test_media_topic_has_playbook_id_column -v`
Expected: FAIL — `assert 'playbook_id' in {...}`（列还没建）

- [ ] **Step 3: 加迁移**

在 `app/database.py` 的 `MIGRATIONS` 列表里，`"ALTER TABLE media_content ADD COLUMN summary TEXT DEFAULT ''",` 之后、闭合 `]` 之前，加一行：

```python
    "ALTER TABLE media_topic ADD COLUMN playbook_id TEXT DEFAULT ''",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_playbook_decision.py::test_media_topic_has_playbook_id_column -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/database.py tests/test_media_playbook_decision.py
git commit -m "feat(media): media_topic 加 playbook_id 列(打法库接决策引擎)"
```

---

### Task 2: 打分器 — playbook 因子（纯函数）

**Files:**
- Modify: `app/services/media_decision.py`（常量区 line ~56、`WEIGHTS` line 11-24、`build_decision_context` line 72-88、`score_topic` line 95-269）
- Test: `tests/test_media_decision.py`（扩展 `_ctx` helper + 加测试）

**Interfaces:**
- Consumes: `topic["playbook_id"]`（Task 1 的列；纯测试里直接 dict 传）。
- Produces:
  - `build_decision_context(..., dropped_anchors=None, playbooks=None)` — 新增末位关键字参数 `playbooks`（list of `{id,name,status}`）。Task 4 依赖此签名。
  - `score_topic` 返回的 `factors["playbook"]` = `{"value": float, "note": str}`；命中时 value∈{1.0,0.6}、note 含 `匹配到打法《名字》（状态）`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_decision.py`，先把 `_ctx` helper 加上 `playbooks` 参数（改现有定义）：

```python
def _ctx(traits=None, audiences=None, anchors=None, materials=None,
         recent=None, history=None, dropped_anchors=None, playbooks=None):
    return build_decision_context(
        traits or [], audiences or [], anchors or [],
        materials or [], recent or [], history or [],
        dropped_anchors or [], playbooks=playbooks or [])
```

再追加测试（文件末尾）：

```python
def test_playbook_proven_matched_full_value():
    pb = {"id": "PB1", "name": "反常识开场", "status": "proven"}
    t = {"title": "随便", "puzzle": "", "heat": 3, "playbook_id": "PB1"}
    res = score_topic(t, _ctx(playbooks=[pb]), "涨粉")
    assert res["factors"]["playbook"]["value"] == 1.0
    assert "反常识开场" in res["factors"]["playbook"]["note"]
    assert "匹配到打法《反常识开场》" in res["report"]


def test_playbook_validating_partial_value():
    pb = {"id": "PB2", "name": "痛点先行", "status": "validating"}
    t = {"title": "随便", "puzzle": "", "heat": 3, "playbook_id": "PB2"}
    res = score_topic(t, _ctx(playbooks=[pb]), "涨粉")
    assert res["factors"]["playbook"]["value"] == 0.6


def test_playbook_no_match_zero_and_no_report_line():
    t = {"title": "随便", "puzzle": "", "heat": 3}  # 无 playbook_id
    res = score_topic(t, _ctx(playbooks=[
        {"id": "PB1", "name": "反常识开场", "status": "proven"}]), "涨粉")
    assert res["factors"]["playbook"]["value"] == 0.0
    assert "匹配到打法" not in res["report"]


def test_playbook_bogus_id_zero():
    t = {"title": "随便", "puzzle": "", "heat": 3, "playbook_id": "NOPE"}
    res = score_topic(t, _ctx(playbooks=[
        {"id": "PB1", "name": "x", "status": "proven"}]), "涨粉")
    assert res["factors"]["playbook"]["value"] == 0.0


def test_playbook_matched_boosts_score_unmatched_unchanged():
    """命中才计入分母：没匹配的选题分数与今天(无 playbook 参与)一致；命中的更高。"""
    pb = {"id": "PB1", "name": "反常识开场", "status": "proven"}
    base = {"title": "中小企业AI落地", "puzzle": "", "heat": 3}
    matched = dict(base, playbook_id="PB1")
    r_unmatched = score_topic(base, _ctx(playbooks=[pb]), "涨粉")
    r_matched = score_topic(matched, _ctx(playbooks=[pb]), "涨粉")
    r_no_pb_ctx = score_topic(base, _ctx(playbooks=[]), "涨粉")
    # 没匹配：有没有打法库在 ctx 里都一样（不进分母）
    assert r_unmatched["score"] == r_no_pb_ctx["score"]
    # 命中：分更高
    assert r_matched["score"] > r_unmatched["score"]


def test_weights_playbook_by_phase():
    assert WEIGHTS["冷启动"]["playbook"] == 1
    assert WEIGHTS["涨粉"]["playbook"] == 2
    assert WEIGHTS["转化"]["playbook"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_decision.py -k playbook -v`
Expected: FAIL（`playbooks` 参数还没加 / playbook 因子恒 0 / WEIGHTS 还是 0）

- [ ] **Step 3: 改实现**

**3a.** `app/services/media_decision.py` 常量区，在 `_ANCHOR_STATUS_W = {...}`（line 56）之后加：

```python
_PLAYBOOK_STATUS_W = {"proven": 1.0, "validating": 0.6}
```

**3b.** `WEIGHTS`（line 11-24）三套预设把每个 `"playbook": 0` 改成对应值。改后三行的 playbook 部分：

- 冷启动那套：`"evidence": 0, "playbook": 1, "gap": 0`
- 涨粉那套：`"evidence": 0, "playbook": 2, "gap": 0`
- 转化那套：`"evidence": 0, "playbook": 2, "gap": 0`

**3c.** `build_decision_context`（line 72）签名末位加 `playbooks=None`，return dict 加一项：

```python
def build_decision_context(traits, audiences, anchors, materials,
                           recent_contents, history_contents,
                           dropped_anchors=None, playbooks=None) -> dict:
```
在 return 的 dict 里（`"dropped_anchors": ...` 那项后面）加：
```python
        "playbooks": list(playbooks or []),
```

**3d.** `score_topic`，在构造 `anc_by_id`（line 105-107）之后加一行：

```python
    pb_by_id = {p.get("id"): p for p in ctx.get("playbooks", [])}
```

**3e.** 把 line 242 那条恒 0 的 playbook factor：
```python
    factors["playbook"] = {"value": 0.0, "note": "⚙️ 打法库未建，此项未计"}
```
替换成真计算：
```python
    pb = pb_by_id.get(topic.get("playbook_id"))
    if pb:
        factors["playbook"] = {
            "value": _PLAYBOOK_STATUS_W.get(pb.get("status"), 0.0),
            "note": f"匹配到打法《{pb.get('name', '')}》（{pb.get('status', '')}）"}
    else:
        factors["playbook"] = {"value": 0.0, "note": "无匹配打法"}
```

**3f.** 归一化的 `pos`（line 249）改成命中才计入：
```python
    pos = ["fit", "heat", "audience_hit", "anchor_distance", "material_ready"]
    if factors["playbook"]["value"] > 0:
        pos = pos + ["playbook"]
```

**3g.** 报告段（line 258-266）改成：
```python
    lines = [f"决策得分 {score}/10（{phase}期权重）"]
    for k in ["fit", "heat", "audience_hit", "anchor_distance", "material_ready"]:
        lines.append(f"＋{factors[k]['note']}")
    if factors["playbook"]["value"] > 0:
        lines.append(f"＋{factors['playbook']['note']}")
    for k in ["risk", "fatigue", "dup_penalty", "dropped_drift"]:
        if factors[k]["value"] > 0:
            lines.append(f"－{factors[k]['note']}")
    for k in ["evidence", "gap"]:
        lines.append(factors[k]["note"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_decision.py -v`
Expected: PASS（新加 playbook 测试全绿，且**原有决策测试不回归**——命中才计入分母保证无 playbook 时行为不变）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_decision.py tests/test_media_decision.py
git commit -m "feat(media): 决策引擎 playbook 因子接线(status加权/命中才计入分母/阶段权重1-2-2)"
```

---

### Task 3: AI 打标 — `tag_topics` 顺带挑一条打法

**Files:**
- Modify: `app/services/media_ai.py`（`_clean_ids` 附近加 `_clean_one_id`；`_build_asset_menu` line ~296-335；`TAG_SYSTEM` line 338-348；`tag_topics` line 351-399）
- Test: `tests/test_media_ai_tagging.py`（扩展）

**Interfaces:**
- Consumes: `media_topic.playbook_id` 列（Task 1）。
- Produces:
  - `_clean_one_id(v, valid) -> str`：单 id 清洗，合法且在 valid 里才留否则空串。
  - `_build_asset_menu` 返回 dict 加 `"valid_pb": set`；menu 文本含【打法库】段。
  - `tag_topics` 把清洗后的 `playbook_id` 写进 `media_topic`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_ai_tagging.py` 顶部 import 加 `_clean_one_id, tag_topics`：
```python
from app.services.media_ai import _clean_ids, _build_asset_menu, _clean_one_id, tag_topics
import app.services.media_ai as media_ai
```

追加测试：
```python
def test_clean_one_id_keeps_valid_drops_bogus():
    assert _clean_one_id("A", {"A", "B"}) == "A"
    assert _clean_one_id("X", {"A", "B"}) == ""
    assert _clean_one_id(None, {"A"}) == ""
    assert _clean_one_id(123, {"A"}) == ""


def test_build_asset_menu_lists_playbooks():
    async def run():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('PBMENU','人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_playbook (id,persona_id,name,structure,when_to_use,"
                "evidence,source,status) VALUES "
                "('PBX','PBMENU','反常识开场','钩子-冲突-收','标题反直觉时','','legacy_mine','proven'),"
                "('PBV','PBMENU','痛点先行','','','','legacy_mine','validating')")
            await db.commit()
            return await _build_asset_menu(db, "PBMENU")
        finally:
            await db.close()
    menu = asyncio.run(run())
    assert "反常识开场" in menu["menu"]
    assert menu["valid_pb"] == {"PBX", "PBV"}


def test_tag_topics_writes_cleaned_playbook_id(monkeypatch):
    async def run():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('TAGP','人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_playbook (id,persona_id,name,structure,when_to_use,"
                "evidence,source,status) VALUES "
                "('GOODPB','TAGP','反常识开场','','','','legacy_mine','proven')")
            await db.execute(
                "INSERT INTO media_topic (id,persona_id,title,puzzle,status,tagged) VALUES "
                "('T_OK','TAGP','选题甲','谜','pool',0),"
                "('T_BOGUS','TAGP','选题乙','谜','pool',0)")
            await db.commit()

            async def fake_ask_ai(prompt, **kw):
                return {"response":
                        '[{"id":"T_OK","audience_ids":[],"anchor_ids":[],'
                        '"dropped_drift_ids":[],"playbook_id":"GOODPB"},'
                        '{"id":"T_BOGUS","audience_ids":[],"anchor_ids":[],'
                        '"dropped_drift_ids":[],"playbook_id":"编造的id"}]',
                        "cost": 0, "model": "test", "tokens": 0}
            monkeypatch.setattr(media_ai, "ask_ai", fake_ask_ai)
            await tag_topics(db, "TAGP")
            cur = await db.execute(
                "SELECT id,playbook_id FROM media_topic WHERE persona_id='TAGP'")
            return {r["id"]: r["playbook_id"] for r in await cur.fetchall()}
        finally:
            await db.close()
    got = asyncio.run(run())
    assert got["T_OK"] == "GOODPB"       # 合法 id 写入
    assert got["T_BOGUS"] == ""          # 编造 id 被清洗成空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_ai_tagging.py -k "playbook or clean_one" -v`
Expected: FAIL（`_clean_one_id` 未定义 / menu 无 valid_pb / playbook_id 未写）

- [ ] **Step 3: 改实现**

**3a.** `app/services/media_ai.py`，在 `_clean_ids` 定义之后加：
```python
def _clean_one_id(v, valid) -> str:
    """单个 id 清洗：合法字符串且在 valid 集里才留，否则空串。"""
    return v if isinstance(v, str) and v in valid else ""
```

**3b.** `_build_asset_menu`：在查 dropped 锚点之后、拼 `lines` 的段落里，加打法查询与菜单段。先在函数内查锚点那几段之后加：
```python
    cur = await db.execute(
        "SELECT id,name,structure,status FROM media_playbook "
        "WHERE persona_id=? AND status IN ('validating','proven')", (persona_id,))
    playbooks = [dict(r) for r in await cur.fetchall()]
```
在 `menu = "\n".join(...)` **之前**、其它 `lines.append` 段之后加：
```python
    if playbooks:
        lines.append("【打法库】选题若能套用某条已验证结构/打法，挑最贴的一条填进 playbook_id：")
        for pb in playbooks:
            struct = (pb.get("structure") or "")[:60]
            lines.append(f"- id={pb['id']}｜{pb['name']}｜结构:{struct}｜{pb['status']}")
```
return 的 dict 里加：
```python
        "valid_pb": {p["id"] for p in playbooks},
```

**3c.** `TAG_SYSTEM`：铁律加一条、输出格式加字段。铁律区加：
```
6. playbook_id 只填一条最贴的打法 id（真能套用该结构才填，不沾留空字符串 ""），只能从【打法库】给的 id 里选，绝不编造。
```
输出格式那行改成：
```
[{"id":"话题原样id","audience_ids":[],"anchor_ids":[],"dropped_drift_ids":[],"playbook_id":""}]
```

**3d.** `tag_topics`：解析循环里，`drift = _clean_ids(...)` 之后加：
```python
        pb = _clean_one_id(it.get("playbook_id"), menu["valid_pb"])
```
UPDATE 语句加 `playbook_id=?`：
```python
        await db.execute(
            "UPDATE media_topic SET audience_ids=?, anchor_ids=?, "
            "dropped_drift_ids=?, playbook_id=?, tagged=1 WHERE id=?",
            (json.dumps(aud, ensure_ascii=False), json.dumps(anc, ensure_ascii=False),
             json.dumps(drift, ensure_ascii=False), pb, tid))
```
`all_ids`（喂 log_injection 那行）并上打法 id：
```python
    all_ids = list(menu["valid_aud"] | menu["valid_anc"] | menu["valid_dropped"] | menu["valid_pb"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_ai_tagging.py -v`
Expected: PASS（新测全绿，原有 menu/clean_ids 测试不回归）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_ai_tagging.py
git commit -m "feat(media): tag_topics 给选题挑一条打法写 playbook_id(菜单/系统提示/清洗)"
```

---

### Task 4: 路由接线 — 决策打分把 playbooks 传进上下文

**Files:**
- Modify: `app/api/media.py:1062-1068`（决策打分路由，查资产→`build_decision_context`→`rank_pool`）
- Test: `tests/test_media_playbook_decision.py`（加一条 DB 集成测试，复现路由的查询+编排）

**Interfaces:**
- Consumes: `build_decision_context(..., playbooks=)`（Task 2）、`media_topic.playbook_id`（Task 1）、`media_playbook` 表。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_playbook_decision.py` 追加（复现路由的查询+build+rank 链路，验证命中打法的选题报告里出现打法行）：

```python
def test_route_query_wiring_surfaces_playbook_in_report():
    """复现决策路由：查 playbooks 传进 ctx，命中打法的选题报告含打法行。"""
    from app.services.media_decision import build_decision_context, rank_pool

    async def run():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('RT','人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_playbook (id,persona_id,name,structure,when_to_use,"
                "evidence,source,status) VALUES "
                "('RTPB','RT','反常识开场','','','','legacy_mine','proven')")
            await db.execute(
                "INSERT INTO media_topic (id,persona_id,title,puzzle,status,tagged,playbook_id) "
                "VALUES ('RTT','RT','选题','谜','pool',1,'RTPB')")
            await db.commit()
            # —— 复现路由 line 1062-1068 的查询与编排 ——
            cur = await db.execute(
                "SELECT id,name,status FROM media_playbook "
                "WHERE persona_id=? AND status IN ('validating','proven')", ("RT",))
            playbooks = [dict(r) for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT * FROM media_topic WHERE persona_id=? AND status='pool'", ("RT",))
            topics = [dict(r) for r in await cur.fetchall()]
            ctx = build_decision_context([], [], [], [], [], [], [], playbooks=playbooks)
            ranked = rank_pool(topics, ctx, "涨粉")
            return ranked[0]["decision_report"]
        finally:
            await db.close()
    report = asyncio.run(run())
    assert "匹配到打法《反常识开场》" in report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_playbook_decision.py::test_route_query_wiring_surfaces_playbook_in_report -v`
Expected: 此测试本身用的是 Task 2 已实现的 `build_decision_context(playbooks=)`，应能过——**若失败**说明 Task 2 未先完成，先做 Task 2。此测试的作用是锁住"路由该查什么、怎么传"的契约，先跑确认它绿，再去改路由让真实路由与之一致。

- [ ] **Step 3: 改路由**

`app/api/media.py`，在 `ctx = build_decision_context(...)`（line 1066）**之前**加 playbook 查询，并把 `playbooks=` 传进去。改动后这一段：

```python
        cur = await db.execute(
            "SELECT * FROM media_topic WHERE persona_id=? AND status='pool'", (pid,))
        topics = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT id,name,status FROM media_playbook "
            "WHERE persona_id=? AND status IN ('validating','proven')", (pid,))
        playbooks = [dict(r) for r in await cur.fetchall()]

        ctx = build_decision_context(traits, audiences, anchors, materials,
                                     recent, history, dropped_anchors, playbooks=playbooks)
        ranked = rank_pool(topics, ctx, phase)
```

- [ ] **Step 4: 跑全套 + controller 冒烟**

Run: `python -m pytest -v`
Expected: 全套 PASS（含本轮所有新测；无回归）

Controller 冒烟（真实路由端到端）：
```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
```
浏览器登录（admin/admin123）→ 给某人设建一条选题 + 一条 proven 打法 → 手动把该选题 `playbook_id` 设成该打法（或先跑一次 AI 打标）→ 触发决策打分（人设页/选题页的"AI 打分/推荐"入口）→ 看该选题的决策报告里出现 `＋匹配到打法《…》（proven）`，且没匹配的选题分数不变。确认无 Jinja/500。

- [ ] **Step 5: 提交**

```bash
git add app/api/media.py tests/test_media_playbook_decision.py
git commit -m "feat(media): 决策打分路由查 playbooks 传进上下文(打法库🅒接决策引擎完成)"
```

---

## Self-Review

**Spec coverage:**
- §1 数据（playbook_id 列）→ Task 1 ✅
- §2 AI 打标（菜单/系统提示/tag_topics 写标/清洗）→ Task 3 ✅
- §3 打分器（status 加权常量/build_decision_context 参数/score_topic 因子/命中才计入 pos/报告/C 类循环改/WEIGHTS）→ Task 2 ✅
- §4 调用侧（路由查 playbooks 传参）→ Task 4 ✅
- §测试（打分器单测/tag_topics 单测/集成）→ Task 2、3、4 各自测试 ✅
- §不在本轮（对标来源/一条打法/不碰写稿/老话题不补标）→ 计划未越界 ✅

**Placeholder scan:** 无 TBD/TODO；每步含真实代码与命令。✅

**Type consistency:**
- `build_decision_context(..., playbooks=None)` 在 Task 2 定义、Task 4 用 `playbooks=` 关键字传，一致。✅
- `_clean_one_id(v, valid) -> str` Task 3 定义即用，一致。✅
- `factors["playbook"]` 结构 `{"value","note"}` 与其它因子一致，报告/pos 读法一致。✅
- `menu["valid_pb"]` Task 3 produces、tag_topics 用，一致。✅
- `media_topic.playbook_id` Task 1 建、Task 3 写、Task 4 SELECT *（自动带出），一致。✅
