# 功能B「AI 学用户改稿」实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 对比"AI 草稿 vs 用户定稿"提炼用户反复出现的改稿习惯，作为语气/记忆点候选条目让人拍板入库，下次写脚本自动注入。

**Architecture:** 复用现有人设条目体系（`media_persona_trait` 的 tone/signature 维度）+ 人拍板闸（复用 `/interview/adopt`，加 `source` 参数）+ 现有注入预算。新增一个 AI 能力 `learn_edit_style`（读最近 N 条定稿的 ai_draft/script 对，返回候选，绝不写库）、一个路由、人设页一块 UI。**零 migration、零新注入逻辑、不动 finalize。**

**Tech Stack:** Python FastAPI + aiosqlite + Jinja2 + vanilla JS（无构建）。测试用 pytest + TestClient + 伪造签名 cookie + monkeypatch 打桩 AI。

## Global Constraints

- **零数据库迁移**：不加列不加表。风格特征落 `media_persona_trait` 现成的 tone/signature 维度。
- **人拍板闸**：AI 只返回候选，绝不写库；采纳走人工点击。
- **诚实不编造**：AI 只许基于给定真实改动归纳，每条候选 evidence 引用真实改动例子。
- **AI 成本可见**：`learn_edit_style` 调用后必须 `log_injection`。
- **向后兼容**：`adopt` 加的 `source` 参数默认 `"interview"`，不破坏现有访谈流程。
- **改模板禁 PowerShell -replace**（毁中文），一律 Edit/Write。
- **TemplateResponse 三参数**；Jinja 无 tojson（用 json.dumps + \| safe）；模板 dict 键别用 items/keys/values/get。
- **模板 JS 必须真浏览器验**（TestClient 测不出 JS 语法崩）；SVG 图标别塞 JS 单引号字符串。
- **跑测试**：`python -m pytest tests/test_media_routes.py -q -p no:cacheprovider`（别用 PowerShell 管道 Select-Object，会假挂）。
- 现有全套基线 **157 passed**，每个 Task 完成后应仍全绿。

---

### Task 1: `learn_edit_style` AI 能力（读定稿对 → 候选风格条目）

**Files:**
- Modify: `app/services/media_ai.py`（加常量 `LEARN_EDIT_MAX_PAIRS`、系统提示 `LEARN_EDIT_SYSTEM`、函数 `learn_edit_style`；紧跟在 `persona_interview_extract` 之后，约 line 447 后）
- Test: `tests/test_media_ai_learn_edit.py`（新建）

**Interfaces:**
- Consumes（现有，签名已核对）：
  - `ask_ai(prompt, model, task_type, system_prompt, json_mode) -> dict`（返回含 `response`/`cost`/`model`/`tokens`）
  - `extract_json(resp, expect="object") -> dict`
  - `log_injection(db, content_id: str, ai_type: str, ids: list, tokens: int)`
  - `_txt(value) -> str`、`_clamp(value, default) -> int`（media_ai.py 内已有）
- Produces（后续 Task 依赖）：
  - `LEARN_EDIT_MAX_PAIRS = 15`（int 常量）
  - `async def learn_edit_style(db, persona_id: str, model: str = "auto") -> dict`
    返回 `{"ok": bool, "traits": [{"dimension","content","brief","evidence","confidence","phase_tag"}], "pair_count": int, "error": str, "cost": float, "model": str}`；`dimension ∈ {"tone","signature"}`；`phase_tag` 恒为 `""`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_ai_learn_edit.py`：

```python
"""功能B learn_edit_style 单测：AI 打桩，验取数/夹取/兜底/成本记账。"""
import asyncio
import json

import app.services.media_ai as mai
from tests.media_helpers import make_db   # 内存 DB 已应用 SCHEMA + MIGRATIONS（含 ai_draft 等列）


async def _seed(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,current_phase,status) "
        "VALUES (?,?,?, 'active')", (pid, "嘉姐", "AI落地期"))
    # 3 条定稿：ai_draft 与 script 不同（有改动）；1 条 script==ai_draft（无改动，应排除）；
    # 1 条 authoring_stage!=finalized（应排除）
    rows = [
        ("C1", pid, "finalized", "首先我们要明确一个概念，那就是落地。", "落地。就这两个字。"),
        ("C2", pid, "finalized", "其次呢，我认为这个方案是可行的。", "这方案能成。"),
        ("C3", pid, "finalized", "总而言之，效果非常好。", "说白了，真香。"),
        ("C4", pid, "finalized", "一样的内容不该被学。", "一样的内容不该被学。"),
        ("C5", pid, "drafting",  "没定稿的稿。", "没定稿的改。"),
    ]
    for cid, p, stage, draft, script in rows:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,authoring_stage,"
            "ai_draft,script,finalized_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (cid, p, cid, stage, draft, script))
    await db.commit()


def test_learn_edit_style_only_reads_finalized_changed_pairs(monkeypatch):
    captured = {}

    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        captured["prompt"] = prompt
        return {"response": json.dumps({"traits": [
            {"dimension": "tone", "content": "开头不铺垫，直接抛结论",
             "brief": "开头直接抛结论", "evidence": "把'首先我们要明确'删成'落地。'",
             "confidence": 4}]}),
            "cost": 0.001, "model": "x", "tokens": 100}

    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.learn_edit_style(db, "P1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    assert res["pair_count"] == 3            # C1/C2/C3，排除 C4(无改动) C5(未定稿)
    assert "首先我们要明确" in captured["prompt"]   # 喂进了真实草稿
    assert "落地。就这两个字。" in captured["prompt"]  # 喂进了真实定稿
    assert res["traits"][0]["dimension"] == "tone"
    assert res["traits"][0]["phase_tag"] == ""       # tone 永久，phase_tag 空
    assert res["traits"][0]["confidence"] == 4


def test_learn_edit_style_clamps_dimension_and_confidence(monkeypatch):
    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps({"traits": [
            {"dimension": "positioning", "content": "越界维度归 tone",
             "brief": "b", "evidence": "e", "confidence": 99},
            {"dimension": "signature", "content": "招牌口头禅'说白了'",
             "brief": "说白了", "evidence": "多条都加了说白了", "confidence": "x"}]}),
            "cost": 0, "model": "x", "tokens": 10}

    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.learn_edit_style(db, "P1")
        await db.close()
        return res

    res = asyncio.run(go())
    dims = [t["dimension"] for t in res["traits"]]
    assert dims == ["tone", "signature"]             # positioning 越界→夹成 tone
    assert res["traits"][0]["confidence"] == 3       # 99 非法→默认 3
    assert res["traits"][1]["confidence"] == 3       # "x" 非法→默认 3


def test_learn_edit_style_empty_when_no_pairs(monkeypatch):
    async def fake_ask(*a, **k):
        raise AssertionError("没有可学的定稿时不该调 AI")

    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await db.execute(
            "INSERT INTO media_persona (id,name,current_phase,status) "
            "VALUES ('P1','嘉姐','AI落地期','active')")
        await db.commit()
        res = await mai.learn_edit_style(db, "P1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True and res["pair_count"] == 0 and res["traits"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_ai_learn_edit.py -q -p no:cacheprovider`
Expected: FAIL（`AttributeError: module 'app.services.media_ai' has no attribute 'learn_edit_style'`）

- [ ] **Step 3: 写最小实现**

在 `app/services/media_ai.py` 的 `persona_interview_extract` 函数之后（约 line 447 后、`write_script` 之前）插入：

```python
LEARN_EDIT_MAX_PAIRS = 15  # 一次最多看多少条定稿对，防 token 撑爆（拍脑袋值，实测调）

LEARN_EDIT_SYSTEM = """你对比"AI 写的草稿"和"创作者真人改后的定稿"，提炼创作者反复出现的改稿习惯，作为其"语气/记忆点"人设条目。

铁律：
1. 只归纳给定改动里真实反复出现的模式，绝不编造创作者没做过的改动。看不出稳定规律就少提甚至不提（返回空）。
2. 已经给你列出的"现有条目"里已有的，别重复提。
3. 每条给：dimension（只能是 tone 或 signature；改口气/句式/节奏归 tone，招牌口头禅/固定收尾归 signature）、content（完整表述这条习惯）、brief（≤30字精简版，注入用）、evidence（引用一个真实的"草稿→定稿"改动例子）、confidence（1-5，出现越多次越高）。
4. 只输出 JSON：{"traits":[{...}]}，不要解释。"""


async def learn_edit_style(db, persona_id: str, model: str = "auto") -> dict:
    """对比最近定稿的 AI 草稿 vs 用户定稿，提炼反复出现的改稿习惯为候选风格条目。
    绝不写库 —— 返回候选，人拍板 adopt 才入。功能B / spec §6.1。"""
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    if not await cur.fetchone():
        return {"ok": False, "error": "人设不存在", "traits": [],
                "pair_count": 0, "cost": 0, "model": ""}

    cur = await db.execute(
        "SELECT title, ai_draft, script FROM media_content "
        "WHERE persona_id=? AND authoring_stage='finalized' "
        "AND ai_draft != '' AND script != '' AND script != ai_draft "
        "ORDER BY finalized_at DESC LIMIT ?",
        (persona_id, LEARN_EDIT_MAX_PAIRS))
    pairs = [dict(r) for r in await cur.fetchall()]
    if not pairs:
        return {"ok": True, "error": "", "traits": [], "pair_count": 0,
                "cost": 0, "model": ""}

    # 现有 tone/signature 条目，喂给 AI 让它别重复提
    cur = await db.execute(
        "SELECT brief, content FROM media_persona_trait "
        "WHERE persona_id=? AND status='active' AND dimension IN ('tone','signature')",
        (persona_id,))
    existing = [(_txt(r["brief"]) or _txt(r["content"])) for r in await cur.fetchall()]

    pair_blocks = []
    for i, p in enumerate(pairs, 1):
        pair_blocks.append(
            f"[改动 {i}]\nAI 草稿：{_txt(p['ai_draft'])[:1200]}\n"
            f"我的定稿：{_txt(p['script'])[:1200]}")

    parts = [
        "【已有的 tone/signature 条目（别重复提）】\n"
        + ("\n".join(f"- {e}" for e in existing) if existing else "（暂无）"),
        "【草稿 vs 定稿 对比】\n\n" + "\n\n".join(pair_blocks),
        "请提炼反复出现的改稿习惯为结构化条目。",
    ]
    result = await ask_ai("\n\n".join(parts), model=model,
                          task_type="media_learn_edit",
                          system_prompt=LEARN_EDIT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "traits": [],
                "pair_count": len(pairs),
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("traits") or []) if isinstance(it, dict)]
    traits = []
    for it in raw:
        content = _txt(it.get("content"))
        if not content:
            continue
        dim = it.get("dimension") if it.get("dimension") in ("tone", "signature") else "tone"
        conf = it.get("confidence")
        conf = conf if isinstance(conf, int) and 1 <= conf <= 5 else 3
        traits.append({
            "dimension": dim,
            "content": content,
            "brief": (_txt(it.get("brief")) or content)[:30],
            "evidence": _txt(it.get("evidence")),
            "confidence": conf,
            "phase_tag": "",   # tone/signature 永久，不绑阶段
        })
    await log_injection(db, "", "media_learn_edit", [], result.get("tokens", 0))
    return {"ok": True, "traits": traits, "error": "", "pair_count": len(pairs),
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

> 注：`confidence` 用 `isinstance(int)` 判断（沿用 `persona_interview_extract` 的写法，与该 Minor 保持一致），不引入 `_clamp` 以免与现有风格分叉。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_ai_learn_edit.py -q -p no:cacheprovider`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_ai_learn_edit.py
git commit -m "feat(media): learn_edit_style——对比草稿vs定稿提炼改稿习惯候选"
```

---

### Task 2: `adopt` 加 source 参数 + `/learn-edits` 路由

**Files:**
- Modify: `app/api/media.py`（`persona_interview_adopt` 加 `source` 参数并写进 SQL，约 line 196-213；新增 `persona_learn_edits` 路由；顶部 import 加 `learn_edit_style`）
- Test: `tests/test_media_routes.py`（追加 3 个测试，放文件末尾）

**Interfaces:**
- Consumes：`learn_edit_style`（Task 1）
- Produces：
  - `POST /media/persona/{pid}/interview/adopt` 现在接受可选 `source: str = Form("interview")`
  - `POST /media/persona/{pid}/learn-edits` → `JSONResponse`（learn_edit_style 的返回）

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_routes.py` 末尾追加（复用文件已有的 `_client`、`_seed_persona_real`、`_count_traits`）：

```python
# ─────────────── 功能B：AI 学改稿 ───────────────

def test_adopt_accepts_learned_edit_source():
    """adopt 加 source 参数：功能B 传 learned_edit，写进 trait.source。"""
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "tone", "content": "开头不铺垫直接抛结论",
        "brief": "开头直接抛结论", "confidence": "4",
        "evidence": "把'首先我们要明确'删成'落地。'", "phase_tag": "",
        "source": "learned_edit"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = _count_traits("RTP2", dimension="tone")
    assert len(rows) == 1
    assert rows[0]["source"] == "learned_edit"


def test_adopt_source_defaults_to_interview():
    """不传 source 时仍写 interview —— 保护现有访谈流程向后兼容。"""
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "signature", "content": "招牌收尾'说白了'",
        "brief": "说白了", "confidence": "5", "evidence": "", "phase_tag": ""})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = _count_traits("RTP2", dimension="signature")
    assert rows[0]["source"] == "interview"


def test_learn_edits_route_returns_candidates(monkeypatch):
    async def fake(db, persona_id, model="auto"):
        return {"ok": True, "traits": [{"dimension": "tone",
                "content": "长句拆短", "brief": "长句拆短", "evidence": "例子",
                "confidence": 4, "phase_tag": ""}],
                "pair_count": 3, "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr("app.api.media.learn_edit_style", fake)
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/learn-edits")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["pair_count"] == 3
    assert body["traits"][0]["dimension"] == "tone"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k "learned_edit or source_defaults or learn_edits_route"`
Expected: FAIL（source 未写入 / `/learn-edits` 404 或 import 错）

- [ ] **Step 3: 写实现**

3a. `app/api/media.py` 顶部 import（约 line 13-18 的 `from app.services.media_ai import (...)` 块）追加 `learn_edit_style`：

```python
from app.services.media_ai import (
    recommend_topics, write_script, generate_platform_copy, review_content,
    interview_questions, extract_evidence, propose_angles,
    critique_draft, revise_draft,
    persona_interview_questions, persona_interview_extract,
    learn_edit_style,
)
```

3b. 改 `persona_interview_adopt`（约 line 196-213）——加 `source` 参数、SQL 里用绑定值替换硬编码的 `'interview'`：

```python
@router.post("/media/persona/{pid}/interview/adopt")
async def persona_interview_adopt(pid: str, dimension: str = Form(...),
                                  content: str = Form(...), brief: str = Form(""),
                                  confidence: int = Form(3), evidence: str = Form(""),
                                  phase_tag: str = Form(""),
                                  source: str = Form("interview")):
    """人拍板：把一条候选条目写进注册表。source 区分来源（interview / learned_edit）。"""
    src = source if source in ("interview", "learned_edit") else "interview"
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), pid, dimension, content.strip(),
             brief.strip()[:30], src, evidence.strip(), confidence, phase_tag.strip()))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})
```

3c. 新增 `/learn-edits` 路由——放在 `persona_interview_adopt` 之后：

```python
@router.post("/media/persona/{pid}/learn-edits")
async def persona_learn_edits(pid: str):
    """功能B：AI 复盘最近定稿的改稿习惯，返回候选（绝不写库，人拍板 adopt 才入）。"""
    db = await get_db()
    try:
        try:
            result = await learn_edit_style(db, pid)
        except Exception as e:
            log.exception("学改稿提炼失败")
            return JSONResponse({"ok": False, "error": str(e),
                                 "traits": [], "pair_count": 0})
    finally:
        await db.close()
    return JSONResponse(result)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider`
Expected: PASS（含新加 3 个 + 原有全部）

- [ ] **Step 5: 提交**

```bash
git add app/api/media.py tests/test_media_routes.py
git commit -m "feat(media): /learn-edits 路由 + adopt 加 source 参数(向后兼容)"
```

---

### Task 3: 人设页「🪞 AI 学我改稿」块 + `learnable_count`

**Files:**
- Modify: `app/api/media.py`（`persona_detail` GET，约 line 85-121，查 `learnable_count` 塞进上下文）
- Modify: `app/templates/media_persona.html`（右栏或条目区下方加一块 UI + AJAX JS）
- Test: `tests/test_media_routes.py`（追加 1 个渲染测试）

**Interfaces:**
- Consumes：`/media/persona/{pid}/learn-edits`（Task 2）、`/media/persona/{pid}/interview/adopt`（带 source）
- Produces：人设页可见的学改稿入口（无下游代码依赖）

- [ ] **Step 1: 写失败测试**

在 `tests/test_media_routes.py` 末尾追加：

```python
def test_persona_page_shows_learn_edit_block_with_count():
    """人设页渲染学改稿块，显示可学定稿数。"""
    _seed_persona_real()

    async def seed_finalized():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_content WHERE persona_id='RTP2'")
            # 2 条有改动的定稿 + 1 条无改动（不计入）
            await db.execute(
                "INSERT INTO media_content (id,persona_id,title,authoring_stage,"
                "ai_draft,script,finalized_at) VALUES "
                "('LC1','RTP2','t1','finalized','AI草稿一','定稿一',CURRENT_TIMESTAMP),"
                "('LC2','RTP2','t2','finalized','AI草稿二','定稿二',CURRENT_TIMESTAMP),"
                "('LC3','RTP2','t3','finalized','一样的','一样的',CURRENT_TIMESTAMP)")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_finalized())

    r = _client().get("/media/persona/RTP2")
    assert r.status_code == 200
    assert "AI 学我改稿" in r.text
    assert "2 条" in r.text          # learnable_count=2（LC3 无改动排除）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k learn_edit_block`
Expected: FAIL（`"AI 学我改稿" not in r.text`）

- [ ] **Step 3a: `persona_detail` 加 learnable_count**

`app/api/media.py` 的 `persona_detail`（约 line 85-121），在 `finally: await db.close()` 之前、`accounts` 查询之后，加一条 COUNT 查询；并把 `learnable_count` 加进 `_tpl(...)` 的 context dict。

在 accounts 查询后加：

```python
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM media_content WHERE persona_id=? "
            "AND authoring_stage='finalized' AND ai_draft != '' "
            "AND script != '' AND script != ai_draft", (pid,))
        learnable_count = (await cur.fetchone())["n"]
```

在返回的 context dict 里加一项（与 `done_count`/`module_total` 并列）：

```python
                 "done_count": len(done_modules), "module_total": len(PERSONA_MODULE_ORDER),
                 "learnable_count": learnable_count})
```

> 注意：空人设分支 `persona_home` 里的 `_tpl(... "media_persona.html" ...)`（约 line 64-67）不传 `learnable_count`，模板里用 `learnable_count|default(0)` 兜底，见 Step 3b。

- [ ] **Step 3b: 模板加 UI 块**

`app/templates/media_persona.html`：在右栏（`<div>` 含"平台账号" module 的那个外层 `<div>`，约 line 126-156）里、平台账号 module 之后加一个新 module。找到平台账号 module 的结束 `</div>`（`{% endif %}` 前那个闭合右栏 div 之前），插入：

```html
    <div class="module" style="margin-top:12px">
      <div class="mh"><span class="ttl" style="font-size:14px">🪞 AI 学我改稿</span></div>
      <div class="inner">
        <p style="font-size:12px; color:var(--ink-3); margin-bottom:10px">
          对比 AI 草稿和你改后的定稿，提炼你反复出现的改稿习惯，攒进语气/记忆点。
        </p>
        {% set lc = learnable_count|default(0) %}
        <div style="font-size:12.5px; color:var(--ink-2); margin-bottom:8px">
          已有 <b>{{ lc }} 条</b>定稿可供学习{% if lc < 3 %}<span style="color:var(--ink-3)">（再攒几条更准）</span>{% endif %}
        </div>
        <button id="learn-btn" class="btn ai" style="width:100%; justify-content:center"
                onclick="learnEdits('{{ persona.id }}')"{% if lc == 0 %} disabled{% endif %}>
          让 AI 复盘
        </button>
        <div id="learn-cands" style="margin-top:12px"></div>
      </div>
    </div>
```

- [ ] **Step 3c: 模板加 AJAX JS**

在 `media_persona.html` 的 `{% endblock %}` 之前加一个 `<script>` 块（**别在字符串里放 SVG/多行图标**）：

```html
<script>
async function learnEdits(pid){
  const btn = document.getElementById('learn-btn');
  const box = document.getElementById('learn-cands');
  btn.disabled = true; btn.textContent = '正在复盘…';
  box.innerHTML = '';
  try{
    const r = await fetch(`/media/persona/${pid}/learn-edits`, {method:'POST'});
    const d = await r.json();
    if(!d.ok){ box.innerHTML = '<div class="empty">复盘失败：'+(d.error||'')+'</div>'; return; }
    if(!d.traits || d.traits.length === 0){
      box.innerHTML = '<div class="empty">这批改稿没提炼出稳定习惯，再攒几条。</div>'; return;
    }
    d.traits.forEach(function(t){
      const dimLabel = t.dimension === 'signature' ? '记忆点' : '语气';
      const card = document.createElement('div');
      card.className = 'trait-row';
      card.style.flexDirection = 'column';
      card.innerHTML =
        '<div style="font-size:13px;color:var(--ink-1)"><span class="tag">'+dimLabel+'</span> '+
        escapeHtml(t.content)+'</div>'+
        (t.evidence ? '<div class="evidence">例子：'+escapeHtml(t.evidence)+'</div>' : '')+
        '<div style="margin-top:6px;display:flex;gap:8px">'+
        '<button class="btn primary" style="padding:4px 12px">采纳</button>'+
        '<button class="btn ghost" style="padding:4px 12px">丢弃</button></div>';
      const adoptBtn = card.querySelectorAll('button')[0];
      const dropBtn = card.querySelectorAll('button')[1];
      adoptBtn.onclick = async function(){
        adoptBtn.disabled = true; adoptBtn.textContent = '采纳中…';
        const fd = new URLSearchParams();
        fd.set('dimension', t.dimension); fd.set('content', t.content);
        fd.set('brief', t.brief || t.content); fd.set('confidence', t.confidence || 3);
        fd.set('evidence', t.evidence || ''); fd.set('phase_tag', '');
        fd.set('source', 'learned_edit');
        const rr = await fetch(`/media/persona/${pid}/interview/adopt`,
          {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:fd});
        const dd = await rr.json();
        if(dd.ok){ card.innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">✓ 已入库（'+dimLabel+'）</div>'; }
        else { adoptBtn.disabled = false; adoptBtn.textContent = '采纳'; }
      };
      dropBtn.onclick = function(){ card.remove(); };
      box.appendChild(card);
    });
  } catch(e){
    box.innerHTML = '<div class="empty">出错了：'+e+'</div>';
  } finally {
    btn.disabled = false; btn.textContent = '让 AI 复盘';
  }
}
function escapeHtml(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q -p no:cacheprovider -k learn_edit_block`
Expected: PASS

- [ ] **Step 5: 真浏览器验 JS（必做，TestClient 测不出 JS 崩）**

启动 `preview_start {name:"ai-pm"}`，用测试同款签名 cookie 登录（不输密码），导航到 `/media/persona/<真实pid>`：
1. `read_console_messages {onlyErrors:true}` → 无错。
2. `javascript_tool`：`typeof learnEdits` 应为 `"function"`、`typeof escapeHtml` 应为 `"function"`（验 `<script>` 没整体崩）。
3. 页面能看到「🪞 AI 学我改稿」块和「让 AI 复盘」按钮。

- [ ] **Step 6: 全套回归 + 提交**

Run: `python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS（161 passed：基线 157 + Task1 的 3 + Task2 的 3 + Task3 的 1 = 164… 以实际为准，全绿即可）

```bash
git add app/api/media.py app/templates/media_persona.html tests/test_media_routes.py
git commit -m "feat(media): 人设页AI学改稿块——复盘出候选人拍板入语气/记忆点"
```

---

### Task 4: 收尾——服务器部署提示 + 记忆更新

**Files:** 无代码改动，仅收尾。

- [ ] **Step 1: 全套测试最终确认**

Run: `python -m pytest tests/ -q -p no:cacheprovider`
Expected: 全绿。

- [ ] **Step 2: push 交给用户**

不自动 push 到服务器。汇报给用户：本地已 commit，按其习惯 `git push` → 服务器 `git pull && systemctl restart ai-pm`（零 migration，重启即生效）。真机验收：定几条稿→人设页点「让 AI 复盘」→看候选像不像自己→采纳后写新稿看是否更像。

- [ ] **Step 3: 更新记忆**

在 `C:\Users\62572\.claude\projects\D--GAGA-5-25\memory\project_aipm.md` 末尾追加功能B完成记录（做了什么/代码落点/未做Minor/接续触发词），并同步 MEMORY.md 若需要。

---

## Self-Review

**Spec coverage：**
- spec §五 数据流 → Task 1（learn_edit_style）+ Task 2（路由/adopt）+ Task 3（UI）✅
- spec §6.1 learn_edit_style（取数/注入现有条目/红线/夹取/成本）→ Task 1 ✅
- spec §6.2 路由（/learn-edits + adopt source + persona_detail learnable_count）→ Task 2 + Task 3 Step 3a ✅
- spec §6.3 UI（module/按钮/候选/阈值提示/JS铁律）→ Task 3 ✅
- spec §七 边界（0条禁用/空返回不装/类型兜底/不加learned列）→ Task1 empty测试 + Task3 lc==0 disabled + JS空态 ✅
- spec §八 测试 1-6 → Task1(3) + Task2(3) + Task3(1) 覆盖取数/结构/source/兼容/渲染；注入仍认（测试6）由现有 build_script_context 不区分 source 天然满足，已在 spec §八注明，未单列 Task（现有注入逻辑对 source 无感，加测试价值低）✅
- spec §九 代码落点 → 与 Task files 一致 ✅
- spec §三 原则：人拍板（Task2 adopt 人工触发）/成本可见（Task1 log_injection）/诚实（LEARN_EDIT_SYSTEM 红线）/不撑爆（复用注入预算，未改）/不覆盖script（只读 ai_draft）✅

**Placeholder scan：** 无 TBD/TODO；每个 code step 给了完整代码。✅

**Type consistency：** `learn_edit_style(db, persona_id, model="auto")` 返回键 `ok/traits/pair_count/error/cost/model` 在 Task1 定义、Task2 fake 与路由、Task3 JS 消费一致；`traits` 项键 `dimension/content/brief/evidence/confidence/phase_tag` 全程一致；adopt 的 `source` 参数 Task2 定义、Task3 JS `fd.set('source','learned_edit')` 消费一致。✅
