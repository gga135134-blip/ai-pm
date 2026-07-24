# 自媒体运营系统 一期（闭环骨架）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ai-pm 中搭出自媒体运营的完整闭环骨架 —— 一条内容能走完「选题 → 脚本 → 录 → 剪 → 三平台发布 → 数据 → 复盘」并沉淀人设条目。

**Architecture:** 独立 `media` 模块，仿现有 `study` 模块结构。核心业务逻辑（状态机、注入预算、数据归一化）写成**纯函数**放在 service 层，可直接用 pytest 单元测试，不需要 async 测试框架；async DB 访问只做薄封装。所有 AI 调用走现有 `ai_router.ask_ai` / `ask_ai_vision`。

**Tech Stack:** Python 3 + FastAPI + Jinja2 + aiosqlite + 本地 Tailwind CSS + vanilla JS + pytest

**Spec:** `docs/superpowers/specs/2026-07-24-media-ops-system-design.md`

---

## Global Constraints

以下约束适用于**每一个** Task，不再逐条重复：

- **不新增 Python 依赖。** 现有 `requirements.txt` 已够用。特别注意：项目**没有 pytest-asyncio**，所以所有单元测试必须是同步的纯函数测试。需要测 DB 的用 `sqlite3` 同步内存库测 SCHEMA。
- **响应式用 `@media (max-width: 767px)` 自定义 CSS，禁用 Tailwind `md:` 断点**（本地裁剪版 tailwind.min.css 不含）。移动端使用频率可能高于桌面端。
- **颜色只用：** `blue-600`（主操作）、`violet-600`（AI 专属）、`amber-600`（警告）、`gray-*`、`green-500`、`red-500`。用其他颜色前先 `grep` 确认 `app/static/tailwind.min.css` 里存在该色阶，不存在则在 `base.html` 的 `<style>` 里补。
- **`TemplateResponse` 必须三参数**：`request.app.state.templates.TemplateResponse(request, "name.html", ctx)`。省略 `request` 会报 `unhashable type: dict`。
- **Jinja2 无 `tojson` 过滤器**。传给前端 JS 的数据在 Python 侧用 `json.dumps()` 预序列化，模板里用 `{{ var | safe }}`。
- **禁止用 PowerShell 的 `-replace` 改 UTF-8 模板文件**（中文会变乱码）。一律用 Edit/Write 工具。
- **DB 访问模式**（照抄现有代码）：
  ```python
  db = await get_db()
  try:
      ...
  finally:
      await db.close()
  ```
- **新表加进 `app/database.py` 的 `SCHEMA` 常量**；对已存在表加字段才走 `MIGRATIONS` 列表。
- **ID 一律 `str(uuid.uuid4())`。**
- **AI 调用一律走 `app.services.ai_router.ask_ai(...)`**，不要直接调 anthropic/openai SDK。识图走 `ask_ai_vision`。
- 测试命令统一：`cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/database.py` | 加 10 张 media 表到 SCHEMA | Modify |
| `app/services/media_flow.py` | 常量 + 状态机（**纯函数**） | Create |
| `app/services/media_context.py` | 注入预算 + 上下文拼装 + AI 输出 JSON 解析（**主体纯函数**） | Create |
| `app/services/media_ai.py` | 4 个 AI 能力：推选题 / 写脚本 / 生成平台文案 / L1 复盘 | Create |
| `app/services/media_metrics.py` | 数据采集：截图识图 / 手填 / 归一化（**归一化是纯函数**） | Create |
| `app/api/media.py` | 全部路由 | Create |
| `app/templates/media_board.html` | 内容看板 | Create |
| `app/templates/media_content.html` | 内容详情（一条内容的一生） | Create |
| `app/templates/media_persona.html` | 人设档案 | Create |
| `app/templates/media_topics.html` | 话题库 | Create |
| `app/templates/base.html` | 导航加入口 | Modify |
| `app/main.py` | 注册 router | Modify |
| `tests/test_media_schema.py` | SCHEMA 建表验证 | Create |
| `tests/test_media_flow.py` | 状态机单元测试 | Create |
| `tests/test_media_context.py` | 注入预算 + JSON 解析单元测试 | Create |
| `tests/test_media_metrics.py` | 数据归一化单元测试 | Create |

**拆分理由：** `media_flow` / `media_context` / `media_metrics` 的核心逻辑是纯函数，与 DB 和 AI 解耦，能被完整单元测试 —— 这三块（状态流转、注入预算、数据归一化）恰恰是最容易出隐蔽 bug 的地方。`media_ai.py` 只负责拼 prompt 和调用，逻辑薄。

---

## Task 1: 数据库 Schema（10 张表）

**Files:**
- Modify: `app/database.py`（在 `SCHEMA` 字符串末尾追加，`"""` 闭合之前）
- Test: `tests/test_media_schema.py`

**Interfaces:**
- Consumes: 无
- Produces: 10 张表可供后续所有 Task 读写。表名与字段名以本 Task 为准。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_media_schema.py`：

```python
import sqlite3
from app.database import SCHEMA

EXPECTED = {
    "media_persona", "media_persona_trait", "media_account",
    "media_topic", "media_content", "media_publish", "media_metrics",
    "media_review", "media_case", "media_injection_log",
}


def _tables():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _cols(table):
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_schema_creates_all_media_tables():
    assert EXPECTED <= _tables()


def test_trait_has_brief_and_confidence():
    # brief 是注入预算机制的基础，confidence 是截断排序依据
    cols = _cols("media_persona_trait")
    assert {"brief", "confidence", "dimension", "status", "persona_id"} <= cols


def test_content_has_fingerprint_and_outcome():
    # 三期查重与归因分析依赖这两个字段，一期就必须写入
    cols = _cols("media_content")
    assert {"topic_fingerprint", "outcome", "stage", "puzzle", "persona_id"} <= cols


def test_topic_has_puzzle_and_reject_reason():
    cols = _cols("media_topic")
    assert {"puzzle", "rejected_reason", "status", "decision_score"} <= cols


def test_metrics_hangs_off_publish():
    cols = _cols("media_metrics")
    assert {"publish_id", "views", "likes", "comments", "shares",
            "new_fans", "collected_by", "snapshot_at"} <= cols


def test_injection_log_records_assets_and_tokens():
    cols = _cols("media_injection_log")
    assert {"content_id", "ai_type", "injected_asset_ids", "token_count"} <= cols


def test_case_has_replicable_and_factors():
    cols = _cols("media_case")
    assert {"replicable", "topic_factor", "hook_factor", "case_type"} <= cols
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_schema.py -v`
Expected: FAIL — `assert EXPECTED <= _tables()` 为 False（media_* 表不存在）

- [ ] **Step 3: 在 `app/database.py` 的 SCHEMA 末尾追加建表语句**

在 `SCHEMA = """` 字符串内、结尾 `"""` 之前追加：

```sql
CREATE TABLE IF NOT EXISTS media_persona (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    one_liner TEXT DEFAULT '',
    current_phase TEXT DEFAULT '冷启动',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_persona_trait (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    dimension TEXT DEFAULT 'positioning',
    content TEXT DEFAULT '',
    brief TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    source_content_id TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    confidence INTEGER DEFAULT 3,
    phase_tag TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_account (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_name TEXT DEFAULT '',
    account_url TEXT DEFAULT '',
    fans_count INTEGER DEFAULT 0,
    platform_note TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_topic (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    title TEXT NOT NULL,
    puzzle TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    reason TEXT DEFAULT '',
    angle TEXT DEFAULT '',
    heat INTEGER DEFAULT 3,
    fit_score INTEGER DEFAULT 3,
    decision_score REAL DEFAULT 0,
    decision_report TEXT DEFAULT '',
    related_trait_ids TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pool',
    adopted_content_id TEXT DEFAULT '',
    rejected_reason TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_content (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    title TEXT NOT NULL,
    puzzle TEXT DEFAULT '',
    stage TEXT DEFAULT 'idea',
    idea_source TEXT DEFAULT 'manual',
    idea_reason TEXT DEFAULT '',
    script TEXT DEFAULT '',
    edit_note TEXT DEFAULT '',
    cover_idea TEXT DEFAULT '',
    used_material_ids TEXT DEFAULT '[]',
    used_playbook_ids TEXT DEFAULT '[]',
    topic_fingerprint TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    archived_status TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_publish (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    publish_text TEXT DEFAULT '',
    published_at DATETIME,
    post_url TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    FOREIGN KEY (content_id) REFERENCES media_content(id),
    FOREIGN KEY (account_id) REFERENCES media_account(id)
);

CREATE TABLE IF NOT EXISTS media_metrics (
    id TEXT PRIMARY KEY,
    publish_id TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    new_fans INTEGER DEFAULT 0,
    collected_by TEXT DEFAULT 'manual',
    snapshot_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (publish_id) REFERENCES media_publish(id)
);

CREATE TABLE IF NOT EXISTS media_review (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    scope TEXT DEFAULT 'overall',
    account_id TEXT DEFAULT '',
    what_worked TEXT DEFAULT '',
    what_failed TEXT DEFAULT '',
    next_action TEXT DEFAULT '',
    proposed_traits TEXT DEFAULT '[]',
    generated_by TEXT DEFAULT 'ai',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_case (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    content_id TEXT NOT NULL,
    case_type TEXT DEFAULT 'normal',
    threshold_basis TEXT DEFAULT '',
    topic_factor TEXT DEFAULT '',
    hook_factor TEXT DEFAULT '',
    structure_factor TEXT DEFAULT '',
    material_factor TEXT DEFAULT '',
    emotion_factor TEXT DEFAULT '',
    platform_factor TEXT DEFAULT '',
    external_factor TEXT DEFAULT '',
    replicable INTEGER DEFAULT 3,
    conclusion TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_injection_log (
    id TEXT PRIMARY KEY,
    content_id TEXT DEFAULT '',
    ai_type TEXT NOT NULL,
    injected_asset_ids TEXT DEFAULT '[]',
    token_count INTEGER DEFAULT 0,
    output_quality REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_schema.py -v`
Expected: 7 passed

- [ ] **Step 5: 确认没有破坏既有测试**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed（原有 study 测试不受影响）

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_media_schema.py
git commit -m "feat(media): 一期 10 张表 schema

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 状态机与常量（media_flow.py）

**Files:**
- Create: `app/services/media_flow.py`
- Test: `tests/test_media_flow.py`

**Interfaces:**
- Consumes: 无（纯常量与纯函数）
- Produces:
  - `STAGES: list[str]` — 7 个阶段，顺序即流转顺序
  - `STAGE_LABELS: dict[str, str]` — 阶段 → 中文标签
  - `PLATFORMS: dict[str, str]` — 平台 code → 中文名
  - `stage_index(stage: str) -> int` — 不存在返回 -1
  - `next_stage(current: str) -> str | None` — 末态或非法返回 None
  - `can_transition(frm: str, to: str) -> bool` — 只允许前进一步或后退任意步
  - `is_published(stage: str) -> bool`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_media_flow.py`：

```python
from app.services.media_flow import (
    STAGES, STAGE_LABELS, PLATFORMS,
    stage_index, next_stage, can_transition, is_published,
)


def test_stages_order_is_the_content_lifecycle():
    assert STAGES == ["idea", "scripted", "recording",
                      "editing", "ready", "published", "reviewed"]


def test_every_stage_has_a_chinese_label():
    assert set(STAGE_LABELS) == set(STAGES)
    assert STAGE_LABELS["idea"] == "选题"
    assert STAGE_LABELS["reviewed"] == "已复盘"


def test_three_platforms_present():
    assert PLATFORMS == {"douyin": "抖音", "xhs": "小红书", "shipinhao": "视频号"}


def test_stage_index():
    assert stage_index("idea") == 0
    assert stage_index("reviewed") == 6
    assert stage_index("nonsense") == -1


def test_next_stage_advances_one_step():
    assert next_stage("idea") == "scripted"
    assert next_stage("ready") == "published"


def test_next_stage_at_terminal_is_none():
    assert next_stage("reviewed") is None


def test_next_stage_unknown_is_none():
    assert next_stage("nonsense") is None


def test_can_advance_exactly_one_step():
    assert can_transition("idea", "scripted") is True


def test_cannot_skip_stages_forward():
    # 不能跳步：选题不能直接跳到已发
    assert can_transition("idea", "published") is False


def test_can_go_back_any_number_of_steps():
    # 允许退回：发现脚本要重写，可以从待剪退回脚本
    assert can_transition("editing", "scripted") is True
    assert can_transition("published", "idea") is True


def test_cannot_transition_to_self():
    assert can_transition("idea", "idea") is False


def test_cannot_transition_with_unknown_stage():
    assert can_transition("idea", "nonsense") is False
    assert can_transition("nonsense", "idea") is False


def test_is_published_covers_published_and_reviewed():
    assert is_published("published") is True
    assert is_published("reviewed") is True
    assert is_published("ready") is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.media_flow'`

- [ ] **Step 3: 实现 `app/services/media_flow.py`**

```python
"""自媒体模块的常量与状态机。全部为纯函数，无 DB / AI 依赖。"""

STAGES = ["idea", "scripted", "recording", "editing", "ready", "published", "reviewed"]

STAGE_LABELS = {
    "idea": "选题",
    "scripted": "脚本",
    "recording": "待录",
    "editing": "待剪",
    "ready": "待发",
    "published": "已发",
    "reviewed": "已复盘",
}

PLATFORMS = {"douyin": "抖音", "xhs": "小红书", "shipinhao": "视频号"}


def stage_index(stage: str) -> int:
    """阶段在流程中的位置。未知阶段返回 -1。"""
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


def next_stage(current: str) -> str | None:
    """下一个阶段。已是末态或阶段未知时返回 None。"""
    i = stage_index(current)
    if i < 0 or i >= len(STAGES) - 1:
        return None
    return STAGES[i + 1]


def can_transition(frm: str, to: str) -> bool:
    """前进只允许一步（防跳步漏工序），后退允许任意步（返工是正常的）。"""
    a, b = stage_index(frm), stage_index(to)
    if a < 0 or b < 0 or a == b:
        return False
    if b > a:
        return b == a + 1
    return True


def is_published(stage: str) -> bool:
    """是否已经发出去了（已发或已复盘）。"""
    return stage_index(stage) >= stage_index("published")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_flow.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/media_flow.py tests/test_media_flow.py
git commit -m "feat(media): 内容状态机与平台常量

前进限一步防跳步漏工序，后退不限步因为返工是正常的。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 注入预算与 AI 输出解析（media_context.py）

> 这是整套系统最关键的约束模块 —— spec §6 的落点。体系可以无限重的前提，就是这里的预算截断永远生效。必须先于任何 AI 调用完成并测透。

**Files:**
- Create: `app/services/media_context.py`
- Test: `tests/test_media_context.py`

**Interfaces:**
- Consumes: 无（核心为纯函数）
- Produces:
  - `INJECTION_BUDGET: dict[str, int]` — 各槽位硬上限
  - `select_by_budget(items: list[dict], slot: str, score_key: str = "confidence") -> list[dict]` — 按分数降序截断
  - `render_brief_list(items: list[dict], label: str) -> str` — 渲染 brief 清单文本
  - `build_script_context(persona: dict, traits: list[dict]) -> tuple[str, list[str]]` — 返回 `(注入文本, 注入的资产id列表)`
  - `extract_json(text: str, expect: str = "object") -> dict | list` — 稳健提取 AI 输出的 JSON，失败返回空容器
  - `async log_injection(db, content_id, ai_type, asset_ids, token_count) -> None` — 写 `media_injection_log`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_media_context.py`：

```python
from app.services.media_context import (
    INJECTION_BUDGET, select_by_budget, render_brief_list,
    build_script_context, extract_json,
)


# ---------- 预算截断 ----------

def test_budget_has_hard_caps_for_every_slot():
    # 这些上限是"体系可以无限重"的前提，改动需同步改 spec §6
    assert INJECTION_BUDGET["trait"] == 8
    assert INJECTION_BUDGET["signature"] == 3
    assert INJECTION_BUDGET["playbook"] == 2
    assert INJECTION_BUDGET["material"] == 3
    assert INJECTION_BUDGET["lesson"] == 3
    assert INJECTION_BUDGET["audience"] == 1


def test_select_truncates_to_budget():
    items = [{"id": f"t{i}", "brief": f"b{i}", "confidence": 1} for i in range(20)]
    assert len(select_by_budget(items, "trait")) == 8


def test_select_keeps_highest_scores_first():
    items = [
        {"id": "low", "brief": "低", "confidence": 1},
        {"id": "high", "brief": "高", "confidence": 5},
        {"id": "mid", "brief": "中", "confidence": 3},
    ]
    got = select_by_budget(items, "signature")  # 上限 3，全保留但要排序
    assert [i["id"] for i in got] == ["high", "mid", "low"]


def test_select_under_budget_returns_all():
    items = [{"id": "a", "brief": "x", "confidence": 2}]
    assert len(select_by_budget(items, "trait")) == 1


def test_select_on_empty_returns_empty():
    assert select_by_budget([], "trait") == []


def test_select_unknown_slot_returns_empty():
    # 未定义预算的槽位不允许注入，防止有人偷偷加注入点绕过预算
    items = [{"id": "a", "brief": "x", "confidence": 2}]
    assert select_by_budget(items, "not_a_slot") == []


def test_select_handles_missing_score_key():
    items = [{"id": "a", "brief": "x"}, {"id": "b", "brief": "y", "confidence": 5}]
    got = select_by_budget(items, "trait")
    assert [i["id"] for i in got] == ["b", "a"]


def test_select_custom_score_key():
    items = [
        {"id": "a", "brief": "x", "hit_rate": 0.2},
        {"id": "b", "brief": "y", "hit_rate": 0.9},
    ]
    got = select_by_budget(items, "playbook", score_key="hit_rate")
    assert [i["id"] for i in got] == ["b", "a"]


# ---------- brief 渲染 ----------

def test_render_brief_list_uses_brief_not_detail():
    # 注入提示词的是 brief（≤30字），detail 要 AI 自己调工具读
    items = [{"id": "t1", "brief": "3秒进主题", "content": "这里是800字的完整方法论……"}]
    out = render_brief_list(items, "人设条目")
    assert "3秒进主题" in out
    assert "800字的完整方法论" not in out


def test_render_brief_falls_back_to_truncated_content():
    items = [{"id": "t1", "brief": "", "content": "x" * 200}]
    out = render_brief_list(items, "人设条目")
    assert len(out) < 120  # 没有 brief 时截断 content，不能整段灌进去


def test_render_brief_list_empty_returns_empty_string():
    assert render_brief_list([], "人设条目") == ""


# ---------- 上下文拼装 ----------

def _persona():
    return {"id": "p1", "name": "小雅", "one_liner": "陪职场妈妈喘口气",
            "current_phase": "冷启动"}


def test_build_script_context_includes_persona_identity():
    text, ids = build_script_context(_persona(), [])
    assert "小雅" in text
    assert "陪职场妈妈喘口气" in text


def test_build_script_context_returns_injected_ids():
    traits = [{"id": "t1", "brief": "语气像闺蜜", "dimension": "tone", "confidence": 5}]
    text, ids = build_script_context(_persona(), traits)
    assert ids == ["t1"]


def test_build_script_context_separates_signature_from_trait_budget():
    # signature 是记忆点，必须单独占槽，不能被普通条目挤掉
    traits = [{"id": f"t{i}", "brief": f"普通{i}", "dimension": "positioning",
               "confidence": 5} for i in range(20)]
    traits += [{"id": "sig1", "brief": "开场必说'姐妹们'", "dimension": "signature",
                "confidence": 1}]
    text, ids = build_script_context(_persona(), traits)
    assert "sig1" in ids          # 置信度最低但仍被注入
    assert "开场必说" in text


def test_build_script_context_total_never_exceeds_budget():
    traits = [{"id": f"t{i}", "brief": f"普通{i}", "dimension": "positioning",
               "confidence": 3} for i in range(50)]
    traits += [{"id": f"s{i}", "brief": f"记忆点{i}", "dimension": "signature",
                "confidence": 3} for i in range(50)]
    text, ids = build_script_context(_persona(), traits)
    assert len(ids) <= INJECTION_BUDGET["trait"] + INJECTION_BUDGET["signature"]


def test_build_script_context_ignores_archived_traits():
    traits = [
        {"id": "old", "brief": "过时的定位", "dimension": "positioning",
         "confidence": 5, "status": "archived"},
        {"id": "new", "brief": "现在的定位", "dimension": "positioning",
         "confidence": 3, "status": "active"},
    ]
    text, ids = build_script_context(_persona(), traits)
    assert ids == ["new"]
    assert "过时的定位" not in text


# ---------- JSON 解析 ----------

def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fenced_block():
    raw = 'AI 的废话\n```json\n{"a": 1}\n```\n收尾废话'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_from_fence_without_lang():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_array_when_expected():
    assert extract_json('[{"a": 1}]', expect="array") == [{"a": 1}]


def test_extract_json_array_from_surrounding_prose():
    raw = '这是我的推荐：\n[{"title": "谜题式选题"}]\n希望有帮助'
    assert extract_json(raw, expect="array") == [{"title": "谜题式选题"}]


def test_extract_json_allows_real_newlines_in_strings():
    # DeepSeek 常在字符串里直接输出真实换行，strict=False 必须开
    assert extract_json('{"script": "第一行\n第二行"}')["script"] == "第一行\n第二行"


def test_extract_json_failure_returns_empty_object():
    assert extract_json("完全不是 JSON 的一段话") == {}


def test_extract_json_failure_returns_empty_array_when_array_expected():
    assert extract_json("完全不是 JSON 的一段话", expect="array") == []


def test_extract_json_empty_input():
    assert extract_json("") == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.media_context'`

- [ ] **Step 3: 实现 `app/services/media_context.py`**

```python
"""media 模块的 AI 输入输出管道：注入预算控制、上下文拼装、AI 输出解析。

设计约束（spec §6）：体系重在库里，不在提示词里。
任何一次 AI 调用看到的资产不超过 INJECTION_BUDGET 的总和。
体系涨到 500 条资产，注入量恒定不变 —— 这是"体系可以无限重"的前提。
"""
import json
import logging
import re
import uuid

log = logging.getLogger(__name__)

# 各注入槽位的硬上限。改动这里需同步更新 spec §6。
INJECTION_BUDGET = {
    "trait": 8,       # 人设条目，按 confidence 降序
    "signature": 3,   # 记忆点，单独占槽，少而硬
    "playbook": 2,    # 二期：只给最匹配的已验证打法
    "material": 3,    # 二期：只给未用过且最匹配的原料
    "lesson": 3,      # 二期：只给 trigger_context 命中的
    "audience": 1,    # 二期：只注本条瞄准的那个 segment
}

# 没有 brief 时，content 的截断长度
_BRIEF_FALLBACK_CHARS = 40


def select_by_budget(items: list[dict], slot: str,
                     score_key: str = "confidence") -> list[dict]:
    """按分数降序取前 N 条，N 由 INJECTION_BUDGET[slot] 决定。

    未在预算表中登记的槽位一律返回空 —— 防止新增注入点时绕过预算。
    """
    cap = INJECTION_BUDGET.get(slot)
    if not cap:
        log.warning("select_by_budget: 未登记的注入槽位 %s，拒绝注入", slot)
        return []
    ranked = sorted(items, key=lambda i: i.get(score_key) or 0, reverse=True)
    return ranked[:cap]


def render_brief_list(items: list[dict], label: str) -> str:
    """渲染成 brief 清单。只放 brief，detail 留给 AI 按需读取。"""
    if not items:
        return ""
    lines = [f"【{label}】"]
    for i in items:
        brief = (i.get("brief") or "").strip()
        if not brief:
            brief = (i.get("content") or "").strip()[:_BRIEF_FALLBACK_CHARS]
        if brief:
            lines.append(f"- {brief}")
    return "\n".join(lines) if len(lines) > 1 else ""


def build_script_context(persona: dict, traits: list[dict]) -> tuple[str, list[str]]:
    """拼装写脚本用的上下文。返回 (注入文本, 注入的资产 id 列表)。

    signature（记忆点）单独占预算槽，不与普通条目竞争 ——
    记忆点是 IP 资产的核心，绝不能被高置信度的普通条目挤掉。
    """
    active = [t for t in traits if (t.get("status") or "active") == "active"]
    signatures = [t for t in active if t.get("dimension") == "signature"]
    others = [t for t in active if t.get("dimension") != "signature"]

    picked_sig = select_by_budget(signatures, "signature")
    picked_other = select_by_budget(others, "trait")

    parts = [
        f"【人设】{persona.get('name', '')}｜{persona.get('one_liner', '')}"
        f"｜当前阶段：{persona.get('current_phase', '')}"
    ]
    other_text = render_brief_list(picked_other, "人设条目")
    if other_text:
        parts.append(other_text)
    sig_text = render_brief_list(picked_sig, "记忆点（必须植入）")
    if sig_text:
        parts.append(sig_text)

    ids = [t["id"] for t in picked_other] + [t["id"] for t in picked_sig]
    return "\n\n".join(parts), ids


def extract_json(text: str, expect: str = "object"):
    """从 AI 回复里稳健提取 JSON。解析失败返回空容器，绝不抛异常。

    绝不做 unicode_escape —— 会把中文搅成乱码（项目历史坑）。
    """
    empty = [] if expect == "array" else {}
    raw = (text or "").strip()
    if not raw:
        return empty

    candidate = raw
    m = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if m:
        candidate = m.group(1).strip()

    # strict=False 允许字符串里有真实换行符，DeepSeek 常这么返回
    try:
        obj = json.loads(candidate, strict=False)
        if (expect == "array" and isinstance(obj, list)) or \
           (expect == "object" and isinstance(obj, dict)):
            return obj
    except json.JSONDecodeError:
        pass

    open_c, close_c = ("[", "]") if expect == "array" else ("{", "}")
    start, end = candidate.find(open_c), candidate.rfind(close_c)
    if start != -1 and end > start:
        try:
            obj = json.loads(candidate[start:end + 1], strict=False)
            if (expect == "array" and isinstance(obj, list)) or \
               (expect == "object" and isinstance(obj, dict)):
                return obj
        except json.JSONDecodeError:
            pass

    log.warning("extract_json 解析失败，raw[:120]=%s", raw[:120])
    return empty


async def log_injection(db, content_id: str, ai_type: str,
                        asset_ids: list[str], token_count: int) -> None:
    """记录本次 AI 调用注入了什么。三期据此分析哪些注入真的有效。

    一期就写入，避免三期开工时从零等待数据积累。
    """
    await db.execute(
        "INSERT INTO media_injection_log "
        "(id, content_id, ai_type, injected_asset_ids, token_count) "
        "VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), content_id or "", ai_type,
         json.dumps(asset_ids, ensure_ascii=False), token_count),
    )
    await db.commit()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_context.py -v`
Expected: 26 passed

- [ ] **Step 5: 运行全量测试**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add app/services/media_context.py tests/test_media_context.py
git commit -m "feat(media): 注入预算与 AI 输出解析

体系重在库里不在提示词里：任何一次 AI 调用注入资产不超预算总和。
记忆点单独占槽，不与普通条目竞争。未登记槽位拒绝注入。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 人设 CRUD 与人设档案页

**Files:**
- Create: `app/api/media.py`
- Create: `app/templates/media_persona.html`
- Modify: `app/main.py`（import + include_router）
- Modify: `app/templates/base.html`（导航加「🎬 自媒体」）

**Interfaces:**
- Consumes: `media_flow.PLATFORMS`
- Produces:
  - `router` — FastAPI APIRouter，注册到 main
  - `_tpl(request, name, ctx)` — 模板渲染 helper（后续所有页面复用）
  - `_first_persona_id(db) -> str | None` — 取当前人设（一期单人设，多人设留口）
  - 路由：`GET /media/persona`、`POST /media/persona`、`GET /media/persona/{pid}`、`POST /media/persona/{pid}/trait`、`POST /media/trait/{tid}/archive`、`POST /media/persona/{pid}/account`

- [ ] **Step 1: 创建 `app/api/media.py`**

```python
import json
import uuid
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.media_flow import PLATFORMS, STAGES, STAGE_LABELS

router = APIRouter()

TRAIT_DIMENSIONS = {
    "positioning": "定位",
    "audience": "受众",
    "tone": "语气",
    "topics": "选题方向",
    "taboo": "内容禁区",
    "signature": "记忆点",
    "differentiator": "差异化",
}


def _tpl(request, name, ctx):
    ctx["request"] = request
    return request.app.state.templates.TemplateResponse(request, name, ctx)


async def _first_persona_id(db) -> str | None:
    """一期只有一个人设；架构上支持多人设，这里取第一个 active 的。"""
    cur = await db.execute(
        "SELECT id FROM media_persona WHERE status='active' ORDER BY created_at LIMIT 1")
    row = await cur.fetchone()
    return row["id"] if row else None


# ─────────────── 人设 ───────────────

@router.get("/media/persona", response_class=HTMLResponse)
async def persona_home(request: Request):
    """没有人设时引导创建，有则跳到第一个人设档案。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
    finally:
        await db.close()
    if pid:
        return RedirectResponse(f"/media/persona/{pid}", status_code=302)
    return _tpl(request, "media_persona.html",
                {"persona": None, "traits_by_dim": {}, "accounts": [],
                 "dimensions": TRAIT_DIMENSIONS, "platforms": PLATFORMS,
                 "archived": []})


@router.post("/media/persona")
async def persona_create(name: str = Form(...), one_liner: str = Form(""),
                         current_phase: str = Form("冷启动")):
    pid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_persona (id,name,one_liner,current_phase) VALUES (?,?,?,?)",
            (pid, name.strip(), one_liner.strip(), current_phase.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.get("/media/persona/{pid}", response_class=HTMLResponse)
async def persona_detail(request: Request, pid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        persona = dict(row) if row else None

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active' "
            "ORDER BY confidence DESC, created_at DESC", (pid,))
        traits = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='archived' "
            "ORDER BY created_at DESC", (pid,))
        archived = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_account WHERE persona_id=? ORDER BY created_at", (pid,))
        accounts = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

    traits_by_dim = {}
    for dim in TRAIT_DIMENSIONS:
        hit = [t for t in traits if t["dimension"] == dim]
        if hit:
            traits_by_dim[dim] = hit

    return _tpl(request, "media_persona.html",
                {"persona": persona, "traits_by_dim": traits_by_dim,
                 "accounts": accounts, "dimensions": TRAIT_DIMENSIONS,
                 "platforms": PLATFORMS, "archived": archived})


@router.post("/media/persona/{pid}/trait")
async def trait_create(pid: str, dimension: str = Form(...),
                       content: str = Form(...), brief: str = Form(""),
                       confidence: int = Form(3), evidence: str = Form("")):
    db = await get_db()
    try:
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        phase = row["current_phase"] if row else ""
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?,?,?,?,'manual',?,?,?)",
            (str(uuid.uuid4()), pid, dimension, content.strip(),
             brief.strip()[:30], evidence.strip(), confidence, phase))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.post("/media/trait/{tid}/archive")
async def trait_archive(tid: str):
    """归档而非删除 —— 人设演化史要完整留痕。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id FROM media_persona_trait WHERE id=?", (tid,))
        row = await cur.fetchone()
        pid = row["persona_id"] if row else ""
        await db.execute(
            "UPDATE media_persona_trait SET status='archived' WHERE id=?", (tid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.post("/media/persona/{pid}/account")
async def account_create(pid: str, platform: str = Form(...),
                         account_name: str = Form(""), account_url: str = Form(""),
                         platform_note: str = Form("")):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_account "
            "(id,persona_id,platform,account_name,account_url,platform_note) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), pid, platform, account_name.strip(),
             account_url.strip(), platform_note.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)
```

- [ ] **Step 2: 创建 `app/templates/media_persona.html`**

```html
{% extends "base.html" %}
{% block title %}人设档案{% endblock %}
{% block content %}
<style>
  .mp-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 1rem; }
  @media (max-width: 767px) { .mp-grid { grid-template-columns: 1fr; } }
</style>
<div class="max-w-5xl mx-auto px-3 py-4">

{% if not persona %}
  <div class="bg-white rounded-xl shadow-sm p-6 max-w-md mx-auto">
    <h1 class="text-lg font-bold mb-1">建立第一个人设</h1>
    <p class="text-sm text-gray-500 mb-4">
      人设不用一次写全 —— 先起个名，条目会在后续内容和复盘里慢慢长出来。
    </p>
    <form method="post" action="/media/persona" class="space-y-3">
      <input name="name" required placeholder="IP 名"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <input name="one_liner" placeholder="一句话定位（可留空）"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <select name="current_phase" class="w-full border rounded-lg px-3 py-2 text-sm">
        <option>冷启动</option><option>涨粉</option><option>转化</option>
      </select>
      <button class="w-full bg-blue-600 text-white rounded-lg py-2 text-sm">创建</button>
    </form>
  </div>
{% else %}
  <div class="flex items-center justify-between mb-4">
    <div>
      <div class="text-xs text-gray-400">
        <a href="/media" class="hover:underline">自媒体</a> / 人设档案
      </div>
      <h1 class="text-xl font-bold">{{ persona.name }}</h1>
      <div class="text-sm text-gray-500">
        {{ persona.one_liner }}
        <span class="ml-2 px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-xs">
          {{ persona.current_phase }}期
        </span>
      </div>
    </div>
    <a href="/media" class="text-sm text-blue-600">← 看板</a>
  </div>

  <div class="mp-grid">
    <div>
      <div class="bg-white rounded-xl shadow-sm p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-semibold text-sm">人设条目</h2>
          <span class="text-xs text-gray-400">置信度越高越优先注入 AI</span>
        </div>

        {% if not traits_by_dim %}
          <p class="text-sm text-gray-400 py-4 text-center">
            还没有条目。先手动加两条，剩下的让 AI 从内容里总结。
          </p>
        {% endif %}

        {% for dim, label in dimensions.items() %}
          {% if traits_by_dim.get(dim) %}
          <div class="mb-3">
            <div class="text-xs font-medium text-gray-500 mb-1">
              {{ label }}{% if dim == 'signature' %} ⭐{% endif %}
            </div>
            {% for t in traits_by_dim[dim] %}
            <div class="flex items-start gap-2 py-1.5 border-b border-gray-50">
              <div class="flex-1">
                <div class="text-sm">{{ t.content }}</div>
                {% if t.brief %}
                  <div class="text-xs text-violet-600 mt-0.5">注入用：{{ t.brief }}</div>
                {% endif %}
                {% if t.evidence %}
                  <div class="text-xs text-gray-400 mt-0.5">依据：{{ t.evidence }}</div>
                {% endif %}
              </div>
              <div class="text-xs text-amber-600 whitespace-nowrap">
                {{ '★' * t.confidence }}
              </div>
              <form method="post" action="/media/trait/{{ t.id }}/archive">
                <button class="text-xs text-gray-400 hover:text-red-500"
                        title="归档（保留演化史）">归档</button>
              </form>
            </div>
            {% endfor %}
          </div>
          {% endif %}
        {% endfor %}
      </div>

      <details class="bg-white rounded-xl shadow-sm p-4 mb-4">
        <summary class="font-semibold text-sm cursor-pointer">+ 新增条目</summary>
        <form method="post" action="/media/persona/{{ persona.id }}/trait"
              class="space-y-2 mt-3">
          <select name="dimension" class="w-full border rounded-lg px-3 py-2 text-sm">
            {% for dim, label in dimensions.items() %}
            <option value="{{ dim }}">{{ label }}</option>
            {% endfor %}
          </select>
          <textarea name="content" required rows="2" placeholder="条目内容"
                    class="w-full border rounded-lg px-3 py-2 text-sm"></textarea>
          <input name="brief" maxlength="30" placeholder="注入用精简版（≤30字，留空则自动截断）"
                 class="w-full border rounded-lg px-3 py-2 text-sm">
          <input name="evidence" placeholder="依据（可留空）"
                 class="w-full border rounded-lg px-3 py-2 text-sm">
          <select name="confidence" class="w-full border rounded-lg px-3 py-2 text-sm">
            <option value="5">★★★★★ 被数据反复验证</option>
            <option value="4">★★★★ 比较确定</option>
            <option value="3" selected>★★★ 一般</option>
            <option value="2">★★ 待验证</option>
            <option value="1">★ 只是猜测</option>
          </select>
          <button class="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm">保存</button>
        </form>
      </details>

      {% if archived %}
      <details class="bg-white rounded-xl shadow-sm p-4">
        <summary class="font-semibold text-sm cursor-pointer text-gray-500">
          演化史（已归档 {{ archived|length }} 条）
        </summary>
        <div class="mt-3">
          {% for t in archived %}
          <div class="py-1.5 border-b border-gray-50 text-sm text-gray-400">
            <span class="text-xs">{{ dimensions.get(t.dimension, t.dimension) }}</span>
            · {{ t.content }}
            <span class="text-xs">（{{ t.phase_tag }}期）</span>
          </div>
          {% endfor %}
        </div>
      </details>
      {% endif %}
    </div>

    <div>
      <div class="bg-white rounded-xl shadow-sm p-4">
        <h2 class="font-semibold text-sm mb-3">平台账号</h2>
        {% for a in accounts %}
        <div class="py-2 border-b border-gray-50">
          <div class="text-sm font-medium">{{ platforms.get(a.platform, a.platform) }}</div>
          <div class="text-xs text-gray-500">{{ a.account_name }}</div>
          {% if a.platform_note %}
            <div class="text-xs text-gray-400 mt-0.5">{{ a.platform_note }}</div>
          {% endif %}
        </div>
        {% else %}
        <p class="text-sm text-gray-400 mb-2">还没有账号。至少加一个才能发布。</p>
        {% endfor %}

        <details class="mt-3">
          <summary class="text-sm text-blue-600 cursor-pointer">+ 添加平台</summary>
          <form method="post" action="/media/persona/{{ persona.id }}/account"
                class="space-y-2 mt-2">
            <select name="platform" class="w-full border rounded-lg px-3 py-2 text-sm">
              {% for code, label in platforms.items() %}
              <option value="{{ code }}">{{ label }}</option>
              {% endfor %}
            </select>
            <input name="account_name" placeholder="账号名"
                   class="w-full border rounded-lg px-3 py-2 text-sm">
            <input name="account_url" placeholder="主页链接（可留空）"
                   class="w-full border rounded-lg px-3 py-2 text-sm">
            <input name="platform_note" placeholder="该平台差异化策略（可留空）"
                   class="w-full border rounded-lg px-3 py-2 text-sm">
            <button class="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm w-full">
              添加
            </button>
          </form>
        </details>
      </div>
    </div>
  </div>
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: 在 `app/main.py` 注册 router**

把第 17 行的 import 改成（追加 `media`）：

```python
from app.api import dashboard, projects, tasks, settings, agents, notes, chat, auth, finance, study, media
```

在 `app.include_router(study.router)` 之后追加一行：

```python
app.include_router(media.router)
```

- [ ] **Step 4: 在 `app/templates/base.html` 导航加入口**

桌面导航 —— 在 `/study` 那一行（第 94 行）之后插入：

```html
                <a href="/media" class="text-gray-600 hover:text-gray-900 pb-0.5 {% if path.startswith('/media') %}border-b-2 border-purple-500 font-medium{% endif %}">🎬 自媒体</a>
```

移动端导航 —— 在 `/study` 那一行（第 122 行）之后插入：

```html
                <a href="/media" class="flex items-center gap-3 px-3 py-2.5 rounded-lg {% if path.startswith('/media') %}bg-purple-50 text-purple-700 font-medium{% else %}text-gray-700 hover:bg-gray-50{% endif %}">🎬 自媒体</a>
```

- [ ] **Step 5: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

浏览器打开 `http://localhost:8000/media/persona`，验证：
1. 显示「建立第一个人设」表单
2. 填名字提交后跳到人设档案页，不报错
3. 新增一条 `dimension=signature` 的条目，页面显示 ⭐ 和星级
4. 添加一个抖音账号，右栏出现
5. 点「归档」后条目移到「演化史」折叠区
6. 浏览器窗口缩到 375px 宽，两栏变单栏不横向滚动

Expected: 全部通过。若报 `unhashable type: dict`，检查 `_tpl` 是否传了 `request` 三参数。

- [ ] **Step 6: 运行全量测试**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 7: Commit**

```bash
git add app/api/media.py app/templates/media_persona.html app/main.py app/templates/base.html
git commit -m "feat(media): 人设档案页与条目管理

条目归档而非删除，人设演化史完整留痕。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 话题库（手动增删 + 采用/弃）

**Files:**
- Modify: `app/api/media.py`（追加话题路由）
- Create: `app/templates/media_topics.html`

**Interfaces:**
- Consumes: `_tpl`、`_first_persona_id`（Task 4）
- Produces:
  - `_adopt_topic(db, topic_id) -> str | None` — 话题转内容，返回 content_id。供 Task 6 看板复用。
  - 路由：`GET /media/topics`、`POST /media/topics`、`POST /media/topic/{tid}/adopt`、`POST /media/topic/{tid}/reject`

- [ ] **Step 1: 在 `app/api/media.py` 末尾追加话题路由**

```python
# ─────────────── 话题库 ───────────────

TOPIC_SOURCES = {
    "manual": "人工", "ai_rec": "AI推荐", "hot": "热点",
    "comment": "评论区", "competitor": "对标", "review": "复盘衍生",
}


@router.get("/media/topics", response_class=HTMLResponse)
async def topics_home(request: Request, source: str = ""):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        sql = ("SELECT * FROM media_topic WHERE persona_id=? AND status='pool'")
        args = [pid]
        if source:
            sql += " AND source=?"
            args.append(source)
        sql += " ORDER BY decision_score DESC, fit_score DESC, heat DESC, created_at DESC"
        cur = await db.execute(sql, tuple(args))
        topics = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_topic WHERE persona_id=? AND status='rejected' "
            "ORDER BY created_at DESC LIMIT 20", (pid,))
        rejected = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_topics.html",
                {"topics": topics, "rejected": rejected, "persona_id": pid,
                 "sources": TOPIC_SOURCES, "cur_source": source})


@router.post("/media/topics")
async def topic_create(persona_id: str = Form(...), title: str = Form(...),
                       puzzle: str = Form(""), reason: str = Form(""),
                       angle: str = Form(""), heat: int = Form(3),
                       fit_score: int = Form(3)):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score) "
            "VALUES (?,?,?,?,'manual',?,?,?,?)",
            (str(uuid.uuid4()), persona_id, title.strip(), puzzle.strip(),
             reason.strip(), angle.strip(), heat, fit_score))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)


async def _adopt_topic(db, topic_id: str) -> str | None:
    """话题 → 内容。把谜题和理由一起带过去，开工时不用重新想。"""
    cur = await db.execute("SELECT * FROM media_topic WHERE id=?", (topic_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "pool":
        return None
    t = dict(row)
    cid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_content "
        "(id,persona_id,title,puzzle,stage,idea_source,idea_reason) "
        "VALUES (?,?,?,?,'idea',?,?)",
        (cid, t["persona_id"], t["title"], t["puzzle"], t["source"], t["reason"]))
    await db.execute(
        "UPDATE media_topic SET status='adopted', adopted_content_id=? WHERE id=?",
        (cid, topic_id))
    await db.commit()
    return cid


@router.post("/media/topic/{tid}/adopt")
async def topic_adopt(tid: str):
    db = await get_db()
    try:
        cid = await _adopt_topic(db, tid)
    finally:
        await db.close()
    if not cid:
        return RedirectResponse("/media/topics", status_code=302)
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/topic/{tid}/reject")
async def topic_reject(tid: str, rejected_reason: str = Form("")):
    """弃单必须留原因 —— 下次 AI 推荐时带上，防止重复推同类垃圾。"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_topic SET status='rejected', rejected_reason=? WHERE id=?",
            (rejected_reason.strip(), tid))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)
```

- [ ] **Step 2: 创建 `app/templates/media_topics.html`**

```html
{% extends "base.html" %}
{% block title %}话题库{% endblock %}
{% block content %}
<style>
  .tp-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.75rem; }
  @media (max-width: 767px) { .tp-grid { grid-template-columns: 1fr; } }
</style>
<div class="max-w-5xl mx-auto px-3 py-4">
  <div class="flex items-center justify-between mb-3">
    <div>
      <div class="text-xs text-gray-400">
        <a href="/media" class="hover:underline">自媒体</a> / 话题库
      </div>
      <h1 class="text-xl font-bold">话题库 <span class="text-sm text-gray-400 font-normal">{{ topics|length }} 条待选</span></h1>
    </div>
    <button onclick="aiRecommend()" id="ai-btn"
            class="bg-violet-600 text-white rounded-lg px-4 py-2 text-sm">
      ✨ AI 推选题
    </button>
  </div>

  <div class="flex gap-2 mb-3 text-xs overflow-x-auto">
    <a href="/media/topics" class="px-3 py-1 rounded-full whitespace-nowrap
       {% if not cur_source %}bg-blue-600 text-white{% else %}bg-white text-gray-600{% endif %}">全部</a>
    {% for code, label in sources.items() %}
    <a href="/media/topics?source={{ code }}" class="px-3 py-1 rounded-full whitespace-nowrap
       {% if cur_source == code %}bg-blue-600 text-white{% else %}bg-white text-gray-600{% endif %}">{{ label }}</a>
    {% endfor %}
  </div>

  <div id="ai-status" class="hidden text-sm text-violet-600 mb-3"></div>

  <div class="tp-grid mb-4">
    {% for t in topics %}
    <div class="bg-white rounded-xl shadow-sm p-3">
      <div class="flex items-start justify-between gap-2">
        <div class="text-sm font-medium flex-1">{{ t.title }}</div>
        <span class="text-xs text-gray-400 whitespace-nowrap">
          {{ sources.get(t.source, t.source) }}
        </span>
      </div>
      {% if t.puzzle %}
      <div class="text-sm text-violet-700 mt-1.5 bg-violet-50 rounded px-2 py-1">
        ❓ {{ t.puzzle }}
      </div>
      {% endif %}
      {% if t.reason %}
      <div class="text-xs text-gray-500 mt-1.5">{{ t.reason }}</div>
      {% endif %}
      {% if t.angle %}
      <div class="text-xs text-gray-400 mt-1">切入角度：{{ t.angle }}</div>
      {% endif %}
      <div class="text-xs text-amber-600 mt-1.5">
        契合 {{ '★' * t.fit_score }}　热度 {{ '★' * t.heat }}
      </div>
      <div class="flex gap-2 mt-2">
        <form method="post" action="/media/topic/{{ t.id }}/adopt" class="flex-1">
          <button class="w-full bg-blue-600 text-white rounded-lg py-1.5 text-xs">采用</button>
        </form>
        <button onclick="rejectTopic('{{ t.id }}')"
                class="px-3 text-xs text-gray-400 hover:text-red-500">弃</button>
      </div>
    </div>
    {% else %}
    <p class="text-sm text-gray-400 py-8 text-center col-span-2">
      话题池是空的。点右上「AI 推选题」，或手动加一条。
    </p>
    {% endfor %}
  </div>

  <details class="bg-white rounded-xl shadow-sm p-4 mb-4">
    <summary class="font-semibold text-sm cursor-pointer">+ 手动加话题</summary>
    <form method="post" action="/media/topics" class="space-y-2 mt-3">
      <input type="hidden" name="persona_id" value="{{ persona_id }}">
      <input name="title" required placeholder="话题"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <input name="puzzle" placeholder="核心谜题（受众想解开的疑问，如"为什么越努力越叛逆？"）"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <input name="reason" placeholder="为什么值得做"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <input name="angle" placeholder="切入角度"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <button class="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm">加入话题池</button>
    </form>
  </details>

  {% if rejected %}
  <details class="bg-white rounded-xl shadow-sm p-4">
    <summary class="font-semibold text-sm cursor-pointer text-gray-500">
      已弃（{{ rejected|length }}）—— 这些原因会喂给 AI，避免重复推荐
    </summary>
    <div class="mt-3">
      {% for t in rejected %}
      <div class="py-1.5 border-b border-gray-50 text-sm text-gray-400">
        {{ t.title }}
        {% if t.rejected_reason %}
          <span class="text-xs">— {{ t.rejected_reason }}</span>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </details>
  {% endif %}
</div>

<script>
function rejectTopic(id) {
  const reason = prompt("为什么弃这个选题？（会喂给 AI 避免重复推荐）");
  if (reason === null) return;
  const f = document.createElement('form');
  f.method = 'post';
  f.action = '/media/topic/' + id + '/reject';
  const i = document.createElement('input');
  i.name = 'rejected_reason';
  i.value = reason;
  f.appendChild(i);
  document.body.appendChild(f);
  f.submit();
}

async function aiRecommend() {
  const btn = document.getElementById('ai-btn');
  const status = document.getElementById('ai-status');
  btn.disabled = true;
  btn.textContent = '思考中…';
  status.classList.remove('hidden');
  status.textContent = 'AI 正在基于人设和历史表现推选题…';
  try {
    const r = await fetch('/media/topics/ai-recommend', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      status.textContent = '已加入 ' + d.count + ' 条，刷新中…';
      location.reload();
    } else {
      status.textContent = '失败：' + (d.error || '未知错误');
      btn.disabled = false;
      btn.textContent = '✨ AI 推选题';
    }
  } catch (e) {
    status.textContent = '请求失败：' + e;
    btn.disabled = false;
    btn.textContent = '✨ AI 推选题';
  }
}
</script>
{% endblock %}
```

- [ ] **Step 3: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

打开 `http://localhost:8000/media/topics`，验证：
1. 空池显示引导文案
2. 手动加一条带谜题的话题，卡片显示 ❓ 谜题块
3. 点「弃」弹窗填原因，话题移到「已弃」折叠区并显示原因
4. 再加一条，点「采用」→ 跳转到 `/media/content/<id>`（此时 404 是正常的，Task 6 才建该页）
5. 375px 宽度下两栏变单栏

Expected: 1-3、5 通过；4 跳转发生即可（目标页暂不存在）

- [ ] **Step 4: Commit**

```bash
git add app/api/media.py app/templates/media_topics.html
git commit -m "feat(media): 话题库与采用/弃流程

弃单强制留原因，下次 AI 推荐时带上防止重复推同类。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: 内容看板与状态流转

**Files:**
- Modify: `app/api/media.py`（追加看板与内容路由）
- Create: `app/templates/media_board.html`

**Interfaces:**
- Consumes: `media_flow.STAGES / STAGE_LABELS / can_transition`、`_tpl`、`_first_persona_id`
- Produces:
  - 路由：`GET /media`（看板）、`POST /media/content`（手动建内容）、`POST /media/content/{cid}/stage`（推进/退回）
  - 看板卡片数据结构：每条 content 附带 `publishes`（该内容的发布记录列表，含 platform 与 views）

- [ ] **Step 1: 在 `app/api/media.py` 末尾追加看板路由**

```python
# ─────────────── 内容看板 ───────────────

@router.get("/media", response_class=HTMLResponse)
async def board(request: Request):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        persona = dict(await cur.fetchone())

        cur = await db.execute(
            "SELECT * FROM media_content WHERE persona_id=? "
            "ORDER BY updated_at DESC, created_at DESC", (pid,))
        contents = [dict(r) for r in await cur.fetchall()]

        # 每条内容的三平台发布状态 + 最新播放量，看板卡片上直接显示
        cur = await db.execute(
            "SELECT p.content_id, p.id AS publish_id, a.platform, p.status, "
            "  (SELECT views FROM media_metrics m WHERE m.publish_id=p.id "
            "   ORDER BY snapshot_at DESC LIMIT 1) AS views "
            "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
            "JOIN media_content c ON c.id=p.content_id WHERE c.persona_id=?", (pid,))
        pubs = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_topic WHERE persona_id=? AND status='pool'",
            (pid,))
        pool_count = (await cur.fetchone())["c"]
    finally:
        await db.close()

    by_content = {}
    for p in pubs:
        by_content.setdefault(p["content_id"], []).append(p)
    for c in contents:
        c["publishes"] = by_content.get(c["id"], [])

    columns = [{"stage": s, "label": STAGE_LABELS[s],
                "items": [c for c in contents if c["stage"] == s]} for s in STAGES]

    return _tpl(request, "media_board.html",
                {"persona": persona, "columns": columns, "platforms": PLATFORMS,
                 "pool_count": pool_count, "total": len(contents)})


@router.post("/media/content")
async def content_create(persona_id: str = Form(...), title: str = Form(...),
                         puzzle: str = Form("")):
    cid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source) "
            "VALUES (?,?,?,?,'idea','manual')",
            (cid, persona_id, title.strip(), puzzle.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/content/{cid}/stage")
async def content_stage(cid: str, to: str = Form(...), back: str = Form("")):
    """推进或退回阶段。非法流转静默忽略，不报错打断用户。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if row and can_transition(row["stage"], to):
            await db.execute(
                "UPDATE media_content SET stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (to, cid))
            await db.commit()
    finally:
        await db.close()
    target = "/media" if back == "board" else f"/media/content/{cid}"
    return RedirectResponse(target, status_code=302)
```

同时把文件顶部的 import 改成（追加 `can_transition`、`next_stage`）：

```python
from app.services.media_flow import (
    PLATFORMS, STAGES, STAGE_LABELS, can_transition, next_stage,
)
```

- [ ] **Step 2: 创建 `app/templates/media_board.html`**

```html
{% extends "base.html" %}
{% block title %}内容看板{% endblock %}
{% block content %}
<style>
  .kb { display: flex; gap: 0.75rem; overflow-x: auto; padding-bottom: 1rem; }
  .kb-col { flex: 0 0 240px; }
  @media (max-width: 767px) {
    .kb-col { flex: 0 0 200px; }
    .fab { bottom: 80px !important; }
  }
  .fab { position: fixed; right: 1.25rem; bottom: 1.5rem; z-index: 40; }
</style>
<div class="px-3 py-4">
  <div class="flex items-center justify-between mb-4 max-w-6xl mx-auto">
    <div>
      <h1 class="text-xl font-bold">{{ persona.name }}</h1>
      <div class="text-sm text-gray-500">
        {{ persona.one_liner }}
        <span class="ml-1 px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-xs">
          {{ persona.current_phase }}期
        </span>
        <span class="ml-1 text-xs text-gray-400">{{ total }} 条内容</span>
      </div>
    </div>
    <div class="flex gap-2 text-sm">
      <a href="/media/topics" class="text-blue-600">
        话题库{% if pool_count %} ({{ pool_count }}){% endif %}
      </a>
      <a href="/media/persona/{{ persona.id }}" class="text-blue-600">人设档案</a>
    </div>
  </div>

  <div class="kb">
    {% for col in columns %}
    <div class="kb-col">
      <div class="text-xs font-medium text-gray-500 mb-2 px-1">
        {{ col.label }}
        <span class="text-gray-300">{{ col.items|length }}</span>
      </div>
      {% for c in col.items %}
      <a href="/media/content/{{ c.id }}"
         class="block bg-white rounded-xl shadow-sm p-3 mb-2 hover:shadow">
        <div class="text-sm font-medium">{{ c.title }}</div>
        {% if c.puzzle %}
        <div class="text-xs text-violet-600 mt-1">❓ {{ c.puzzle }}</div>
        {% endif %}
        {% if c.publishes %}
        <div class="flex flex-wrap gap-1 mt-2">
          {% for p in c.publishes %}
          <span class="text-xs px-1.5 py-0.5 rounded
            {% if p.status == 'published' %}bg-green-50 text-green-600
            {% else %}bg-gray-100 text-gray-400{% endif %}">
            {{ platforms.get(p.platform, p.platform) }}{% if p.views %} {{ p.views }}{% endif %}
          </span>
          {% endfor %}
        </div>
        {% endif %}
      </a>
      {% endfor %}
      {% if not col.items %}
      <div class="text-xs text-gray-300 px-1 py-3">—</div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>

<div class="fab">
  <button onclick="document.getElementById('new-overlay').classList.remove('hidden')"
          class="bg-blue-600 text-white rounded-full w-14 h-14 text-2xl shadow-lg">+</button>
</div>

<div id="new-overlay" class="hidden fixed inset-0 bg-black bg-opacity-40 z-50 flex items-center justify-center p-4"
     onclick="if(event.target===this) this.classList.add('hidden')">
  <div class="bg-white rounded-xl p-4 w-full max-w-sm">
    <h2 class="font-semibold mb-1">新建内容</h2>
    <p class="text-xs text-gray-400 mb-3">
      建议先去话题库让 AI 推选题 —— 有谜题的选题才算想透了。
    </p>
    <form method="post" action="/media/content" class="space-y-2">
      <input type="hidden" name="persona_id" value="{{ persona.id }}">
      <input name="title" required placeholder="选题"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <input name="puzzle" placeholder="核心谜题"
             class="w-full border rounded-lg px-3 py-2 text-sm">
      <div class="flex gap-2">
        <button class="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm">创建</button>
        <button type="button" class="px-4 text-sm text-gray-500"
                onclick="document.getElementById('new-overlay').classList.add('hidden')">
          取消
        </button>
      </div>
    </form>
    <a href="/media/topics" class="block text-center text-sm text-violet-600 mt-3">
      → 去话题库
    </a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

打开 `http://localhost:8000/media`，验证：
1. 显示 7 列看板，列名依次是 选题/脚本/待录/待剪/待发/已发/已复盘
2. 点右下 + 号浮层建一条内容 → 跳转（`/media/content/<id>` 暂 404 正常）
3. 回到 `/media`，新内容出现在「选题」列
4. Task 5 里「采用」的话题也出现在「选题」列
5. 375px 宽度下看板横向滚动，页面 body 不横向滚动，FAB 位于 bottom:80px

Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add app/api/media.py app/templates/media_board.html
git commit -m "feat(media): 内容看板与状态流转

看板卡片直接显示三平台发布状态和播放量，一眼看到卡在哪。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: AI 推选题（AI 能力 #1）

**Files:**
- Create: `app/services/media_ai.py`
- Modify: `app/api/media.py`（追加 `POST /media/topics/ai-recommend`）

**Interfaces:**
- Consumes: `media_context.extract_json / log_injection`、`ai_router.ask_ai`
- Produces:
  - `async recommend_topics(db, persona_id: str, model: str = "auto") -> dict`
    返回 `{"ok": bool, "count": int, "cost": float, "model": str, "error": str}`
    副作用：把候选话题写入 `media_topic`（`source='ai_rec'`, `status='pool'`）

- [ ] **Step 1: 创建 `app/services/media_ai.py`**

```python
"""media 模块的 AI 能力。每个能力是独立调用，各拿各的上下文。

设计约束（spec §6 办法一）：分工不分身 —— 绝不让一个 AI 一次干完所有事。
选题 AI 看不到原料库和脚本细节，脚本 AI 看不到数据表和话题池。
一个 AI 一件事，注意力天然集中。
"""
import json
import logging
import uuid

from app.services.ai_router import ask_ai
from app.services.media_context import extract_json, log_injection

log = logging.getLogger(__name__)

RECOMMEND_SYSTEM = """你是资深自媒体选题策划。你的任务是基于人设推荐选题。

铁律（必须全部满足）：
1. 每个选题必须写出「核心谜题」—— 一个受众想解开的具体疑问，带悬念。
   反面例子："聊聊育儿焦虑"（平铺主题，没有钩子）
   正面例子："为什么越努力的妈妈，孩子越叛逆？"（有悬念，想看答案）
2. 给不出谜题的选题说明还没想透，不要输出。
3. 不得推荐与「已弃选题」同类的方向。
4. 只输出 JSON 数组，不要任何解释文字。

输出格式：
[{"title":"选题","puzzle":"核心谜题","reason":"为什么值得做","angle":"切入角度","heat":3,"fit_score":4}]
heat 和 fit_score 都是 1-5 的整数。"""


async def recommend_topics(db, persona_id: str, model: str = "auto") -> dict:
    """AI 推选题。基于人设条目 + 已发内容表现 + 弃单原因。

    看不到：原料库、脚本细节、剪辑信息 —— 与选题决策无关。
    """
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "人设不存在", "count": 0, "cost": 0, "model": ""}
    persona = dict(row)

    cur = await db.execute(
        "SELECT id,dimension,content,brief,confidence FROM media_persona_trait "
        "WHERE persona_id=? AND status='active' ORDER BY confidence DESC LIMIT 12",
        (persona_id,))
    traits = [dict(r) for r in await cur.fetchall()]

    # 已发内容的表现，让 AI 知道什么方向有效
    cur = await db.execute(
        "SELECT c.title, MAX(m.views) AS views FROM media_content c "
        "JOIN media_publish p ON p.content_id=c.id "
        "JOIN media_metrics m ON m.publish_id=p.id "
        "WHERE c.persona_id=? GROUP BY c.id ORDER BY views DESC LIMIT 10",
        (persona_id,))
    performance = [dict(r) for r in await cur.fetchall()]

    # 池子里已有的，避免重复推
    cur = await db.execute(
        "SELECT title FROM media_topic WHERE persona_id=? AND status='pool' LIMIT 30",
        (persona_id,))
    existing = [r["title"] for r in await cur.fetchall()]

    # 弃单原因 —— 防止 AI 重复推垃圾
    cur = await db.execute(
        "SELECT title, rejected_reason FROM media_topic "
        "WHERE persona_id=? AND status='rejected' ORDER BY created_at DESC LIMIT 15",
        (persona_id,))
    rejected = [dict(r) for r in await cur.fetchall()]

    parts = [
        f"人设：{persona['name']}｜{persona['one_liner']}｜当前阶段：{persona['current_phase']}",
    ]
    if traits:
        parts.append("人设条目：\n" + "\n".join(
            f"- [{t['dimension']}] {t['brief'] or t['content'][:40]}" for t in traits))
    if performance:
        parts.append("已发内容表现（播放量）：\n" + "\n".join(
            f"- {p['title']}：{p['views'] or 0}" for p in performance))
    if existing:
        parts.append("话题池已有（不要重复）：\n" + "\n".join(f"- {t}" for t in existing))
    if rejected:
        parts.append("已弃选题及原因（不要推同类）：\n" + "\n".join(
            f"- {r['title']}：{r['rejected_reason'] or '未说明'}" for r in rejected))
    parts.append("请推荐 5 个新选题。")

    prompt = "\n\n".join(parts)
    result = await ask_ai(prompt, model=model, task_type="media_topic",
                          system_prompt=RECOMMEND_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    items = extract_json(resp, expect="array")
    if not items:
        # AI 可能把数组包在 {"topics": [...]} 里
        obj = extract_json(resp, expect="object")
        items = obj.get("topics") or obj.get("data") or []
    if not items:
        return {"ok": False, "error": "AI 输出无法解析为选题列表", "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    trait_ids = [t["id"] for t in traits]
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        puzzle = (it.get("puzzle") or "").strip()
        if not title or not puzzle:
            continue  # 铁律 2：没谜题的不入库
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score,"
            " related_trait_ids) VALUES (?,?,?,?,'ai_rec',?,?,?,?,?)",
            (str(uuid.uuid4()), persona_id, title, puzzle,
             (it.get("reason") or "").strip(), (it.get("angle") or "").strip(),
             _clamp(it.get("heat"), 3), _clamp(it.get("fit_score"), 3),
             json.dumps(trait_ids, ensure_ascii=False)))
        count += 1
    await db.commit()

    await log_injection(db, "", "recommend_topics", trait_ids,
                        result.get("tokens", 0))

    return {"ok": True, "count": count, "cost": result.get("cost", 0),
            "model": result.get("model", ""), "error": ""}


def _clamp(value, default: int) -> int:
    """把 AI 给的评分夹到 1-5。AI 偶尔会返回 0、10 或字符串。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(5, v))
```

- [ ] **Step 2: 在 `app/api/media.py` 追加路由**

在文件顶部的 import 区追加：

```python
from fastapi.responses import JSONResponse
from app.services.media_ai import recommend_topics
```

在文件末尾追加：

```python
@router.post("/media/topics/ai-recommend")
async def topics_ai_recommend():
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        try:
            result = await recommend_topics(db, pid)
        except Exception as e:
            log.exception("AI 推选题失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)
```

并在 `app/api/media.py` 顶部加日志器（放在 `router = APIRouter()` 之前）：

```python
import logging
log = logging.getLogger(__name__)
```

- [ ] **Step 3: 验证 `_clamp` 的健壮性（快速单元测试）**

在 `tests/test_media_context.py` 末尾追加：

```python
def test_clamp_rating_handles_ai_garbage():
    from app.services.media_ai import _clamp
    assert _clamp(3, 3) == 3
    assert _clamp(0, 3) == 1        # AI 给 0 → 夹到 1
    assert _clamp(10, 3) == 5       # AI 给 10 → 夹到 5
    assert _clamp("4", 3) == 4      # AI 给字符串
    assert _clamp(None, 3) == 3     # AI 没给
    assert _clamp("很高", 3) == 3   # AI 给中文
```

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_context.py -v`
Expected: 27 passed

- [ ] **Step 4: 启动服务实测 AI 调用**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

前提：设置页已配置至少一个 API Key（DeepSeek 最便宜）。

打开 `http://localhost:8000/media/topics`，点「✨ AI 推选题」，验证：
1. 按钮变「思考中…」，状态栏显示提示
2. 几秒后页面刷新，出现 5 条 AI 推荐话题
3. **每条都有 ❓ 谜题块**（这是铁律 2 生效的证据）
4. 每条 source 标签显示「AI推荐」
5. 弃掉一条并填原因，再点「AI 推选题」，新推荐里不应出现同类方向

若第 3 点失败（有话题没谜题），说明 `recommend_topics` 的过滤逻辑没生效，检查 `if not title or not puzzle: continue`。

Expected: 全部通过

- [ ] **Step 5: 验证注入日志已写入**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3
con = sqlite3.connect('data/aipm.db')
rows = con.execute('SELECT ai_type, injected_asset_ids, token_count FROM media_injection_log').fetchall()
print('injection_log 行数:', len(rows))
for r in rows: print(r)
"
```

Expected: 至少 1 行，`ai_type='recommend_topics'`，`token_count > 0`

- [ ] **Step 6: 运行全量测试并 Commit**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed

```bash
git add app/services/media_ai.py app/api/media.py tests/test_media_context.py
git commit -m "feat(media): AI 推选题

铁律：没有核心谜题的选题不入库 —— 给不出谜题说明还没想透。
弃单原因喂给 AI 防止重复推同类。注入日志一期就开始记。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: 内容详情页与 AI 写脚本（AI 能力 #2）

**Files:**
- Modify: `app/services/media_ai.py`（追加 `write_script`）
- Modify: `app/api/media.py`（追加内容详情与脚本路由）
- Create: `app/templates/media_content.html`

**Interfaces:**
- Consumes: `media_context.build_script_context / log_injection`
- Produces:
  - `async write_script(db, content_id: str, mode: str = "full", model: str = "auto") -> dict`
    返回 `{"ok": bool, "script": str, "cost": float, "model": str, "injected_count": int, "error": str}`
    `mode="lean"` 只注入人设身份行，`mode="full"` 注入完整预算内容 —— 供 spec §6 的「一键对比」兜底。
  - 路由：`GET /media/content/{cid}`、`POST /media/content/{cid}/script`、`POST /media/content/{cid}/ai-script`

- [ ] **Step 1: 在 `app/services/media_ai.py` 追加 `write_script`**

顶部 import 追加：

```python
from app.services.media_context import build_script_context
```

文件末尾追加：

```python
SCRIPT_SYSTEM = """你是资深口播脚本撰稿人，为真人出镜的短视频写口播稿。

铁律（必须全部满足，不超过 5 条 —— 规则多了每条都做不好）：
1. 必须以谜题开场，3 秒内抛出，禁止任何铺垫和自我介绍。
2. 必须植入给定的记忆点（如果提供了）。
3. 口语化 —— 写的是说出来的话，不是书面文章。短句，能断则断。
4. 标注时长节奏，全片控制在 60-90 秒。
5. 结尾留钩子，引导评论互动。

输出纯文本脚本，用 Markdown 分段。禁止用 ASCII 字符画（中文等宽会错位），
需要表格就用 Markdown 表格。不要输出 JSON，不要写解释。"""


async def write_script(db, content_id: str, mode: str = "full",
                       model: str = "auto") -> dict:
    """AI 写口播脚本。

    mode="full"：注入完整预算内的人设资产（默认）
    mode="lean"：只注入人设身份行 —— 用于与 full 对比，判断注入是否真的有效
                （spec §6 兜底：人的判断本身就是最好的评估器）

    看不到：数据表、话题池、财务 —— 与写脚本无关。
    """
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "script": "",
                "cost": 0, "model": "", "injected_count": 0}
    content = dict(row)

    cur = await db.execute(
        "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
    persona = dict(await cur.fetchone())

    if mode == "lean":
        context_text = (f"【人设】{persona['name']}｜{persona['one_liner']}"
                        f"｜当前阶段：{persona['current_phase']}")
        injected_ids = []
    else:
        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active'",
            (content["persona_id"],))
        traits = [dict(r) for r in await cur.fetchall()]
        context_text, injected_ids = build_script_context(persona, traits)

    parts = [context_text, f"【本条选题】{content['title']}"]
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

    await log_injection(db, content_id, f"write_script:{mode}",
                        injected_ids, result.get("tokens", 0))

    return {"ok": True, "script": resp, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", ""),
            "injected_count": len(injected_ids)}
```

- [ ] **Step 2: 在 `app/api/media.py` 追加内容详情路由**

顶部 import 追加：

```python
from app.services.media_ai import write_script
```

文件末尾追加：

```python
# ─────────────── 内容详情 ───────────────

@router.get("/media/content/{cid}", response_class=HTMLResponse)
async def content_detail(request: Request, cid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/media", status_code=302)
        content = dict(row)

        cur = await db.execute(
            "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
        persona = dict(await cur.fetchone())

        cur = await db.execute(
            "SELECT * FROM media_account WHERE persona_id=? AND status='active' "
            "ORDER BY created_at", (content["persona_id"],))
        accounts = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_publish WHERE content_id=?", (cid,))
        pubs = {r["account_id"]: dict(r) for r in await cur.fetchall()}

        # 每个发布记录的最新数据
        metrics = {}
        for aid, p in pubs.items():
            cur = await db.execute(
                "SELECT * FROM media_metrics WHERE publish_id=? "
                "ORDER BY snapshot_at DESC LIMIT 1", (p["id"],))
            m = await cur.fetchone()
            if m:
                metrics[p["id"]] = dict(m)

        cur = await db.execute(
            "SELECT * FROM media_review WHERE content_id=? ORDER BY created_at", (cid,))
        reviews = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

    for r in reviews:
        try:
            r["proposed_traits"] = json.loads(r["proposed_traits"] or "[]")
        except (json.JSONDecodeError, TypeError):
            r["proposed_traits"] = []

    return _tpl(request, "media_content.html",
                {"content": content, "persona": persona, "accounts": accounts,
                 "pubs": pubs, "metrics": metrics, "reviews": reviews,
                 "platforms": PLATFORMS, "stages": STAGES,
                 "stage_labels": STAGE_LABELS,
                 "next_stage": next_stage(content["stage"])})


@router.post("/media/content/{cid}/script")
async def content_save_script(cid: str, script: str = Form(""),
                              edit_note: str = Form(""), cover_idea: str = Form("")):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_content SET script=?, edit_note=?, cover_idea=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (script, edit_note, cover_idea, cid))
        # 脚本从空变有 → 自动推进到 scripted，省一次手动点击
        cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if script.strip() and row and row["stage"] == "idea":
            await db.execute(
                "UPDATE media_content SET stage='scripted' WHERE id=?", (cid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/content/{cid}/ai-script")
async def content_ai_script(cid: str, mode: str = Form("full")):
    db = await get_db()
    try:
        try:
            result = await write_script(db, cid, mode=mode)
        except Exception as e:
            log.exception("AI 写脚本失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)
```

- [ ] **Step 3: 创建 `app/templates/media_content.html`**

```html
{% extends "base.html" %}
{% block title %}{{ content.title }}{% endblock %}
{% block content %}
<style>
  .mc-steps { display: flex; gap: 0.25rem; overflow-x: auto; }
  .mc-step { flex: 0 0 auto; font-size: 0.7rem; padding: 0.25rem 0.5rem;
             border-radius: 999px; white-space: nowrap; }
  .pub-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem; }
  @media (max-width: 767px) { .pub-grid { grid-template-columns: 1fr; } }
</style>
<div class="max-w-3xl mx-auto px-3 py-4">

  <div class="text-xs text-gray-400 mb-1">
    <a href="/media" class="hover:underline">自媒体</a> / 内容
  </div>
  <h1 class="text-xl font-bold mb-1">{{ content.title }}</h1>
  {% if content.puzzle %}
  <div class="text-sm text-violet-700 bg-violet-50 rounded px-3 py-2 mb-2">
    ❓ {{ content.puzzle }}
  </div>
  {% endif %}
  {% if content.idea_reason %}
  <div class="text-xs text-gray-500 mb-3">选题理由：{{ content.idea_reason }}</div>
  {% endif %}

  <div class="mc-steps mb-4">
    {% for s in stages %}
    <span class="mc-step
      {% if s == content.stage %}bg-blue-600 text-white
      {% else %}bg-gray-100 text-gray-400{% endif %}">{{ stage_labels[s] }}</span>
    {% endfor %}
  </div>

  {% if next_stage %}
  <form method="post" action="/media/content/{{ content.id }}/stage" class="mb-4">
    <input type="hidden" name="to" value="{{ next_stage }}">
    <button class="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm">
      推进到「{{ stage_labels[next_stage] }}」→
    </button>
  </form>
  {% endif %}

  <!-- 脚本 -->
  <div class="bg-white rounded-xl shadow-sm p-4 mb-3">
    <div class="flex items-center justify-between mb-2">
      <h2 class="font-semibold text-sm">口播脚本</h2>
      <div class="flex gap-2">
        <button onclick="aiScript('full')" id="btn-full"
                class="bg-violet-600 text-white rounded-lg px-3 py-1.5 text-xs">
          ✨ AI 写脚本
        </button>
        <button onclick="aiScript('lean')" id="btn-lean"
                class="border border-violet-600 text-violet-600 rounded-lg px-3 py-1.5 text-xs"
                title="只注入人设身份，用于对比注入是否真的有效">
          精简注入
        </button>
      </div>
    </div>
    <div id="ai-status" class="hidden text-xs text-violet-600 mb-2"></div>
    <form method="post" action="/media/content/{{ content.id }}/script">
      <textarea name="script" id="script-box" rows="12"
                placeholder="点「AI 写脚本」生成，或直接手写"
                class="w-full border rounded-lg px-3 py-2 text-sm font-mono">{{ content.script }}</textarea>
      <input name="edit_note" value="{{ content.edit_note }}" placeholder="剪辑要点"
             class="w-full border rounded-lg px-3 py-2 text-sm mt-2">
      <input name="cover_idea" value="{{ content.cover_idea }}" placeholder="封面思路"
             class="w-full border rounded-lg px-3 py-2 text-sm mt-2">
      <button class="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm mt-2">
        保存脚本
      </button>
    </form>
  </div>

  <!-- 三平台发布：Task 9 填充 -->
  <div id="publish-section"></div>

  <!-- 复盘：Task 11 填充 -->
  <div id="review-section"></div>
</div>

<script>
async function aiScript(mode) {
  const box = document.getElementById('script-box');
  if (box.value.trim() && !confirm('会覆盖当前脚本，继续？')) return;
  const btns = [document.getElementById('btn-full'), document.getElementById('btn-lean')];
  const status = document.getElementById('ai-status');
  btns.forEach(b => b.disabled = true);
  status.classList.remove('hidden');
  status.textContent = mode === 'lean'
    ? 'AI 写作中（精简注入，仅人设身份）…'
    : 'AI 写作中（完整注入人设条目与记忆点）…';
  try {
    const fd = new FormData();
    fd.append('mode', mode);
    const r = await fetch('/media/content/{{ content.id }}/ai-script',
                          {method: 'POST', body: fd});
    const d = await r.json();
    if (d.ok) {
      box.value = d.script;
      status.textContent = '完成（' + d.model + '，注入 ' + d.injected_count
                         + ' 条资产，费用 $' + (d.cost || 0).toFixed(4)
                         + '）。记得点「保存脚本」。';
    } else {
      status.textContent = '失败：' + (d.error || '未知错误');
    }
  } catch (e) {
    status.textContent = '请求失败：' + e;
  }
  btns.forEach(b => b.disabled = false);
}
</script>
{% endblock %}
```

- [ ] **Step 4: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

打开看板点进一条内容，验证：
1. 页面显示选题、谜题、7 步进度条（当前步高亮蓝色）
2. 点「✨ AI 写脚本」→ 状态栏显示注入条数，脚本填入文本框
3. 状态栏的「注入 N 条资产」中 N ≤ 11（trait 8 + signature 3 的预算上限）
4. 点「精简注入」→ 状态栏显示「注入 0 条资产」，生成另一版脚本
5. 点「保存脚本」→ 页面刷新，进度条从「选题」自动跳到「脚本」
6. 点「推进到『待录』」→ 进度条前进
7. 375px 宽度下不横向滚动

Expected: 全部通过。第 3 点是注入预算生效的直接证据。

- [ ] **Step 5: 验证两种模式都记了注入日志**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3
con = sqlite3.connect('data/aipm.db')
rows = con.execute(\"SELECT ai_type, injected_asset_ids, token_count FROM media_injection_log WHERE ai_type LIKE 'write_script%'\").fetchall()
for r in rows: print(r)
"
```

Expected: 至少两行，一行 `write_script:full`（asset_ids 非空数组），一行 `write_script:lean`（asset_ids 为 `[]`）

- [ ] **Step 6: 运行全量测试并 Commit**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed

```bash
git add app/services/media_ai.py app/api/media.py app/templates/media_content.html
git commit -m "feat(media): 内容详情页与 AI 写脚本

5 条铁律而非 20 条建议 —— 规则多了每条都做不好。
提供精简/完整注入两个按钮做对比，人的判断是最好的评估器。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: 三平台发布与 AI 生成文案（AI 能力 #3）

**Files:**
- Modify: `app/services/media_ai.py`（追加 `generate_platform_copy`）
- Modify: `app/api/media.py`（追加发布路由）
- Modify: `app/templates/media_content.html`（填充 `#publish-section`）

**Interfaces:**
- Consumes: 脚本内容（Task 8）、`media_account`
- Produces:
  - `async generate_platform_copy(db, content_id: str, account_id: str, model: str = "auto") -> dict`
    返回 `{"ok": bool, "publish_text": str, "cost": float, "model": str, "error": str}`
  - `async _ensure_publish(db, content_id, account_id) -> str` — 取或建发布记录，返回 publish_id
  - 路由：`POST /media/content/{cid}/publish/{aid}/copy`、`POST /media/content/{cid}/publish/{aid}/save`

- [ ] **Step 1: 在 `app/services/media_ai.py` 追加 `generate_platform_copy`**

```python
PLATFORM_STYLE = {
    "douyin": "抖音：标题要短要炸，前 10 个字决定点开率。3-5 个话题标签。",
    "xhs": "小红书：标题带 emoji，正文分点排版，口语化像跟朋友说话。"
           "正文控制在 600 字内（超过阅读完成率骤降）。5-8 个话题标签。",
    "shipinhao": "视频号：受众年龄偏大，标题直白讲清价值，少用网络黑话。"
                 "2-3 个话题标签。",
}

COPY_SYSTEM = """你是自媒体平台文案专家。根据口播脚本，为指定平台写发布文案。

铁律：
1. 标题必须承接脚本的核心谜题，保留悬念。
2. 严格遵守该平台的字数和风格要求。
3. 话题标签用该平台真实存在的通用标签，不要造词。
4. 只输出 JSON，不要解释。

输出格式：{"title":"标题","body":"正文","tags":["标签1","标签2"]}"""


async def generate_platform_copy(db, content_id: str, account_id: str,
                                 model: str = "auto") -> dict:
    """为单个平台生成发布文案。

    看不到：人设全档、原料库、历史数据 —— 有脚本和平台特性就够了。
    """
    cur = await db.execute(
        "SELECT title, puzzle, script FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "publish_text": "",
                "cost": 0, "model": ""}
    content = dict(row)
    if not (content["script"] or "").strip():
        return {"ok": False, "error": "请先写脚本，文案是从脚本来的",
                "publish_text": "", "cost": 0, "model": ""}

    cur = await db.execute(
        "SELECT platform, platform_note FROM media_account WHERE id=?", (account_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "账号不存在", "publish_text": "",
                "cost": 0, "model": ""}
    account = dict(row)

    parts = [
        f"【平台要求】{PLATFORM_STYLE.get(account['platform'], account['platform'])}",
    ]
    if account["platform_note"]:
        parts.append(f"【本账号在该平台的策略】{account['platform_note']}")
    parts.append(f"【选题】{content['title']}")
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    parts.append(f"【口播脚本】\n{content['script'][:4000]}")
    parts.append("请生成该平台的发布文案。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_copy",
                          system_prompt=COPY_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "publish_text": "",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    if not obj:
        # 解析不了就把原文给用户，总比丢掉强
        text = resp.strip()
    else:
        tags = obj.get("tags") or []
        tag_line = " ".join(f"#{t.lstrip('#')}" for t in tags if t)
        text = "\n\n".join(x for x in [
            (obj.get("title") or "").strip(),
            (obj.get("body") or "").strip(),
            tag_line,
        ] if x)

    await log_injection(db, content_id, f"platform_copy:{account['platform']}",
                        [], result.get("tokens", 0))

    return {"ok": True, "publish_text": text, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 2: 在 `app/api/media.py` 追加发布路由**

顶部 import 追加 `generate_platform_copy`：

```python
from app.services.media_ai import recommend_topics, write_script, generate_platform_copy
```

文件末尾追加：

```python
# ─────────────── 三平台发布 ───────────────

async def _ensure_publish(db, content_id: str, account_id: str) -> str:
    """取或建该内容在该平台的发布记录。"""
    cur = await db.execute(
        "SELECT id FROM media_publish WHERE content_id=? AND account_id=?",
        (content_id, account_id))
    row = await cur.fetchone()
    if row:
        return row["id"]
    pubid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_publish (id,content_id,account_id) VALUES (?,?,?)",
        (pubid, content_id, account_id))
    await db.commit()
    return pubid


@router.post("/media/content/{cid}/publish/{aid}/copy")
async def publish_ai_copy(cid: str, aid: str):
    db = await get_db()
    try:
        try:
            result = await generate_platform_copy(db, cid, aid)
            if result.get("ok"):
                pubid = await _ensure_publish(db, cid, aid)
                await db.execute(
                    "UPDATE media_publish SET publish_text=? WHERE id=?",
                    (result["publish_text"], pubid))
                await db.commit()
        except Exception as e:
            log.exception("AI 生成平台文案失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/publish/{aid}/save")
async def publish_save(cid: str, aid: str, publish_text: str = Form(""),
                       post_url: str = Form(""), mark_published: str = Form("")):
    db = await get_db()
    try:
        pubid = await _ensure_publish(db, cid, aid)
        if mark_published:
            await db.execute(
                "UPDATE media_publish SET publish_text=?, post_url=?, "
                "status='published', published_at=CURRENT_TIMESTAMP WHERE id=?",
                (publish_text, post_url.strip(), pubid))
        else:
            await db.execute(
                "UPDATE media_publish SET publish_text=?, post_url=? WHERE id=?",
                (publish_text, post_url.strip(), pubid))
        await db.commit()

        # 任一平台已发 → 内容自动进入 published 阶段
        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_publish "
            "WHERE content_id=? AND status='published'", (cid,))
        if (await cur.fetchone())["c"] > 0:
            cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
            row = await cur.fetchone()
            if row and stage_index(row["stage"]) < stage_index("published"):
                await db.execute(
                    "UPDATE media_content SET stage='published', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)
```

顶部 media_flow import 追加 `stage_index`：

```python
from app.services.media_flow import (
    PLATFORMS, STAGES, STAGE_LABELS, can_transition, next_stage, stage_index,
)
```

- [ ] **Step 3: 在 `app/templates/media_content.html` 填充 `#publish-section`**

把 `<div id="publish-section"></div>` 整行替换为：

```html
  <div class="bg-white rounded-xl shadow-sm p-4 mb-3">
    <h2 class="font-semibold text-sm mb-3">三平台发布</h2>
    {% if not accounts %}
      <p class="text-sm text-gray-400">
        还没有平台账号，
        <a href="/media/persona/{{ persona.id }}" class="text-blue-600">先去人设档案添加</a>。
      </p>
    {% else %}
    <div class="pub-grid">
      {% for a in accounts %}
      {% set p = pubs.get(a.id) %}
      <div class="border rounded-lg p-3
        {% if p and p.status == 'published' %}border-green-200 bg-green-50{% endif %}">
        <div class="flex items-center justify-between mb-2">
          <span class="text-sm font-medium">
            {{ platforms.get(a.platform, a.platform) }}
          </span>
          {% if p and p.status == 'published' %}
            <span class="text-xs text-green-600">已发</span>
          {% else %}
            <span class="text-xs text-gray-400">待发</span>
          {% endif %}
        </div>
        <button onclick="aiCopy('{{ a.id }}')" id="copy-btn-{{ a.id }}"
                class="w-full border border-violet-600 text-violet-600 rounded-lg py-1.5 text-xs mb-2">
          ✨ 生成文案
        </button>
        <div id="copy-status-{{ a.id }}" class="hidden text-xs text-violet-600 mb-1"></div>
        <form method="post"
              action="/media/content/{{ content.id }}/publish/{{ a.id }}/save">
          <textarea name="publish_text" id="copy-box-{{ a.id }}" rows="6"
                    placeholder="该平台文案"
                    class="w-full border rounded-lg px-2 py-1.5 text-xs">{{ p.publish_text if p else '' }}</textarea>
          <input name="post_url" value="{{ p.post_url if p else '' }}"
                 placeholder="发布后的链接"
                 class="w-full border rounded-lg px-2 py-1.5 text-xs mt-1">
          <div class="flex gap-1 mt-1">
            <button class="flex-1 bg-gray-100 text-gray-700 rounded-lg py-1.5 text-xs">
              保存
            </button>
            {% if not p or p.status != 'published' %}
            <button name="mark_published" value="1"
                    class="flex-1 bg-blue-600 text-white rounded-lg py-1.5 text-xs">
              标记已发
            </button>
            {% endif %}
          </div>
        </form>

        {% if p and p.status == 'published' %}
        {% set m = metrics.get(p.id) %}
        <div class="mt-2 pt-2 border-t border-green-200">
          {% if m %}
          <div class="text-xs text-gray-600">
            播放 {{ m.views }}　赞 {{ m.likes }}<br>
            评 {{ m.comments }}　转 {{ m.shares }}　粉 +{{ m.new_fans }}
          </div>
          <div class="text-xs text-gray-400 mt-0.5">
            {{ m.collected_by }}　{{ m.snapshot_at[:16] }}
          </div>
          {% else %}
          <div class="text-xs text-gray-400">还没有数据</div>
          {% endif %}
          <div id="metrics-slot-{{ p.id }}"></div>
        </div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
```

在 `<script>` 块内追加：

```javascript
async function aiCopy(accountId) {
  const btn = document.getElementById('copy-btn-' + accountId);
  const status = document.getElementById('copy-status-' + accountId);
  const box = document.getElementById('copy-box-' + accountId);
  btn.disabled = true;
  btn.textContent = '生成中…';
  status.classList.remove('hidden');
  status.textContent = '正在按该平台特性改写…';
  try {
    const r = await fetch('/media/content/{{ content.id }}/publish/'
                          + accountId + '/copy', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      box.value = d.publish_text;
      status.textContent = '完成（' + d.model + '，$'
                         + (d.cost || 0).toFixed(4) + '）。记得点保存。';
    } else {
      status.textContent = '失败：' + (d.error || '未知错误');
    }
  } catch (e) {
    status.textContent = '请求失败：' + e;
  }
  btn.disabled = false;
  btn.textContent = '✨ 生成文案';
}
```

- [ ] **Step 4: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

前提：人设档案里已添加至少两个平台账号（抖音 + 小红书）。

打开一条**已有脚本**的内容详情，验证：
1. 「三平台发布」区显示每个账号一张卡
2. 点抖音卡的「✨ 生成文案」→ 文案填入文本框，含标题+正文+#标签
3. 点小红书卡的「生成文案」→ **文案明显更长、带 emoji、分点排版**（平台差异化生效的证据）
4. 填个链接点「标记已发」→ 卡片变绿，显示「已发」，页面顶部进度条跳到「已发」
5. 对**没有脚本**的内容点「生成文案」→ 提示「请先写脚本，文案是从脚本来的」
6. 375px 宽度下三卡变单列

Expected: 全部通过。第 3 点是平台适配生效的关键证据。

- [ ] **Step 5: Commit**

```bash
git add app/services/media_ai.py app/api/media.py app/templates/media_content.html
git commit -m "feat(media): 三平台发布与 AI 平台文案

每平台独立文案，小红书限 600 字、抖音标题前 10 字定生死。
任一平台标记已发即推进内容到 published。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: 数据采集（手填 + 截图识图，AI 能力 #4）

**Files:**
- Create: `app/services/media_metrics.py`
- Create: `tests/test_media_metrics.py`
- Modify: `app/api/media.py`（追加数据路由）
- Modify: `app/templates/media_content.html`（填充 `#metrics-slot-*`）

**Interfaces:**
- Consumes: `media_context.extract_json`、`ai_router.ask_ai_vision`
- Produces:
  - `normalize_metrics(raw: dict) -> dict` — **纯函数**，把 AI 输出归一化成 5 个整数字段。处理「1.2万」「3.5k」「暂无」等脏数据。
  - `async recognize_screenshot(image_bytes: bytes, media_type: str) -> dict` — 返回 `{"ok", "data", "cost", "model", "error"}`
  - `async save_metrics(db, publish_id: str, data: dict, collected_by: str) -> str`
  - 路由：`POST /media/publish/{pubid}/metrics`（手填）、`POST /media/publish/{pubid}/metrics/screenshot`（识图）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_media_metrics.py`：

```python
from app.services.media_metrics import normalize_metrics, METRIC_FIELDS


def test_all_five_fields_always_present():
    got = normalize_metrics({})
    assert set(got) == set(METRIC_FIELDS)
    assert all(v == 0 for v in got.values())


def test_plain_integers():
    got = normalize_metrics({"views": 1234, "likes": 56})
    assert got["views"] == 1234
    assert got["likes"] == 56


def test_string_integers():
    assert normalize_metrics({"views": "1234"})["views"] == 1234


def test_chinese_wan_unit():
    # 平台后台普遍显示"1.2万"而不是 12000
    assert normalize_metrics({"views": "1.2万"})["views"] == 12000
    assert normalize_metrics({"views": "3万"})["views"] == 30000


def test_chinese_yi_unit():
    assert normalize_metrics({"views": "1.5亿"})["views"] == 150000000


def test_k_and_w_suffix():
    assert normalize_metrics({"views": "3.5k"})["views"] == 3500
    assert normalize_metrics({"views": "12K"})["views"] == 12000
    assert normalize_metrics({"views": "2.4w"})["views"] == 24000


def test_comma_separated():
    assert normalize_metrics({"views": "1,234,567"})["views"] == 1234567


def test_plus_prefix_for_fans():
    assert normalize_metrics({"new_fans": "+128"})["new_fans"] == 128


def test_garbage_becomes_zero():
    assert normalize_metrics({"views": "暂无"})["views"] == 0
    assert normalize_metrics({"views": None})["views"] == 0
    assert normalize_metrics({"views": "--"})["views"] == 0


def test_negative_clamped_to_zero():
    # 播放量不可能为负；AI 看错负号不该污染数据
    assert normalize_metrics({"views": -5})["views"] == 0


def test_float_truncated():
    assert normalize_metrics({"views": 12.9})["views"] == 12


def test_unknown_keys_ignored():
    got = normalize_metrics({"views": 5, "完播率": "35%"})
    assert set(got) == set(METRIC_FIELDS)


def test_alias_keys_from_ai():
    # AI 有时用中文键名返回
    got = normalize_metrics({"播放": 100, "点赞": 20, "评论": 5,
                             "转发": 3, "涨粉": 8})
    assert got == {"views": 100, "likes": 20, "comments": 5,
                   "shares": 3, "new_fans": 8}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.media_metrics'`

- [ ] **Step 3: 实现 `app/services/media_metrics.py`**

```python
"""数据采集：截图识图 / 手填 / 归一化。

采集降级链（spec §3.4）：自动抓取 → 失败 → 截图识图 → 仍失败 → 手动填表单。
一期实现后两条；自动抓取因平台反爬不稳定，二期再评估。
"""
import base64
import logging
import re
import uuid

from app.services.ai_router import ask_ai_vision
from app.services.media_context import extract_json

log = logging.getLogger(__name__)

METRIC_FIELDS = ["views", "likes", "comments", "shares", "new_fans"]

# AI 有时用中文键名返回，做个映射
_ALIASES = {
    "播放": "views", "播放量": "views", "观看": "views",
    "点赞": "likes", "赞": "likes",
    "评论": "comments", "评论数": "comments",
    "转发": "shares", "分享": "shares",
    "涨粉": "new_fans", "新增粉丝": "new_fans", "粉丝": "new_fans",
}

_UNITS = {"万": 10_000, "w": 10_000, "W": 10_000,
          "k": 1_000, "K": 1_000,
          "亿": 100_000_000}


def _to_int(value) -> int:
    """把平台后台的各种数字写法转成整数。转不了返回 0。

    平台普遍显示"1.2万"而不是 12000，识图 AI 会原样返回，必须在这里统一。
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))

    s = str(value).strip().replace(",", "").replace("+", "")
    if not s:
        return 0

    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([万wWkK亿])?$", s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2)
    if unit:
        num *= _UNITS[unit]
    return max(0, int(num))


def normalize_metrics(raw: dict) -> dict:
    """把 AI 或表单给的原始字典，归一化成 5 个非负整数字段。

    纯函数：AI 输出脏数据是常态，归一化必须可测试、可预期。
    """
    src = dict(raw or {})
    for cn, en in _ALIASES.items():
        if cn in src and en not in src:
            src[en] = src[cn]
    return {f: _to_int(src.get(f)) for f in METRIC_FIELDS}


VISION_PROMPT = """这是自媒体平台后台的数据截图。请读出这条内容的数据。

只输出 JSON，不要任何解释：
{"views":播放量,"likes":点赞,"comments":评论,"shares":转发,"new_fans":涨粉}

规则：
- 数字保留截图上的原始写法（如"1.2万"就写"1.2万"），不要自己换算。
- 截图上没有的项填 0。
- 看不清就填 0，不要猜。"""


async def recognize_screenshot(image_bytes: bytes, media_type: str) -> dict:
    """识别后台截图里的数据。DeepSeek 不支持识图，ask_ai_vision 会自动选模型。"""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    result = await ask_ai_vision(
        VISION_PROMPT, [{"media_type": media_type, "data": b64}])
    resp = result.get("response", "")
    if resp.startswith("[错误]"):
        return {"ok": False, "error": resp, "data": {},
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "error": "识别结果无法解析，请手动填写", "data": {},
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    return {"ok": True, "data": normalize_metrics(obj), "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def save_metrics(db, publish_id: str, data: dict, collected_by: str) -> str:
    """写一条数据快照。每次采集都是新行，保留增长曲线。"""
    m = normalize_metrics(data)
    mid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_metrics "
        "(id,publish_id,views,likes,comments,shares,new_fans,collected_by) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (mid, publish_id, m["views"], m["likes"], m["comments"],
         m["shares"], m["new_fans"], collected_by))
    await db.commit()
    return mid
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/test_media_metrics.py -v`
Expected: 13 passed

- [ ] **Step 5: 在 `app/api/media.py` 追加数据路由**

顶部 import 追加：

```python
from fastapi import UploadFile, File
from app.services.media_metrics import recognize_screenshot, save_metrics
```

文件末尾追加：

```python
# ─────────────── 数据采集 ───────────────

@router.post("/media/publish/{pubid}/metrics")
async def metrics_manual(pubid: str, content_id: str = Form(...),
                         views: str = Form("0"), likes: str = Form("0"),
                         comments: str = Form("0"), shares: str = Form("0"),
                         new_fans: str = Form("0")):
    db = await get_db()
    try:
        await save_metrics(db, pubid, {
            "views": views, "likes": likes, "comments": comments,
            "shares": shares, "new_fans": new_fans}, "manual")
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{content_id}", status_code=302)


@router.post("/media/publish/{pubid}/metrics/screenshot")
async def metrics_screenshot(pubid: str, file: UploadFile = File(...)):
    """截图识别。识别失败时返回 ok=false，前端提示改用手填 —— 降级链的第二跳。"""
    try:
        raw = await file.read()
        if not raw:
            return JSONResponse({"ok": False, "error": "文件是空的"})
        media_type = file.content_type or "image/png"
        result = await recognize_screenshot(raw, media_type)
        if result.get("ok"):
            db = await get_db()
            try:
                await save_metrics(db, pubid, result["data"], "screenshot")
            finally:
                await db.close()
    except Exception as e:
        log.exception("截图识别失败")
        return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse(result)
```

- [ ] **Step 6: 在 `app/templates/media_content.html` 填充数据录入 UI**

把 `<div id="metrics-slot-{{ p.id }}"></div>` 整行替换为：

```html
          <div class="mt-2">
            <label class="block">
              <span class="text-xs text-violet-600 cursor-pointer">📷 截图识别</span>
              <input type="file" accept="image/*" class="hidden"
                     onchange="uploadShot('{{ p.id }}', this)">
            </label>
            <div id="shot-status-{{ p.id }}" class="hidden text-xs text-violet-600"></div>
            <details class="mt-1">
              <summary class="text-xs text-gray-400 cursor-pointer">✍️ 手动填</summary>
              <form method="post" action="/media/publish/{{ p.id }}/metrics"
                    class="mt-1 space-y-1">
                <input type="hidden" name="content_id" value="{{ content.id }}">
                <input name="views" placeholder="播放（可填 1.2万）"
                       class="w-full border rounded px-2 py-1 text-xs">
                <input name="likes" placeholder="点赞"
                       class="w-full border rounded px-2 py-1 text-xs">
                <input name="comments" placeholder="评论"
                       class="w-full border rounded px-2 py-1 text-xs">
                <input name="shares" placeholder="转发"
                       class="w-full border rounded px-2 py-1 text-xs">
                <input name="new_fans" placeholder="涨粉"
                       class="w-full border rounded px-2 py-1 text-xs">
                <button class="w-full bg-gray-100 rounded py-1 text-xs">保存数据</button>
              </form>
            </details>
          </div>
```

在 `<script>` 块内追加：

```javascript
async function uploadShot(pubId, input) {
  if (!input.files || !input.files[0]) return;
  const status = document.getElementById('shot-status-' + pubId);
  status.classList.remove('hidden');
  status.textContent = 'AI 识别中…';
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try {
    const r = await fetch('/media/publish/' + pubId + '/metrics/screenshot',
                          {method: 'POST', body: fd});
    const d = await r.json();
    if (d.ok) {
      status.textContent = '已录入，刷新中…';
      location.reload();
    } else {
      status.textContent = '识别失败：' + (d.error || '') + ' 请用下面的手动填写。';
    }
  } catch (e) {
    status.textContent = '上传失败：' + e + ' 请用手动填写。';
  }
  input.value = '';
}
```

- [ ] **Step 7: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

打开一条**已标记发布**的内容，验证：
1. 已发平台卡下方出现「📷 截图识别」和「✍️ 手动填」
2. 手动填 `views=1.2万`、`likes=350` 提交 → 卡片显示「播放 12000　赞 350」（**单位换算生效**）
3. 传一张真实的平台后台截图 → 识别后自动刷新，数据显示，来源标 `screenshot`
4. 传一张无关图片（如风景照）→ 提示识别失败并引导手动填（**降级链生效**）
5. 回到 `/media` 看板，该内容卡片的平台标签显示播放量

Expected: 全部通过。第 2 点验证 `normalize_metrics`，第 4 点验证降级链。

- [ ] **Step 8: 运行全量测试并 Commit**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed

```bash
git add app/services/media_metrics.py tests/test_media_metrics.py app/api/media.py app/templates/media_content.html
git commit -m "feat(media): 数据采集（截图识图 + 手填）

平台后台显示'1.2万'不是12000，归一化统一处理并全测试覆盖。
识图失败降级到手填，不让用户卡住。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: L1 复盘与候选人设条目（AI 能力 #5）

**Files:**
- Modify: `app/services/media_ai.py`（追加 `review_content`）
- Modify: `app/api/media.py`（追加复盘路由）
- Modify: `app/templates/media_content.html`（填充 `#review-section`）

**Interfaces:**
- Consumes: `media_metrics` 数据、脚本、`media_publish`
- Produces:
  - `async review_content(db, content_id: str, model: str = "auto") -> dict`
    返回 `{"ok", "review_count", "cost", "model", "error"}`
    副作用：写入 N 份平台复盘 + 1 份总复盘（`media_review`）+ 1 份归因（`media_case`）；候选人设条目存在总复盘的 `proposed_traits`，**不自动入库**
  - 路由：`POST /media/content/{cid}/ai-review`、`POST /media/content/{cid}/adopt-trait`

- [ ] **Step 1: 在 `app/services/media_ai.py` 追加 `review_content`**

```python
REVIEW_SYSTEM = """你是自媒体数据复盘专家。基于真实数据分析一条内容的表现。

铁律：
1. 结论必须基于给定数据，不许编造没给你的数字。
2. 区分「可复制的方法论」和「运气/热点」—— replicable 打分要诚实，
   蹭上热点的爆款打 1-2 分，方法论过硬的打 4-5 分。把运气当能力会让人学错。
3. 提炼人设条目时必须给证据，证据不足就少提甚至不提。
4. 只输出 JSON，不要解释。

输出格式：
{
  "platform_reviews": [
    {"platform":"douyin","what_worked":"","what_failed":"","next_action":""}
  ],
  "overall": {"what_worked":"","what_failed":"","next_action":""},
  "case": {
    "case_type":"hit|flop|normal",
    "threshold_basis":"判定依据",
    "topic_factor":"选题层归因","hook_factor":"开场钩子归因",
    "structure_factor":"结构归因","material_factor":"原料归因",
    "emotion_factor":"情绪曲线归因","platform_factor":"平台适配归因",
    "external_factor":"外部因素与运气成分",
    "replicable":3,
    "conclusion":"一句话结论"
  },
  "proposed_traits": [
    {"dimension":"positioning|audience|tone|topics|taboo|signature|differentiator",
     "content":"条目内容","brief":"≤30字精简版","evidence":"证据","confidence":3}
  ],
  "topic_fingerprint": "3-6个核心语义标签，逗号分隔，用于以后查重"
}

关于 topic_fingerprint：写这条内容"讲的是什么"的语义标签，不是标题复述。
例："职场妈妈,时间管理,愧疚感,边界感"。以后做同方向选题时靠它查重。"""


async def review_content(db, content_id: str, model: str = "auto") -> dict:
    """L1 单条复盘：N 份平台复盘 + 1 份总复盘 + 1 份归因 + 候选人设条目。

    候选条目绝不自动写入 trait 表 —— AI 提炼，人拍板。
    防止 AI 把偶然当规律污染人设（spec §5.2 关键设计）。

    看不到：原料库、话题池 —— 与复盘无关。
    """
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "review_count": 0,
                "cost": 0, "model": ""}
    content = dict(row)

    cur = await db.execute(
        "SELECT p.id AS publish_id, p.account_id, p.publish_text, a.platform, "
        "  m.views, m.likes, m.comments, m.shares, m.new_fans "
        "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
        "LEFT JOIN media_metrics m ON m.id = ("
        "  SELECT id FROM media_metrics WHERE publish_id=p.id "
        "  ORDER BY snapshot_at DESC LIMIT 1) "
        "WHERE p.content_id=? AND p.status='published'", (content_id,))
    pubs = [dict(r) for r in await cur.fetchall()]
    if not pubs:
        return {"ok": False, "error": "还没有已发布的平台，无法复盘",
                "review_count": 0, "cost": 0, "model": ""}
    if not any(p["views"] for p in pubs):
        return {"ok": False, "error": "还没有采集到数据，先录入播放量再复盘",
                "review_count": 0, "cost": 0, "model": ""}

    # 账号历史中位播放量 —— 判定爆款/失败的基准
    cur = await db.execute(
        "SELECT MAX(m.views) v FROM media_content c "
        "JOIN media_publish p ON p.content_id=c.id "
        "JOIN media_metrics m ON m.publish_id=p.id "
        "WHERE c.persona_id=? AND c.id != ? GROUP BY c.id",
        (content["persona_id"], content_id))
    history = sorted(r["v"] or 0 for r in await cur.fetchall())
    median = history[len(history) // 2] if history else 0

    parts = [f"【选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if content["script"]:
        parts.append(f"【口播脚本】\n{content['script'][:3000]}")
    parts.append("【各平台数据】\n" + "\n".join(
        f"- {p['platform']}：播放 {p['views'] or 0}，赞 {p['likes'] or 0}，"
        f"评 {p['comments'] or 0}，转 {p['shares'] or 0}，粉 +{p['new_fans'] or 0}"
        for p in pubs))
    parts.append(f"【账号历史中位播放量】{median}"
                 f"（用于判定 case_type：显著高于为 hit，显著低于为 flop）")
    parts.append("请复盘这条内容。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_review",
                          system_prompt=REVIEW_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "review_count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "error": "复盘结果无法解析", "review_count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 重跑复盘时清掉旧的，避免堆积
    await db.execute("DELETE FROM media_review WHERE content_id=?", (content_id,))
    await db.execute("DELETE FROM media_case WHERE content_id=?", (content_id,))

    by_platform = {p["platform"]: p["account_id"] for p in pubs}
    count = 0
    for pr in obj.get("platform_reviews") or []:
        if not isinstance(pr, dict):
            continue
        aid = by_platform.get(pr.get("platform", ""), "")
        await db.execute(
            "INSERT INTO media_review "
            "(id,content_id,scope,account_id,what_worked,what_failed,next_action) "
            "VALUES (?,?,'platform',?,?,?,?)",
            (str(uuid.uuid4()), content_id, aid,
             (pr.get("what_worked") or "").strip(),
             (pr.get("what_failed") or "").strip(),
             (pr.get("next_action") or "").strip()))
        count += 1

    ov = obj.get("overall") or {}
    traits = [t for t in (obj.get("proposed_traits") or []) if isinstance(t, dict)]
    await db.execute(
        "INSERT INTO media_review "
        "(id,content_id,scope,what_worked,what_failed,next_action,proposed_traits) "
        "VALUES (?,?,'overall',?,?,?,?)",
        (str(uuid.uuid4()), content_id,
         (ov.get("what_worked") or "").strip(),
         (ov.get("what_failed") or "").strip(),
         (ov.get("next_action") or "").strip(),
         json.dumps(traits, ensure_ascii=False)))
    count += 1

    case = obj.get("case") or {}
    case_type = case.get("case_type") if case.get("case_type") in \
        ("hit", "flop", "normal") else "normal"
    await db.execute(
        "INSERT INTO media_case "
        "(id,persona_id,content_id,case_type,threshold_basis,topic_factor,"
        " hook_factor,structure_factor,material_factor,emotion_factor,"
        " platform_factor,external_factor,replicable,conclusion) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), content["persona_id"], content_id, case_type,
         (case.get("threshold_basis") or "").strip(),
         (case.get("topic_factor") or "").strip(),
         (case.get("hook_factor") or "").strip(),
         (case.get("structure_factor") or "").strip(),
         (case.get("material_factor") or "").strip(),
         (case.get("emotion_factor") or "").strip(),
         (case.get("platform_factor") or "").strip(),
         (case.get("external_factor") or "").strip(),
         _clamp(case.get("replicable"), 3),
         (case.get("conclusion") or "").strip()))

    # outcome + fingerprint 供三期查重用：以后撞到同方向的 flop 会强提示。
    # 一期就写入，否则三期开工时历史内容全是空指纹，查重形同虚设。
    fingerprint = (obj.get("topic_fingerprint") or "").strip()[:200]
    await db.execute(
        "UPDATE media_content SET outcome=?, topic_fingerprint=?, stage='reviewed', "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (case_type, fingerprint, content_id))
    await db.commit()

    await log_injection(db, content_id, "review_content", [],
                        result.get("tokens", 0))

    return {"ok": True, "review_count": count, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 2: 在 `app/api/media.py` 追加复盘路由**

顶部 import 追加 `review_content`：

```python
from app.services.media_ai import (
    recommend_topics, write_script, generate_platform_copy, review_content,
)
```

文件末尾追加：

```python
# ─────────────── 复盘 ───────────────

@router.post("/media/content/{cid}/ai-review")
async def content_ai_review(cid: str):
    db = await get_db()
    try:
        try:
            result = await review_content(db, cid)
        except Exception as e:
            log.exception("AI 复盘失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/adopt-trait")
async def adopt_trait(cid: str, dimension: str = Form(...), content: str = Form(...),
                      brief: str = Form(""), evidence: str = Form(""),
                      confidence: int = Form(3)):
    """把 AI 提炼的候选条目写入人设 —— 人拍板这一步是故意保留的。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if row:
            pid = row["persona_id"]
            cur = await db.execute(
                "SELECT current_phase FROM media_persona WHERE id=?", (pid,))
            prow = await cur.fetchone()
            await db.execute(
                "INSERT INTO media_persona_trait "
                "(id,persona_id,dimension,content,brief,source,source_content_id,"
                " evidence,confidence,phase_tag) "
                "VALUES (?,?,?,?,?,'ai_from_review',?,?,?,?)",
                (str(uuid.uuid4()), pid, dimension, content.strip(),
                 brief.strip()[:30], cid, evidence.strip(), confidence,
                 prow["current_phase"] if prow else ""))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)
```

- [ ] **Step 3: 在 `app/templates/media_content.html` 填充 `#review-section`**

把 `<div id="review-section"></div>` 整行替换为：

```html
  <div class="bg-white rounded-xl shadow-sm p-4 mb-3">
    <div class="flex items-center justify-between mb-2">
      <h2 class="font-semibold text-sm">复盘</h2>
      <button onclick="aiReview()" id="review-btn"
              class="bg-violet-600 text-white rounded-lg px-3 py-1.5 text-xs">
        ✨ AI 复盘
      </button>
    </div>
    <div id="review-status" class="hidden text-xs text-violet-600 mb-2"></div>

    {% if not reviews %}
      <p class="text-sm text-gray-400">
        发布并录入数据后，点「AI 复盘」——
        复盘产出的人设条目要你确认才入库。
      </p>
    {% endif %}

    {% for r in reviews %}
    <div class="border-l-2 {% if r.scope == 'overall' %}border-blue-600{% else %}border-gray-200{% endif %} pl-3 py-2 mb-2">
      <div class="text-xs font-medium text-gray-500 mb-1">
        {% if r.scope == 'overall' %}总复盘{% else %}平台复盘{% endif %}
      </div>
      {% if r.what_worked %}
      <div class="text-sm"><span class="text-green-600">✓</span> {{ r.what_worked }}</div>
      {% endif %}
      {% if r.what_failed %}
      <div class="text-sm"><span class="text-amber-600">✗</span> {{ r.what_failed }}</div>
      {% endif %}
      {% if r.next_action %}
      <div class="text-sm text-gray-500 mt-1">→ {{ r.next_action }}</div>
      {% endif %}

      {% if r.proposed_traits %}
      <div class="mt-3 bg-violet-50 rounded-lg p-3">
        <div class="text-xs font-medium text-violet-700 mb-2">
          AI 提炼的候选人设条目（确认后才入库）
        </div>
        {% for t in r.proposed_traits %}
        <div class="bg-white rounded p-2 mb-2">
          <div class="text-sm">{{ t.content }}</div>
          {% if t.evidence %}
          <div class="text-xs text-gray-400 mt-0.5">依据：{{ t.evidence }}</div>
          {% endif %}
          <form method="post" action="/media/content/{{ content.id }}/adopt-trait"
                class="mt-1.5">
            <input type="hidden" name="dimension" value="{{ t.dimension }}">
            <input type="hidden" name="content" value="{{ t.content }}">
            <input type="hidden" name="brief" value="{{ t.brief }}">
            <input type="hidden" name="evidence" value="{{ t.evidence }}">
            <input type="hidden" name="confidence" value="{{ t.confidence }}">
            <button class="bg-blue-600 text-white rounded px-3 py-1 text-xs">
              确认加入人设
            </button>
          </form>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
```

在 `<script>` 块内追加：

```javascript
async function aiReview() {
  const btn = document.getElementById('review-btn');
  const status = document.getElementById('review-status');
  btn.disabled = true;
  btn.textContent = '复盘中…';
  status.classList.remove('hidden');
  status.textContent = 'AI 正在对比各平台数据做归因…';
  try {
    const r = await fetch('/media/content/{{ content.id }}/ai-review',
                          {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      status.textContent = '完成，刷新中…';
      location.reload();
    } else {
      status.textContent = '失败：' + (d.error || '未知错误');
      btn.disabled = false;
      btn.textContent = '✨ AI 复盘';
    }
  } catch (e) {
    status.textContent = '请求失败：' + e;
    btn.disabled = false;
    btn.textContent = '✨ AI 复盘';
  }
}
```

- [ ] **Step 4: 启动服务验证**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

打开一条**已发布且已录入数据**的内容，验证：
1. 点「✨ AI 复盘」→ 刷新后出现 N 份平台复盘 + 1 份总复盘（总复盘左边框是蓝色）
2. 总复盘下方出现紫色的「AI 提炼的候选人设条目」区，每条有依据和「确认加入人设」按钮
3. **候选条目此时还没进人设** —— 打开 `/media/persona/<pid>` 确认条目列表没变
4. 点「确认加入人设」→ 跳回内容页；再打开人设档案，条目已出现
5. 进度条自动跳到「已复盘」
6. 对**没录数据**的内容点复盘 → 提示「还没有采集到数据，先录入播放量再复盘」

Expected: 全部通过。第 3 点是「AI 提炼，人拍板」生效的关键证据。

- [ ] **Step 5: 验证 case 归因已写入（三期分析的数据基础）**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3
con = sqlite3.connect('data/aipm.db')
rows = con.execute('SELECT case_type, replicable, hook_factor, conclusion FROM media_case').fetchall()
print('case 行数:', len(rows))
for r in rows: print(r)
print('---')
print('outcome+指纹:', con.execute('SELECT title, outcome, topic_fingerprint FROM media_content WHERE outcome != \"\"').fetchall())
"
```

Expected: 至少 1 行 case，`replicable` 在 1-5 之间，`hook_factor` 非空；对应 content 的 `outcome` 和 `topic_fingerprint` 都已写入（指纹是语义标签而非标题复述）

- [ ] **Step 6: 运行全量测试并 Commit**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed

```bash
git add app/services/media_ai.py app/api/media.py app/templates/media_content.html
git commit -m "feat(media): L1 复盘与候选人设条目

候选条目绝不自动入库 —— AI 提炼，人拍板，防止把偶然当规律污染人设。
case 归因一期就写入，replicable 诚实区分能力与运气。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: 端到端闭环验收

**Files:**
- 无新文件。这是一次完整走查，验证闭环真的闭上了。

**Interfaces:**
- Consumes: Task 1-11 全部
- Produces: 一份可用的一期系统

- [ ] **Step 1: 运行全量测试**

Run: `cd D:\GAGA-5-25\ai-pm && python -m pytest tests/ -v`
Expected: 全部 passed，无 warning 级别的错误

- [ ] **Step 2: 清空 media 测试数据，从零走一遍完整闭环**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3
con = sqlite3.connect('data/aipm.db')
for t in ['media_injection_log','media_case','media_review','media_metrics',
          'media_publish','media_content','media_topic','media_account',
          'media_persona_trait','media_persona']:
    con.execute('DELETE FROM ' + t)
con.commit()
print('media 数据已清空')
"
```

- [ ] **Step 3: 走完整闭环（每步都要成功才能进下一步）**

Run: `cd D:\GAGA-5-25\ai-pm && python run.py`

按顺序操作并逐项打勾：

1. `/media` → 自动跳到人设创建页
2. 创建人设（名字 + 一句话定位）
3. 人设档案手动加 3 条条目，其中 1 条 `dimension=记忆点`
4. 添加抖音 + 小红书两个账号
5. `/media/topics` → 点「AI 推选题」→ 出现 5 条带 ❓ 谜题的话题
6. 弃 1 条并填原因 → 再点「AI 推选题」→ 新推荐无同类方向
7. 采用 1 条 → 跳到内容详情，选题和谜题已带过来
8. 点「AI 写脚本」→ 状态栏显示注入条数 ≤ 11 → 保存 → 阶段自动到「脚本」
9. 手动推进：脚本 → 待录 → 待剪 → 待发
10. 抖音卡「生成文案」→ 小红书卡「生成文案」→ **两版明显不同**
11. 两个平台都填链接并「标记已发」→ 阶段到「已发」
12. 抖音手填 `views=1.2万`，小红书手填 `views=800` → 卡片显示 12000 和 800
13. 点「AI 复盘」→ 出现 2 份平台复盘 + 1 份总复盘 + 候选人设条目
14. 「确认加入人设」1 条 → 人设档案里出现该条目，来源标记为 AI
15. 回 `/media` → 内容在「已复盘」列，卡片显示两个绿色平台标签和播放量
16. **闭环验证：** 再点「AI 推选题」→ 新推荐应体现刚加入的人设条目和这条内容的表现

Expected: 16 步全部通过。第 16 步是飞轮闭合的直接证据 —— 这一轮的产出影响了下一轮的输入。

- [ ] **Step 4: 移动端走查（375px 宽度）**

浏览器开发者工具切到 375px，逐页检查**页面 body 不出现横向滚动条**：

1. `/media` 看板 —— 列可横向滚动，但 body 不滚动；FAB 在 bottom:80px 不挡底部导航
2. `/media/topics` —— 话题卡单列
3. `/media/persona/<id>` —— 两栏变单栏
4. `/media/content/<id>` —— 三平台发布卡单列；进度条可横向滚动

Expected: 4 页全部无 body 横向滚动

- [ ] **Step 5: 验证注入日志覆盖所有 AI 调用**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3
con = sqlite3.connect('data/aipm.db')
rows = con.execute('SELECT ai_type, COUNT(*), SUM(token_count) FROM media_injection_log GROUP BY ai_type').fetchall()
for r in rows: print(r)
"
```

Expected: 至少出现 4 类 —— `recommend_topics`、`write_script:full`、`platform_copy:*`、`review_content`。这是三期分析的数据基础，一期就在积累。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(media): 一期端到端闭环验收通过

闭环已闭合：本轮沉淀的人设条目与数据表现，影响下一轮选题推荐。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 一期完成后的状态

**已跑通的闭环：**
```
AI推选题(带谜题) → 采用 → AI写脚本(注入预算内的人设资产) → 人录 → 人剪
→ AI生成三平台差异化文案 → 人发布 → 数据(截图识图/手填)
→ AI复盘(平台×N + 总盘) → AI提炼候选人设条目 → 人确认入库
→ 回到推选题（这次 AI 知道得更多了）
```

**一期建立但一期不分析的数据（三期直接可用）：**
- `media_injection_log` —— 每次 AI 调用注入了什么、花了多少 token
- `media_case` —— 每条内容的七维归因 + `replicable` 可复制性评分
- `media_content.outcome` —— hit/flop/normal
- `media_content.topic_fingerprint` —— 语义标签，二期决策引擎和三期查重的依据

**二期接续点：** `media_decision.py` 决策引擎（11 项打分）、受众画像、生意锚点、原料库、打法库、教训、红线。二期开工时 `build_script_context()` 只需扩展注入槽位，预算机制无需改动。

**一期不做但已留好口子：**
- 多人设：`_first_persona_id()` 是唯一的单人设假设点，二期换成人设切换器即可
- 平台扩展：`PLATFORMS` 加一行 + `PLATFORM_STYLE` 加一条即可支持 B站/快手
- 自动抓取数据：`media_metrics.py` 已有 `save_metrics(collected_by=...)`，加个 `auto` 来源即可
