# 人设框架地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭一个 AI 访谈引导流程，把当前账号人设一次性梳理成 AI-PM 里第一批 `media_persona_trait` 真资产。

**Architecture:** 访谈是"人设登记表"的冷启动入口，与 L1 复盘同表、同 dimension、同人拍板闸——零新表。7 个访谈模块映射到 8 个 dimension（新增 `anchor`）。阶段（定位时代）是一等公民：单人设 + `current_phase` + 每条 trait 的 `phase_tag`；注入只喂当前阶段 + 永久条目。AI 只负责问和提炼，人拍板才入库。

**Tech Stack:** Python FastAPI + aiosqlite + Jinja2 + vanilla JS + finesse 设计系统。测试无 pytest-asyncio，异步用 `asyncio.run`；服务测试用内存 aiosqlite + 打桩 `ask_ai`；路由测试用 `TestClient` + 伪造签名 session + 临时 DB。

## Global Constraints

- 设计 spec：`docs/superpowers/specs/2026-08-08-media-phase2-persona-framework-design.md`（每个 Task 都服从它）。
- 走法 C：ip-strategist 是设计蓝图，判断标准翻译进提示词，**不做运行时依赖**（不读外部 skill 文件）。
- AI 提炼、**人拍板**才生效（candidate → 入库），`persona_interview_extract` **绝不写库**。
- 诚实红线：AI 不替用户编造人设，缺料标空。
- 改模板用 Edit/Write，**禁 PowerShell `-replace`**（毁中文）；模板用 finesse 设计系统（`.module`/`.btn`/CSS 变量），非 Tailwind。
- 测试隔离到临时/内存 DB，**绝不写真实 `data/aipm.db`**（历史教训：真库被锁 → 连接泄漏死循环）。
- 跑测试用 `Start-Process` 重定向到文件，不用 `python -m pytest | Select-Object`（管道缓冲会被误判挂起）。
- `dimension`/`phase_tag`/`status`/`evidence`/`confidence`/`source` 全部是 `media_persona_trait` 已有列，本计划**不改表结构**。

---

### Task 1: `anchor` 维度 + 人设模块常量与纯函数

新增 `anchor` 维度，并在 `media_flow.py`（纯函数区）定义 7 模块的骨架、模块→维度映射、默认阶段策略、注入判定、换阶段归档判定、模块完成度判定。

**Files:**
- Modify: `app/api/media.py:25-33`（`TRAIT_DIMENSIONS` 加 `anchor`）
- Modify: `app/services/media_ai.py:610`（AI 输出枚举加 `anchor`）
- Modify: `app/services/media_flow.py`（文件末尾追加常量与纯函数）
- Test: `tests/test_media_flow.py`（追加）

**Interfaces:**
- Produces:
  - `PERSONA_MODULES: dict[str, dict]` — key=模块标识，value=`{"label": str, "dims": list[str], "phase_bound": bool}`
  - `PERSONA_MODULE_ORDER: list[str]` — 7 模块顺序
  - `module_dims(module: str) -> list[str]`
  - `default_phase_tag(module: str, current_phase: str) -> str`（phase_bound→current_phase，否则 `""`）
  - `is_injectable(trait: dict, current_phase: str) -> bool`
  - `archive_targets(traits: list[dict], old_phase: str) -> list[str]`（返回要归档的 trait id）
  - `completed_modules(active_dims: list[str]) -> set[str]`

- [ ] **Step 1: 写失败测试（纯函数）**

追加到 `tests/test_media_flow.py`：

```python
from app.services.media_flow import (
    PERSONA_MODULES, PERSONA_MODULE_ORDER,
    module_dims, default_phase_tag, is_injectable,
    archive_targets, completed_modules,
)


def test_seven_modules_in_order():
    assert PERSONA_MODULE_ORDER == [
        "positioning", "audience", "topics",
        "tone", "signature", "taboo", "anchor",
    ]
    assert set(PERSONA_MODULES) == set(PERSONA_MODULE_ORDER)


def test_positioning_module_covers_two_dims_and_is_phase_bound():
    m = PERSONA_MODULES["positioning"]
    assert m["dims"] == ["positioning", "differentiator"]
    assert m["phase_bound"] is True


def test_permanent_modules_are_not_phase_bound():
    for key in ("tone", "signature", "taboo"):
        assert PERSONA_MODULES[key]["phase_bound"] is False


def test_module_dims_returns_dims():
    assert module_dims("anchor") == ["anchor"]
    assert module_dims("nonsense") == []


def test_default_phase_tag_phase_bound_vs_permanent():
    assert default_phase_tag("positioning", "AI落地期") == "AI落地期"
    assert default_phase_tag("tone", "AI落地期") == ""       # 永久
    assert default_phase_tag("anchor", "AI落地期") == "AI落地期"


def test_is_injectable_current_phase_and_permanent_pass():
    cur = "AI落地期"
    assert is_injectable({"status": "active", "phase_tag": "AI落地期"}, cur) is True
    assert is_injectable({"status": "active", "phase_tag": ""}, cur) is True     # 永久
    assert is_injectable({"phase_tag": ""}, cur) is True                          # status 默认 active


def test_is_injectable_other_phase_and_archived_fail():
    cur = "AI落地期"
    assert is_injectable({"status": "active", "phase_tag": "旧带货期"}, cur) is False
    assert is_injectable({"status": "archived", "phase_tag": "AI落地期"}, cur) is False


def test_archive_targets_only_hits_old_phase_actives():
    traits = [
        {"id": "t1", "status": "active", "phase_tag": "旧带货期"},   # 命中
        {"id": "t2", "status": "active", "phase_tag": ""},           # 永久，不动
        {"id": "t3", "status": "active", "phase_tag": "AI落地期"},   # 别的阶段，不动
        {"id": "t4", "status": "archived", "phase_tag": "旧带货期"}, # 已归档，不动
    ]
    assert archive_targets(traits, "旧带货期") == ["t1"]
    assert archive_targets(traits, "") == []                         # 空阶段名不误伤永久条目


def test_completed_modules_maps_dims_back_to_modules():
    assert completed_modules(["positioning"]) == {"positioning"}
    assert completed_modules(["differentiator"]) == {"positioning"}  # 同属定位模块
    assert completed_modules(["tone", "anchor"]) == {"tone", "anchor"}
    assert completed_modules([]) == set()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_flow.py -k "module or injectable or archive or phase_tag" -v`
Expected: FAIL（`ImportError: cannot import name 'PERSONA_MODULES'`）

- [ ] **Step 3: 在 `media_flow.py` 末尾追加实现**

```python
# ─────────────── 二期 · 人设框架地基 ───────────────
# 7 个访谈模块 → 8 个 dimension。phase_bound=True 的模块打当前阶段标签，
# False 的（声音/记忆点/红线）是跨阶段永久条目，phase_tag 留空、换阶段不归档。
PERSONA_MODULES = {
    "positioning": {"label": "你是谁·定位", "dims": ["positioning", "differentiator"], "phase_bound": True},
    "audience":    {"label": "说给谁听·受众", "dims": ["audience"], "phase_bound": True},
    "topics":      {"label": "讲什么·选题域", "dims": ["topics"], "phase_bound": True},
    "tone":        {"label": "怎么说·声音", "dims": ["tone"], "phase_bound": False},
    "signature":   {"label": "招牌·记忆点", "dims": ["signature"], "phase_bound": False},
    "taboo":       {"label": "红线·禁忌", "dims": ["taboo"], "phase_bound": False},
    "anchor":      {"label": "生意锚点", "dims": ["anchor"], "phase_bound": True},
}
PERSONA_MODULE_ORDER = [
    "positioning", "audience", "topics", "tone", "signature", "taboo", "anchor",
]


def module_dims(module: str) -> list[str]:
    """模块允许写入的维度。未知模块返回空列表。"""
    m = PERSONA_MODULES.get(module)
    return list(m["dims"]) if m else []


def default_phase_tag(module: str, current_phase: str) -> str:
    """模块提炼出的条目默认打什么阶段标签。永久模块返回空串。"""
    m = PERSONA_MODULES.get(module)
    if m and m["phase_bound"]:
        return current_phase or ""
    return ""


def is_injectable(trait: dict, current_phase: str) -> bool:
    """写稿注入时这条 trait 该不该喂：active 且（永久 或 属当前阶段）。"""
    if (trait.get("status") or "active") != "active":
        return False
    ptag = trait.get("phase_tag") or ""
    return ptag == "" or ptag == current_phase


def archive_targets(traits: list[dict], old_phase: str) -> list[str]:
    """换阶段时要归档的 trait id：仅 active 且 phase_tag==old_phase 的。
    永久条目 phase_tag 为空，old_phase 非空时永不命中 —— 天然不误伤。"""
    if not old_phase:
        return []
    return [t["id"] for t in traits
            if (t.get("status") or "active") == "active"
            and (t.get("phase_tag") or "") == old_phase]


def completed_modules(active_dims: list[str]) -> set[str]:
    """哪些模块已至少采纳过一条 active 条目（详情页 N/7 进度）。"""
    dims = set(active_dims)
    return {mod for mod, m in PERSONA_MODULES.items()
            if dims & set(m["dims"])}
```

- [ ] **Step 4: 改 `TRAIT_DIMENSIONS`（`app/api/media.py:25-33`）**

在 dict 末尾 `"differentiator": "差异化",` 后加一行：

```python
    "anchor": "生意锚点",
```

- [ ] **Step 5: 改 AI 输出枚举（`app/services/media_ai.py` 约 610 行）**

把 `review_content` 系统提示词里 `proposed_traits` 的枚举串加上 `anchor`：

```python
    {"dimension":"positioning|audience|tone|topics|taboo|signature|differentiator|anchor",
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_flow.py -v`
Expected: PASS（全部，含既有用例）

- [ ] **Step 7: Commit**

```bash
git add app/services/media_flow.py app/api/media.py app/services/media_ai.py tests/test_media_flow.py
git commit -m "feat(media): 人设框架模块常量+纯函数, 新增 anchor 维度"
```

---

### Task 2: 写稿注入按当前阶段过滤

`build_script_context` 现在只按 `status=='active'` 过滤，加入阶段过滤：只注入当前阶段专属 + 永久条目。

**Files:**
- Modify: `app/services/media_context.py:56-81`（`build_script_context`）+ 顶部 import
- Test: `tests/test_media_context.py`（追加）

**Interfaces:**
- Consumes: `is_injectable(trait, current_phase)`（Task 1）
- Produces: `build_script_context(persona, traits)` 行为变更 —— 别的阶段条目不再进 prompt

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_context.py`：

```python
from app.services.media_context import build_script_context


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_context.py -k current_phase -v`
Expected: FAIL（`教你月入十万` 仍出现在 text 中，assert 失败）

- [ ] **Step 3: 改实现**

`app/services/media_context.py` 顶部 import 区加：

```python
from app.services.media_flow import is_injectable
```

把 `build_script_context` 里这行：

```python
    active = [t for t in traits if (t.get("status") or "active") == "active"]
```

改成：

```python
    phase = persona.get("current_phase", "")
    active = [t for t in traits if is_injectable(t, phase)]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_context.py -v`
Expected: PASS（含既有 build_script_context 用例）

- [ ] **Step 5: 回归**

Run: `python -m pytest tests/test_media_pipeline.py tests/test_media_flow.py -v`
Expected: PASS（确认注入改动没打破 🅐 写稿链路）

- [ ] **Step 6: Commit**

```bash
git add app/services/media_context.py tests/test_media_context.py
git commit -m "feat(media): 写稿注入按当前阶段过滤(当前阶段+永久条目)"
```

---

### Task 3: AI 能力 `persona_interview_questions`

按模块生成 5–8 个引导问题；只读不写库。

**Files:**
- Modify: `app/services/media_ai.py`（import 区 + 新系统提示词 + 新函数）
- Test: `tests/test_media_pipeline.py`（追加）

**Interfaces:**
- Consumes: `PERSONA_MODULES`, `PERSONA_MODULE_GUIDE`（本任务定义）
- Produces: `async persona_interview_questions(db, persona_id, module, model="auto") -> dict`
  返回 `{"ok": bool, "questions": list[str], "error": str, "cost": float, "model": str}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_pipeline.py`（文件已 import `asyncio`、`make_db`、`fake_ai`；若无则一并加 `from tests.media_helpers import make_db, fake_ai`）：

```python
from app.services import media_ai


async def _seed_persona(db, pid="P1", phase="AI落地期"):
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "务实落地AI", phase))
    await db.commit()


def test_persona_interview_questions_returns_list(monkeypatch):
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai",
                            fake_ai('{"questions":["你帮谁？","你跟同类不同在哪？"]}'))
        res = await media_ai.persona_interview_questions(db, "P1", "positioning")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["ok"] is True
    assert res["questions"] == ["你帮谁？", "你跟同类不同在哪？"]


def test_persona_interview_questions_unknown_module(monkeypatch):
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai('{"questions":[]}'))
        res = await media_ai.persona_interview_questions(db, "P1", "nonsense")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["ok"] is False
    assert "模块" in res["error"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -k persona_interview_questions -v`
Expected: FAIL（`AttributeError: module 'app.services.media_ai' has no attribute 'persona_interview_questions'`）

- [ ] **Step 3: 实现**

`media_ai.py` import 区（约 12-17 行的 import 块附近）加：

```python
from app.services.media_flow import (
    PERSONA_MODULES, module_dims, default_phase_tag,
)
```

在 `SCRIPT_SYSTEM` 之后、`write_script` 之前插入系统提示词 + 模块方法论 + 函数：

```python
# ─────────────── 二期 · 人设访谈（冷启动播种人设登记表）───────────────
# 把 ip-strategist 的判断标准翻译进提示词（走法C，不运行时读外部skill）。
PERSONA_MODULE_GUIDE = {
    "positioning": "一句话定位（帮谁解决什么）、跟同类账号最大的不同、现在处在什么阶段。",
    "audience": "目标人群是谁、他们最痛的问题分几层。提醒创作者：画像只是待验证假设，别当既定事实。",
    "topics": "能持续讲的内容主场、哪些话题方向是你的、哪些方向坚决不碰。",
    "tone": "人称视角、是自嘲还是端着、平视还是高高在上、有没有口头禅、真实说话的腔调。",
    "signature": "标志性观点/口号/固定桥段。少而硬，是别人记住你的钩子，不要贪多。",
    "taboo": "内容红线：不编造本人经历、警惕AI味/卖课味/焦虑营销、身份错位禁忌。",
    "anchor": "这个号最终为什么做、怎么变现、生意目标是什么。",
}

PERSONA_INTERVIEW_SYSTEM = """你是资深 IP 人设访谈者。就给定的人设模块，向创作者提出精准的引导问题，
帮他把脑子里的东西挖出来、说清楚。

铁律：
1. 问题要具体、可回答，避免"你的定位是什么"这种大而空的问法。
2. 一次提 5-8 个问题，围绕本模块的挖掘目标，别跑题到别的模块。
3. 只输出 JSON：{"questions":["...","..."]}，不要解释。"""


async def persona_interview_questions(db, persona_id: str, module: str,
                                      model: str = "auto") -> dict:
    """就某个人设模块生成 5-8 个引导问题。只读不写库。"""
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "人设不存在", "questions": [],
                "cost": 0, "model": ""}
    if module not in PERSONA_MODULES:
        return {"ok": False, "error": "未知模块", "questions": [],
                "cost": 0, "model": ""}
    persona = dict(row)
    mod = PERSONA_MODULES[module]
    parts = [
        f"【人设】{persona['name']}｜{persona.get('one_liner', '')}"
        f"｜当前阶段：{persona.get('current_phase', '')}",
        f"【本模块】{mod['label']}",
        f"【挖掘目标】{PERSONA_MODULE_GUIDE.get(module, '')}",
        "请就本模块向创作者提出引导问题。",
    ]
    result = await ask_ai("\n\n".join(parts), model=model,
                          task_type="media_persona_interview",
                          system_prompt=PERSONA_INTERVIEW_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "questions": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    questions = [_txt(q) for q in (obj.get("questions") or []) if _txt(q)]
    return {"ok": True, "questions": questions, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -k persona_interview_questions -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media): persona_interview_questions 按模块出引导问题"
```

---

### Task 4: AI 能力 `persona_interview_extract`（返回候选，绝不写库）

把创作者一次性回答提炼成 candidate 人设条目返回前端待拍板。**不写库**（人拍板闸）。

**Files:**
- Modify: `app/services/media_ai.py`（新系统提示词 + 新函数）
- Test: `tests/test_media_pipeline.py`（追加）

**Interfaces:**
- Consumes: `module_dims`, `default_phase_tag`（Task 1）
- Produces: `async persona_interview_extract(db, persona_id, module, answers, model="auto") -> dict`
  返回 `{"ok": bool, "traits": list[dict], "error": str, "cost": float, "model": str}`
  每个 trait dict：`{"dimension","content","brief","evidence","confidence","phase_tag"}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_pipeline.py`：

```python
def test_persona_interview_extract_returns_candidates_and_does_not_write(monkeypatch):
    payload = ('{"traits":[{"dimension":"positioning","content":"帮中小企业务实落地AI",'
               '"brief":"帮中小企业落地AI","evidence":"我自己就是做这个的","confidence":4}]}')
    async def go():
        db = await make_db()
        await _seed_persona(db, phase="AI落地期")
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai(payload))
        res = await media_ai.persona_interview_extract(
            db, "P1", "positioning", "我帮中小企业落地AI，自己就是做这个的")
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait")
        n = (await cur.fetchone())["c"]
        await db.close()
        return res, n
    res, n = asyncio.run(go())
    assert res["ok"] is True
    assert n == 0                                   # 绝不写库
    t = res["traits"][0]
    assert t["dimension"] == "positioning"
    assert t["phase_tag"] == "AI落地期"             # phase_bound 模块打当前阶段
    assert t["confidence"] == 4


def test_persona_interview_extract_permanent_module_empty_phase(monkeypatch):
    payload = '{"traits":[{"dimension":"taboo","content":"不编造本人经历","confidence":5}]}'
    async def go():
        db = await make_db()
        await _seed_persona(db, phase="AI落地期")
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai(payload))
        res = await media_ai.persona_interview_extract(db, "P1", "taboo", "绝不编造经历")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["traits"][0]["phase_tag"] == ""      # 永久模块 phase_tag 留空


def test_persona_interview_extract_empty_answers(monkeypatch):
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai('{"traits":[]}'))
        res = await media_ai.persona_interview_extract(db, "P1", "positioning", "   ")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["ok"] is False
    assert "空" in res["error"]


def test_persona_interview_extract_clamps_dimension_to_module(monkeypatch):
    # AI 乱给一个不属于本模块的维度，应被夹回本模块首维
    payload = '{"traits":[{"dimension":"taboo","content":"帮中小企业","confidence":3}]}'
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai(payload))
        res = await media_ai.persona_interview_extract(db, "P1", "positioning", "答案")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["traits"][0]["dimension"] == "positioning"   # 夹回 module_dims 首维
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -k persona_interview_extract -v`
Expected: FAIL（`AttributeError: ... has no attribute 'persona_interview_extract'`）

- [ ] **Step 3: 实现**

在 `persona_interview_questions` 之后插入：

```python
PERSONA_EXTRACT_SYSTEM = """你把创作者对某个人设模块的回答，提炼成结构化人设条目。

铁律：
1. 只提炼回答里真实说过的，绝不替他编造或脑补人设。回答里没有就少提甚至不提。
2. 每条给：dimension（限本模块允许的维度）、content（完整表述）、
   brief（≤30字精简版）、evidence（引用他的原话）、confidence（1-5，他说得越笃定越高）。
3. 只输出 JSON：{"traits":[{...}]}，不要解释。"""


async def persona_interview_extract(db, persona_id: str, module: str,
                                    answers: str, model: str = "auto") -> dict:
    """把创作者的一次性回答提炼成 candidate 人设条目。绝不写库 —— 人拍板才入库。"""
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "人设不存在", "traits": [], "cost": 0, "model": ""}
    if module not in PERSONA_MODULES:
        return {"ok": False, "error": "未知模块", "traits": [], "cost": 0, "model": ""}
    if not (answers or "").strip():
        return {"ok": False, "error": "回答是空的", "traits": [], "cost": 0, "model": ""}
    persona = dict(row)
    dims = module_dims(module)
    phase_tag = default_phase_tag(module, persona.get("current_phase", ""))

    parts = [
        f"【本模块】{PERSONA_MODULES[module]['label']}",
        f"【允许的维度】{'/'.join(dims)}",
        f"【创作者的回答】\n{answers[:8000]}",
        "请把回答提炼成结构化人设条目。",
    ]
    result = await ask_ai("\n\n".join(parts), model=model,
                          task_type="media_persona_extract",
                          system_prompt=PERSONA_EXTRACT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "traits": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("traits") or []) if isinstance(it, dict)]
    traits = []
    for it in raw:
        content = _txt(it.get("content"))
        if not content:
            continue
        dim = it.get("dimension") if it.get("dimension") in dims else dims[0]
        conf = it.get("confidence")
        conf = conf if isinstance(conf, int) and 1 <= conf <= 5 else 3
        traits.append({
            "dimension": dim,
            "content": content,
            "brief": (_txt(it.get("brief")) or content)[:30],
            "evidence": _txt(it.get("evidence")),
            "confidence": conf,
            "phase_tag": phase_tag,
        })
    return {"ok": True, "traits": traits, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -k persona_interview_extract -v`
Expected: PASS（4 个用例）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media): persona_interview_extract 提炼候选人设条目(不写库,人拍板)"
```

---

### Task 5: 访谈 AJAX 路由（出题 + 提炼）

**Files:**
- Modify: `app/api/media.py`（import + 2 个路由）
- Test: `tests/test_media_routes.py`（追加）

**Interfaces:**
- Consumes: `persona_interview_questions`, `persona_interview_extract`（Task 3/4）
- Produces:
  - `POST /media/persona/{pid}/interview/{module}/questions` → JSON（透传 questions 结果）
  - `POST /media/persona/{pid}/interview/{module}/extract`（form `answers`）→ JSON（透传 traits 结果）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_routes.py`（复用文件顶部 `_client`、`_db_ready`、`_seed_real`）。先加一个建人设的 helper：

```python
def _seed_persona_real(pid="RTP2", phase="AI落地期"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona_trait WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "务实落地AI", phase))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_persona_interview_questions_route(monkeypatch):
    async def fake(db, persona_id, module, model="auto"):
        return {"ok": True, "questions": ["Q1", "Q2"], "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr("app.api.media.persona_interview_questions", fake)
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/positioning/questions")
    assert r.status_code == 200
    assert r.json()["questions"] == ["Q1", "Q2"]


def test_persona_interview_extract_route(monkeypatch):
    async def fake(db, persona_id, module, answers, model="auto"):
        return {"ok": True, "traits": [{"dimension": "positioning", "content": "帮中小企业",
                "brief": "帮中小企业", "evidence": "原话", "confidence": 4,
                "phase_tag": "AI落地期"}], "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr("app.api.media.persona_interview_extract", fake)
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/positioning/extract",
                       data={"answers": "我帮中小企业落地AI"})
    assert r.status_code == 200
    assert r.json()["traits"][0]["phase_tag"] == "AI落地期"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -k "persona_interview_questions_route or persona_interview_extract_route" -v`
Expected: FAIL（404 —— 路由不存在）

- [ ] **Step 3: 实现**

确认 `app/api/media.py` 顶部已 import `JSONResponse`（若无则在 `from fastapi.responses import ...` 加上）、并 import 新 AI 函数：

```python
from app.services.media_ai import (
    persona_interview_questions, persona_interview_extract,
)
```

在 `trait_archive` 路由（约 153 行）之后插入：

```python
@router.post("/media/persona/{pid}/interview/{module}/questions")
async def persona_interview_q(pid: str, module: str):
    """出题：AJAX，透传 AI 结果给前端。"""
    db = await get_db()
    try:
        res = await persona_interview_questions(db, pid, module)
    finally:
        await db.close()
    return JSONResponse(res)


@router.post("/media/persona/{pid}/interview/{module}/extract")
async def persona_interview_ex(pid: str, module: str, answers: str = Form(...)):
    """提炼候选条目：AJAX，返回 traits 待前端逐条拍板。不写库。"""
    db = await get_db()
    try:
        res = await persona_interview_extract(db, pid, module, answers)
    finally:
        await db.close()
    return JSONResponse(res)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -k "persona_interview_questions_route or persona_interview_extract_route" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/media.py tests/test_media_routes.py
git commit -m "feat(media): 人设访谈 AJAX 路由(出题+提炼)"
```

---

### Task 6: 拍板入库 + 换阶段归档路由

**Files:**
- Modify: `app/api/media.py`（import + 2 个路由）
- Test: `tests/test_media_routes.py`（追加）

**Interfaces:**
- Consumes: 无（直接 SQL；换阶段逻辑等价于 `archive_targets`）
- Produces:
  - `POST /media/persona/{pid}/interview/adopt`（form `dimension,content,brief,confidence,evidence,phase_tag`）→ JSON，插入 `source='interview'`
  - `POST /media/persona/{pid}/new-phase`（form `new_phase`）→ 302，归档旧阶段 active 条目并改 `current_phase`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_routes.py`：

```python
def _count_traits(pid, **where):
    async def go():
        db = await get_db()
        try:
            sql = "SELECT * FROM media_persona_trait WHERE persona_id=?"
            args = [pid]
            for k, v in where.items():
                sql += f" AND {k}=?"
                args.append(v)
            cur = await db.execute(sql, args)
            rows = [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
        return rows
    return asyncio.run(go())


def test_persona_interview_adopt_writes_interview_source():
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "positioning", "content": "帮中小企业务实落地AI",
        "brief": "帮中小企业落地AI", "confidence": "4",
        "evidence": "我自己就是做这个的", "phase_tag": "AI落地期"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = _count_traits("RTP2", dimension="positioning")
    assert len(rows) == 1
    assert rows[0]["source"] == "interview"
    assert rows[0]["phase_tag"] == "AI落地期"
    assert rows[0]["status"] == "active"


def test_new_phase_archives_old_phase_actives_keeps_permanent():
    _seed_persona_real(phase="旧带货期")
    c = _client()
    # 一条旧阶段定位（会归档）+ 一条永久红线（phase_tag 空，保留）
    c.post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "positioning", "content": "教你月入十万",
        "brief": "月入十万", "confidence": "3", "evidence": "", "phase_tag": "旧带货期"})
    c.post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "taboo", "content": "不编造本人经历",
        "brief": "不编造", "confidence": "5", "evidence": "", "phase_tag": ""})
    r = c.post("/media/persona/RTP2/new-phase", data={"new_phase": "AI落地期"},
               follow_redirects=False)
    assert r.status_code == 302
    actives = _count_traits("RTP2", status="active")
    archived = _count_traits("RTP2", status="archived")
    assert {a["dimension"] for a in actives} == {"taboo"}       # 永久红线还在
    assert {a["dimension"] for a in archived} == {"positioning"}  # 旧阶段定位归档
    cur_rows = _count_traits("RTP2")
    # current_phase 已更新
    async def phase():
        db = await get_db()
        try:
            row = await (await db.execute(
                "SELECT current_phase FROM media_persona WHERE id='RTP2'")).fetchone()
        finally:
            await db.close()
        return row["current_phase"]
    assert asyncio.run(phase()) == "AI落地期"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -k "adopt_writes or new_phase_archives" -v`
Expected: FAIL（404）

- [ ] **Step 3: 实现**

在 Task 5 的两个路由之后插入（`uuid` 已在 media.py import）：

```python
@router.post("/media/persona/{pid}/interview/adopt")
async def persona_interview_adopt(pid: str, dimension: str = Form(...),
                                  content: str = Form(...), brief: str = Form(""),
                                  confidence: int = Form(3), evidence: str = Form(""),
                                  phase_tag: str = Form("")):
    """人拍板：把一条候选条目写进注册表，source='interview'。"""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?,?,?,?, 'interview', ?,?,?)",
            (str(uuid.uuid4()), pid, dimension, content.strip(),
             brief.strip()[:30], evidence.strip(), confidence, phase_tag.strip()))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/persona/{pid}/new-phase")
async def persona_new_phase(pid: str, new_phase: str = Form(...)):
    """换阶段：归档旧阶段的 active 条目（永久条目 phase_tag 空、不受影响），更新当前阶段。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        old = row["current_phase"] if row else ""
        if old:
            await db.execute(
                "UPDATE media_persona_trait SET status='archived' "
                "WHERE persona_id=? AND status='active' AND phase_tag=?", (pid, old))
        await db.execute(
            "UPDATE media_persona SET current_phase=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (new_phase.strip(), pid))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -k "adopt_writes or new_phase_archives" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/media.py tests/test_media_routes.py
git commit -m "feat(media): 人设候选拍板入库(source=interview) + 换阶段归档路由"
```

---

### Task 7: 访谈页模板 + 详情页进度

访谈页：7 模块卡（已完成 ✓ / 待做），点模块 → 出题 → 答题 textarea → 提炼 → 候选条目逐条采纳/丢弃。含"换阶段"控件。详情页头部显示"已完成 N/7"。

**Files:**
- Create: `templates/media_persona_interview.html`
- Modify: `app/api/media.py`（新增访谈页 GET 路由 + `persona_detail` 传进度）
- Modify: `templates/media_persona.html`（头部加"访谈梳理"入口 + N/7 进度徽标）
- Test: `tests/test_media_routes.py`（追加页面可达性用例）

**Interfaces:**
- Consumes: `PERSONA_MODULES`, `PERSONA_MODULE_ORDER`, `completed_modules`（Task 1）
- Produces: `GET /media/persona/{pid}/interview` → HTML 访谈页

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_routes.py`：

```python
def test_persona_interview_page_renders_seven_modules():
    _seed_persona_real()
    r = _client().get("/media/persona/RTP2/interview")
    assert r.status_code == 200
    assert "你是谁·定位" in r.text
    assert "生意锚点" in r.text        # 第 7 模块 anchor 在页面上
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -k persona_interview_page -v`
Expected: FAIL（404）

- [ ] **Step 3: 加访谈页路由 + 详情页进度**

`app/api/media.py` import 区加：

```python
from app.services.media_flow import (
    PERSONA_MODULES, PERSONA_MODULE_ORDER, completed_modules,
)
```

在 `persona_new_phase` 之后加 GET 路由：

```python
@router.get("/media/persona/{pid}/interview", response_class=HTMLResponse)
async def persona_interview_page(request: Request, pid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        persona = dict(row) if row else None
        cur = await db.execute(
            "SELECT dimension FROM media_persona_trait "
            "WHERE persona_id=? AND status='active'", (pid,))
        active_dims = [r["dimension"] for r in await cur.fetchall()]
    finally:
        await db.close()
    done = completed_modules(active_dims)
    return _tpl(request, "media_persona_interview.html",
                {"persona": persona, "modules": PERSONA_MODULES,
                 "module_order": PERSONA_MODULE_ORDER, "done": done})
```

在 `persona_detail`（约 106-115 行）计算 `traits_by_dim` 之后、`return` 之前加：

```python
    done_modules = completed_modules([t["dimension"] for t in traits])
```

并把 `return _tpl(...)` 的 context 里加上 `"done_count": len(done_modules), "module_total": len(PERSONA_MODULE_ORDER)`。

- [ ] **Step 4: 建访谈页模板**

Create `templates/media_persona_interview.html`（finesse 设计系统；vanilla JS fetch）：

```html
{% extends "base.html" %}
{% block content %}
<div class="module">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <h1 class="page-title">人设访谈 · {{ persona.name if persona else '' }}</h1>
    <a class="btn" href="/media/persona/{{ persona.id }}">← 回人设档案</a>
  </div>
  <p class="hint">一个模块一坐，AI 出题→你一次答完→AI 提炼→你逐条拍板。可随时中断续做。</p>

  <div class="card" style="margin:12px 0;">
    <strong>当前阶段：</strong>{{ persona.current_phase }}
    <form method="post" action="/media/persona/{{ persona.id }}/new-phase"
          style="display:inline-flex;gap:6px;margin-left:12px;"
          onsubmit="return confirm('换阶段会把当前阶段的定位/受众/选题/锚点类条目归档（永久条目保留）。确定？');">
      <input name="new_phase" placeholder="新阶段名（如 AI落地期）" required>
      <button class="btn btn-warn" type="submit">换阶段</button>
    </form>
  </div>

  <div id="modules">
    {% for key in module_order %}
    {% set m = modules[key] %}
    <div class="card module-card" data-module="{{ key }}" style="margin:10px 0;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <strong>{{ loop.index }}. {{ m.label }}
          {% if key in done %}<span style="color:var(--ok,green)">✓ 已梳理</span>{% endif %}
        </strong>
        <button class="btn btn-ai" onclick="ask('{{ key }}')">AI 出题</button>
      </div>
      <div class="qa" style="display:none;margin-top:10px;">
        <ol class="questions"></ol>
        <textarea class="answers" rows="6" style="width:100%"
                  placeholder="把上面的问题一次性答完，不知道的可以跳过"></textarea>
        <button class="btn btn-ai" onclick="extract('{{ key }}')"
                style="margin-top:6px;">提炼成人设条目</button>
        <div class="candidates" style="margin-top:10px;"></div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<script>
const PID = "{{ persona.id }}";
function card(mod){ return document.querySelector('.module-card[data-module="'+mod+'"]'); }

async function ask(mod){
  const c = card(mod);
  const r = await fetch(`/media/persona/${PID}/interview/${mod}/questions`, {method:'POST'});
  const d = await r.json();
  if(!d.ok){ alert(d.error||'出题失败'); return; }
  const ol = c.querySelector('.questions'); ol.innerHTML='';
  (d.questions||[]).forEach(q=>{ const li=document.createElement('li'); li.textContent=q; ol.appendChild(li); });
  c.querySelector('.qa').style.display='block';
}

async function extract(mod){
  const c = card(mod);
  const answers = c.querySelector('.answers').value.trim();
  if(!answers){ alert('先答几句再提炼'); return; }
  const fd = new FormData(); fd.append('answers', answers);
  const r = await fetch(`/media/persona/${PID}/interview/${mod}/extract`, {method:'POST', body:fd});
  const d = await r.json();
  if(!d.ok){ alert(d.error||'提炼失败'); return; }
  const box = c.querySelector('.candidates'); box.innerHTML='';
  if(!(d.traits||[]).length){ box.innerHTML='<p class="hint">AI 没从回答里提炼出条目（可能答得太少）。</p>'; return; }
  d.traits.forEach(t=>box.appendChild(candidate(t)));
}

function candidate(t){
  const div = document.createElement('div');
  div.className='card'; div.style.margin='6px 0';
  div.innerHTML = `<div>[<b>${t.dimension}</b>] <input class="c-content" value="" style="width:70%"> 置信 ${t.confidence}</div>
    <div class="hint">原话：${t.evidence||'—'}｜阶段：${t.phase_tag||'永久'}</div>
    <button class="btn btn-ai c-adopt">采纳</button>
    <button class="btn c-drop">丢弃</button>`;
  div.querySelector('.c-content').value = t.content;
  div.querySelector('.c-drop').onclick = ()=>div.remove();
  div.querySelector('.c-adopt').onclick = async ()=>{
    const fd = new FormData();
    fd.append('dimension', t.dimension);
    fd.append('content', div.querySelector('.c-content').value);
    fd.append('brief', t.brief||'');
    fd.append('confidence', t.confidence);
    fd.append('evidence', t.evidence||'');
    fd.append('phase_tag', t.phase_tag||'');
    const r = await fetch(`/media/persona/${PID}/interview/adopt`, {method:'POST', body:fd});
    if((await r.json()).ok){ div.style.opacity=.4; div.querySelector('.c-adopt').textContent='已采纳 ✓'; div.querySelector('.c-adopt').disabled=true; }
  };
  return div;
}
</script>
{% endblock %}
```

> 注：`base.html` 的 finesse 设计系统若无 `--ok` 变量，`✓ 已梳理` 用 `color:green` 兜底（模板已写 `var(--ok,green)`）。若 `btn-warn`/`btn-ai` class 名与本项目实际不符，改用项目现有按钮 class（先 grep `templates/media_persona.html` 确认现有按钮 class 再套）。

- [ ] **Step 5: 详情页加入口 + 进度徽标**

在 `templates/media_persona.html` 头部（人设名附近）加（用 Edit，找到标题区域插入）：

```html
<a class="btn btn-ai" href="/media/persona/{{ persona.id }}/interview">🧭 访谈梳理人设（{{ done_count }}/{{ module_total }}）</a>
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -k persona_interview_page -v`
Expected: PASS

- [ ] **Step 7: 全量回归**

Run（PowerShell，重定向到文件避管道误判）：

```
Start-Process -FilePath python -ArgumentList '-m','pytest','-q' -NoNewWindow -Wait -RedirectStandardOutput pytest_out.txt -RedirectStandardError pytest_err.txt; Get-Content pytest_out.txt -Tail 20
```

Expected: 全绿（既有 123 + 本计划新增用例）

- [ ] **Step 8: 手动验证访谈页（可选，需 AI key）**

`python run.py` → 登录 → `/media/persona/<pid>/interview` → 对"①定位"点"AI 出题"→ 答题 → 提炼 → 采纳一条 → 回详情页看条目在 positioning 下、`source=interview`、进度 +1。

- [ ] **Step 9: Commit**

```bash
git add templates/media_persona_interview.html templates/media_persona.html app/api/media.py tests/test_media_routes.py
git commit -m "feat(media): 人设访谈页模板 + 详情页 N/7 进度徽标"
```

---

## Self-Review

**Spec coverage：**
- §二 同表/同闸/同维度 → Task 6 adopt 写 `media_persona_trait`（source=interview）、Task 1 复用 dimension ✓
- §三 7 模块 → 8 维度（anchor）→ Task 1（常量 + TRAIT_DIMENSIONS + AI 枚举）✓
- §四 阶段一等公民 + 永久 vs 阶段性 + 换阶段归档 → Task 1（default_phase_tag/archive_targets）+ Task 6（new-phase）✓
- §4.1 注入 = 当前阶段 + 永久 → Task 2（is_injectable + build_script_context）✓
- §五 访谈跑法（出题→答→提炼→拍板）→ Task 3/4/5/6 ✓
- §5.1 断点续做 N/7 → Task 1 completed_modules + Task 7 进度 ✓
- §5.1 创作器≠审稿器（人是审稿器）→ Task 4 extract 不写库 + Task 6 人拍板入库 ✓
- §5.2 红线不开保底槽 → 不动 build_script_context 的 signature 独占逻辑（Task 2 只加阶段过滤）✓
- §六 代码改动清单 → 全部覆盖 ✓
- §九 验收 1-6 → 分散在各 Task 测试 + Task 7 Step 8 手动验收 ✓

**Placeholder scan：** 无 TBD/TODO；每个 code step 都是完整代码。Task 7 Step 4/5 有两处"先 grep 确认现有 class 名再套"——这是对既有模板 finesse class 的合理核对，非占位（默认值已给）。

**Type consistency：**
- `persona_interview_questions(db, persona_id, module, model)` / `persona_interview_extract(db, persona_id, module, answers, model)` 在 Task 3/4 定义，Task 5 路由按此签名调用 ✓
- extract 返回 trait dict 键 `dimension/content/brief/evidence/confidence/phase_tag`（Task 4）与 Task 5/6 前端 FormData 字段、adopt 路由 Form 参数一致 ✓
- `is_injectable`/`default_phase_tag`/`module_dims`/`completed_modules`/`archive_targets` 在 Task 1 定义，Task 2/4/6/7 消费，签名一致 ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-08-media-phase2-persona-framework.md`.
