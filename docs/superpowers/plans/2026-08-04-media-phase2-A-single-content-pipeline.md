# 自媒体二期 🅐 单条口播生产线升级 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把一期"一次性 AI 出稿"升级成"证据包 → 角度 → 草稿(自评缺料) → 独立审稿 → 人定稿"的单条口播生产线，默认两步不累人、按需加码、创作与审稿分离、定稿存真。

**Architecture:** 沿用一期 media 独立模块。数据层加 3 张过程表(evidence/angle/draft_review)+ 原料库雏形表(material)+ media_content 若干新列(走 MIGRATIONS)。逻辑层：纯函数(状态机/注入拼装/换脑策略)进 media_flow.py & media_context.py；AI 能力(采访/角度/草稿升级/审稿/修订)进 media_ai.py，全部独立 `ask_ai` 调用。路由层 media.py 加过程端点，详情页模板加"创作区"。

**Tech Stack:** Python FastAPI + aiosqlite(SQLite) + Jinja2 + 本地裁剪版 Tailwind + vanilla JS。AI 走现有 `ai_router.ask_ai`。测试：pytest（无 pytest-asyncio，异步用 `asyncio.run`）。

## Global Constraints

以下为全项目硬约束，每个 Task 的要求都隐含包含本节，值照抄自 spec §11：

- **前端**：不引入新框架，vanilla JS + 本地裁剪版 Tailwind。响应式用 `@media (max-width:767px)` 自定义 CSS，**不用 Tailwind `md:` 断点**（本地裁剪版不含）。
- **颜色**：blue-600 主操作 / violet-600 AI 专属 / amber-600 警告；用新色阶前先在本地 `static/tailwind.min.css` grep 确认存在。
- **模板改动一律用 Edit/Write 工具，禁用 PowerShell `-replace`**（会把中文写成乱码）。
- **Jinja2 无 `tojson` 过滤器**：需要 JSON 时用 `json.dumps(...)` 预序列化 + `| safe`。
- **`TemplateResponse` 必须三参数** `(request, "name.html", ctx)`；模块内统一走 `_tpl(request, name, ctx)`。
- **模板 dict 键别用** `items/keys/values/get`（会被 Jinja2 当方法解析）。
- **AI 全走 `ask_ai`**（三模型路由 + fallback + 费用记账 + `MAX_PROMPT_CHARS` 保护）。
- **DeepSeek 模型名坑**：现用 `deepseek-v4-flash`，禁止写回废弃的 `deepseek-chat`。
- **AI 输出类型不可信**：所有从 AI JSON 取的字段走 `_txt()`（文本）/`_clamp()`（1-5 分），已在 media_ai.py。
- **数据真实性红线**：AI 缺真料时标缺口或采访，**绝不编造本人经历/数字**。
- **新表进 `app/database.py` 的 `SCHEMA`**；给已有表加列进 `MIGRATIONS` 列表（`ALTER TABLE`）。
- **DB 连接模式**：路由内 `db = await get_db()` + `try/finally: await db.close()`。
- **提交粒度**：每个 Task 独立可测、独立提交。提交信息尾部加
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

**测试基建约定（Task 1 建立，后续复用）：** `tests/media_helpers.py` 提供 `make_db()`（内存 aiosqlite，已应用 SCHEMA+MIGRATIONS）与 `fake_ai(response, tokens=10, cost=0.0)`（返回可 await 的假 `ask_ai`）。异步测试用 `asyncio.run(...)`。AI 函数测试用 `monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai(...))`。

**当前分支：** `feature/media-phase2`（已存在）。本计划全部提交到该分支。

---

## 文件结构（改动地图）

| 文件 | 责任 | 动作 |
|---|---|---|
| `app/database.py` | 表结构 + 迁移 | Modify：SCHEMA 加 4 表；MIGRATIONS 加 media_content 列 |
| `app/services/media_flow.py` | 纯状态机常量 | Modify：加 authoring 常量 + `finalize_updates()` |
| `app/services/media_context.py` | 注入拼装（纯） | Modify：加 evidence/angle/material 渲染 + `select_materials()` |
| `app/services/media_ai.py` | AI 能力 | Modify：加 换脑策略纯函数 + 采访/角度/审稿/修订；升级 `write_script` |
| `app/api/media.py` | 路由 | Modify：加过程端点 + 详情页装载新数据 + 换脑策略设置端点 |
| `templates/media_content.html` | 内容详情页 | Modify：加"创作区"（草稿/角度/审稿/素材包） |
| `templates/media_settings.html`(或设置页) | 换脑策略下拉 | Modify：加 `media_review_strategy` 选择 |
| `tests/media_helpers.py` | 测试基建 | Create |
| `tests/test_media_schema.py` | 表/列断言 | Modify：加新表新列断言 |
| `tests/test_media_flow.py` | 状态机 | Modify：加 authoring 断言 |
| `tests/test_media_context.py` | 注入拼装 | Modify：加渲染/select_materials 断言 |
| `tests/test_media_pipeline.py` | AI 能力 + 策略 | Create |

---

## Task 1: 数据层 — 4 张新表 + media_content 新列 + 测试基建

**Files:**
- Modify: `app/database.py`（SCHEMA 末尾 `"""` 前加 4 张表；MIGRATIONS 列表末尾加列）
- Create: `tests/media_helpers.py`
- Test: `tests/test_media_schema.py`

**Interfaces:**
- Produces：表 `media_evidence` / `media_angle` / `media_draft_review` / `media_material`；`media_content` 新列 `authoring_stage, brief, evidence_gap, selected_angle_id, ai_draft, revision_count, finalized_at`。
- Produces：`tests/media_helpers.py::make_db()`（async，返回已建表的内存连接）、`tests/media_helpers.py::fake_ai(response, tokens=10, cost=0.0)`。

- [ ] **Step 1: 写失败测试（新表 + 新列断言）**

在 `tests/test_media_schema.py` 顶部 `EXPECTED` 集合加入新表，并追加测试。注意：`media_content` 新列走 MIGRATIONS，`_cols()` 只跑 SCHEMA 拿不到，需新增一个应用迁移的 helper。

```python
# 追加到 tests/test_media_schema.py

from app.database import MIGRATIONS


def _cols_migrated(table):
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            con.execute(sql)
        except Exception:
            pass
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_new_pipeline_tables_exist():
    assert {"media_evidence", "media_angle",
            "media_draft_review", "media_material"} <= _tables()


def test_evidence_columns():
    cols = _cols("media_evidence")
    assert {"content_id", "persona_id", "item", "item_type", "source",
            "from_material_id", "promoted_to_material_id"} <= cols


def test_angle_columns():
    cols = _cols("media_angle")
    assert {"content_id", "angle", "rationale", "is_selected", "status"} <= cols


def test_draft_review_columns():
    cols = _cols("media_draft_review")
    assert {"content_id", "reviewed_draft", "reviewer_strategy", "reviewer_model",
            "fact_flags", "persona_flags", "platform_flags", "gap_flags",
            "risk_flags", "score", "verdict", "notes"} <= cols


def test_material_columns():
    cols = _cols("media_material")
    assert {"persona_id", "type", "detail", "brief", "emotion",
            "usable_scene", "audience_hit", "used_in", "use_count", "status"} <= cols


def test_content_gains_authoring_columns():
    cols = _cols_migrated("media_content")
    assert {"authoring_stage", "brief", "evidence_gap", "selected_angle_id",
            "ai_draft", "revision_count", "finalized_at"} <= cols
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_schema.py -q`
Expected: FAIL —— 新表/新列不存在（`AssertionError`）。

- [ ] **Step 3: 在 SCHEMA 加 4 张表**

在 `app/database.py` 的 `SCHEMA` 字符串里、`media_feishu_unmatched` 表之后、结尾 `"""` 之前插入：

```sql
CREATE TABLE IF NOT EXISTS media_evidence (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    item TEXT DEFAULT '',
    item_type TEXT DEFAULT 'experience',
    source TEXT DEFAULT 'interview',
    from_material_id TEXT DEFAULT '',
    promoted_to_material_id TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_angle (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    angle TEXT DEFAULT '',
    rationale TEXT DEFAULT '',
    is_selected INTEGER DEFAULT 0,
    status TEXT DEFAULT 'candidate',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_draft_review (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    reviewed_draft TEXT DEFAULT '',
    reviewer_strategy TEXT DEFAULT 'layered',
    reviewer_model TEXT DEFAULT '',
    fact_flags TEXT DEFAULT '[]',
    persona_flags TEXT DEFAULT '[]',
    platform_flags TEXT DEFAULT '[]',
    gap_flags TEXT DEFAULT '[]',
    risk_flags TEXT DEFAULT '[]',
    score INTEGER DEFAULT 3,
    verdict TEXT DEFAULT 'pass',
    notes TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_material (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    type TEXT DEFAULT 'story',
    title TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    brief TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    usable_scene TEXT DEFAULT '',
    audience_hit TEXT DEFAULT '',
    used_in TEXT DEFAULT '[]',
    use_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: 在 MIGRATIONS 加 media_content 新列**

在 `app/database.py` 的 `MIGRATIONS` 列表末尾（最后一个元素后）追加：

```python
    "ALTER TABLE media_content ADD COLUMN authoring_stage TEXT DEFAULT 'none'",
    "ALTER TABLE media_content ADD COLUMN brief TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN evidence_gap TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN selected_angle_id TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN ai_draft TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN revision_count INTEGER DEFAULT 0",
    "ALTER TABLE media_content ADD COLUMN finalized_at DATETIME",
```

- [ ] **Step 5: 建测试基建 `tests/media_helpers.py`**

```python
"""media 二期测试基建：内存 DB + 假 AI。无 pytest-asyncio，异步用 asyncio.run。"""
import aiosqlite
from app.database import SCHEMA, MIGRATIONS


async def make_db():
    """内存 aiosqlite 连接，已应用 SCHEMA + MIGRATIONS，row_factory=Row。"""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()
    return db


def fake_ai(response, tokens=10, cost=0.0):
    """返回一个可替换 media_ai.ask_ai 的 async stub，固定返回 response。"""
    async def _stub(prompt, model="auto", task_type="", system_prompt="",
                    json_mode=False):
        return {"response": response, "model": model or "deepseek",
                "tokens": tokens, "cost": cost}
    return _stub


async def seed_content(db, *, persona_id="P1", content_id="C1",
                       title="AI如何真落地到企业", puzzle="为什么多数企业上AI三个月就放弃？",
                       stage="idea"):
    """插入一个人设 + 一条内容，返回 content_id。"""
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')",
        (persona_id, "嘉姐", "帮中小企业务实落地AI", "涨粉"))
    await db.execute(
        "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_reason) "
        "VALUES (?,?,?,?,?,?)",
        (content_id, persona_id, title, puzzle, stage, "受众常被AI焦虑营销割"))
    await db.commit()
    return content_id
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_schema.py -q`
Expected: PASS（全部新断言通过；原有断言不受影响）。

- [ ] **Step 7: 提交**

```bash
git add app/database.py tests/media_helpers.py tests/test_media_schema.py
git commit -m "feat(media-A): 加生产线4表+media_content新列+测试基建"
```

---

## Task 2: media_flow — authoring 常量与定稿更新（纯函数）

**Files:**
- Modify: `app/services/media_flow.py`
- Test: `tests/test_media_flow.py`

**Interfaces:**
- Consumes：`STAGES`（已有）。
- Produces：`AUTHORING_STAGES: list[str]`；`AUTHORING_LABELS: dict`；`finalize_updates(script: str) -> dict`（返回定稿时要写入 media_content 的字段，纯函数，不碰 DB）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_flow.py`：

```python
from app.services.media_flow import (
    AUTHORING_STAGES, AUTHORING_LABELS, finalize_updates,
)


def test_authoring_stages_are_coarse_three():
    assert AUTHORING_STAGES == ["none", "drafted", "finalized"]


def test_authoring_labels_cover_all():
    assert set(AUTHORING_LABELS) == set(AUTHORING_STAGES)


def test_finalize_updates_sets_scripted_and_finalized():
    up = finalize_updates("这是定稿脚本")
    assert up["stage"] == "scripted"
    assert up["authoring_stage"] == "finalized"
    assert up["script"] == "这是定稿脚本"


def test_finalize_updates_rejects_empty_script():
    # 空脚本不算定稿，返回空 dict 让调用方不推进
    assert finalize_updates("   ") == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_flow.py -q`
Expected: FAIL —— `ImportError`（名字未定义）。

- [ ] **Step 3: 实现**

追加到 `app/services/media_flow.py` 末尾：

```python
# ─────────────── 二期 🅐：写稿前认知子流程 ───────────────
# 刻意只三档：证据/角度/审稿是详情页可展开的产物，不是用户逐步点的关卡。
AUTHORING_STAGES = ["none", "drafted", "finalized"]

AUTHORING_LABELS = {
    "none": "未出稿",
    "drafted": "AI已出草稿",
    "finalized": "已定稿",
}


def finalize_updates(script: str) -> dict:
    """定稿时要写入 media_content 的字段。空脚本返回空 dict（不推进）。

    定稿 = 人编辑后的真实版进 script；同时 stage 翻 scripted、authoring 翻 finalized。
    ai_draft 由 write_script 单独持有，定稿不动它 —— 保留"AI草稿 vs 定稿"差异供功能B。
    """
    if not (script or "").strip():
        return {}
    return {
        "script": script,
        "stage": "scripted",
        "authoring_stage": "finalized",
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_flow.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/media_flow.py tests/test_media_flow.py
git commit -m "feat(media-A): media_flow加authoring常量与finalize_updates纯函数"
```

---

## Task 3: media_context — evidence/angle/material 渲染 + select_materials（纯函数）

**Files:**
- Modify: `app/services/media_context.py`
- Test: `tests/test_media_context.py`

**Interfaces:**
- Consumes：`select_by_budget`（已有）、`INJECTION_BUDGET`（已有 `material=3`）。
- Produces：
  - `render_evidence_block(evidence: list[dict]) -> str`（把本条真料列成"【真实素材】"块；空则返回 ""）。
  - `render_angle_block(angle: str, rationale: str) -> str`（"【本条角度】…"；angle 空则 ""）。
  - `select_materials(materials: list[dict]) -> list[dict]`（原料库雏形：优先未用过，按 use_count 升序取前 `INJECTION_BUDGET['material']` 条）。
  - `render_material_block(materials: list[dict]) -> str`（"【可复用原料】brief 清单"）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_context.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_context.py -q`
Expected: FAIL —— `ImportError`。

- [ ] **Step 3: 实现**

追加到 `app/services/media_context.py` 末尾：

```python
def render_evidence_block(evidence: list[dict]) -> str:
    """本条内容的真实素材包。全部注入（本条真料数量少，不走预算截断）。"""
    items = [e for e in evidence if (e.get("item") or "").strip()]
    if not items:
        return ""
    lines = ["【真实素材（只能用这些真料，缺了就标缺口，绝不编造）】"]
    for e in items:
        lines.append(f"- [{e.get('item_type', '')}] {e['item'].strip()}")
    return "\n".join(lines)


def render_angle_block(angle: str, rationale: str) -> str:
    """选中的切入角度。angle 为空返回空串（还没选角度就不加这块）。"""
    if not (angle or "").strip():
        return ""
    text = f"【本条切入角度（必须按这个角度写）】{angle.strip()}"
    if (rationale or "").strip():
        text += f"（理由：{rationale.strip()}）"
    return text


def select_materials(materials: list[dict]) -> list[dict]:
    """原料库雏形注入：优先未用过的（use_count 升序），取前 INJECTION_BUDGET['material'] 条。

    与 select_by_budget（按分数降序）方向相反 —— 原料越少用越该优先，
    避免同一个故事被反复调用听腻（spec §5.5 链路1）。
    """
    cap = INJECTION_BUDGET.get("material", 0)
    if not cap:
        return []
    ranked = sorted(materials, key=lambda m: m.get("use_count") or 0)
    return ranked[:cap]


def render_material_block(materials: list[dict]) -> str:
    """可复用原料的 brief 清单（detail 留给 AI 按需，不塞进提示词）。"""
    if not materials:
        return ""
    lines = ["【可复用原料（来自原料库，优先复用避免每条都采访）】"]
    for m in materials:
        brief = (m.get("brief") or "").strip() or (m.get("title") or "").strip()
        if brief:
            lines.append(f"- {brief}")
    return "\n".join(lines) if len(lines) > 1 else ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_context.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/media_context.py tests/test_media_context.py
git commit -m "feat(media-A): media_context加evidence/angle/material注入拼装"
```

---

## Task 4: 换脑审稿策略（纯函数）

**Files:**
- Modify: `app/services/media_ai.py`（加模块级纯函数）
- Test: `tests/test_media_pipeline.py`（Create）

**Interfaces:**
- Produces：
  - `available_providers(config: dict) -> list[str]`（按 claude/openai/deepseek/qwen 顺序，返回已配 key 的）。
  - `resolve_reviewer_model(strategy: str, writer_model: str, providers: list[str]) -> str`：
    - `swap_model` → 强制返回 providers 里第一个 != writer_model 的（只有一个则退化返回 writer_model）。
    - `same_model` → 返回 writer_model。
    - 其它(含 `layered`) → 返回 `"auto"`（让 task_type=media_critique 路由决定，角色靠独立 system_prompt 分离）。

- [ ] **Step 1: 写失败测试（新建 test_media_pipeline.py）**

```python
"""二期 🅐 生产线：换脑策略（纯）+ 采访/角度/草稿/审稿/修订（AI，asyncio.run）。"""
import asyncio
import json

from tests.media_helpers import make_db, fake_ai, seed_content
from app.services.media_ai import available_providers, resolve_reviewer_model


# ---------- 换脑策略（纯函数）----------

def test_available_providers_orders_by_configured_keys():
    cfg = {"deepseek_api_key": "x", "anthropic_api_key": "y"}
    assert available_providers(cfg) == ["claude", "deepseek"]


def test_available_providers_empty():
    assert available_providers({}) == []


def test_swap_model_forces_different_provider():
    got = resolve_reviewer_model("swap_model", "deepseek", ["claude", "deepseek"])
    assert got == "claude"


def test_swap_model_single_provider_degrades():
    got = resolve_reviewer_model("swap_model", "deepseek", ["deepseek"])
    assert got == "deepseek"


def test_same_model_returns_writer():
    assert resolve_reviewer_model("same_model", "deepseek", ["claude", "deepseek"]) == "deepseek"


def test_layered_returns_auto():
    assert resolve_reviewer_model("layered", "deepseek", ["claude", "deepseek"]) == "auto"
    assert resolve_reviewer_model("", "deepseek", ["claude"]) == "auto"  # 缺省即 layered
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: FAIL —— `ImportError`。

- [ ] **Step 3: 实现**

在 `app/services/media_ai.py` 里 `_txt` 定义之后追加：

```python
_PROVIDER_KEY = {
    "claude": "anthropic_api_key",
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
    "qwen": "qwen_api_key",
}
_PROVIDER_ORDER = ["claude", "openai", "deepseek", "qwen"]


def available_providers(config: dict) -> list[str]:
    """已配置 API Key 的模型 provider，按固定优先级排序。"""
    return [p for p in _PROVIDER_ORDER if config.get(_PROVIDER_KEY[p])]


def resolve_reviewer_model(strategy: str, writer_model: str,
                           providers: list[str]) -> str:
    """按换脑策略决定审稿用哪个模型。spec §6.3。

    swap_model：强制换一个与写稿不同的 provider（最独立最贵）。
    same_model：同模型，仅靠独立 system_prompt 分离角色（最省最弱）。
    layered（默认）：返回 'auto'，走 task_type=media_critique 路由，角色靠独立调用分离。
    """
    if strategy == "swap_model":
        for p in providers:
            if p != writer_model:
                return p
        return writer_model  # 只有一个 provider，退化
    if strategy == "same_model":
        return writer_model
    return "auto"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: PASS（6 个策略测试通过）。

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media-A): 换脑审稿策略纯函数(layered/swap_model/same_model)"
```

---

## Task 5: 采访补料 — interview_questions + extract_evidence（AI）

**Files:**
- Modify: `app/services/media_ai.py`
- Test: `tests/test_media_pipeline.py`

**Interfaces:**
- Consumes：`ask_ai`、`extract_json`、`_txt`、`log_injection`、`seed_content`（测试）。
- Produces：
  - `async interview_questions(db, content_id, model="auto") -> dict`：`{"ok","questions":list[str],"cost","model","error"}`。只读不写库。基于已有原料库只问缺口。
  - `async extract_evidence(db, content_id, answers, model="auto") -> dict`：把用户一次性回答提炼成 `media_evidence` 行（source='interview'），返回 `{"ok","count","cost","model","error"}`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_pipeline.py`：

```python
from app.services.media_ai import interview_questions, extract_evidence


def test_interview_questions_returns_list(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"questions": [
                            "你自己帮企业落地AI时最惨的一次是什么？",
                            "有没有具体的转化率/成本数字？"]}, ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await interview_questions(db, "C1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    assert len(res["questions"]) == 2
    assert "转化率" in res["questions"][1]


def test_extract_evidence_writes_rows(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"items": [
                            {"item": "帮做鞋厂上客服AI，三周上线", "item_type": "experience"},
                            {"item": "人力省了2个", "item_type": "data"}]},
                            ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await extract_evidence(db, "C1", "我去年帮一个鞋厂做的……省了2个人力")
        cur = await db.execute(
            "SELECT item,item_type,source FROM media_evidence WHERE content_id='C1' "
            "ORDER BY item_type")
        rows = [dict(r) for r in await cur.fetchall()]
        await db.close()
        return res, rows

    res, rows = asyncio.run(go())
    assert res["ok"] is True and res["count"] == 2
    assert all(r["source"] == "interview" for r in rows)
    assert any("鞋厂" in r["item"] for r in rows)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: FAIL —— `ImportError`。

- [ ] **Step 3: 实现**

追加到 `app/services/media_ai.py`（放在 `write_script` 之前的区块，与其它 SYSTEM 常量一起）：

```python
INTERVIEW_SYSTEM = """你是口播内容的采访者。目标：就本条选题，向创作者提出精准问题，
挖出只有他本人有的真实素材（经历/案例/数字/判断），供后续写稿用真料。

铁律：
1. 只问「本条选题」需要、而系统现有资料里没有的（给你的"已有真料"不要重复问）。
2. 问具体的真事，不问空泛感受。要能挖出细节/数字/冲突的问题。
3. 问题控制在 5-8 个，一次性问完（创作者会一次答完）。
4. 只输出 JSON：{"questions":["问题1","问题2"]}"""

EVIDENCE_SYSTEM = """你是素材整理员。把创作者的口述回答，拆成一条条结构化真实素材。

铁律：
1. 只整理创作者真说了的，不补充、不发挥、不编造。
2. 每条标类型：experience经历 / case案例 / data数据 / opinion观点 / judgment判断。
3. 一句话说不清的可拆成多条；空泛没信息量的丢掉。
4. 只输出 JSON：{"items":[{"item":"素材内容","item_type":"experience"}]}"""


async def interview_questions(db, content_id: str, model: str = "auto") -> dict:
    """就本条选题生成 5-8 个采访问题。只读不写库；基于已有原料库只问缺口。"""
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "questions": [],
                "cost": 0, "model": ""}
    content = dict(row)

    # 已有原料库 brief —— 告诉 AI 别重复问
    cur = await db.execute(
        "SELECT brief,title FROM media_material WHERE persona_id=? AND status='active' "
        "LIMIT 30", (content["persona_id"],))
    mats = [((r["brief"] or r["title"]) or "").strip() for r in await cur.fetchall()]
    mats = [m for m in mats if m]

    parts = [f"【本条选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if mats:
        parts.append("【系统已有真料（不要重复问）】\n" + "\n".join(f"- {m}" for m in mats))
    parts.append("请就这条选题提出采访问题。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_interview",
                          system_prompt=INTERVIEW_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "questions": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    questions = [_txt(q) for q in (obj.get("questions") or []) if _txt(q)]
    await log_injection(db, content_id, "interview_questions", [],
                        result.get("tokens", 0))
    return {"ok": True, "questions": questions, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def extract_evidence(db, content_id: str, answers: str,
                           model: str = "auto") -> dict:
    """把创作者的一次性回答提炼成 media_evidence 行（source='interview'）。"""
    cur = await db.execute(
        "SELECT persona_id, title, puzzle FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "count": 0, "cost": 0, "model": ""}
    content = dict(row)
    if not (answers or "").strip():
        return {"ok": False, "error": "回答是空的", "count": 0, "cost": 0, "model": ""}

    parts = [f"【选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    parts.append(f"【创作者的回答】\n{answers[:8000]}")
    parts.append("请整理成结构化真实素材。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_evidence",
                          system_prompt=EVIDENCE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    items = [it for it in (obj.get("items") or []) if isinstance(it, dict)]
    valid_types = {"experience", "case", "data", "opinion", "judgment"}
    count = 0
    for it in items:
        item_text = _txt(it.get("item"))
        if not item_text:
            continue
        itype = it.get("item_type") if it.get("item_type") in valid_types else "experience"
        await db.execute(
            "INSERT INTO media_evidence "
            "(id,content_id,persona_id,item,item_type,source) "
            "VALUES (?,?,?,?,?, 'interview')",
            (str(uuid.uuid4()), content_id, content["persona_id"], item_text, itype))
        count += 1
    await db.commit()
    await log_injection(db, content_id, "extract_evidence", [], result.get("tokens", 0))
    return {"ok": True, "count": count, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media-A): 采访补料 interview_questions + extract_evidence"
```

---

## Task 6: 角度候选 — propose_angles（AI）

**Files:**
- Modify: `app/services/media_ai.py`
- Test: `tests/test_media_pipeline.py`

**Interfaces:**
- Consumes：`ask_ai`、`extract_json`、`_txt`、`media_evidence`（Task 5）、`media_angle`（Task 1）。
- Produces：`async propose_angles(db, content_id, model="auto") -> dict`：写 `media_angle` 行（旧的先删），第一个（AI 按最佳排序的首条）`is_selected=1`，并回写 `media_content.selected_angle_id`。返回 `{"ok","count","selected_id","cost","model","error"}`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_pipeline.py`：

```python
from app.services.media_ai import propose_angles


def test_propose_angles_writes_and_selects_first(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"angles": [
                            {"angle": "从我踩过的坑切入", "rationale": "第一人称最可信"},
                            {"angle": "从一个鞋厂案例切入", "rationale": "具体可感"}]},
                            ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await propose_angles(db, "C1")
        cur = await db.execute(
            "SELECT id,angle,is_selected FROM media_angle WHERE content_id='C1' "
            "ORDER BY is_selected DESC")
        angles = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT selected_angle_id FROM media_content WHERE id='C1'")
        sel = (await cur.fetchone())["selected_angle_id"]
        await db.close()
        return res, angles, sel

    res, angles, sel = asyncio.run(go())
    assert res["ok"] is True and res["count"] == 2
    assert angles[0]["is_selected"] == 1 and "踩过的坑" in angles[0]["angle"]
    assert sum(a["is_selected"] for a in angles) == 1  # 只选一个
    assert sel == angles[0]["id"] == res["selected_id"]


def test_propose_angles_replaces_old(monkeypatch):
    # 二次调用应清掉旧角度，不堆积
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"angles": [{"angle": "新角度", "rationale": "r"}]},
                                           ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("INSERT INTO media_angle (id,content_id,angle) VALUES "
                         "('old','C1','旧角度')")
        await db.commit()
        await propose_angles(db, "C1")
        cur = await db.execute("SELECT angle FROM media_angle WHERE content_id='C1'")
        rows = [r["angle"] for r in await cur.fetchall()]
        await db.close()
        return rows

    rows = asyncio.run(go())
    assert rows == ["新角度"]  # 旧的被清掉
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: FAIL —— `ImportError`。

- [ ] **Step 3: 实现**

追加到 `app/services/media_ai.py`：

```python
ANGLE_SYSTEM = """你是口播选题的角度策划。基于真实素材，给出 2-3 个不同的切入角度。

铁律：
1. 每个角度是一个「怎么讲这条」的具体切入点，不是选题的复述。
2. 角度之间要真不同（换个人称/换个场景/换个冲突点），不是换壳同一套。
3. 按你认为最好的排最前（第一个会被默认选中）。
4. 只用给定真实素材能支撑的角度，别设计需要编造的角度。
5. 只输出 JSON：{"angles":[{"angle":"切入角度","rationale":"为什么这个角度打得中"}]}"""


async def propose_angles(db, content_id: str, model: str = "auto") -> dict:
    """基于证据包 + 人设，出 2-3 个候选角度，默认选中第一个。看不到：数据表、话题池。"""
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "count": 0,
                "selected_id": "", "cost": 0, "model": ""}
    content = dict(row)

    cur = await db.execute(
        "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
    persona = dict(await cur.fetchone())

    cur = await db.execute(
        "SELECT item,item_type FROM media_evidence WHERE content_id=?", (content_id,))
    evidence = [dict(r) for r in await cur.fetchall()]

    parts = [
        f"【人设】{persona['name']}｜{persona['one_liner']}",
        f"【选题】{content['title']}",
    ]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if evidence:
        parts.append("【真实素材】\n" + "\n".join(
            f"- [{e['item_type']}] {e['item']}" for e in evidence))
    else:
        parts.append("【真实素材】暂无 —— 只给这条选题现有信息能支撑的角度。")
    parts.append("请给出 2-3 个切入角度。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_angle",
                          system_prompt=ANGLE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0, "selected_id": "",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    angles = [a for a in (obj.get("angles") or []) if isinstance(a, dict)
              and _txt(a.get("angle"))]
    if not angles:
        return {"ok": False, "error": "AI 没给出可用角度", "count": 0, "selected_id": "",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 重出角度时清掉旧的，避免堆积
    await db.execute("DELETE FROM media_angle WHERE content_id=?", (content_id,))
    selected_id = ""
    count = 0
    for idx, a in enumerate(angles):
        aid = str(uuid.uuid4())
        is_sel = 1 if idx == 0 else 0
        if is_sel:
            selected_id = aid
        await db.execute(
            "INSERT INTO media_angle "
            "(id,content_id,angle,rationale,is_selected,status) "
            "VALUES (?,?,?,?,?,?)",
            (aid, content_id, _txt(a.get("angle")), _txt(a.get("rationale")),
             is_sel, "selected" if is_sel else "candidate"))
        count += 1
    await db.execute(
        "UPDATE media_content SET selected_angle_id=? WHERE id=?",
        (selected_id, content_id))
    await db.commit()
    await log_injection(db, content_id, "propose_angles", [], result.get("tokens", 0))
    return {"ok": True, "count": count, "selected_id": selected_id, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media-A): 角度候选 propose_angles(默认选首个)"
```

---

## Task 7: 升级 write_script — 注入证据/角度/原料 + 自评缺口 + 持久化 ai_draft

**Files:**
- Modify: `app/services/media_ai.py`（`SCRIPT_SYSTEM` 加缺口标注规则；`write_script` 加载证据/角度/原料、写 `ai_draft`/`evidence_gap`/`authoring_stage`；新增纯函数 `extract_gap_markers`）
- Test: `tests/test_media_pipeline.py`

**Interfaces:**
- Consumes：`build_script_context`（已有）、`render_evidence_block`/`render_angle_block`/`select_materials`/`render_material_block`（Task 3）。
- Produces：
  - `extract_gap_markers(text: str) -> list[str]`（纯函数，抽出草稿里 `【缺真料：…】` 的说明）。
  - 升级后的 `write_script`：额外把草稿写进 `media_content.ai_draft`，把缺口写进 `evidence_gap`，`authoring_stage='drafted'`。返回值仍含 `script`（向后兼容 `content_ai_script` 路由）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_pipeline.py`：

```python
from app.services.media_ai import extract_gap_markers, write_script


def test_extract_gap_markers_pure():
    text = "开场抛问题。【缺真料：需要一个真实客户名字】中间讲案例。【缺真料：转化数字】"
    gaps = extract_gap_markers(text)
    assert gaps == ["需要一个真实客户名字", "转化数字"]


def test_extract_gap_markers_none():
    assert extract_gap_markers("干干净净的稿子") == []


def test_write_script_persists_draft_and_gap(monkeypatch):
    draft = "3秒抛谜题……【缺真料：具体鞋厂转化率】……结尾钩子。"
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai(draft))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await write_script(db, "C1", mode="full")
        cur = await db.execute(
            "SELECT ai_draft,evidence_gap,authoring_stage,script FROM media_content "
            "WHERE id='C1'")
        c = dict(await cur.fetchone())
        await db.close()
        return res, c

    res, c = asyncio.run(go())
    assert res["ok"] is True and res["script"] == draft
    assert c["ai_draft"] == draft          # 草稿进 ai_draft
    assert c["script"] == ""               # 定稿字段还没动
    assert "鞋厂转化率" in c["evidence_gap"]  # 缺口被抽出
    assert c["authoring_stage"] == "drafted"


def test_write_script_injects_selected_angle(monkeypatch):
    captured = {}

    async def spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        captured["prompt"] = prompt
        return {"response": "稿子", "model": "deepseek", "tokens": 5, "cost": 0}

    monkeypatch.setattr("app.services.media_ai.ask_ai", spy)

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("INSERT INTO media_angle (id,content_id,angle,rationale,is_selected)"
                         " VALUES ('a1','C1','从我踩过的坑切入','最可信',1)")
        await db.execute("UPDATE media_content SET selected_angle_id='a1' WHERE id='C1'")
        await db.execute("INSERT INTO media_evidence (id,content_id,persona_id,item,item_type)"
                         " VALUES ('e1','C1','P1','帮鞋厂上客服AI','experience')")
        await db.commit()
        await write_script(db, "C1", mode="full")
        await db.close()

    asyncio.run(go())
    assert "从我踩过的坑切入" in captured["prompt"]  # 角度注入了
    assert "帮鞋厂上客服AI" in captured["prompt"]    # 证据注入了
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: FAIL —— `ImportError`（`extract_gap_markers`）+ 断言失败（草稿未持久化）。

- [ ] **Step 3: 改 SCRIPT_SYSTEM 加缺口规则**

把 `app/services/media_ai.py` 里现有 `SCRIPT_SYSTEM` 的铁律区，在第 5 条后增加第 6 条（注意保持"不超过 5 条"的注释语义——这里明确它是硬约束不是偏好，改注释为"核心铁律 5 条 + 1 条红线"）。用 Edit 替换：

找到：
```
铁律（必须全部满足，不超过 5 条 —— 规则多了每条都做不好）：
1. 必须以谜题开场，3 秒内抛出，禁止任何铺垫和自我介绍。
2. 必须植入给定的记忆点（如果提供了）。
3. 口语化 —— 写的是说出来的话，不是书面文章。短句，能断则断。
4. 标注时长节奏，全片控制在 60-90 秒。
5. 结尾留钩子，引导评论互动。
```
替换为：
```
铁律（前 5 条是手艺，第 6 条是红线，全部必须满足）：
1. 必须以谜题开场，3 秒内抛出，禁止任何铺垫和自我介绍。
2. 必须植入给定的记忆点（如果提供了）。
3. 口语化 —— 写的是说出来的话，不是书面文章。短句，能断则断。
4. 标注时长节奏，全片控制在 60-90 秒。
5. 结尾留钩子，引导评论互动。
6. 【真实性红线】只能用给定的真实素材。若某处需要你没有的真事/数字/案例，
   用 【缺真料：具体说明缺什么】 原样标注在该处，绝不编造本人经历或数字来填。
   给了角度就按角度写；缺真料标注不影响其它部分正常写。
```

- [ ] **Step 4: 加纯函数 extract_gap_markers**

在 `app/services/media_ai.py` 顶部 `import` 区加 `import re`（若已存在则跳过），并在 `_txt` 之后加：

```python
_GAP_RE = re.compile(r"【缺真料：(.+?)】")


def extract_gap_markers(text: str) -> list[str]:
    """抽出草稿里 AI 标注的真料缺口。无缺口返回空列表。"""
    return [m.strip() for m in _GAP_RE.findall(text or "") if m.strip()]
```

- [ ] **Step 5: 升级 write_script 主体**

用 Edit 把现有 `write_script` 从 `parts = [context_text, ...]` 到 `return {...}` 的区段替换。完整替换后的函数体（从 `mode` 分支之后开始，保留前面加载 content/persona 与 mode 分支不变）：

在现有 `context_text, injected_ids = build_script_context(persona, traits)`（full 分支）之后、`parts = [...]` 之前，插入证据/角度/原料加载；并把 `parts` 拼装、`log_injection`、DB 持久化、`return` 整段替换为：

```python
    # ── 二期 🅐：加载本条证据包 + 选中角度 + 可复用原料 ──
    cur = await db.execute(
        "SELECT item,item_type FROM media_evidence WHERE content_id=?", (content_id,))
    evidence = [dict(r) for r in await cur.fetchall()]

    angle_text, angle_rationale = "", ""
    if content.get("selected_angle_id"):
        cur = await db.execute(
            "SELECT angle,rationale FROM media_angle WHERE id=?",
            (content["selected_angle_id"],))
        arow = await cur.fetchone()
        if arow:
            angle_text, angle_rationale = arow["angle"], arow["rationale"]

    material_ids = []
    material_block = ""
    if mode != "lean":
        cur = await db.execute(
            "SELECT id,brief,title,use_count FROM media_material "
            "WHERE persona_id=? AND status='active'", (content["persona_id"],))
        mats = [dict(r) for r in await cur.fetchall()]
        picked_mats = select_materials(mats)
        material_ids = [m["id"] for m in picked_mats]
        material_block = render_material_block(picked_mats)

    parts = [context_text]
    ev_block = render_evidence_block(evidence)
    if ev_block:
        parts.append(ev_block)
    if material_block:
        parts.append(material_block)
    ang_block = render_angle_block(angle_text, angle_rationale)
    if ang_block:
        parts.append(ang_block)
    parts.append(f"【本条选题】{content['title']}")
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if content["idea_reason"]:
        parts.append(f"【为什么做这条】{content['idea_reason']}")
    parts.append("请写出这条内容的口播脚本。")

    prompt = "\n\n".join(parts)
    result = await ask_ai(prompt, model=model, task_type="media_script",
                          system_prompt=SCRIPT_SYSTEM)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "script": "",
                "cost": result.get("cost", 0), "model": result.get("model", ""),
                "injected_count": 0}

    # ── 持久化草稿：进 ai_draft（不碰 script，script 留给人定稿）──
    gaps = extract_gap_markers(resp)
    gap_text = "；".join(gaps)
    await db.execute(
        "UPDATE media_content SET ai_draft=?, evidence_gap=?, "
        "authoring_stage='drafted', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (resp, gap_text, content_id))
    await db.commit()

    all_injected = injected_ids + material_ids
    await log_injection(db, content_id, f"write_script:{mode}",
                        all_injected, result.get("tokens", 0))

    return {"ok": True, "script": resp, "error": "", "gap": gap_text,
            "cost": result.get("cost", 0), "model": result.get("model", ""),
            "injected_count": len(all_injected)}
```

> ⚠️ 确认 `write_script` 顶部的 import 处已从 media_context 引入新函数。在 `media_ai.py` 顶部把
> `from app.services.media_context import build_script_context`
> 改为
> `from app.services.media_context import (build_script_context, render_evidence_block, render_angle_block, select_materials, render_material_block)`。

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: PASS（缺口抽取 + 草稿持久化 + 角度/证据注入全绿）。

- [ ] **Step 7: 回归确认没弄坏一期**

Run: `python -m pytest tests/ -q`
Expected: PASS（全部既有测试仍通过）。

- [ ] **Step 8: 提交**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media-A): write_script升级(注入证据/角度/原料+自评缺口+存ai_draft)"
```

---

## Task 8: 独立审稿 critique_draft + 定向修订 revise_draft（AI）

**Files:**
- Modify: `app/services/media_ai.py`
- Test: `tests/test_media_pipeline.py`

**Interfaces:**
- Consumes：`ask_ai`、`extract_json`、`_txt`、`_clamp`、`available_providers`/`resolve_reviewer_model`（Task 4）、`get_model_for_task`（从 ai_router import）、`extract_gap_markers`（Task 7）。
- Produces：
  - `async critique_draft(db, content_id, strategy="layered", model="auto") -> dict`：审 `ai_draft`，写一行 `media_draft_review`，返回 `{"ok","review_id","score","verdict","reviewer_model","cost","model","error"}`。审稿器只挑毛病不改稿。
  - `async revise_draft(db, content_id, model="auto") -> dict`：按最近一条 `media_draft_review` 让写稿 AI 改一次；`revision_count>=1` 则拒绝。更新 `ai_draft`、`revision_count+1`。返回 `{"ok","script","revision_count","cost","model","error"}`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_media_pipeline.py`：

```python
from app.services.media_ai import critique_draft, revise_draft


def test_critique_writes_review_row(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
        fake_ai(json.dumps({
            "fact_flags": ["'80%企业'这个数字没出处"],
            "persona_flags": [], "platform_flags": [],
            "gap_flags": ["缺一个真实客户名"], "risk_flags": [],
            "score": 3, "verdict": "revise", "notes": "整体可用但要补一个真数字"
        }, ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("UPDATE media_content SET ai_draft='一版草稿' WHERE id='C1'")
        await db.commit()
        res = await critique_draft(db, "C1", strategy="layered")
        cur = await db.execute("SELECT * FROM media_draft_review WHERE content_id='C1'")
        row = dict(await cur.fetchone())
        await db.close()
        return res, row

    res, row = asyncio.run(go())
    assert res["ok"] is True and res["verdict"] == "revise" and res["score"] == 3
    assert row["reviewer_strategy"] == "layered"
    assert json.loads(row["fact_flags"])[0].startswith("'80%")
    assert row["reviewed_draft"] == "一版草稿"      # 审的哪版有快照
    assert "补一个真数字" in row["notes"]


def test_critique_needs_a_draft(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai("{}"))

    async def go():
        db = await make_db()
        await seed_content(db)  # ai_draft 为空
        res = await critique_draft(db, "C1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is False and "草稿" in res["error"]


def test_revise_updates_draft_and_counts(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai("改好的第二版草稿"))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("UPDATE media_content SET ai_draft='第一版' WHERE id='C1'")
        await db.execute(
            "INSERT INTO media_draft_review (id,content_id,notes,verdict) "
            "VALUES ('r1','C1','补个真数字','revise')")
        await db.commit()
        res = await revise_draft(db, "C1")
        cur = await db.execute(
            "SELECT ai_draft,revision_count FROM media_content WHERE id='C1'")
        c = dict(await cur.fetchone())
        await db.close()
        return res, c

    res, c = asyncio.run(go())
    assert res["ok"] is True
    assert c["ai_draft"] == "改好的第二版草稿"
    assert c["revision_count"] == 1


def test_revise_refuses_second_time(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai("不该被用到"))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute(
            "UPDATE media_content SET ai_draft='已改过', revision_count=1 WHERE id='C1'")
        await db.commit()
        res = await revise_draft(db, "C1")
        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id='C1'")
        keep = (await cur.fetchone())["ai_draft"]
        await db.close()
        return res, keep

    res, keep = asyncio.run(go())
    assert res["ok"] is False and "一次" in res["error"]
    assert keep == "已改过"  # 没被覆盖
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: FAIL —— `ImportError`。

- [ ] **Step 3: 实现**

在 `app/services/media_ai.py` 顶部 import 区补：
```python
from app.services.ai_router import ask_ai, get_model_for_task, _load_config
```
（原本只 import 了 `ask_ai`；`_load_config` 用于取已配置 providers，同包内部可用。）

追加实现：

```python
CRITIQUE_SYSTEM = """你是独立的口播稿审稿人。你没参与写作，只负责挑毛病。

铁律：
1. 你只【指出】问题，绝不改写、绝不给出修改后的文本。
2. 逐维度找证据支撑的问题，找不到就留空数组，别硬凑。
3. 真实性最优先：任何看起来像编造的经历/数字/案例都要标进 fact_flags。
4. 打分诚实：score 1-5。verdict 只能是 pass(可直接用)/revise(建议改一次)/reject(建议回素材或角度)。
5. 只输出 JSON：
{"fact_flags":[],"persona_flags":[],"platform_flags":[],"gap_flags":[],
 "risk_flags":[],"score":3,"verdict":"pass","notes":"一句话总评"}

维度说明：fact_flags事实/数字存疑；persona_flags不像本人/AI味/卖课味/焦虑词；
platform_flags平台适配问题；gap_flags缺真料的地方；risk_flags红线/边界风险。"""

REVISE_SYSTEM = """你是原稿作者，现在根据审稿意见做【一次】定向修订。

铁律：
1. 只针对审稿指出的问题改，别推倒重写。
2. 仍守真实性红线：缺真料的地方继续用 【缺真料：说明】 标注，绝不编造。
3. 输出修订后的完整脚本纯文本，不要解释，不要输出 JSON。"""


async def critique_draft(db, content_id: str, strategy: str = "layered",
                         model: str = "auto") -> dict:
    """独立审稿 ai_draft。写稿器≠审稿器：这是与 write_script 完全独立的调用。

    看不到"这是刚才那个 AI 写的" —— 只给草稿全文，避免自我背书。
    """
    cur = await db.execute(
        "SELECT persona_id, ai_draft FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "review_id": "",
                "score": 0, "verdict": "", "reviewer_model": "", "cost": 0, "model": ""}
    draft = (row["ai_draft"] or "").strip()
    if not draft:
        return {"ok": False, "error": "还没有草稿可审，先让 AI 出草稿",
                "review_id": "", "score": 0, "verdict": "",
                "reviewer_model": "", "cost": 0, "model": ""}

    # 换脑：按策略决定审稿模型（与写稿模型错开）
    config = _load_config()
    writer_model = get_model_for_task("media_script", "auto")
    reviewer_model = resolve_reviewer_model(strategy, writer_model,
                                            available_providers(config))
    use_model = model if model != "auto" else reviewer_model

    prompt = f"【待审口播稿】\n{draft[:6000]}\n\n请审这份稿子。"
    result = await ask_ai(prompt, model=use_model, task_type="media_critique",
                          system_prompt=CRITIQUE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "review_id": "", "score": 0,
                "verdict": "", "reviewer_model": use_model,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")

    def _flags(key):
        v = obj.get(key)
        if not isinstance(v, list):
            return []
        return [_txt(x) for x in v if _txt(x)]

    verdict = obj.get("verdict") if obj.get("verdict") in ("pass", "revise", "reject") \
        else "revise"
    review_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_draft_review "
        "(id,content_id,reviewed_draft,reviewer_strategy,reviewer_model,"
        " fact_flags,persona_flags,platform_flags,gap_flags,risk_flags,"
        " score,verdict,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (review_id, content_id, draft, strategy, result.get("model", use_model),
         json.dumps(_flags("fact_flags"), ensure_ascii=False),
         json.dumps(_flags("persona_flags"), ensure_ascii=False),
         json.dumps(_flags("platform_flags"), ensure_ascii=False),
         json.dumps(_flags("gap_flags"), ensure_ascii=False),
         json.dumps(_flags("risk_flags"), ensure_ascii=False),
         _clamp(obj.get("score"), 3), verdict, _txt(obj.get("notes"))))
    await db.commit()
    await log_injection(db, content_id, f"critique_draft:{strategy}", [],
                        result.get("tokens", 0))
    return {"ok": True, "review_id": review_id, "score": _clamp(obj.get("score"), 3),
            "verdict": verdict, "reviewer_model": result.get("model", use_model),
            "error": "", "cost": result.get("cost", 0), "model": result.get("model", "")}


async def revise_draft(db, content_id: str, model: str = "auto") -> dict:
    """按最近一条审稿意见改一次。至多一次：revision_count>=1 直接拒绝。"""
    cur = await db.execute(
        "SELECT ai_draft, revision_count FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "script": "",
                "revision_count": 0, "cost": 0, "model": ""}
    if (row["revision_count"] or 0) >= 1:
        return {"ok": False, "error": "已经定向修订过一次了，第二次请回到素材或角度重来",
                "script": "", "revision_count": row["revision_count"],
                "cost": 0, "model": ""}
    draft = (row["ai_draft"] or "").strip()
    if not draft:
        return {"ok": False, "error": "还没有草稿", "script": "",
                "revision_count": 0, "cost": 0, "model": ""}

    cur = await db.execute(
        "SELECT fact_flags,persona_flags,platform_flags,gap_flags,risk_flags,notes "
        "FROM media_draft_review WHERE content_id=? ORDER BY created_at DESC LIMIT 1",
        (content_id,))
    rev = await cur.fetchone()
    if not rev:
        return {"ok": False, "error": "没有审稿意见可依据，先审稿", "script": "",
                "revision_count": 0, "cost": 0, "model": ""}

    flag_lines = []
    for key, label in [("fact_flags", "事实存疑"), ("persona_flags", "不像本人"),
                       ("platform_flags", "平台适配"), ("gap_flags", "缺真料"),
                       ("risk_flags", "风险")]:
        try:
            arr = json.loads(rev[key] or "[]")
        except (json.JSONDecodeError, TypeError):
            arr = []
        for a in arr:
            flag_lines.append(f"- [{label}] {a}")
    notes = _txt(rev["notes"])

    parts = [f"【原稿】\n{draft[:6000]}", "【审稿意见】"]
    if flag_lines:
        parts.append("\n".join(flag_lines))
    if notes:
        parts.append(f"总评：{notes}")
    parts.append("请据此做一次定向修订，输出完整脚本。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_script",
                          system_prompt=REVISE_SYSTEM)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "script": "",
                "revision_count": row["revision_count"] or 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    new_count = (row["revision_count"] or 0) + 1
    gap_text = "；".join(extract_gap_markers(resp))
    await db.execute(
        "UPDATE media_content SET ai_draft=?, evidence_gap=?, revision_count=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (resp, gap_text, new_count, content_id))
    await db.commit()
    await log_injection(db, content_id, "revise_draft", [], result.get("tokens", 0))
    return {"ok": True, "script": resp, "revision_count": new_count, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_pipeline.py -q`
Expected: PASS（审稿写行 / 需草稿 / 修订计数 / 拒绝第二次 全绿）。

- [ ] **Step 5: 提交**

```bash
git add app/services/media_ai.py tests/test_media_pipeline.py
git commit -m "feat(media-A): 独立审稿critique_draft+定向修订revise_draft(至多一次)"
```

---

## Task 9: 路由 — 过程端点 + 详情页装载新数据 + 换脑策略设置

**Files:**
- Modify: `app/api/media.py`
- Test: `tests/test_media_routes.py`（Create，用 FastAPI TestClient）

**Interfaces:**
- Consumes：`interview_questions`/`extract_evidence`/`propose_angles`/`critique_draft`/`revise_draft`（Task 5-8）、`finalize_updates`（Task 2）、`_load_config`。
- Produces（新端点，全部返回 JSON 除非注明）：
  - `POST /media/content/{cid}/interview` → `{ok,questions}`
  - `POST /media/content/{cid}/evidence`（Form `answers`）→ `{ok,count}`
  - `POST /media/content/{cid}/angles` → `{ok,count,selected_id}`
  - `POST /media/content/{cid}/angle/{aid}/select`（选中某备选角度并重出草稿）→ `{ok}` 后由前端触发 ai-script
  - `POST /media/content/{cid}/critique` → `{ok,score,verdict,review_id}`
  - `POST /media/content/{cid}/revise` → `{ok,script,revision_count}`
  - `POST /media/content/{cid}/finalize`（Form `script`）→ 302 回详情（定稿，走 `finalize_updates`）
  - `POST /media/settings/review-strategy`（Form `strategy`）→ 302 回设置页（写 settings.json）
  - 详情页 `content_detail` 额外装载 `angles / evidence / latest_review` 传模板。

- [ ] **Step 1: 写失败测试（TestClient）**

创建 `tests/test_media_routes.py`。注意：路由用真实 `get_db()`（落 `data/aipm.db`），TestClient 会真的读写该库。为隔离，测试用 monkeypatch 把 AI 能力换成假实现，只验证"端点存在 + 参数透传 + JSON 结构"，不真调模型。

```python
"""🅐 过程端点路由测试。用 TestClient；AI 能力打桩，不真调模型。"""
import asyncio
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db, init_db
from tests.media_helpers import seed_content


@pytest.fixture(scope="module", autouse=True)
def _db_ready():
    asyncio.run(init_db())


def _seed_real():
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_content WHERE id='RT1'")
            await db.execute("DELETE FROM media_persona WHERE id='RTP'")
            await seed_content(db, persona_id="RTP", content_id="RT1")
        finally:
            await db.close()
    asyncio.run(go())


def test_interview_endpoint(monkeypatch):
    async def fake(db, cid, model="auto"):
        return {"ok": True, "questions": ["Q1", "Q2"], "cost": 0, "model": "x", "error": ""}
    monkeypatch.setattr("app.api.media.interview_questions", fake)
    _seed_real()
    client = TestClient(app)
    r = client.post("/media/content/RT1/interview")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["questions"] == ["Q1", "Q2"]


def test_evidence_endpoint(monkeypatch):
    async def fake(db, cid, answers, model="auto"):
        assert answers == "我的回答"
        return {"ok": True, "count": 3, "cost": 0, "model": "x", "error": ""}
    monkeypatch.setattr("app.api.media.extract_evidence", fake)
    _seed_real()
    client = TestClient(app)
    r = client.post("/media/content/RT1/evidence", data={"answers": "我的回答"})
    assert r.json()["count"] == 3


def test_finalize_sets_scripted():
    _seed_real()
    client = TestClient(app)
    r = client.post("/media/content/RT1/finalize", data={"script": "定稿内容"},
                    follow_redirects=False)
    assert r.status_code == 302

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT stage,authoring_stage,script FROM media_content WHERE id='RT1'")
            return dict(await cur.fetchone())
        finally:
            await db.close()
    c = asyncio.run(check())
    assert c["stage"] == "scripted" and c["authoring_stage"] == "finalized"
    assert c["script"] == "定稿内容"


def test_critique_endpoint(monkeypatch):
    async def fake(db, cid, strategy="layered", model="auto"):
        assert strategy in ("layered", "swap_model", "same_model")
        return {"ok": True, "score": 4, "verdict": "pass", "review_id": "rv",
                "reviewer_model": "claude", "cost": 0, "model": "x", "error": ""}
    monkeypatch.setattr("app.api.media.critique_draft", fake)
    _seed_real()
    client = TestClient(app)
    r = client.post("/media/content/RT1/critique")
    assert r.json()["verdict"] == "pass"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -q`
Expected: FAIL —— 端点 404 / import 名字不存在。

- [ ] **Step 3: 扩展 media.py 的 import**

把 `app/api/media.py` 顶部：
```python
from app.services.media_ai import (
    recommend_topics, write_script, generate_platform_copy, review_content,
)
```
改为：
```python
from app.services.media_ai import (
    recommend_topics, write_script, generate_platform_copy, review_content,
    interview_questions, extract_evidence, propose_angles,
    critique_draft, revise_draft,
)
from app.services.media_flow import finalize_updates
from app.services.ai_router import _load_config
from app.config import BASE_DIR
```
（`PLATFORMS, STAGES...` 那行 media_flow import 保持不变，另起一行 import finalize_updates 即可。）

- [ ] **Step 4: 加过程端点**

在 `app/api/media.py` 的 `content_ai_script`（现有）之后插入：

```python
@router.post("/media/content/{cid}/interview")
async def content_interview(cid: str):
    db = await get_db()
    try:
        try:
            result = await interview_questions(db, cid)
        except Exception as e:
            log.exception("AI 采访提问失败")
            return JSONResponse({"ok": False, "error": str(e), "questions": []})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/evidence")
async def content_evidence(cid: str, answers: str = Form("")):
    db = await get_db()
    try:
        try:
            result = await extract_evidence(db, cid, answers)
        except Exception as e:
            log.exception("提炼素材失败")
            return JSONResponse({"ok": False, "error": str(e), "count": 0})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/angles")
async def content_angles(cid: str):
    db = await get_db()
    try:
        try:
            result = await propose_angles(db, cid)
        except Exception as e:
            log.exception("出角度失败")
            return JSONResponse({"ok": False, "error": str(e), "count": 0})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/angle/{aid}/select")
async def content_angle_select(cid: str, aid: str):
    """把某个备选角度设为选中（前端随后再触发 ai-script 重出草稿）。"""
    db = await get_db()
    try:
        await db.execute("UPDATE media_angle SET is_selected=0 WHERE content_id=?", (cid,))
        await db.execute(
            "UPDATE media_angle SET is_selected=1, status='selected' WHERE id=?", (aid,))
        await db.execute(
            "UPDATE media_content SET selected_angle_id=? WHERE id=?", (aid, cid))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/content/{cid}/critique")
async def content_critique(cid: str):
    strategy = _load_config().get("media_review_strategy", "layered")
    db = await get_db()
    try:
        try:
            result = await critique_draft(db, cid, strategy=strategy)
        except Exception as e:
            log.exception("独立审稿失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/revise")
async def content_revise(cid: str):
    db = await get_db()
    try:
        try:
            result = await revise_draft(db, cid)
        except Exception as e:
            log.exception("定向修订失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/finalize")
async def content_finalize(cid: str, script: str = Form("")):
    """定稿：人编辑后的真实版进 script，stage→scripted，authoring→finalized。
    ai_draft 不动 —— 保留 AI 草稿供功能 B 对比。"""
    updates = finalize_updates(script)
    db = await get_db()
    try:
        if updates:
            await db.execute(
                "UPDATE media_content SET script=?, stage=?, authoring_stage=?, "
                "finalized_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (updates["script"], updates["stage"], updates["authoring_stage"], cid))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/settings/review-strategy")
async def set_review_strategy(strategy: str = Form("layered")):
    """换脑审稿策略写进 settings.json。合法值 layered/swap_model/same_model。"""
    if strategy not in ("layered", "swap_model", "same_model"):
        strategy = "layered"
    path = BASE_DIR / "data" / "settings.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        cfg = {}
    cfg["media_review_strategy"] = strategy
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return RedirectResponse("/media/settings", status_code=302)
```

> 说明：`content_ai_script`（既有）现在会把草稿写进 `ai_draft`（Task 7 改的），前端"出草稿/重出草稿"仍打这个端点即可，无需改。

- [ ] **Step 5: 详情页装载新数据**

在 `content_detail` 里，`reviews = [...]` 之后、`finally` 之前，追加装载角度/证据/最近审稿：

```python
        cur = await db.execute(
            "SELECT * FROM media_angle WHERE content_id=? ORDER BY is_selected DESC, "
            "created_at", (cid,))
        angles = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_evidence WHERE content_id=? ORDER BY created_at", (cid,))
        evidence = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_draft_review WHERE content_id=? "
            "ORDER BY created_at DESC LIMIT 1", (cid,))
        drow = await cur.fetchone()
        latest_review = dict(drow) if drow else None
```

并把 `latest_review` 的 flags 反序列化（在 `finally` 之后、`return _tpl` 之前）：

```python
    if latest_review:
        for k in ("fact_flags", "persona_flags", "platform_flags",
                  "gap_flags", "risk_flags"):
            try:
                latest_review[k] = json.loads(latest_review.get(k) or "[]")
            except (json.JSONDecodeError, TypeError):
                latest_review[k] = []
```

把 `return _tpl(request, "media_content.html", {...})` 的 ctx 里补上：
```python
                 "angles": angles, "evidence": evidence,
                 "latest_review": latest_review,
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q`
Expected: PASS。

- [ ] **Step 7: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: PASS（全绿）。

- [ ] **Step 8: 提交**

```bash
git add app/api/media.py tests/test_media_routes.py
git commit -m "feat(media-A): 过程端点(采访/证据/角度/审稿/修订/定稿)+详情页装载+换脑策略设置"
```

---

## Task 10: 模板 — 内容详情页"创作区" + 设置页换脑策略下拉

**Files:**
- Modify: `templates/media_content.html`
- Modify: 设置页模板（先 `grep` 定位：`grep -rl "media/settings" templates/` 或找渲染 `/media/settings` 的模板；若无独立设置页，则加到内容详情页顶部一个小设置条）
- Test: 人工/live 验证（模板不做单测，遵循项目惯例：TestClient live 点一遍）

**Interfaces:**
- Consumes：模板上下文 `content`（含 `ai_draft/script/evidence_gap/authoring_stage/selected_angle_id`）、`angles`、`evidence`、`latest_review`。
- Produces：详情页"创作区"UI + JS 调各过程端点。

- [ ] **Step 1: 读现有模板结构**

Run: `sed -n '1,60p' templates/media_content.html`（或 Read 工具），确认现有脚本编辑区（`/media/content/{cid}/script` 表单）位置，"创作区"插在脚本区之前。

- [ ] **Step 2: 加"创作区"HTML（stage=idea 时展示）**

在脚本编辑表单之前插入以下块（用 Edit 工具，禁 PowerShell）。颜色用 violet-600(AI)/blue-600(主)/amber-600(警告)，响应式用 `@media (max-width:767px)`：

```html
{% if content.stage == 'idea' %}
<section class="mb-6 rounded-lg border border-gray-200 bg-white p-4">
  <div class="flex items-center justify-between mb-3">
    <h3 class="text-base font-semibold">✍️ 创作区</h3>
    <span class="text-xs text-gray-400">
      {{ {'none':'未出稿','drafted':'AI已出草稿','finalized':'已定稿'}[content.authoring_stage or 'none'] }}
    </span>
  </div>

  <!-- AI 草稿 -->
  <div class="mb-4">
    <div class="flex gap-2 mb-2">
      <button onclick="aiDraft()" class="px-3 py-1.5 rounded bg-violet-600 text-white text-sm">
        {% if content.ai_draft %}重出草稿{% else %}AI 出草稿{% endif %}
      </button>
      <button onclick="interview()" class="px-3 py-1.5 rounded border border-violet-600 text-violet-600 text-sm">采访我补料</button>
      <button onclick="genAngles()" class="px-3 py-1.5 rounded border border-gray-300 text-sm">换角度</button>
      <button onclick="critique()" class="px-3 py-1.5 rounded border border-gray-300 text-sm">审稿</button>
    </div>
    {% if content.evidence_gap %}
    <div class="mb-2 rounded bg-amber-50 border border-amber-200 text-amber-700 text-sm p-2">
      ⚠️ AI 标了缺真料：{{ content.evidence_gap }}（去"采访我补料"补上，别让它编）
    </div>
    {% endif %}
    <div id="draft-box" class="whitespace-pre-wrap text-sm text-gray-700 bg-gray-50 rounded p-3 min-h-[80px]">{{ content.ai_draft or 'AI 还没出草稿，点上面"AI 出草稿"。' }}</div>
  </div>

  <!-- 候选角度 -->
  {% if angles %}
  <details class="mb-3"><summary class="cursor-pointer text-sm text-gray-600">候选角度（{{ angles|length }}）</summary>
    <div class="mt-2 space-y-1">
      {% for a in angles %}
      <div class="text-sm flex items-start gap-2">
        <button onclick="selectAngle('{{ a.id }}')" class="shrink-0 px-2 py-0.5 rounded text-xs {% if a.is_selected %}bg-blue-600 text-white{% else %}border border-gray-300{% endif %}">
          {% if a.is_selected %}已选{% else %}选它{% endif %}
        </button>
        <span>{{ a.angle }}<span class="text-gray-400"> —— {{ a.rationale }}</span></span>
      </div>
      {% endfor %}
    </div>
  </details>
  {% endif %}

  <!-- 审稿意见 -->
  {% if latest_review %}
  <details class="mb-3"><summary class="cursor-pointer text-sm text-gray-600">
    审稿意见 · {{ latest_review.score }}/5 · {{ {'pass':'可用','revise':'建议改一次','reject':'建议回素材/角度'}[latest_review.verdict] }}</summary>
    <div class="mt-2 text-sm space-y-1">
      {% for label, key in [('事实存疑','fact_flags'),('不像本人','persona_flags'),('平台适配','platform_flags'),('缺真料','gap_flags'),('风险','risk_flags')] %}
        {% for f in latest_review[key] %}<div>· <span class="text-amber-600">[{{ label }}]</span> {{ f }}</div>{% endfor %}
      {% endfor %}
      {% if latest_review.notes %}<div class="text-gray-500 mt-1">总评：{{ latest_review.notes }}</div>{% endif %}
      {% if content.revision_count == 0 %}
      <button onclick="revise()" class="mt-2 px-3 py-1 rounded bg-violet-600 text-white text-xs">照审稿改一次</button>
      {% else %}
      <div class="mt-2 text-gray-400 text-xs">已定向修订过一次；再不满意请回到素材或角度。</div>
      {% endif %}
    </div>
  </details>
  {% endif %}

  <!-- 素材包 -->
  {% if evidence %}
  <details class="mb-1"><summary class="cursor-pointer text-sm text-gray-600">素材包（{{ evidence|length }} 条真料）</summary>
    <div class="mt-2 text-sm space-y-1">
      {% for e in evidence %}<div>· <span class="text-gray-400">[{{ e.item_type }}]</span> {{ e.item }}</div>{% endfor %}
    </div>
  </details>
  {% endif %}

  <p class="mt-3 text-xs text-gray-400">满意就把草稿复制到下面脚本框，改成你要念的话，保存即定稿。</p>
</section>
{% endif %}
```

- [ ] **Step 3: 加创作区 JS**

在模板底部 `{% block scripts %}`（或现有 `<script>` 区）加：

```html
<script>
const CID = "{{ content.id }}";
async function _post(url, body){
  const r = await fetch(url, {method:'POST', body: body});
  return await r.json();
}
async function aiDraft(){
  document.getElementById('draft-box').textContent = 'AI 正在出草稿…';
  const j = await _post(`/media/content/${CID}/ai-script`, new URLSearchParams({mode:'full'}));
  if(j.ok){ location.reload(); } else { alert(j.error || '出草稿失败'); }
}
async function interview(){
  const j = await _post(`/media/content/${CID}/interview`, null);
  if(!j.ok){ alert(j.error||'采访失败'); return; }
  const answers = prompt('AI 想问你（一次性答完，尽量给真事和数字）：\n\n' + j.questions.map((q,i)=>`${i+1}. ${q}`).join('\n'));
  if(answers && answers.trim()){
    const e = await _post(`/media/content/${CID}/evidence`, new URLSearchParams({answers}));
    if(e.ok){ alert(`已补 ${e.count} 条真料，重出草稿看看`); location.reload(); }
    else { alert(e.error||'提炼失败'); }
  }
}
async function genAngles(){
  const j = await _post(`/media/content/${CID}/angles`, null);
  if(j.ok){ location.reload(); } else { alert(j.error||'出角度失败'); }
}
async function selectAngle(aid){
  await _post(`/media/content/${CID}/angle/${aid}/select`, null);
  await aiDraft();  // 换角度后重出草稿
}
async function critique(){
  const j = await _post(`/media/content/${CID}/critique`, null);
  if(j.ok){ location.reload(); } else { alert(j.error||'审稿失败'); }
}
async function revise(){
  const j = await _post(`/media/content/${CID}/revise`, null);
  if(j.ok){ location.reload(); } else { alert(j.error||'修订失败'); }
}
</script>
```

- [ ] **Step 4: 脚本保存表单改指向定稿端点**

把现有脚本编辑表单的 `action` 从 `/media/content/{{ content.id }}/script` 改为 `/media/content/{{ content.id }}/finalize`（字段名保持 `script`）。若该表单还负责 edit_note/cover_idea 的保存，则保留原 `/script` 表单不动，另在其下方加一个只含 `script` 的"定稿"按钮 form 指向 `/finalize`——二选一，取决于现有表单结构（Step 1 已读）。**推荐：** 保留 `/script` 存草稿态编辑，新增独立"✅ 定稿"按钮 form 指向 `/finalize`，语义更清晰（存 ≠ 定稿）。

```html
<form method="post" action="/media/content/{{ content.id }}/finalize" class="mt-2">
  <input type="hidden" name="script" id="finalize-script-mirror">
  <button type="submit" onclick="document.getElementById('finalize-script-mirror').value=document.querySelector('[name=script]').value"
          class="px-4 py-2 rounded bg-blue-600 text-white text-sm">✅ 定稿（推进到"脚本"）</button>
</form>
```

- [ ] **Step 5: 设置页加换脑策略下拉**

在设置页模板（Step 定位到的文件）合适位置加：

```html
<form method="post" action="/media/settings/review-strategy" class="flex items-center gap-2">
  <label class="text-sm text-gray-600">审稿独立性</label>
  <select name="strategy" class="border rounded px-2 py-1 text-sm">
    <option value="layered" {% if review_strategy=='layered' %}selected{% endif %}>分层（默认·省）</option>
    <option value="swap_model" {% if review_strategy=='swap_model' %}selected{% endif %}>换模型（最独立·贵）</option>
    <option value="same_model" {% if review_strategy=='same_model' %}selected{% endif %}>同模型分身（最省）</option>
  </select>
  <button class="px-3 py-1 rounded bg-blue-600 text-white text-sm">保存</button>
</form>
```

并在渲染设置页的路由 ctx 里加 `"review_strategy": _load_config().get("media_review_strategy", "layered")`。

- [ ] **Step 6: live 验证（起 dev server 点一遍）**

Run: `python run.py`（另开终端）→ 浏览器开 `http://localhost:8000/media`，走一遍：
1. 新建/打开一条 idea 内容 → 点"AI 出草稿" → 草稿出现（若配了真 key）。
2. 若草稿有"⚠️ AI 标了缺真料" → 点"采访我补料" → 答问题 → 重出草稿缺口消失。
3. 点"换角度" → 出候选 → 选一个 → 草稿按新角度重出。
4. 点"审稿" → 审稿意见展开，含分数/verdict → 点"照审稿改一次" → 草稿更新，再点"照审稿改一次"变灰。
5. 复制草稿到脚本框、改两句 → 点"✅ 定稿" → 卡片进入"脚本"列，`script`=你改的、`ai_draft`=AI 那版（可在 DB 或详情核对）。
6. 设置页切"换模型" → 再审稿 → 审稿意见的模型与写稿不同（看 `media_draft_review.reviewer_model`）。

- [ ] **Step 7: 提交**

```bash
git add templates/media_content.html templates/  # 含设置页模板
git commit -m "feat(media-A): 详情页创作区(草稿/采访/角度/审稿/定稿)+设置页换脑策略下拉"
```

---

## Self-Review（写计划后自查，已核对）

**1. Spec 覆盖：**
- §3 默认两步/补救工具 → Task 7(默认出稿) + Task 5/6/8(采访/角度/审稿补救) + Task 10(UI 默认两步、工具按需)。✅
- §3.4 缺料不编造 → Task 7 `SCRIPT_SYSTEM` 红线 + `extract_gap_markers` + `evidence_gap`。✅
- §4 状态机做法1 → Task 2 `authoring_stage`/`finalize_updates` + Task 10 详情页展开、看板不加列。✅
- §5.1 media_content 新列 → Task 1。✅ §5.2/5.3/5.4 三过程表 → Task 1。✅ §5.5 原料库雏形 → Task 1 建表 + Task 3 `select_materials` + Task 7 出稿前读 + （定稿后"建议入库"人拍板：见下"已知缺口"）。
- §6.1 六函数 → Task 5/6/7/8。✅ §6.2 创作≠审稿隔离 → Task 8 独立调用+只挑毛病+`revise` 单独。✅ §6.3 三策略 → Task 4 + Task 9 设置 + Task 10 下拉。✅ §6.4 注入预算复用 → Task 3/7 走 `select_by_budget`/`select_materials` + `log_injection` 新 ai_type。✅
- §7 ip 方法论翻译进 prompt → Task 5/6/7/8 各 SYSTEM 常量。✅
- §8 回流：采访→evidence→（建议入原料库）；定稿差异 ai_draft vs script 留存 → Task 7/9。✅（风格学习属功能 B，不做）
- §10 UI → Task 10。✅ §12 验收 → 各 Task 测试 + Task 10 Step 6 live。✅

**2. 占位扫描：** 无 TBD/TODO/"类似 Task N"。所有代码步含完整代码。✅

**3. 类型一致：** `write_script`/`critique_draft`/`revise_draft`/`propose_angles`/`interview_questions`/`extract_evidence` 返回 dict 键在测试与路由中一致；`resolve_reviewer_model(strategy, writer_model, providers)` 三参一致；`finalize_updates(script)->dict` 键 `script/stage/authoring_stage` 一致。✅

**已知缺口（明确留给执行者或后续，非遗漏）：**
- **定稿后"AI 建议把好素材入原料库（人拍板）"** —— spec §5.5 链路2。本计划建了 `media_material` 表、打通了"出稿前读"（Task 7），但"定稿后 AI 提炼 evidence → 建议入库 → 人确认写 media_material（回填 promoted_to_material_id）"这条**未排任务**。原因：属"沉淀"动作、不阻断生产线跑通，且需一个额外 AI 提炼 + 确认 UI。**建议作为 Task 11 独立追加**（可复用 review_content 里"proposed→人 adopt"的成熟模式）。执行本计划到 Task 10 即可端到端跑通 🅐 主线；Task 11 让飞轮"越用越省心"闭合。若执行时决定纳入，模式：新 AI 函数 `promote_evidence(db, content_id)` 出候选 → 详情页"建议入原料库"列表 → 复用类似 `/media/content/{cid}/adopt-trait` 的 adopt 端点写 media_material。

---

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-08-04-media-phase2-A-single-content-pipeline.md`。

**执行前置提醒（写进了记忆）：** 🅐 跑通不依赖人设框架，但**跑好**需要人设 traits 就绪（人设声音是 write_script 的注入源）+ 结构曲库（依赖 80 条历史文案，属打法库🅑，本计划不含）。可先梳理人设或先实现机器，顺序随意。

两种执行方式：
1. **Subagent-Driven（推荐）** —— 每个 Task 派一个新 subagent 实现，Task 间两段式审查，快速迭代。
2. **Inline 执行** —— 本会话内按 executing-plans 批量执行，检查点审查。
