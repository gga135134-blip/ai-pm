# 受众画像 + 生意锚点 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把受众画像和生意锚点做成结构化资产富表（2 张新表 + AI 访谈起草 + 人拍板 + 各自独立查看页），为后面的选题筛选/决策引擎备好数据底座。

**Architecture:** 两张新表 `media_audience` / `media_anchor` 进 SCHEMA（零 ALTER 迁移）。录入复用人设访谈那套「AI 起草候选 → 人拍板 adopt 写库」。查看页仿刚上线的原料库页（`media_materials.html`）卡片风。**不接写脚本注入**（写稿继续用人设 trait 的一句话），不动 finalize / 人设 trait / 注入预算。

**Tech Stack:** Python FastAPI + aiosqlite + Jinja2 + vanilla JS（无构建）。测试 pytest + TestClient + 伪造签名 cookie + monkeypatch 打桩 AI。

## Global Constraints

- **零 ALTER 迁移**：新表进 `app/database.py` 的 `SCHEMA` 字符串（`CREATE TABLE IF NOT EXISTS`），不写 MIGRATIONS。
- **不接写稿注入**：不改 `write_script` / `build_script_context` / 注入预算 / 人设 trait / finalize。富表只被查看页读写。
- **人拍板闸**：AI 起草函数只返回候选，绝不写库（除 `log_injection` 记账）。写库只在 adopt/manual 路由由人工触发。
- **AI 成本可见**：`draft_audience_segments` / `draft_anchors` 调用后必须 `log_injection`。
- **诚实不编造**：SYSTEM 提示要求 AI 只基于用户给的文本归纳，`language` 必须是受众真实原话/用户措辞，不自造。
- **字段夹取**：`pay_willingness`/`confidence` 夹 1-5、非法默认 3（用 `isinstance(x,int)` 判断，沿用 `persona_interview_extract` 写法）；anchor `type` 越界默认 `service`；全部 AI 取值走 `_txt()` 兜底。
- **改模板禁 PowerShell -replace**（毁中文），一律 Edit/Write。Jinja 无 tojson；TemplateResponse 三参数（由现有 `_tpl` 处理）；模板 dict 键别用 items/keys/values/get。
- **`<script>` 铁律**：AJAX 里绝不把 SVG/emoji/多行图标塞进 JS 单引号字符串；escapeHtml 防 XSS；采纳 POST 用 x-www-form-urlencoded。（沿用功能B 验证过的写法。）
- **跑测试**：`python -m pytest tests/ -q -p no:cacheprovider`（别用 PowerShell 管道 Select-Object，会假挂）。
- 现有全套基线 **164 passed**，每个 Task 完成后应仍全绿。
- 参考已上线的同构代码：路由/模板仿 `app/api/media.py` 的 `materials_home`/`material_create`/`material_archive`（约 line 274-350）+ `app/templates/media_materials.html`；AI 函数仿 `app/services/media_ai.py` 的 `persona_interview_extract`（约 line 397-446）；AJAX 仿 `media_persona.html` 末尾的 `learnEdits` 脚本。

---

### Task 1: Schema —— media_audience + media_anchor 两张新表

**Files:**
- Modify: `app/database.py`（SCHEMA 字符串里、`media_material` 表定义之后、SCHEMA 结束 `"""` 之前插入两张表）
- Test: `tests/test_media_schema.py`（追加）

**Interfaces:**
- Produces：表 `media_audience`、`media_anchor` 及其列（后续所有 Task 依赖）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_schema.py` 末尾追加：

```python
# ─────────────── 二期资产层🅑：受众画像 + 生意锚点 ───────────────

def test_audience_anchor_tables_exist():
    assert {"media_audience", "media_anchor"} <= _tables()


def test_audience_columns():
    cols = _cols("media_audience")
    assert {"persona_id", "segment", "who", "anxiety", "desire", "objection",
            "language", "pay_willingness", "pay_scene", "pay_ceiling",
            "evidence", "confidence", "source", "status"} <= cols


def test_anchor_columns():
    cols = _cols("media_anchor")
    assert {"persona_id", "name", "type", "value_prop", "price_band",
            "path", "evidence", "source", "status"} <= cols
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_schema.py -q -p no:cacheprovider -k "audience or anchor"`
Expected: FAIL（表不存在）

- [ ] **Step 3: 写实现**

在 `app/database.py` 的 SCHEMA 字符串里，`media_material` 表定义之后（`CREATE TABLE IF NOT EXISTS media_material (...);` 之后、SCHEMA 的结束 `"""` 之前）插入：

```sql
CREATE TABLE IF NOT EXISTS media_audience (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    segment TEXT DEFAULT '',
    who TEXT DEFAULT '',
    anxiety TEXT DEFAULT '',
    desire TEXT DEFAULT '',
    objection TEXT DEFAULT '',
    language TEXT DEFAULT '',
    pay_willingness INTEGER DEFAULT 3,
    pay_scene TEXT DEFAULT '',
    pay_ceiling TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    confidence INTEGER DEFAULT 3,
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_anchor (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    type TEXT DEFAULT 'service',
    value_prop TEXT DEFAULT '',
    price_band TEXT DEFAULT '',
    path TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'validating',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

> 注意：加在 SCHEMA 那段三引号字符串**内部**（其它 `CREATE TABLE` 旁边），不是 Python 代码区。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_schema.py -q -p no:cacheprovider`
Expected: PASS（含新加 3 个）

- [ ] **Step 5: 提交**

```bash
git add app/database.py tests/test_media_schema.py
git commit -m "feat(media): media_audience/media_anchor 两张资产表(进SCHEMA零迁移)"
```

---

### Task 2: AI 起草函数 draft_audience_segments + draft_anchors

**Files:**
- Modify: `app/services/media_ai.py`（追加 2 常量 SYSTEM 提示 + 2 函数；放在 `learn_edit_style` 之后即可）
- Test: `tests/test_media_audience_ai.py`（新建）

**Interfaces:**
- Consumes（现有）：`ask_ai`、`extract_json`、`log_injection`、`_txt`（同文件/同 import，功能B 已验证可用）
- Produces：
  - `AUDIENCE_TYPES` 无需；`ANCHOR_TYPES = {"product","service","带货","广告","引流私域"}`（set 常量）
  - `async def draft_audience_segments(db, persona_id, answers, model="auto") -> dict`
    返回 `{"ok", "segments":[{segment,who,anxiety,desire,objection,language,pay_willingness,pay_scene,pay_ceiling,evidence,confidence}], "error","cost","model"}`
  - `async def draft_anchors(db, persona_id, answers, model="auto") -> dict`
    返回 `{"ok", "anchors":[{name,type,value_prop,price_band,path,evidence}], "error","cost","model"}`；`type ∈ ANCHOR_TYPES`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_audience_ai.py`：

```python
"""受众/锚点 AI 起草函数单测：AI 打桩，验夹取/兜底/成本记账/空回答短路。"""
import asyncio
import json

import app.services.media_ai as mai
from tests.media_helpers import make_db


async def _seed(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,current_phase,status) "
        "VALUES (?,?,?, 'active')", (pid, "嘉姐", "AI落地期"))
    await db.commit()


def test_draft_audience_clamps_fields(monkeypatch):
    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps({"segments": [
            {"segment": "焦虑的中小老板", "who": "35-50岁传统行业老板",
             "anxiety": "怕被AI淘汰又不懂", "desire": "花小钱把AI用起来",
             "objection": "怕交智商税", "language": "这玩意到底能不能落地",
             "pay_willingness": 99, "pay_scene": "看到同行用上了",
             "pay_ceiling": "几千", "evidence": "私信常问", "confidence": "x"}]}),
            "cost": 0, "model": "m", "tokens": 50}
    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.draft_audience_segments(db, "P1", "我的粉丝大多是传统老板")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    seg = res["segments"][0]
    assert seg["pay_willingness"] == 3      # 99 非法→默认 3
    assert seg["confidence"] == 3           # "x" 非法→默认 3
    assert seg["language"] == "这玩意到底能不能落地"


def test_draft_audience_empty_answer_no_ai(monkeypatch):
    async def fake_ask(*a, **k):
        raise AssertionError("空回答不该调 AI")
    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.draft_audience_segments(db, "P1", "   ")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is False and res["segments"] == []


def test_draft_anchors_clamps_type(monkeypatch):
    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps({"anchors": [
            {"name": "1v1陪跑", "type": "乱写的类型", "value_prop": "手把手落地",
             "price_band": "几千", "path": "内容→私信→付费", "evidence": "成过3单"},
            {"name": "训练营", "type": "service", "value_prop": "系统课",
             "price_band": "低", "path": "内容→社群→报名", "evidence": ""}]}),
            "cost": 0, "model": "m", "tokens": 30}
    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.draft_anchors(db, "P1", "我靠陪跑和训练营变现")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["anchors"][0]["type"] == "service"   # 越界→默认 service
    assert res["anchors"][1]["type"] == "service"
    assert res["anchors"][0]["name"] == "1v1陪跑"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_audience_ai.py -q -p no:cacheprovider`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 写实现**

在 `app/services/media_ai.py` 的 `learn_edit_style` 函数之后追加：

```python
ANCHOR_TYPES = {"product", "service", "带货", "广告", "引流私域"}

AUDIENCE_DRAFT_SYSTEM = """你把创作者对自己受众的一段描述（或粘贴的评论/私信文本），提炼成结构化的受众画像 segment。

铁律：
1. 只基于给定文本归纳，绝不编造创作者没提到的人群或数据。看不出就少提。
2. language 字段必须是受众真实原话或创作者提供的措辞，绝不自造口吻——它会直接进文案。
3. 每个 segment 给：segment(人群名)、who(他们是谁)、anxiety(在焦虑什么)、desire(渴望)、objection(顾虑)、language(他们的原话)、pay_willingness(付费意愿1-5)、pay_scene(什么场景掏钱)、pay_ceiling(价格带)、evidence(依据)、confidence(1-5)。
4. 只输出 JSON：{"segments":[{...}]}，不要解释。"""

ANCHOR_DRAFT_SYSTEM = """你把创作者对自己变现方式的描述，提炼成结构化的生意锚点。

铁律：
1. 只基于给定文本归纳，绝不编造不存在的产品或转化数据。
2. type 只能是 product/service/带货/广告/引流私域 之一。
3. 每个锚点给：name(锚点名)、type、value_prop(解决什么问题)、price_band(价格带)、path(从内容到成交的路径)、evidence(转化数据/依据)。
4. 只输出 JSON：{"anchors":[{...}]}，不要解释。"""


async def draft_audience_segments(db, persona_id: str, answers: str,
                                  model: str = "auto") -> dict:
    """把用户对受众的一段回答/粘贴文本，提炼成 segment 画像候选。绝不写库。资产层🅑。"""
    cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (persona_id,))
    if not await cur.fetchone():
        return {"ok": False, "error": "人设不存在", "segments": [], "cost": 0, "model": ""}
    if not (answers or "").strip():
        return {"ok": False, "error": "没有内容可提炼", "segments": [], "cost": 0, "model": ""}

    result = await ask_ai(f"【创作者的描述】\n{answers[:8000]}\n\n请提炼成受众画像 segment。",
                          model=model, task_type="media_draft_audience",
                          system_prompt=AUDIENCE_DRAFT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "segments": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("segments") or []) if isinstance(it, dict)]
    segments = []
    for it in raw:
        seg = _txt(it.get("segment"))
        if not seg:
            continue
        pw = it.get("pay_willingness")
        pw = pw if isinstance(pw, int) and 1 <= pw <= 5 else 3
        cf = it.get("confidence")
        cf = cf if isinstance(cf, int) and 1 <= cf <= 5 else 3
        segments.append({
            "segment": seg, "who": _txt(it.get("who")), "anxiety": _txt(it.get("anxiety")),
            "desire": _txt(it.get("desire")), "objection": _txt(it.get("objection")),
            "language": _txt(it.get("language")), "pay_willingness": pw,
            "pay_scene": _txt(it.get("pay_scene")), "pay_ceiling": _txt(it.get("pay_ceiling")),
            "evidence": _txt(it.get("evidence")), "confidence": cf,
        })
    await log_injection(db, "", "media_draft_audience", [], result.get("tokens", 0))
    return {"ok": True, "segments": segments, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def draft_anchors(db, persona_id: str, answers: str, model: str = "auto") -> dict:
    """把用户对变现方式的描述，提炼成锚点候选。绝不写库。资产层🅑。"""
    cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (persona_id,))
    if not await cur.fetchone():
        return {"ok": False, "error": "人设不存在", "anchors": [], "cost": 0, "model": ""}
    if not (answers or "").strip():
        return {"ok": False, "error": "没有内容可提炼", "anchors": [], "cost": 0, "model": ""}

    result = await ask_ai(f"【创作者的描述】\n{answers[:8000]}\n\n请提炼成生意锚点。",
                          model=model, task_type="media_draft_anchor",
                          system_prompt=ANCHOR_DRAFT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "anchors": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("anchors") or []) if isinstance(it, dict)]
    anchors = []
    for it in raw:
        name = _txt(it.get("name"))
        if not name:
            continue
        atype = it.get("type") if it.get("type") in ANCHOR_TYPES else "service"
        anchors.append({
            "name": name, "type": atype, "value_prop": _txt(it.get("value_prop")),
            "price_band": _txt(it.get("price_band")), "path": _txt(it.get("path")),
            "evidence": _txt(it.get("evidence")),
        })
    await log_injection(db, "", "media_draft_anchor", [], result.get("tokens", 0))
    return {"ok": True, "anchors": anchors, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_audience_ai.py -q -p no:cacheprovider`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_audience_ai.py
git commit -m "feat(media): draft_audience_segments/draft_anchors——AI起草画像候选"
```

---

### Task 3: 受众页 —— 路由 + 模板 + 入口

**Files:**
- Modify: `app/api/media.py`（import 加 `draft_audience_segments`；加 5 个受众路由）
- Create: `app/templates/media_audience.html`
- Modify: `app/templates/media_board.html` + `app/templates/media_persona.html`（各加「受众」入口）
- Test: `tests/test_media_routes.py`（追加）

**Interfaces:**
- Consumes：`draft_audience_segments`（Task 2）、`_first_persona_id`、`_tpl`（现有）
- Produces：`GET /media/audience`、`POST /media/audience`、`POST /media/audience/draft`、`POST /media/audience/adopt`、`POST /media/audience/{aid}/archive`

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_routes.py` 末尾追加（复用现有 `_client`、`_seed_persona_real`、`_only_active_persona`）：

```python
# ─────────────── 受众画像 ───────────────

def _audience_rows(pid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT * FROM media_audience WHERE persona_id=? ORDER BY created_at", (pid,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    return asyncio.run(go())


def test_audience_manual_create():
    _seed_persona_real()
    r = _client().post("/media/audience", data={
        "persona_id": "RTP2", "segment": "焦虑的中小老板", "who": "35-50传统行业",
        "anxiety": "怕被AI淘汰", "language": "这玩意能落地不", "pay_willingness": "4"},
        follow_redirects=False)
    assert r.status_code == 302
    rows = [x for x in _audience_rows("RTP2") if x["segment"] == "焦虑的中小老板"]
    assert len(rows) == 1
    assert rows[0]["source"] == "manual" and rows[0]["pay_willingness"] == 4
    assert rows[0]["language"] == "这玩意能落地不"


def test_audience_draft_route(monkeypatch):
    async def fake(db, pid, answers, model="auto"):
        return {"ok": True, "segments": [{"segment": "S1", "who": "w", "anxiety": "a",
                "desire": "d", "objection": "o", "language": "原话", "pay_willingness": 4,
                "pay_scene": "ps", "pay_ceiling": "pc", "evidence": "e", "confidence": 3}],
                "error": "", "cost": 0, "model": "m"}
    monkeypatch.setattr("app.api.media.draft_audience_segments", fake)
    _seed_persona_real()
    r = _client().post("/media/audience/draft", data={"answers": "我的粉丝是老板"})
    assert r.status_code == 200
    assert r.json()["segments"][0]["language"] == "原话"


def test_audience_adopt_writes_row():
    _seed_persona_real()
    r = _client().post("/media/audience/adopt", data={
        "persona_id": "RTP2", "segment": "S2", "who": "w", "anxiety": "a",
        "language": "原话2", "pay_willingness": "5", "confidence": "4"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = [x for x in _audience_rows("RTP2") if x["segment"] == "S2"]
    assert rows[0]["source"] == "interview" and rows[0]["pay_willingness"] == 5


def test_audience_archive_and_page(monkeypatch):
    _only_active_persona()

    async def seed():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_audience WHERE persona_id='RTP2'")
            await db.execute("INSERT INTO media_audience "
                "(id,persona_id,segment,pay_willingness,status) VALUES "
                "('AUD1','RTP2','高付费段',5,'active'),"
                "('AUD2','RTP2','低付费段',2,'active')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    # 归档一条
    r = _client().post("/media/audience/AUD2/archive", follow_redirects=False)
    assert r.status_code == 302

    html = _client().get("/media/audience").text
    assert "高付费段" in html            # active 显示
    assert "低付费段" not in html         # archived 不显示
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k audience`
Expected: FAIL（路由不存在）

- [ ] **Step 3a: media.py import + 受众路由**

`app/api/media.py` 顶部 `from app.services.media_ai import (...)` 块追加 `draft_audience_segments`（与 Task 4 的 `draft_anchors` 一起加；Task 3 先加 `draft_audience_segments`）。

在原料库路由块之后（`material_archive` 之后、`# ─────────────── 话题库` 之前）加：

```python
# ─────────────── 受众画像（资产层🅑 media_audience）───────────────

@router.get("/media/audience", response_class=HTMLResponse)
async def audience_home(request: Request):
    """受众画像查看页：segment 卡片，按付费意愿降序（值钱的靠前）。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        persona = None
        segments = []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_audience WHERE persona_id=? AND status='active' "
                "ORDER BY pay_willingness DESC, confidence DESC, created_at DESC", (pid,))
            segments = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_audience.html",
                {"persona": persona, "segments": segments, "total": len(segments)})


@router.post("/media/audience")
async def audience_create(persona_id: str = Form(...), segment: str = Form(...),
                          who: str = Form(""), anxiety: str = Form(""),
                          desire: str = Form(""), objection: str = Form(""),
                          language: str = Form(""), pay_willingness: int = Form(3),
                          pay_scene: str = Form(""), pay_ceiling: str = Form(""),
                          evidence: str = Form("")):
    """手动新增一条 segment（source='manual'）。"""
    if not segment.strip():
        return RedirectResponse("/media/audience", status_code=302)
    pw = pay_willingness if 1 <= pay_willingness <= 5 else 3
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_audience (id,persona_id,segment,who,anxiety,desire,"
            "objection,language,pay_willingness,pay_scene,pay_ceiling,evidence,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'manual')",
            (str(uuid.uuid4()), persona_id, segment.strip(), who.strip(), anxiety.strip(),
             desire.strip(), objection.strip(), language.strip(), pw,
             pay_scene.strip(), pay_ceiling.strip(), evidence.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/audience", status_code=302)


@router.post("/media/audience/draft")
async def audience_draft(answers: str = Form("")):
    """AI 起草受众画像候选（不写库）。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return JSONResponse({"ok": False, "error": "先建人设", "segments": []})
        try:
            result = await draft_audience_segments(db, pid, answers)
        except Exception as e:
            log.exception("受众起草失败")
            return JSONResponse({"ok": False, "error": str(e), "segments": []})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/audience/adopt")
async def audience_adopt(persona_id: str = Form(...), segment: str = Form(...),
                         who: str = Form(""), anxiety: str = Form(""),
                         desire: str = Form(""), objection: str = Form(""),
                         language: str = Form(""), pay_willingness: int = Form(3),
                         pay_scene: str = Form(""), pay_ceiling: str = Form(""),
                         evidence: str = Form(""), confidence: int = Form(3)):
    """人拍板：把一条候选 segment 写库（source='interview'）。"""
    pw = pay_willingness if 1 <= pay_willingness <= 5 else 3
    cf = confidence if 1 <= confidence <= 5 else 3
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_audience (id,persona_id,segment,who,anxiety,desire,"
            "objection,language,pay_willingness,pay_scene,pay_ceiling,evidence,"
            "confidence,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'interview')",
            (str(uuid.uuid4()), persona_id, segment.strip(), who.strip(), anxiety.strip(),
             desire.strip(), objection.strip(), language.strip(), pw,
             pay_scene.strip(), pay_ceiling.strip(), evidence.strip(), cf))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/audience/{aid}/archive")
async def audience_archive(aid: str):
    db = await get_db()
    try:
        await db.execute("UPDATE media_audience SET status='archived' WHERE id=?", (aid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/audience", status_code=302)
```

- [ ] **Step 3b: 模板 media_audience.html**

新建 `app/templates/media_audience.html`（仿 `media_materials.html` 卡片风 + 功能B AJAX 模式）：

```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}受众画像{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} <b>受众画像</b></span>
{% endblock %}
{% block content %}
<style>
  .seg-card{ border:1px solid var(--border); border-radius:8px; padding:12px 14px; margin-bottom:10px; }
  .seg-name{ font-size:14px; font-weight:600; color:var(--ink-1); }
  .seg-lang{ font-size:12.5px; color:var(--ai); background:var(--panel-2); border-radius:6px; padding:6px 9px; margin-top:6px; }
  .seg-meta{ font-size:11.5px; color:var(--ink-3); margin-top:5px; display:flex; flex-wrap:wrap; gap:10px; }
  .pw{ color:var(--warn); white-space:nowrap; }
</style>

{% if not persona %}
<div class="module" style="max-width:460px; margin:0 auto">
  <div class="mh"><span class="ttl">还没有人设</span></div>
  <div class="inner"><p style="font-size:13px; color:var(--ink-3); margin-bottom:14px">受众画像挂在人设下，先去建一个人设。</p>
  <a href="/media/persona" class="btn primary">去建人设</a></div>
</div>
{% else %}

<div style="margin-bottom:18px">
  <div class="pname" style="margin-bottom:8px">受众画像</div>
  <div style="display:flex; align-items:center; flex-wrap:wrap; gap:10px">
    <span style="font-size:13px; color:var(--ink-2)">{{ persona.name }} · 选题和生意的双重筛子</span>
    <span class="pill run">{{ total }} 个人群</span>
    <span class="sp"></span>
    <a href="/media/anchor" class="btn" style="padding:6px 12px; font-size:12.5px">{{ ic.icon('folder') }}生意锚点</a>
    <a href="/media" class="btn" style="padding:6px 12px; font-size:12.5px">← 看板</a>
  </div>
</div>

<div class="module">
  <div class="mh"><span class="ttl">🎯 AI 帮我梳理受众</span></div>
  <div class="inner">
    <p style="font-size:12px; color:var(--ink-3); margin-bottom:8px">说说你的粉丝/买家是谁、在愁什么、口头怎么说；也可以把评论区/私信粘一段进来。</p>
    <textarea id="aud-answers" rows="3" class="input w-full" placeholder="我的粉丝大多是……他们最焦虑……常说……"></textarea>
    <button id="aud-draft-btn" class="btn ai" style="margin-top:8px" onclick="draftAudience('{{ persona.id }}')">让 AI 提炼</button>
    <div id="aud-cands" style="margin-top:12px"></div>
  </div>
</div>

<div class="module" style="margin-top:12px">
  <div class="mh"><span class="ttl">人群</span><span class="sp"></span><span class="sub">按付费意愿排序，值钱的靠前</span></div>
  <div class="inner">
    {% if not segments %}<div class="empty">还没有受众画像。上面让 AI 帮你梳理，或下面手动加一条。</div>{% endif %}
    {% for s in segments %}
    <div class="seg-card">
      <div style="display:flex; align-items:flex-start; gap:10px">
        <div style="flex:1"><span class="seg-name">{{ s.segment }}</span>
          {% if s.who %}<span style="font-size:12px; color:var(--ink-3)"> · {{ s.who }}</span>{% endif %}</div>
        <span class="pw" title="付费意愿">{{ '★' * s.pay_willingness }}</span>
        <form method="post" action="/media/audience/{{ s.id }}/archive" onsubmit="return confirm('归档这个人群？')">
          <button class="iconbtn danger" title="归档">{{ ic.icon('trash') }}</button></form>
      </div>
      {% if s.anxiety %}<div style="font-size:12.5px; color:var(--ink-2); margin-top:4px">焦虑：{{ s.anxiety }}</div>{% endif %}
      {% if s.language %}<div class="seg-lang">🗣️ 原话（可直接进文案）：{{ s.language }}</div>{% endif %}
      <div class="seg-meta">
        {% if s.desire %}<span>渴望：{{ s.desire }}</span>{% endif %}
        {% if s.objection %}<span>顾虑：{{ s.objection }}</span>{% endif %}
        {% if s.pay_scene %}<span>掏钱场景：{{ s.pay_scene }}</span>{% endif %}
        {% if s.pay_ceiling %}<span>价格带：{{ s.pay_ceiling }}</span>{% endif %}
        <span class="tag" style="color:var(--ink-3); background:var(--panel-2)">{{ s.source or 'manual' }}</span>
      </div>
    </div>
    {% endfor %}

    <details style="margin-top:10px">
      <summary style="font-size:13px; color:var(--accent); cursor:pointer">+ 手动加一个人群</summary>
      <form method="post" action="/media/audience" class="space-y-2" style="margin-top:10px">
        <input type="hidden" name="persona_id" value="{{ persona.id }}">
        <input name="segment" required placeholder="人群名（必填）" class="input w-full">
        <input name="who" placeholder="他们是谁（年龄/职业/状态）" class="input w-full" style="margin-top:8px">
        <input name="anxiety" placeholder="在焦虑什么" class="input w-full" style="margin-top:8px">
        <input name="language" placeholder="他们的原话（进文案用）" class="input w-full" style="margin-top:8px">
        <input name="desire" placeholder="渴望（可留空）" class="input w-full" style="margin-top:8px">
        <input name="objection" placeholder="顾虑（可留空）" class="input w-full" style="margin-top:8px">
        <select name="pay_willingness" class="input w-full" style="margin-top:8px">
          <option value="5">付费意愿 ★★★★★</option><option value="4">★★★★</option>
          <option value="3" selected>★★★</option><option value="2">★★</option><option value="1">★</option>
        </select>
        <input name="pay_scene" placeholder="什么场景掏钱（可留空）" class="input w-full" style="margin-top:8px">
        <input name="pay_ceiling" placeholder="价格带（可留空）" class="input w-full" style="margin-top:8px">
        <button class="btn primary" style="margin-top:10px">保存</button>
      </form>
    </details>
  </div>
</div>

<script>
async function draftAudience(pid){
  var btn = document.getElementById('aud-draft-btn');
  var box = document.getElementById('aud-cands');
  var ans = document.getElementById('aud-answers').value;
  btn.disabled = true; btn.textContent = '正在提炼…'; box.innerHTML = '';
  try{
    var fd = new URLSearchParams(); fd.set('answers', ans);
    var r = await fetch('/media/audience/draft', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:fd});
    var d = await r.json();
    if(!d.ok){ box.innerHTML = '<div class="empty">'+(d.error||'提炼失败')+'</div>'; return; }
    if(!d.segments || d.segments.length===0){ box.innerHTML = '<div class="empty">没提炼出画像，多说点或换个角度。</div>'; return; }
    d.segments.forEach(function(s){
      var card = document.createElement('div'); card.className = 'seg-card';
      card.innerHTML =
        '<div class="seg-name">'+escapeHtml(s.segment)+' <span style="font-size:12px;color:var(--ink-3)">'+escapeHtml(s.who||'')+'</span></div>'+
        (s.anxiety?'<div style="font-size:12.5px;color:var(--ink-2);margin-top:4px">焦虑：'+escapeHtml(s.anxiety)+'</div>':'')+
        (s.language?'<div class="seg-lang">🗣️ 原话：'+escapeHtml(s.language)+'</div>':'')+
        '<div style="margin-top:8px;display:flex;gap:8px">'+
        '<button class="btn primary" style="padding:4px 12px">采纳</button>'+
        '<button class="btn ghost" style="padding:4px 12px">丢弃</button></div>';
      var btns = card.querySelectorAll('button');
      btns[0].onclick = async function(){
        btns[0].disabled = true; btns[0].textContent = '采纳中…';
        var f = new URLSearchParams();
        f.set('persona_id', pid); f.set('segment', s.segment); f.set('who', s.who||'');
        f.set('anxiety', s.anxiety||''); f.set('desire', s.desire||''); f.set('objection', s.objection||'');
        f.set('language', s.language||''); f.set('pay_willingness', s.pay_willingness||3);
        f.set('pay_scene', s.pay_scene||''); f.set('pay_ceiling', s.pay_ceiling||'');
        f.set('evidence', s.evidence||''); f.set('confidence', s.confidence||3);
        var rr = await fetch('/media/audience/adopt', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:f});
        var dd = await rr.json();
        if(dd.ok){ card.innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">✓ 已入库（刷新页面看）</div>'; }
        else { btns[0].disabled=false; btns[0].textContent='采纳'; }
      };
      btns[1].onclick = function(){ card.remove(); };
      box.appendChild(card);
    });
  } catch(e){ box.innerHTML = '<div class="empty">出错了：'+e+'</div>'; }
  finally { btn.disabled = false; btn.textContent = '让 AI 提炼'; }
}
function escapeHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 3c: 入口**

`app/templates/media_board.html`：在原料库入口那一行后加（找到 `<a href="/media/materials"` 那行，在其后加）：

```html
    <a href="/media/audience" class="btn" style="padding:6px 12px; font-size:12.5px">{{ ic.icon('folder') }}受众</a>
```

`app/templates/media_persona.html`：在原料库入口那行（`<a href="/media/materials"`）后加同样一行。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k audience`
Expected: PASS（4 passed）；再跑整文件 `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider` 全绿。

- [ ] **Step 5: 提交**

```bash
git add app/api/media.py app/templates/media_audience.html app/templates/media_board.html app/templates/media_persona.html tests/test_media_routes.py
git commit -m "feat(media): 受众画像页——AI起草人拍板+手动录入+付费意愿排序"
```

> 浏览器 JS 验证由 controller 在 Task 4 之后统一做（受众+锚点两页一起验）。

---

### Task 4: 锚点页 —— 路由 + 模板 + 入口

**Files:**
- Modify: `app/api/media.py`（import 加 `draft_anchors`；加 5 个锚点路由 + `ANCHOR_TYPE_LABELS` 常量）
- Create: `app/templates/media_anchor.html`
- Modify: `app/templates/media_board.html` + `app/templates/media_persona.html`（各加「锚点」入口）
- Test: `tests/test_media_routes.py`（追加）

**Interfaces:**
- Consumes：`draft_anchors`（Task 2）、`ANCHOR_TYPES`（Task 2，media_ai）
- Produces：`GET /media/anchor`、`POST /media/anchor`、`POST /media/anchor/draft`、`POST /media/anchor/adopt`、`POST /media/anchor/{aid}/archive`

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_routes.py` 末尾追加：

```python
# ─────────────── 生意锚点 ───────────────

def _anchor_rows(pid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT * FROM media_anchor WHERE persona_id=? ORDER BY created_at", (pid,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    return asyncio.run(go())


def test_anchor_manual_create():
    _seed_persona_real()
    r = _client().post("/media/anchor", data={
        "persona_id": "RTP2", "name": "1v1陪跑", "type": "service",
        "value_prop": "手把手落地", "price_band": "几千", "path": "内容→私信→付费"},
        follow_redirects=False)
    assert r.status_code == 302
    rows = [x for x in _anchor_rows("RTP2") if x["name"] == "1v1陪跑"]
    assert rows[0]["source"] == "manual" and rows[0]["type"] == "service"


def test_anchor_draft_route(monkeypatch):
    async def fake(db, pid, answers, model="auto"):
        return {"ok": True, "anchors": [{"name": "训练营", "type": "service",
                "value_prop": "系统课", "price_band": "低", "path": "内容→社群→报名",
                "evidence": ""}], "error": "", "cost": 0, "model": "m"}
    monkeypatch.setattr("app.api.media.draft_anchors", fake)
    _seed_persona_real()
    r = _client().post("/media/anchor/draft", data={"answers": "我靠训练营变现"})
    assert r.status_code == 200
    assert r.json()["anchors"][0]["name"] == "训练营"


def test_anchor_adopt_and_archive_and_page():
    _only_active_persona()
    c = _client()
    r = c.post("/media/anchor/adopt", data={
        "persona_id": "RTP2", "name": "已跑通锚点", "type": "service",
        "value_prop": "vp", "status": "proven"})
    assert r.json()["ok"] is True

    async def seed_more():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_anchor "
                "(id,persona_id,name,type,status) VALUES ('ANC_D','RTP2','废弃锚点','service','dropped')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_more())

    r = c.post("/media/anchor/ANC_D/archive", follow_redirects=False)
    assert r.status_code == 302
    html = c.get("/media/anchor").text
    assert "已跑通锚点" in html         # active 显示
    assert "废弃锚点" not in html        # archived 不显示
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k anchor`
Expected: FAIL

- [ ] **Step 3a: media.py import + 锚点路由**

`from app.services.media_ai import (...)` 块补 `draft_anchors`（与 Task 3 的 `draft_audience_segments` 同块）。在受众路由之后加常量 + 5 路由：

```python
ANCHOR_TYPE_LABELS = {
    "product": "自有产品", "service": "服务", "带货": "带货",
    "广告": "广告", "引流私域": "引流私域",
}
ANCHOR_STATUS_ORDER = ["proven", "validating", "dropped"]
ANCHOR_STATUS_LABELS = {"proven": "已跑通", "validating": "验证中", "dropped": "已放弃"}


@router.get("/media/anchor", response_class=HTMLResponse)
async def anchor_home(request: Request):
    """生意锚点查看页：按 status 分组 proven→validating→dropped。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        persona = None
        anchors = []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_anchor WHERE persona_id=? AND status!='archived' "
                "ORDER BY created_at DESC", (pid,))
            anchors = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    by_status = {}
    for st in ANCHOR_STATUS_ORDER:
        hit = [a for a in anchors if a["status"] == st]
        if hit:
            by_status[st] = hit
    return _tpl(request, "media_anchor.html",
                {"persona": persona, "anchors_by_status": by_status, "total": len(anchors),
                 "type_labels": ANCHOR_TYPE_LABELS, "status_labels": ANCHOR_STATUS_LABELS,
                 "status_order": ANCHOR_STATUS_ORDER})


@router.post("/media/anchor")
async def anchor_create(persona_id: str = Form(...), name: str = Form(...),
                        type: str = Form("service"), value_prop: str = Form(""),
                        price_band: str = Form(""), path: str = Form(""),
                        evidence: str = Form(""), status: str = Form("validating")):
    if not name.strip():
        return RedirectResponse("/media/anchor", status_code=302)
    atype = type if type in ANCHOR_TYPE_LABELS else "service"
    st = status if status in ANCHOR_STATUS_LABELS else "validating"
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_anchor (id,persona_id,name,type,value_prop,price_band,"
            "path,evidence,status,source) VALUES (?,?,?,?,?,?,?,?,?, 'manual')",
            (str(uuid.uuid4()), persona_id, name.strip(), atype, value_prop.strip(),
             price_band.strip(), path.strip(), evidence.strip(), st))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/anchor", status_code=302)


@router.post("/media/anchor/draft")
async def anchor_draft(answers: str = Form("")):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return JSONResponse({"ok": False, "error": "先建人设", "anchors": []})
        try:
            result = await draft_anchors(db, pid, answers)
        except Exception as e:
            log.exception("锚点起草失败")
            return JSONResponse({"ok": False, "error": str(e), "anchors": []})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/anchor/adopt")
async def anchor_adopt(persona_id: str = Form(...), name: str = Form(...),
                       type: str = Form("service"), value_prop: str = Form(""),
                       price_band: str = Form(""), path: str = Form(""),
                       evidence: str = Form(""), status: str = Form("validating")):
    atype = type if type in ANCHOR_TYPE_LABELS else "service"
    st = status if status in ANCHOR_STATUS_LABELS else "validating"
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_anchor (id,persona_id,name,type,value_prop,price_band,"
            "path,evidence,status,source) VALUES (?,?,?,?,?,?,?,?,?, 'interview')",
            (str(uuid.uuid4()), persona_id, name.strip(), atype, value_prop.strip(),
             price_band.strip(), path.strip(), evidence.strip(), st))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/anchor/{aid}/archive")
async def anchor_archive(aid: str):
    db = await get_db()
    try:
        await db.execute("UPDATE media_anchor SET status='archived' WHERE id=?", (aid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/anchor", status_code=302)
```

- [ ] **Step 3b: 模板 media_anchor.html**

新建 `app/templates/media_anchor.html`（按 status 分组，AI 起草块 + 手动表单，AJAX 仿受众页）：

```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}生意锚点{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} <b>生意锚点</b></span>
{% endblock %}
{% block content %}
<style>
  .anc-card{ border:1px solid var(--border); border-radius:8px; padding:12px 14px; margin-bottom:10px; }
  .anc-name{ font-size:14px; font-weight:600; color:var(--ink-1); }
  .anc-meta{ font-size:11.5px; color:var(--ink-3); margin-top:5px; display:flex; flex-wrap:wrap; gap:10px; }
</style>

{% if not persona %}
<div class="module" style="max-width:460px; margin:0 auto">
  <div class="mh"><span class="ttl">还没有人设</span></div>
  <div class="inner"><p style="font-size:13px; color:var(--ink-3); margin-bottom:14px">生意锚点挂在人设下，先去建一个人设。</p>
  <a href="/media/persona" class="btn primary">去建人设</a></div>
</div>
{% else %}

<div style="margin-bottom:18px">
  <div class="pname" style="margin-bottom:8px">生意锚点</div>
  <div style="display:flex; align-items:center; flex-wrap:wrap; gap:10px">
    <span style="font-size:13px; color:var(--ink-2)">{{ persona.name }} · 内容为什么值钱</span>
    <span class="pill run">{{ total }} 个</span>
    <span class="sp"></span>
    <a href="/media/audience" class="btn" style="padding:6px 12px; font-size:12.5px">{{ ic.icon('folder') }}受众画像</a>
    <a href="/media" class="btn" style="padding:6px 12px; font-size:12.5px">← 看板</a>
  </div>
</div>

<div class="module">
  <div class="mh"><span class="ttl">🧭 AI 帮我梳理变现</span></div>
  <div class="inner">
    <p style="font-size:12px; color:var(--ink-3); margin-bottom:8px">说说你靠什么变现、卖给谁、多少钱、怎么成交。</p>
    <textarea id="anc-answers" rows="3" class="input w-full" placeholder="我主要靠……收费……成交路径是……"></textarea>
    <button id="anc-draft-btn" class="btn ai" style="margin-top:8px" onclick="draftAnchor('{{ persona.id }}')">让 AI 提炼</button>
    <div id="anc-cands" style="margin-top:12px"></div>
  </div>
</div>

<div class="module" style="margin-top:12px">
  <div class="mh"><span class="ttl">锚点</span></div>
  <div class="inner">
    {% if not anchors_by_status %}<div class="empty">还没有生意锚点。上面让 AI 帮你梳理，或下面手动加。</div>{% endif %}
    {% for st in status_order %}
      {% if anchors_by_status.get(st) %}
      <div style="margin-bottom:14px">
        <div style="font-size:11.5px; font-weight:600; color:var(--ink-3); margin-bottom:6px">{{ status_labels[st] }} · {{ anchors_by_status[st]|length }}</div>
        {% for a in anchors_by_status[st] %}
        <div class="anc-card">
          <div style="display:flex; align-items:flex-start; gap:10px">
            <div style="flex:1"><span class="anc-name">{{ a.name }}</span>
              <span class="tag" style="margin-left:6px; color:var(--ink-3); background:var(--panel-2)">{{ type_labels.get(a.type, a.type) }}</span></div>
            <form method="post" action="/media/anchor/{{ a.id }}/archive" onsubmit="return confirm('归档这个锚点？')">
              <button class="iconbtn danger" title="归档">{{ ic.icon('trash') }}</button></form>
          </div>
          {% if a.value_prop %}<div style="font-size:12.5px; color:var(--ink-2); margin-top:4px">{{ a.value_prop }}</div>{% endif %}
          <div class="anc-meta">
            {% if a.price_band %}<span>价格带：{{ a.price_band }}</span>{% endif %}
            {% if a.path %}<span>路径：{{ a.path }}</span>{% endif %}
            {% if a.evidence %}<span>依据：{{ a.evidence }}</span>{% endif %}
            <span class="tag" style="color:var(--ink-3); background:var(--panel-2)">{{ a.source or 'manual' }}</span>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    {% endfor %}

    <details style="margin-top:10px">
      <summary style="font-size:13px; color:var(--accent); cursor:pointer">+ 手动加一个锚点</summary>
      <form method="post" action="/media/anchor" class="space-y-2" style="margin-top:10px">
        <input type="hidden" name="persona_id" value="{{ persona.id }}">
        <input name="name" required placeholder="锚点名（必填）" class="input w-full">
        <select name="type" class="input w-full" style="margin-top:8px">
          {% for code, label in type_labels.items() %}<option value="{{ code }}">{{ label }}</option>{% endfor %}
        </select>
        <input name="value_prop" placeholder="解决什么问题" class="input w-full" style="margin-top:8px">
        <input name="price_band" placeholder="价格带（可留空）" class="input w-full" style="margin-top:8px">
        <input name="path" placeholder="成交路径（可留空）" class="input w-full" style="margin-top:8px">
        <select name="status" class="input w-full" style="margin-top:8px">
          <option value="validating" selected>验证中</option><option value="proven">已跑通</option><option value="dropped">已放弃</option>
        </select>
        <button class="btn primary" style="margin-top:10px">保存</button>
      </form>
    </details>
  </div>
</div>

<script>
async function draftAnchor(pid){
  var btn = document.getElementById('anc-draft-btn');
  var box = document.getElementById('anc-cands');
  var ans = document.getElementById('anc-answers').value;
  btn.disabled = true; btn.textContent = '正在提炼…'; box.innerHTML = '';
  try{
    var fd = new URLSearchParams(); fd.set('answers', ans);
    var r = await fetch('/media/anchor/draft', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:fd});
    var d = await r.json();
    if(!d.ok){ box.innerHTML = '<div class="empty">'+(d.error||'提炼失败')+'</div>'; return; }
    if(!d.anchors || d.anchors.length===0){ box.innerHTML = '<div class="empty">没提炼出锚点，多说点。</div>'; return; }
    d.anchors.forEach(function(a){
      var card = document.createElement('div'); card.className = 'anc-card';
      card.innerHTML =
        '<div class="anc-name">'+escapeHtml(a.name)+' <span class="tag" style="color:var(--ink-3);background:var(--panel-2)">'+escapeHtml(a.type||'')+'</span></div>'+
        (a.value_prop?'<div style="font-size:12.5px;color:var(--ink-2);margin-top:4px">'+escapeHtml(a.value_prop)+'</div>':'')+
        (a.path?'<div style="font-size:11.5px;color:var(--ink-3);margin-top:4px">路径：'+escapeHtml(a.path)+'</div>':'')+
        '<div style="margin-top:8px;display:flex;gap:8px">'+
        '<button class="btn primary" style="padding:4px 12px">采纳</button>'+
        '<button class="btn ghost" style="padding:4px 12px">丢弃</button></div>';
      var btns = card.querySelectorAll('button');
      btns[0].onclick = async function(){
        btns[0].disabled = true; btns[0].textContent = '采纳中…';
        var f = new URLSearchParams();
        f.set('persona_id', pid); f.set('name', a.name); f.set('type', a.type||'service');
        f.set('value_prop', a.value_prop||''); f.set('price_band', a.price_band||'');
        f.set('path', a.path||''); f.set('evidence', a.evidence||''); f.set('status', 'validating');
        var rr = await fetch('/media/anchor/adopt', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:f});
        var dd = await rr.json();
        if(dd.ok){ card.innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">✓ 已入库（刷新页面看）</div>'; }
        else { btns[0].disabled=false; btns[0].textContent='采纳'; }
      };
      btns[1].onclick = function(){ card.remove(); };
      box.appendChild(card);
    });
  } catch(e){ box.innerHTML = '<div class="empty">出错了：'+e+'</div>'; }
  finally { btn.disabled = false; btn.textContent = '让 AI 提炼'; }
}
function escapeHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 3c: 入口**

`app/templates/media_board.html` 和 `media_persona.html`：在 Task 3 加的「受众」入口那行后，各加一行：

```html
    <a href="/media/anchor" class="btn" style="padding:6px 12px; font-size:12.5px">{{ ic.icon('folder') }}锚点</a>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider`
Expected: PASS（含新加锚点测试 + 全部原有）

- [ ] **Step 5: 提交**

```bash
git add app/api/media.py app/templates/media_anchor.html app/templates/media_board.html app/templates/media_persona.html tests/test_media_routes.py
git commit -m "feat(media): 生意锚点页——AI起草人拍板+手动录入+按状态分组"
```

---

### Task 5: 收尾 —— 全套回归 + 浏览器验证 + 部署 + 记忆

**Files:** 无代码改动（controller 执行）。

- [ ] **Step 1: 全套测试**

Run: `python -m pytest tests/ -q -p no:cacheprovider`
Expected: 全绿（164 基线 + Task1(3) + Task2(3) + Task3(4) + Task4(3) ≈ 177，以实际为准）。

- [ ] **Step 2: 真浏览器验 JS（controller 做，两页一起）**

`preview_start {name:"ai-pm"}` → 测试同款签名 cookie 登录 → 分别导航 `/media/audience`、`/media/anchor`：
1. `read_console_messages {onlyErrors:true}` 无错。
2. `javascript_tool`：`typeof draftAudience`、`typeof draftAnchor`、`typeof escapeHtml` 均为 `"function"`（验 `<script>` 没崩）。
3. stub `fetch` 返假候选驱动 draft→采纳，验候选渲染、escapeHtml 安全、adopt 带 source。
4. 两页入口按钮在看板/人设页出现。

- [ ] **Step 3: push 交给用户**

不自动 push 服务器。汇报：本地已 commit，按流程 `git push` → 服务器 `git pull && systemctl restart ai-pm`（零 migration，重启自动跑 SCHEMA 建新表）。真机验收：受众页/锚点页 AI 起草→采纳→看卡片。

- [ ] **Step 4: 更新记忆**

`C:\Users\62572\.claude\projects\D--GAGA-5-25\memory\project_aipm.md` 末尾追加本块完成记录（做了什么/代码落点/两层分工/未做Minor/接续触发词）。

---

## Self-Review

**Spec coverage：**
- spec §四 两表 → Task 1 ✅
- spec §5.1 两起草函数（红线/夹取/成本）→ Task 2 ✅
- spec §5.2 两组路由（各 5 个）→ Task 3（受众）+ Task 4（锚点）✅
- spec §5.3 两模板（卡片/AI起草/手动/`<script>`铁律）→ Task 3b + Task 4b ✅
- spec §5.4 入口 → Task 3c + Task 4c ✅
- spec §六 边界（无人设空态/空返回不装/类型兜底）→ 模板空态 + Task2 空回答测 + AI夹取 ✅
- spec §七 测试 1-8 → schema(Task1,3) + draft夹取(Task2,3) + adopt/manual/archive/page(Task3,4) ✅
- spec §二 锁死原则：不接写稿注入（富表只被查看页读写，全程不碰 write_script/build_script_context）/人拍板闸（adopt人工触发,draft不写库）/成本可见(draft函数log_injection)/诚实(SYSTEM红线) ✅
- spec §九 不做：锚点↔segment（表无 target_audience_ids 列）/富表接写稿（无）/自动爬评论（无）✅

**Placeholder scan：** 无 TBD/TODO；每个 code step 给完整代码。✅

**Type consistency：**
- `draft_audience_segments` 返回键 `ok/segments/error/cost/model`；`segments` 项键 segment/who/anxiety/desire/objection/language/pay_willingness/pay_scene/pay_ceiling/evidence/confidence —— Task2 定义、Task3 fake+路由+模板 JS 消费一致。
- `draft_anchors` 返回 `ok/anchors/...`；`anchors` 项 name/type/value_prop/price_band/path/evidence —— Task2 定义、Task4 消费一致。
- 路由名/URL：audience 5 个、anchor 5 个，Task3/4 定义与模板 fetch URL、测试 URL 全一致。
- `ANCHOR_TYPES`（media_ai, Task2）vs `ANCHOR_TYPE_LABELS`（media.py, Task4）—— 两处枚举值一致（product/service/带货/广告/引流私域）。⚠️ 实现时注意：media_ai 用 set 校验、media.py 用 dict 校验+展示，值必须对齐。
