# 教训/红线库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给自媒体模块加一个「不要做什么」的库（教训/红线），写稿时按注意力纪律注入相关的少数几条，让用户的每一次纠正开始复利而不是一次性消费。

**Architecture:** 新表 `media_lesson`（教训与红线同表、`kind` 区分、按人设独享）。`media_context.py` 加三个纯函数做筛选与渲染——红线单独占注入槽不与教训竞争（照 `signature` 记忆点先例）。`write_script` 在提示词末尾注入。产出侧两条入口：L2 复盘的 `advisory` 加采纳按钮，助手对话加 `propose_lesson` 工具走现有待确认卡。

**Tech Stack:** Python 3 / FastAPI / Jinja2 / aiosqlite (SQLite) / pytest（无 pytest-asyncio，异步测试用 `asyncio.run`）

**Spec:** `docs/superpowers/specs/2026-08-31-media-lesson-memory-design.md`

## Global Constraints

- **注意力纪律（项目宪法第 1 条）**：注入提示词的只有 `brief`，`detail` 永不进 prompt。任何新注入点必须登记在 `INJECTION_BUDGET`，未登记的槽位 `select_by_budget` 会拒绝。
- **人拍板（宪法第 2 条）**：AI 只提候选，绝不自动写 `media_lesson`。所有入库路径都要人点确认。
- **动作日志是唯一撤销底座（宪法第 7 条）**：新的「写库」动作复用 `log_action` / `apply_action` / `revert_action`，各加一个 `action_type` 分支，不另造机制。
- **注入配额**：`redline: 2`，`lesson: 3`。红线与教训**不共享配额**。
- **迁移**：新表走 `SCHEMA`（`CREATE TABLE IF NOT EXISTS`），重启自动建，零手动。不写 `MIGRATIONS`。
- **不做**（spec §2 非目标）：AI 自动写库、`revise_draft`/`critique_draft` 注入、`weight_suggestion` 自动改 `WEIGHTS`、红线跨人设共享、任何定时/主动推送。
- **测试**：`python -m pytest`。当前基线 380 passed，收工必须全绿。
- **改模板一律用编辑器工具**，不要用 PowerShell `-replace`（会把中文搞乱码）。
- **本地起 dev server**：`pkill -f uvicorn` 在 Windows 上杀不掉，会留旧进程占端口跑旧代码骗过冒烟。正确做法：`netstat -ano | grep :<端口> | grep LISTENING` 拿 PID → `taskkill //F //PID <pid>`，或直接换端口（习惯用 8011~8013）。
- **分支**：本计划在 feature 分支上做，不要直接提交到 `main`。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/database.py` | 修改 | `SCHEMA` 里加 `media_lesson` 建表语句 |
| `app/services/media_context.py` | 修改 | `INJECTION_BUDGET` 加 `redline`；加 `select_redlines` / `select_lessons` / `render_lesson_block` 三个纯函数 |
| `app/services/media_ai.py` | 修改 | `write_script` 查库、注入、记 `hit_count`；`L2` 用不到 |
| `app/services/media_review_cycle.py` | 修改 | `L2_SYSTEM` 输出契约结构化；加 `normalize_advisory_items` 归一化旧数据 |
| `app/services/media_lesson.py` | **新建** | `media_lesson` 的 CRUD（list/create/update/archive/delete），单一职责，不掺 AI |
| `app/services/media_agent_tools.py` | 修改 | 加 `_tool_propose_lesson` + schema，归 `_CORE` |
| `app/services/media_assistant.py` | 修改 | `apply_action` / `revert_action` 各加 `propose_lesson` 分支；`MEDIA_ASSISTANT_SYSTEM` 加主动性指引 |
| `app/api/media.py` | 修改 | 本子页路由 + CRUD 路由 + `/media/lesson/adopt` |
| `app/api/media_ui.py` | 修改 | `/media/ui/steps` 的 `libs` 加 `lesson` 计数 |
| `app/templates/media_lessons.html` | **新建** | 本子页 |
| `app/templates/_media_shell.html` | 修改 | 体系库面板加「教训/红线」入口卡 |
| `app/templates/media_review_cycle.html` | 修改 | advisory 条目旁加「采纳进本子」按钮 |
| `tests/test_media_lesson_select.py` | **新建** | 三个纯函数 |
| `tests/test_media_lesson_inject.py` | **新建** | `write_script` 注入路径 + `hit_count` |
| `tests/test_media_lesson_crud.py` | **新建** | service CRUD + 路由 |
| `tests/test_media_lesson_advisory.py` | **新建** | advisory 归一化 + 采纳 |
| `tests/test_media_lesson_assistant.py` | **新建** | `propose_lesson` 工具 + apply/revert |

**为什么单开 `media_lesson.py`**：`media.py` 已经 2186 行。本子的 CRUD 是自足的一块（无 AI、无跨模块依赖），放独立 service 让路由层只做参数解析和渲染，与 `media_batch.py` / `media_legacy.py` 的先例一致。

---

## Task 1: 建表 + 三个纯函数

**Files:**
- Modify: `app/database.py`（`SCHEMA` 字符串内，`media_playbook` 建表之后、`media_mine_candidate` 之前）
- Modify: `app/services/media_context.py:16-24`（`INJECTION_BUDGET`）与文件末尾
- Test: `tests/test_media_lesson_select.py`（新建）

**Interfaces:**
- Consumes: `INJECTION_BUDGET`（已存在，`media_context.py:16`）、`media_decision._overlap`（已存在，`media_decision.py:38`，纯函数无副作用，`media_decision` 仅 `import json` 故无循环依赖）
- Produces:
  - `select_redlines(lessons: list[dict]) -> list[dict]`
  - `select_lessons(lessons: list[dict], topic_text: str) -> list[dict]`
  - `render_lesson_block(redlines: list[dict], lessons: list[dict]) -> str`
  - 表 `media_lesson`，列：`id, persona_id, kind, brief, detail, trigger_context, evidence, source, status, hit_count, created_at`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_media_lesson_select.py`：

```python
"""教训/红线库的筛选与渲染（纯函数，无 DB 无 AI）。"""
from app.services.media_context import (
    select_redlines, select_lessons, render_lesson_block)


def _mk(kind, brief, trigger="", created="2026-08-01"):
    return {"id": brief, "kind": kind, "brief": brief,
            "trigger_context": trigger, "created_at": created}


def test_redlines_capped_at_two():
    """红线超过 2 条时只取前 2（created_at 升序）。"""
    reds = [_mk("redline", "红一", created="2026-08-01"),
            _mk("redline", "红二", created="2026-08-02"),
            _mk("redline", "红三", created="2026-08-03")]
    picked = select_redlines(reds)
    assert [r["brief"] for r in picked] == ["红一", "红二"]


def test_redlines_ignore_lesson_kind():
    """select_redlines 只认 kind='redline'，教训不混进来。"""
    items = [_mk("lesson", "教训甲"), _mk("redline", "红一")]
    assert [r["brief"] for r in select_redlines(items)] == ["红一"]


def test_lessons_ranked_by_trigger_overlap():
    """教训按 trigger_context 与选题文本的 bigram 重合度降序。"""
    items = [_mk("lesson", "不相干", trigger="做菜的时候"),
             _mk("lesson", "该命中", trigger="讲方法论类的内容")]
    picked = select_lessons(items, "四步方法论，让AI稳定做好一件事")
    assert picked[0]["brief"] == "该命中"


def test_lessons_capped_at_three():
    items = [_mk("lesson", f"教训{i}", trigger="讲方法论") for i in range(5)]
    assert len(select_lessons(items, "讲方法论")) == 3


def test_lesson_without_trigger_ranks_last():
    """trigger_context 为空视为 0 分，排在有 trigger 的之后（但不被排除）。"""
    items = [_mk("lesson", "没trigger", trigger=""),
             _mk("lesson", "有trigger", trigger="讲方法论")]
    picked = select_lessons(items, "讲方法论怎么落地")
    assert [x["brief"] for x in picked] == ["有trigger", "没trigger"]


def test_redlines_and_lessons_do_not_compete():
    """核心语义：红线与教训分槽，3 条高匹配教训不会挤掉任何红线。"""
    items = ([_mk("redline", f"红{i}") for i in range(2)]
             + [_mk("lesson", f"教{i}", trigger="讲方法论") for i in range(3)])
    reds = select_redlines(items)
    less = select_lessons(items, "讲方法论")
    assert len(reds) == 2 and len(less) == 3


def test_render_both_blocks():
    block = render_lesson_block(
        [_mk("redline", "不许编数据")],
        [_mk("lesson", "开头别铺垫")])
    assert "【红线（绝对不许违反）】" in block
    assert "- 不许编数据" in block
    assert "【教训（这次特别注意）】" in block
    assert "- 开头别铺垫" in block


def test_render_empty_returns_empty_string():
    """两者皆空返回空串，绝不产生一个只有标题的空块。"""
    assert render_lesson_block([], []) == ""


def test_render_skips_blank_brief():
    """brief 全空时那一块整块不渲染（不留裸标题）。"""
    assert render_lesson_block([_mk("redline", "")], []) == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_lesson_select.py -v`
Expected: FAIL —— `ImportError: cannot import name 'select_redlines' from 'app.services.media_context'`

- [ ] **Step 3: 加建表语句**

在 `app/database.py` 的 `SCHEMA` 字符串里，`media_playbook` 建表语句之后、`media_mine_candidate` 之前插入：

```sql
CREATE TABLE IF NOT EXISTS media_lesson (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    kind TEXT DEFAULT 'lesson',
    brief TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    trigger_context TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    hit_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: 加注入槽位**

`app/services/media_context.py` 的 `INJECTION_BUDGET`，在 `"lesson": 3,` 那行之后加一行：

```python
    "redline": 2,     # 红线：无条件带，不做匹配。单独占槽不与 lesson 竞争
```

同时把 `"lesson"` 那行的注释改成（原注释写的是「二期」，现在就是这轮）：

```python
    "lesson": 3,      # 教训：按 trigger_context 与选题文本的重合度取前 3
```

- [ ] **Step 5: 加三个纯函数**

`app/services/media_context.py`，在 `render_material_block` 之后（文件末尾）追加。注意 import 放文件顶部现有 import 区：

```python
from app.services.media_decision import _overlap as _text_overlap
```

```python
# ─────────────── 教训/红线库注入 ───────────────
# 红线与教训分槽，互不挤占 —— 照 signature（记忆点）的先例：
# 硬约束不能被一条恰好匹配度高的软建议挤掉（spec §4.2）。

def select_redlines(lessons: list[dict]) -> list[dict]:
    """红线：无条件取前 INJECTION_BUDGET['redline'] 条（created_at 升序）。

    不做适用性匹配 —— 红线语义就是「任何时候都不许」，对它做匹配自相矛盾。
    上限 2 是有意的设计压力：红线一多就不值钱。
    """
    cap = INJECTION_BUDGET.get("redline")
    if not cap:
        log.warning("select_redlines: redline 槽位未登记，拒绝注入")
        return []
    reds = [x for x in lessons if (x.get("kind") or "") == "redline"]
    reds.sort(key=lambda x: x.get("created_at") or "")
    return reds[:cap]


def select_lessons(lessons: list[dict], topic_text: str) -> list[dict]:
    """教训：按 trigger_context 与 topic_text 的 bigram 重合度降序取前 N。

    trigger_context 为空视为 0 分，排最后（配额有余时仍可进，不主动排除）。
    sorted 是稳定排序，同分保持原有顺序。
    """
    cap = INJECTION_BUDGET.get("lesson")
    if not cap:
        log.warning("select_lessons: lesson 槽位未登记，拒绝注入")
        return []
    items = [x for x in lessons if (x.get("kind") or "") == "lesson"]
    ranked = sorted(
        items,
        key=lambda x: _text_overlap(x.get("trigger_context") or "", topic_text or ""),
        reverse=True)
    return ranked[:cap]


def _brief_lines(items: list[dict], title: str) -> str:
    lines = [title]
    lines += [f"- {b}" for b in
              ((x.get("brief") or "").strip() for x in items) if b]
    return "\n".join(lines) if len(lines) > 1 else ""


def render_lesson_block(redlines: list[dict], lessons: list[dict]) -> str:
    """渲染本子注入块。只放 brief —— detail 永不进提示词（注意力纪律）。

    两者皆空（或 brief 全空）返回空串，绝不产生只有标题的空块。
    """
    parts = [t for t in (
        _brief_lines(redlines, "【红线（绝对不许违反）】"),
        _brief_lines(lessons, "【教训（这次特别注意）】"),
    ) if t]
    return "\n\n".join(parts)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_lesson_select.py -v`
Expected: PASS，9 passed

- [ ] **Step 7: 跑全量确认没打破别的**

Run: `python -m pytest -q`
Expected: 380 passed + 9 = 389 passed，0 failed

- [ ] **Step 8: 提交**

```bash
git add app/database.py app/services/media_context.py tests/test_media_lesson_select.py
git commit -m "feat: media_lesson 表 + 教训/红线筛选渲染纯函数

红线与教训分槽（redline:2 / lesson:3），互不挤占——照 signature 先例：
硬约束不能被一条恰好匹配度高的软建议挤掉。红线不做匹配（语义即「任何
时候都不许」），教训按 trigger_context 的 bigram 重合度排序，复用
media_decision._overlap 不新造轮子。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: 接进 write_script

**Files:**
- Modify: `app/services/media_ai.py`（import 区、`write_script` 函数体）
- Test: `tests/test_media_lesson_inject.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `select_redlines` / `select_lessons` / `render_lesson_block`；已有的 `log_injection(db, content_id, ai_type, asset_ids, token_count)`
- Produces: `write_script` 返回值的 `injected_count` 现在把 lesson 也算进去；`media_lesson.hit_count` 会被递增

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_media_lesson_inject.py`：

```python
"""教训/红线注入 write_script 的路径。用内存 DB + 假 AI，不打真模型。"""
import asyncio
import uuid

from app.services import media_ai
from tests.media_helpers import make_db, fake_ai, seed_content


async def _seed_lesson(db, persona_id, kind, brief, trigger="", status="active"):
    lid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_lesson (id,persona_id,kind,brief,trigger_context,status) "
        "VALUES (?,?,?,?,?,?)", (lid, persona_id, kind, brief, trigger, status))
    await db.commit()
    return lid


def test_redline_and_lesson_both_injected(monkeypatch):
    """红线无条件进，教训按匹配进，两者同时出现在提示词里。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await _seed_lesson(db, "P1", "lesson", "开头别铺垫", trigger="企业落地AI")
        out = await media_ai.write_script(db, cid)
        await db.close()
        return out

    out = asyncio.run(run())
    assert out["ok"] is True
    assert "【红线（绝对不许违反）】" in seen["prompt"]
    assert "不许编造数据" in seen["prompt"]
    assert "开头别铺垫" in seen["prompt"]


def test_lesson_block_sits_before_final_instruction(monkeypatch):
    """位置：本子在「请写出…」那句之前（近因效应，spec §4.5）。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    p = seen["prompt"]
    assert p.index("不许编造数据") < p.index("请写出这条内容的口播脚本。")


def test_lean_mode_skips_lessons(monkeypatch):
    """lean 模式语义是「只给身份行做对照」，不注入本子。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await media_ai.write_script(db, cid, mode="lean")
        await db.close()

    asyncio.run(run())
    assert "不许编造数据" not in seen["prompt"]


def test_archived_lesson_not_injected(monkeypatch):
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "已归档的红线", status="archived")
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    assert "已归档的红线" not in seen["prompt"]


def test_hit_count_increments_on_success(monkeypatch):
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai("稿子正文"))

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        lid = await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await media_ai.write_script(db, cid)
        cur = await db.execute("SELECT hit_count FROM media_lesson WHERE id=?", (lid,))
        n = (await cur.fetchone())["hit_count"]
        await db.close()
        return n

    assert asyncio.run(run()) == 1


def test_hit_count_not_incremented_on_ai_error(monkeypatch):
    """AI 报错时不计数——hit_count 要回答的是「参与过一次成品生产吗」。"""
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai("[错误] 模型不可用"))

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        lid = await _seed_lesson(db, "P1", "redline", "不许编造数据")
        out = await media_ai.write_script(db, cid)
        cur = await db.execute("SELECT hit_count FROM media_lesson WHERE id=?", (lid,))
        n = (await cur.fetchone())["hit_count"]
        await db.close()
        return out, n

    out, n = asyncio.run(run())
    assert out["ok"] is False and n == 0


def test_no_lessons_prompt_has_no_empty_block(monkeypatch):
    """库里一条都没有时，提示词里不该多出空标题。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    assert "【红线" not in seen["prompt"] and "【教训" not in seen["prompt"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_lesson_inject.py -v`
Expected: FAIL —— `sqlite3.OperationalError: no such table: media_lesson` 不会出现（Task 1 已建表），实际失败是断言失败：`assert "【红线（绝对不许违反）】" in seen["prompt"]`

- [ ] **Step 3: 扩 import**

`app/services/media_ai.py` 第 13-16 行的 import 块，加三个名字：

```python
from app.services.media_context import (
    build_script_context, render_evidence_block, render_angle_block,
    select_materials, render_material_block,
    select_redlines, select_lessons, render_lesson_block,
)
```

- [ ] **Step 4: 查库并拼块**

在 `write_script` 里，`playbook` 那段之后、`parts = [context_text]` 之前，插入：

```python
    # ── 教训/红线库：红线无条件带，教训按 trigger_context 匹配 ──
    lesson_ids, lesson_block = [], ""
    if mode != "lean":
        cur = await db.execute(
            "SELECT * FROM media_lesson WHERE persona_id=? AND status='active'",
            (content["persona_id"],))
        all_lessons = [dict(r) for r in await cur.fetchall()]
        topic_text = " ".join(x for x in (content.get("title"),
                                          content.get("puzzle"),
                                          content.get("idea_reason")) if x)
        picked_red = select_redlines(all_lessons)
        picked_les = select_lessons(all_lessons, topic_text)
        lesson_ids = [x["id"] for x in picked_red] + [x["id"] for x in picked_les]
        lesson_block = render_lesson_block(picked_red, picked_les)
```

- [ ] **Step 5: 插进提示词末尾**

同函数内，把这两行：

```python
    if hint and hint.strip():
        parts.append(f"【本次重写要求（务必满足）】{hint.strip()}")
    parts.append("请写出这条内容的口播脚本。")
```

改成：

```python
    if hint and hint.strip():
        parts.append(f"【本次重写要求（务必满足）】{hint.strip()}")
    # 本子放最末尾：近因效应，AI 对靠近末尾的约束更敏感（spec §4.5）
    if lesson_block:
        parts.append(lesson_block)
    parts.append("请写出这条内容的口播脚本。")
```

- [ ] **Step 6: 成功后才计数**

同函数内，把 `all_injected = injected_ids + material_ids` 那段改成：

```python
    all_injected = injected_ids + material_ids + lesson_ids
    # hit_count 只在 AI 成功出稿后递增：它回答的是「这条真的参与过一次
    # 成品生产吗」，不是「被拼进过几次提示词」。报错/费用保护/空返回都不计。
    for lid in lesson_ids:
        await db.execute(
            "UPDATE media_lesson SET hit_count = hit_count + 1 WHERE id=?", (lid,))
    if lesson_ids:
        await db.commit()
    await log_injection(db, content_id, f"write_script:{mode}",
                        all_injected, result.get("tokens", 0))
```

（这段位置在 `UPDATE media_content SET ai_draft=...` 与 `await db.commit()` 之后，所有 `return {"ok": False, ...}` 的早退分支之下——那些分支天然不会走到这里，正是我们要的语义。）

- [ ] **Step 7: 跑测试确认通过**

Run: `python -m pytest tests/test_media_lesson_inject.py -v`
Expected: PASS，7 passed

- [ ] **Step 8: 跑全量**

Run: `python -m pytest -q`
Expected: 396 passed，0 failed

- [ ] **Step 9: 提交**

```bash
git add app/services/media_ai.py tests/test_media_lesson_inject.py
git commit -m "feat: write_script 注入教训/红线

位置在提示词最末尾、紧挨「请写出…」——近因效应，红线放开头会被前面
一两千字的人设/素材/打法冲淡。lean 模式不注入（语义是只给身份行做对照）。
hit_count 只在 AI 成功出稿后递增，报错/空返回不计。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: 本子页（service + 路由 + 模板 + 入口）

**Files:**
- Create: `app/services/media_lesson.py`
- Create: `app/templates/media_lessons.html`
- Modify: `app/api/media.py`（import 区 + 路由，加在 `/media/playbook` 路由之后）
- Modify: `app/api/media_ui.py:62-78`（`libs` 字典）
- Modify: `app/templates/_media_shell.html`（体系库 `libgrid` 加一张卡）
- Test: `tests/test_media_lesson_crud.py`（新建）

**Interfaces:**
- Consumes: Task 1 的表；`media.py` 已有的 `_tpl(request, name, ctx)`、`_current_persona_id(request, db)`、`get_db()`
- Produces:
  - `list_lessons(db, persona_id, include_archived=False) -> list[dict]`
  - `create_lesson(db, persona_id, kind, brief, detail="", trigger_context="", evidence="", source="manual") -> str`（返回新 id）
  - `update_lesson(db, lesson_id, **fields) -> bool`
  - `set_lesson_status(db, lesson_id, status) -> bool`
  - `delete_lesson(db, lesson_id) -> bool`
  - `count_redlines(db, persona_id) -> int`
  - 路由：`GET /media/lessons`、`POST /media/lesson/create`、`POST /media/lesson/{lid}/update`、`POST /media/lesson/{lid}/status`、`POST /media/lesson/{lid}/delete`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_media_lesson_crud.py`：

```python
"""media_lesson 的 CRUD service。"""
import asyncio

from app.services.media_lesson import (
    list_lessons, create_lesson, update_lesson,
    set_lesson_status, delete_lesson, count_redlines)
from tests.media_helpers import make_db


async def _persona(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "帮中小企业落地AI", "涨粉"))
    await db.commit()


def test_create_then_list():
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "redline", "不许编造数据",
                                  evidence="8/20 复盘")
        rows = await list_lessons(db, "P1")
        await db.close()
        return lid, rows

    lid, rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["id"] == lid
    assert rows[0]["kind"] == "redline"
    assert rows[0]["hit_count"] == 0
    assert rows[0]["source"] == "manual"


def test_list_excludes_archived_by_default():
    async def run():
        db = await make_db()
        await _persona(db)
        keep = await create_lesson(db, "P1", "lesson", "留着的")
        gone = await create_lesson(db, "P1", "lesson", "归档的")
        await set_lesson_status(db, gone, "archived")
        default = await list_lessons(db, "P1")
        withall = await list_lessons(db, "P1", include_archived=True)
        await db.close()
        return keep, default, withall

    keep, default, withall = asyncio.run(run())
    assert [r["id"] for r in default] == [keep]
    assert len(withall) == 2


def test_list_is_scoped_to_persona():
    """按人设独享（宪法第 4 条）：别的人设的本子不该串过来。"""
    async def run():
        db = await make_db()
        await _persona(db, "P1")
        await _persona(db, "P2")
        await create_lesson(db, "P1", "lesson", "P1的")
        await create_lesson(db, "P2", "lesson", "P2的")
        rows = await list_lessons(db, "P1")
        await db.close()
        return rows

    rows = asyncio.run(run())
    assert [r["brief"] for r in rows] == ["P1的"]


def test_update_changes_fields():
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "原来的")
        ok = await update_lesson(db, lid, brief="改过的", trigger_context="讲方法论")
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows[0]

    ok, row = asyncio.run(run())
    assert ok is True
    assert row["brief"] == "改过的"
    assert row["trigger_context"] == "讲方法论"


def test_update_rejects_unknown_column():
    """白名单防注入：不在允许列表里的字段一律忽略，不拼进 SQL。"""
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "原来的")
        ok = await update_lesson(db, lid, hit_count=999, persona_id="P9")
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows[0]

    ok, row = asyncio.run(run())
    assert ok is False
    assert row["hit_count"] == 0
    assert row["persona_id"] == "P1"


def test_count_redlines_only_counts_active_redlines():
    async def run():
        db = await make_db()
        await _persona(db)
        await create_lesson(db, "P1", "redline", "红一")
        await create_lesson(db, "P1", "lesson", "教一")
        gone = await create_lesson(db, "P1", "redline", "红二归档")
        await set_lesson_status(db, gone, "archived")
        n = await count_redlines(db, "P1")
        await db.close()
        return n

    assert asyncio.run(run()) == 1


def test_delete_removes_row():
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "删我")
        ok = await delete_lesson(db, lid)
        rows = await list_lessons(db, "P1", include_archived=True)
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is True and rows == []


def test_create_rejects_blank_brief():
    """brief 是唯一进提示词的字段，空的没有意义。"""
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "   ")
        rows = await list_lessons(db, "P1")
        await db.close()
        return lid, rows

    lid, rows = asyncio.run(run())
    assert lid == "" and rows == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_lesson_crud.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.media_lesson'`

- [ ] **Step 3: 写 service**

新建 `app/services/media_lesson.py`：

```python
"""教训/红线库的 CRUD。教训与红线同表、kind 区分、按人设独享。

这里只管存取，不掺 AI、不掺注入逻辑 ——
筛选与渲染在 media_context.py，注入在 media_ai.write_script。
spec: docs/superpowers/specs/2026-08-31-media-lesson-memory-design.md
"""
import uuid

KINDS = ("lesson", "redline")
STATUSES = ("active", "archived")

# update_lesson 允许改的列白名单。persona_id / kind / hit_count / created_at
# 不在其中：改归属和类型该走新建，hit_count 是系统记账不该手改。
_UPDATABLE = ("brief", "detail", "trigger_context", "evidence")


async def list_lessons(db, persona_id: str, include_archived: bool = False) -> list:
    """按人设列出。默认只给 active —— 注入侧和 UI 主视图都只关心 active。"""
    sql = "SELECT * FROM media_lesson WHERE persona_id=?"
    args = [persona_id]
    if not include_archived:
        sql += " AND status='active'"
    sql += " ORDER BY kind DESC, created_at"
    cur = await db.execute(sql, tuple(args))
    return [dict(r) for r in await cur.fetchall()]


async def create_lesson(db, persona_id: str, kind: str, brief: str,
                        detail: str = "", trigger_context: str = "",
                        evidence: str = "", source: str = "manual") -> str:
    """新建一条。brief 为空或 kind 非法则拒绝，返回空串。"""
    brief = (brief or "").strip()
    if not brief or kind not in KINDS:
        return ""
    lid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_lesson "
        "(id,persona_id,kind,brief,detail,trigger_context,evidence,source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (lid, persona_id, kind, brief, (detail or "").strip(),
         (trigger_context or "").strip(), (evidence or "").strip(), source))
    await db.commit()
    return lid


async def update_lesson(db, lesson_id: str, **fields) -> bool:
    """只改白名单里的列。有任何列名不在白名单就整个拒绝（不静默吞掉）。"""
    if not fields or any(k not in _UPDATABLE for k in fields):
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    args = [(v or "").strip() for v in fields.values()] + [lesson_id]
    cur = await db.execute(
        f"UPDATE media_lesson SET {sets} WHERE id=?", tuple(args))
    await db.commit()
    return cur.rowcount > 0


async def set_lesson_status(db, lesson_id: str, status: str) -> bool:
    """归档/恢复。归档的不注入但保留证据链（与 media_material 同构）。"""
    if status not in STATUSES:
        return False
    cur = await db.execute(
        "UPDATE media_lesson SET status=? WHERE id=?", (status, lesson_id))
    await db.commit()
    return cur.rowcount > 0


async def delete_lesson(db, lesson_id: str) -> bool:
    cur = await db.execute("DELETE FROM media_lesson WHERE id=?", (lesson_id,))
    await db.commit()
    return cur.rowcount > 0


async def count_redlines(db, persona_id: str) -> int:
    """当前 active 红线条数。UI 据此提示「注入只带前 2 条」。"""
    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM media_lesson "
        "WHERE persona_id=? AND kind='redline' AND status='active'", (persona_id,))
    return (await cur.fetchone())["n"]
```

- [ ] **Step 4: 跑 service 测试确认通过**

Run: `python -m pytest tests/test_media_lesson_crud.py -v`
Expected: PASS，8 passed

- [ ] **Step 5: 加路由**

`app/api/media.py` import 区加：

```python
from app.services.media_lesson import (
    list_lessons, create_lesson, update_lesson,
    set_lesson_status, delete_lesson, count_redlines)
```

在 `/media/playbook/{pid}/status` 路由之后加：

```python
# ─────────────── 教训/红线库 ───────────────
# 「不要做什么」的库。写稿时红线无条件注入（≤2），教训按 trigger_context
# 匹配注入（≤3）。人拍板才入库（宪法第 2 条）。

@router.get("/media/lessons", response_class=HTMLResponse)
async def lessons_home(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        rows = await list_lessons(db, pid, include_archived=True) if pid else []
        red_n = await count_redlines(db, pid) if pid else 0
    finally:
        await db.close()
    active = [r for r in rows if r["status"] == "active"]
    return _tpl(request, "media_lessons.html", {
        "persona_id": pid,
        "redlines": [r for r in active if r["kind"] == "redline"],
        "lessons": [r for r in active if r["kind"] == "lesson"],
        "archived": [r for r in rows if r["status"] == "archived"],
        "redline_cap": INJECTION_BUDGET.get("redline", 2),
        "redline_n": red_n,
    })


@router.post("/media/lesson/create")
async def lesson_create(request: Request, kind: str = Form("lesson"),
                        brief: str = Form(...), detail: str = Form(""),
                        trigger_context: str = Form(""), evidence: str = Form("")):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if pid:
            await create_lesson(db, pid, kind, brief, detail,
                                trigger_context, evidence, source="manual")
    finally:
        await db.close()
    return RedirectResponse("/media/lessons", status_code=302)


@router.post("/media/lesson/{lid}/update")
async def lesson_update(lid: str, brief: str = Form(...), detail: str = Form(""),
                        trigger_context: str = Form(""), evidence: str = Form("")):
    db = await get_db()
    try:
        await update_lesson(db, lid, brief=brief, detail=detail,
                            trigger_context=trigger_context, evidence=evidence)
    finally:
        await db.close()
    return RedirectResponse("/media/lessons", status_code=302)


@router.post("/media/lesson/{lid}/status")
async def lesson_status(lid: str, status: str = Form(...)):
    db = await get_db()
    try:
        await set_lesson_status(db, lid, status)
    finally:
        await db.close()
    return RedirectResponse("/media/lessons", status_code=302)


@router.post("/media/lesson/{lid}/delete")
async def lesson_delete(lid: str):
    db = await get_db()
    try:
        await delete_lesson(db, lid)
    finally:
        await db.close()
    return RedirectResponse("/media/lessons", status_code=302)
```

`INJECTION_BUDGET` 需要 import（`media.py` 若尚未 import 则加）：

```python
from app.services.media_context import INJECTION_BUDGET
```

- [ ] **Step 6: 写模板**

新建 `app/templates/media_lessons.html`（结构照 `media_playbook.html`，同一套 class）：

```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% import "_media_shell.html" as shell %}
{% block title %}教训 / 红线{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} 体系库 {{ ic.icon('chevron') }} <b>教训 / 红线</b></span>
{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  {{ shell.media_shell('lib', persona_id=persona_id, here='教训/红线') }}

  <h1 class="pname" style="margin:0 0 5px">教训 / 红线</h1>
  <p style="font-size:13px;color:var(--ink-3);line-height:1.6;margin:0 0 16px;max-width:620px">
    「不要做什么」的库。写稿时<b>红线无条件带上</b>（最多 {{ redline_cap }} 条），
    <b>教训按「什么时候适用」跟本条选题的匹配度挑</b>（最多 3 条）。两者分开占位，互不挤占。
  </p>

  <div class="module">
    <div class="mh"><span class="ttl">🚫 红线（{{ redline_n }}/{{ redline_cap }}）</span>
      {% if redline_n > redline_cap %}
      <span class="tag" style="color:var(--warn)">超出 {{ redline_cap }} 条，注入只带最早的 {{ redline_cap }} 条——建议合并或降级为教训</span>
      {% endif %}
    </div>
    <div class="inner">
      {% for r in redlines %}
      <details style="border-top:1px solid var(--line); padding:8px 0">
        <summary style="cursor:pointer; font-size:13.5px">{{ r.brief }}
          <span style="color:var(--ink-3); font-size:12px">· 用过 {{ r.hit_count }} 次</span>
        </summary>
        <div style="font-size:12.5px; color:var(--ink-3); margin-top:6px; white-space:pre-wrap">
          {% if r.detail %}<div>{{ r.detail }}</div>{% endif %}
          {% if r.evidence %}<div>出处：{{ r.evidence }}</div>{% endif %}
          <div>来源：{{ r.source }}</div>
        </div>
        <form method="post" action="/media/lesson/{{ r.id }}/status" style="display:inline">
          <input type="hidden" name="status" value="archived">
          <button class="btn ghost" style="font-size:12px">归档</button>
        </form>
        <form method="post" action="/media/lesson/{{ r.id }}/delete" style="display:inline"
              onsubmit="return confirm('删除这条红线？不可撤。')">
          <button class="btn ghost" style="font-size:12px; color:var(--ink-3)">删除</button>
        </form>
      </details>
      {% else %}
      <p style="color:var(--ink-3); font-size:12.5px; margin:6px 0">还没有红线。红线是「什么时候都不许」，少而硬才值钱。</p>
      {% endfor %}
    </div>
  </div>

  <div class="module" style="margin-top:10px">
    <div class="mh"><span class="ttl">⚠️ 教训（{{ lessons|length }}）</span></div>
    <div class="inner">
      {% for l in lessons %}
      <details style="border-top:1px solid var(--line); padding:8px 0">
        <summary style="cursor:pointer; font-size:13.5px">{{ l.brief }}
          <span style="color:var(--ink-3); font-size:12px">· 用过 {{ l.hit_count }} 次</span>
          {% if l.hit_count == 0 %}
          <span class="tag" style="color:var(--warn)" title="可能是「什么时候适用」写歪了，或这条没必要存在">⚠️ 从没被用过</span>
          {% endif %}
        </summary>
        <div style="font-size:12.5px; color:var(--ink-3); margin-top:6px; white-space:pre-wrap">
          <div>什么时候：{{ l.trigger_context or '（没填——填了才可能被匹配到）' }}</div>
          {% if l.detail %}<div>{{ l.detail }}</div>{% endif %}
          {% if l.evidence %}<div>出处：{{ l.evidence }}</div>{% endif %}
          <div>来源：{{ l.source }}</div>
        </div>
        <form method="post" action="/media/lesson/{{ l.id }}/status" style="display:inline">
          <input type="hidden" name="status" value="archived">
          <button class="btn ghost" style="font-size:12px">归档</button>
        </form>
        <form method="post" action="/media/lesson/{{ l.id }}/delete" style="display:inline"
              onsubmit="return confirm('删除这条教训？不可撤。')">
          <button class="btn ghost" style="font-size:12px; color:var(--ink-3)">删除</button>
        </form>
      </details>
      {% else %}
      <p style="color:var(--ink-3); font-size:12.5px; margin:6px 0">还没有教训。跟 AI 助手说「这样不行」时它会问你要不要记一条。</p>
      {% endfor %}
    </div>
  </div>

  <div class="module" style="margin-top:10px">
    <div class="mh"><span class="ttl">＋ 手动加一条</span></div>
    <div class="inner">
      <form method="post" action="/media/lesson/create">
        <select name="kind" class="inp" style="font-size:13px; margin-bottom:6px">
          <option value="lesson">教训（要注意）</option>
          <option value="redline">红线（绝对不许）</option>
        </select>
        <input name="brief" class="inp" required maxlength="60" style="margin-bottom:6px"
               placeholder="一句话（这句就是塞给 AI 看的，写短写狠）">
        <input name="trigger_context" class="inp" style="margin-bottom:6px"
               placeholder="什么时候适用（教训必填；红线可空）">
        <p style="font-size:11.5px; color:var(--ink-3); margin:0 0 6px">
          💡 「什么时候适用」用<b>会出现在选题标题里的词</b>——匹配看的是字面重合，不认同义词。
        </p>
        <textarea name="detail" class="inp" rows="2" style="margin-bottom:6px"
                  placeholder="展开说明（只给你自己看，不会进提示词）"></textarea>
        <input name="evidence" class="inp" style="margin-bottom:8px"
               placeholder="出处/证据（哪条内容、哪次复盘）">
        <button type="submit" class="btn primary" style="font-size:13px">加进本子</button>
      </form>
    </div>
  </div>

  {% if archived %}
  <details style="margin-top:10px">
    <summary class="mh" style="cursor:pointer; list-style:none">
      <span class="ttl" style="font-size:14px; color:var(--ink-2)">已归档（{{ archived|length }}）—— 不再注入，但留着证据链</span>
    </summary>
    {% for a in archived %}
    <div style="padding:6px 0; font-size:13px; color:var(--ink-3)">
      {{ '🚫' if a.kind == 'redline' else '⚠️' }} {{ a.brief }}
      <form method="post" action="/media/lesson/{{ a.id }}/status" style="display:inline">
        <input type="hidden" name="status" value="active">
        <button class="btn ghost" style="font-size:12px">恢复</button>
      </form>
    </div>
    {% endfor %}
  </details>
  {% endif %}

  {{ shell.step_nav(back={'href':'/media/board','label':'内容看板'}) }}
</div>
{% endblock %}
```

- [ ] **Step 7: 加体系库入口**

`app/templates/_media_shell.html` 的第一个 `libgrid` 里，在打法库那张卡之后加：

```html
    <a class="libcard" href="/media/lessons"><div class="rn">{{ ic.icon('x') }}教训/红线</div><div class="rs" data-lib="lesson">—</div></a>
```

- [ ] **Step 8: 加存量计数**

`app/api/media_ui.py` 的 `libs` 字典（约第 62-78 行），在 `"legacy"` 那项之后加一项。
本子是**按人设独享**的，所以带 `persona_id` 过滤（跟 `material` 一样，
不是 `playbook` 那种全公司共享的写法）。该函数里人设 id 的变量名就是 `pid`：

```python
            "lesson": await _scalar(
                db, "SELECT COUNT(*) FROM media_lesson WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
```

- [ ] **Step 9: 冒烟**

起 dev server（换端口避开旧进程）：

```bash
python -c "import uvicorn; uvicorn.run('app.main:app', port=8012)"
```

浏览器开 `http://127.0.0.1:8012/media/lessons`，确认：
- 页面能打开不 500
- 手动加一条红线 + 一条教训，能存进去、列表能看见
- 归档 / 恢复 / 删除三个按钮都能用
- 体系库面板里「教训/红线」卡出现且计数不是 `—`

看完 `taskkill //F //PID <pid>` 收掉。

- [ ] **Step 10: 跑全量**

Run: `python -m pytest -q`
Expected: 404 passed，0 failed

- [ ] **Step 11: 提交**

```bash
git add app/services/media_lesson.py app/templates/media_lessons.html \
        app/api/media.py app/api/media_ui.py app/templates/_media_shell.html \
        tests/test_media_lesson_crud.py
git commit -m "feat: 教训/红线本子页 + CRUD

单开 media_lesson.py 放 CRUD（media.py 已 2186 行），路由层只做参数解析
和渲染。update 走列白名单，不在白名单的字段整个拒绝而不是静默吞掉。
UI 标出 hit_count=0 的条目——那是 trigger_context 写歪了或这条没必要存在。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: L2 advisory 接采纳落点

**Files:**
- Modify: `app/services/media_review_cycle.py`（`L2_SYSTEM` 的输出契约 + 新增 `normalize_advisory_items`）
- Modify: `app/api/media.py`（加 `POST /media/lesson/adopt`）
- Modify: `app/templates/media_review_cycle.html:106` 附近
- Test: `tests/test_media_lesson_advisory.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `create_lesson(db, persona_id, kind, brief, detail, trigger_context, evidence, source)`
- Produces: `normalize_advisory_items(raw) -> list[dict]`（每项含 `brief` / `trigger_context` / `evidence` 三键）

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_media_lesson_advisory.py`：

```python
"""L2 advisory 的归一化与采纳。核心是向后兼容旧的纯字符串格式。"""
import asyncio

from app.services.media_review_cycle import normalize_advisory_items
from app.services.media_lesson import list_lessons, create_lesson
from tests.media_helpers import make_db


def test_normalize_legacy_string_items():
    """旧数据是纯字符串数组，归一化成 dict，缺的键补空串。"""
    out = normalize_advisory_items(["开头别铺垫", "别讲太深"])
    assert out == [
        {"brief": "开头别铺垫", "trigger_context": "", "evidence": ""},
        {"brief": "别讲太深", "trigger_context": "", "evidence": ""},
    ]


def test_normalize_structured_items_pass_through():
    out = normalize_advisory_items(
        [{"brief": "开头别铺垫", "trigger_context": "口播", "evidence": "《X》"}])
    assert out[0]["trigger_context"] == "口播"
    assert out[0]["evidence"] == "《X》"


def test_normalize_fills_missing_keys():
    out = normalize_advisory_items([{"brief": "只有brief"}])
    assert out == [{"brief": "只有brief", "trigger_context": "", "evidence": ""}]


def test_normalize_drops_blank_and_junk():
    """空 brief、空字符串、非 dict 非 str 的垃圾一律丢掉，不报错。"""
    out = normalize_advisory_items(["", "  ", {"brief": ""}, 42, None, "留下"])
    assert out == [{"brief": "留下", "trigger_context": "", "evidence": ""}]


def test_normalize_handles_non_list():
    """AI 返回的不是数组时（None / dict / 字符串）返回空列表，不抛异常。"""
    assert normalize_advisory_items(None) == []
    assert normalize_advisory_items({"a": 1}) == []
    assert normalize_advisory_items("一句话") == []


def test_adopt_from_advisory_lands_in_lesson_table():
    """采纳后进 media_lesson，source 标 l2_advisory 以便追溯。"""
    async def run():
        db = await make_db()
        await db.execute(
            "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
            "VALUES ('P1','嘉姐','帮中小企业落地AI','涨粉','active')")
        await db.commit()
        await create_lesson(db, "P1", "lesson", "开头别铺垫",
                            trigger_context="口播", evidence="8/20 复盘",
                            source="l2_advisory")
        rows = await list_lessons(db, "P1")
        await db.close()
        return rows

    rows = asyncio.run(run())
    assert rows[0]["source"] == "l2_advisory"
    assert rows[0]["trigger_context"] == "口播"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_lesson_advisory.py -v`
Expected: FAIL —— `ImportError: cannot import name 'normalize_advisory_items'`

- [ ] **Step 3: 加归一化函数**

`app/services/media_review_cycle.py`，在 `L2_SYSTEM` 定义之后加：

```python
def normalize_advisory_items(raw) -> list:
    """把 advisory.lessons / advisory.redlines 归一成统一 dict 形状。

    向后兼容：老复盘记录里这两个字段是**纯字符串数组**（没有 trigger_context
    和 evidence）。遇到字符串就当只填了 brief。不做数据迁移 —— 老记录照常
    显示、照常可采纳，只是采纳时需人工补适用场景（spec §5.3）。

    垃圾项（空 brief、非 dict 非 str）直接丢掉，绝不抛异常打断复盘页渲染。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            item = {"brief": item}
        if not isinstance(item, dict):
            continue
        brief = (item.get("brief") or "").strip()
        if not brief:
            continue
        out.append({
            "brief": brief,
            "trigger_context": (item.get("trigger_context") or "").strip(),
            "evidence": (item.get("evidence") or "").strip(),
        })
    return out
```

- [ ] **Step 4: 改 L2 输出契约**

`app/services/media_review_cycle.py` 的 `L2_SYSTEM`：

把「落点分流」那段的最后一行：

```
- 打法/教训/红线/权重调整建议 → advisory（这些暂无自动落点，先当文字建议）。
```

改成：

```
- 打法 / 权重调整建议 → advisory（这两项暂无自动落点，先当文字建议）。
- 教训 → advisory.lessons，每条给 brief（一句话，短而狠）+ trigger_context
  （什么情况下适用，用会出现在选题标题里的词，匹配看字面重合）+ evidence。
- 红线（绝对不许再犯的）→ advisory.redlines，给 brief + evidence。
  红线要少而硬，最多 2 条，宁缺毋滥；够不上「绝对不许」的一律放 lessons。
```

把 JSON 结构那行：

```
 "advisory":{"playbooks":[],"lessons":[],"redlines":[],"weight_suggestion":""}}
```

改成：

```
 "advisory":{"playbooks":[],
  "lessons":[{"brief":"","trigger_context":"","evidence":""}],
  "redlines":[{"brief":"","evidence":""}],
  "weight_suggestion":""}}
```

- [ ] **Step 5: 加采纳路由**

`app/api/media.py`，在 Task 3 加的那批 lesson 路由之后：

复盘详情页的实际路由是 `GET /media/review-cycle/{cid}`（`media.py:1761`），
所以表单要把 `cycle_id` 带回来，采纳后跳回用户刚才那一页而不是跳走：

```python
@router.post("/media/lesson/adopt")
async def lesson_adopt(request: Request, kind: str = Form("lesson"),
                       brief: str = Form(...), trigger_context: str = Form(""),
                       evidence: str = Form(""), cycle_id: str = Form("")):
    """从 L2 复盘的 advisory 采纳一条进本子。人点才入库（宪法第 2 条）。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if pid:
            await create_lesson(db, pid, kind, brief, detail="",
                                trigger_context=trigger_context,
                                evidence=evidence, source="l2_advisory")
    finally:
        await db.close()
    back = f"/media/review-cycle/{cycle_id}" if cycle_id else "/media/lessons"
    return RedirectResponse(back, status_code=302)
```

- [ ] **Step 6: 复盘页加采纳按钮**

`app/templates/media_review_cycle.html` 第 106 行附近，把：

```html
      {% for x in cyc.advisory.lessons or [] %}<div>教训：{{ x }}</div>{% endfor %}
```

改成（路由层渲染前需先对 `cyc.advisory.lessons` / `.redlines` 跑 `normalize_advisory_items`，见 Step 7）：

```html
      {% for x in cyc.advisory.redlines or [] %}
      <div style="display:flex; gap:8px; align-items:baseline; padding:3px 0">
        <span>🚫 {{ x.brief }}</span>
        <form method="post" action="/media/lesson/adopt" style="display:inline">
          <input type="hidden" name="kind" value="redline">
          <input type="hidden" name="brief" value="{{ x.brief }}">
          <input type="hidden" name="evidence" value="{{ x.evidence }}">
          <input type="hidden" name="cycle_id" value="{{ cyc.id }}">
          <button class="btn ghost" style="font-size:12px">采纳进本子</button>
        </form>
      </div>
      {% endfor %}
      {% for x in cyc.advisory.lessons or [] %}
      <div style="display:flex; gap:8px; align-items:baseline; padding:3px 0">
        <span>⚠️ {{ x.brief }}{% if x.trigger_context %}<span style="color:var(--ink-3); font-size:12px">（{{ x.trigger_context }}）</span>{% endif %}</span>
        <form method="post" action="/media/lesson/adopt" style="display:inline">
          <input type="hidden" name="kind" value="lesson">
          <input type="hidden" name="brief" value="{{ x.brief }}">
          <input type="hidden" name="trigger_context" value="{{ x.trigger_context }}">
          <input type="hidden" name="evidence" value="{{ x.evidence }}">
          <input type="hidden" name="cycle_id" value="{{ cyc.id }}">
          <button class="btn ghost" style="font-size:12px">采纳进本子</button>
        </form>
      </div>
      {% endfor %}
```

- [ ] **Step 7: 渲染前归一化**

渲染这页的是 `review_cycle_detail`（`app/api/media.py:1761`），它拿的是**单条** `cyc`
（不是列表）。`get_cycle` 已经把 `advisory` 从 JSON 解成 dict——见
`media_review_cycle.py` 的 `_JSON_FIELDS`。把该函数改成：

```python
@router.get("/media/review-cycle/{cid}", response_class=HTMLResponse)
async def review_cycle_detail(cid: str, request: Request):
    db = await get_db()
    try:
        cyc = await get_cycle(db, cid)
    finally:
        await db.close()
    if not cyc:
        return RedirectResponse("/media/persona", status_code=302)
    # 老复盘的 advisory.lessons 是纯字符串数组，归一化成 dict 供模板取
    # x.brief / x.trigger_context（spec §5.3，不做数据迁移）
    adv = cyc.get("advisory")
    if isinstance(adv, dict):
        adv["lessons"] = normalize_advisory_items(adv.get("lessons"))
        adv["redlines"] = normalize_advisory_items(adv.get("redlines"))
        cyc["advisory"] = adv
    return _tpl(request, "media_review_cycle.html", {"cyc": cyc})
```

`media.py` 的 import 区把 `normalize_advisory_items` 加进现有那行
`from app.services.media_review_cycle import ...`。

- [ ] **Step 8: 跑测试确认通过**

Run: `python -m pytest tests/test_media_lesson_advisory.py -v`
Expected: PASS，6 passed

- [ ] **Step 9: 跑全量**

Run: `python -m pytest -q`
Expected: 410 passed，0 failed

⚠️ 若 `test_media_review_cycle*.py` 里有断言 `advisory.lessons` 是字符串数组的旧测试，改成新形状——那是契约变更的正常连带，不是回归。

- [ ] **Step 10: 提交**

```bash
git add app/services/media_review_cycle.py app/api/media.py \
        app/templates/media_review_cycle.html tests/test_media_lesson_advisory.py
git commit -m "feat: L2 advisory 的教训/红线接采纳落点

原来 L2_SYSTEM 里白纸黑字写着「这些暂无自动落点，先当文字建议」——每轮
花钱复盘出的洞见存进库就死在那儿。现在结构化输出 + 复盘页加采纳按钮。
归一化兼容旧的纯字符串格式，不做数据迁移。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: 助手对话沉淀

**Files:**
- Modify: `app/services/media_agent_tools.py`（`_tool_propose_lesson` + `_CORE` 注册 + schema）
- Modify: `app/services/media_assistant.py`（`apply_action` / `revert_action` 分支 + `MEDIA_ASSISTANT_SYSTEM`）
- Test: `tests/test_media_lesson_assistant.py`（新建）

**Interfaces:**
- Consumes: 已有的 `_core_stage(pid, action_type, target_table, target_id, after)`（`media_agent_tools.py:262`）、`log_action` / `apply_action` / `revert_action`
- Produces: 助手工具 `propose_lesson`；`action_type == "propose_lesson"` 的 apply/revert 分支

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_media_lesson_assistant.py`：

```python
"""助手对话沉淀：propose_lesson 只拟不写，确认后才入库，且可撤。"""
import asyncio
import json
import uuid

from app.services.media_assistant import (
    log_action, apply_action, revert_action, list_pending)
from app.services.media_lesson import list_lessons
from tests.media_helpers import make_db


async def _persona(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "帮中小企业落地AI", "涨粉"))
    await db.commit()


async def _stage(db, pid="P1", kind="lesson"):
    aid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_assistant_action "
        "(id,persona_id,action_type,target_table,target_id,after_json,status) "
        "VALUES (?,?, 'propose_lesson','media_lesson','',?, 'pending')",
        (aid, pid, json.dumps({
            "summary": "把「开头别铺垫」记进本子",
            "kind": kind, "brief": "开头别铺垫",
            "trigger_context": "口播", "evidence": "用户 8/31 说的"},
            ensure_ascii=False)))
    await db.commit()
    return aid


def test_pending_does_not_write_lesson_table():
    """核心安全语义：拟好之后本子里必须还是空的。"""
    async def run():
        db = await make_db()
        await _persona(db)
        await _stage(db)
        pend = await list_pending(db, "P1")
        rows = await list_lessons(db, "P1")
        await db.close()
        return pend, rows

    pend, rows = asyncio.run(run())
    assert len(pend) == 1
    assert rows == []


def test_apply_writes_lesson():
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db)
        ok = await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is True
    assert len(rows) == 1
    assert rows[0]["brief"] == "开头别铺垫"
    assert rows[0]["trigger_context"] == "口播"
    assert rows[0]["source"] == "assistant"


def test_apply_respects_redline_kind():
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db, kind="redline")
        await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return rows

    assert asyncio.run(run())[0]["kind"] == "redline"


def test_revert_removes_the_lesson():
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db)
        await apply_action(db, aid)
        ok = await revert_action(db, aid)
        rows = await list_lessons(db, "P1", include_archived=True)
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is True and rows == []


def test_apply_twice_is_noop():
    """已 applied 的动作再 apply 一次不该重复写库。"""
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db)
        await apply_action(db, aid)
        second = await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return second, rows

    second, rows = asyncio.run(run())
    assert second is False and len(rows) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_lesson_assistant.py -v`
Expected: `test_pending_does_not_write_lesson_table` PASS（表已存在，pending 本来就不写），其余 FAIL —— `apply_action` 走到 `else: return False`

- [ ] **Step 3: 加 apply 分支**

`app/services/media_assistant.py` 的 `apply_action`，在 `elif at == "adopt_playbook":` 那整块之后、`elif at == "run_l2":` 之前插入：

```python
    elif at == "propose_lesson":
        nid = str(uuid.uuid4())
        kind = (after.get("kind") or "lesson").strip()
        if kind not in ("lesson", "redline"):
            kind = "lesson"
        await db.execute(
            "INSERT INTO media_lesson "
            "(id,persona_id,kind,brief,trigger_context,evidence,source) "
            "VALUES (?,?,?,?,?,?, 'assistant')",
            (nid, pid, kind, (after.get("brief") or "").strip(),
             (after.get("trigger_context") or "").strip(),
             (after.get("evidence") or "").strip()))
        after["created_id"], after["created_table"] = nid, "media_lesson"
```

- [ ] **Step 4: 加 revert 分支**

同文件 `revert_action`，把这一行：

```python
    elif a["action_type"] in ("adopt_signature", "adopt_material", "adopt_playbook"):
```

改成：

```python
    elif a["action_type"] in ("adopt_signature", "adopt_material",
                              "adopt_playbook", "propose_lesson"):
```

（该分支已经是「按 `after.created_table` + `created_id` 删掉建的记录」的通用写法，Step 3 写了这两个键，直接就能用——这正是宪法第 7 条说的「不另造撤销机制」。）

- [ ] **Step 5: 加助手工具**

`app/services/media_agent_tools.py`，在 `_tool_run_phase_review` 之后加：

```python
async def _tool_propose_lesson(args, pid):
    a = args or {}
    brief = (a.get("brief") or "").strip()
    if not brief:
        return "（brief 不能为空——这句话就是要塞给写稿 AI 看的那句）"
    kind = (a.get("kind") or "lesson").strip()
    if kind not in ("lesson", "redline"):
        kind = "lesson"
    label = "红线" if kind == "redline" else "教训"
    return await _core_stage(pid, "propose_lesson", "media_lesson", "", {
        "summary": f"把{label}「{brief}」记进本子",
        "kind": kind, "brief": brief,
        "trigger_context": (a.get("trigger_context") or "").strip(),
        "evidence": (a.get("evidence") or "").strip()})
```

在 `_CORE` 字典里注册：

```python
    "propose_lesson": _tool_propose_lesson,
```

在最后那组 `MEDIA_TOOL_SCHEMAS +=` 里加：

```python
    _schema("propose_lesson",
            "把一条教训或红线记进本子（需人确认）。写稿时红线无条件带上、"
            "教训按 trigger_context 匹配带上。",
            {"kind": {"type": "string", "description": "lesson=要注意 / redline=绝对不许"},
             "brief": {"type": "string", "description": "一句话，短而狠——这句会原样塞给写稿 AI"},
             "trigger_context": {"type": "string",
                                 "description": "什么情况下适用。用会出现在选题标题里的词，"
                                                "匹配看字面重合不认同义词。红线可留空"},
             "evidence": {"type": "string", "description": "出处：用户哪句话/哪条内容"}},
            ["brief"]),
```

- [ ] **Step 6: 加主动性指引**

`app/services/media_assistant.py` 的 `MEDIA_ASSISTANT_SYSTEM` 末尾追加：

```
【顺手记本子】
当用户表达的是**否定性判断**（「这样不行」「别这么写」「我们的人不吃这套」
「太软了」），而不只是一次性的改稿要求（「加个案例」「换个开头」）时，
在完成改动后调 propose_lesson 提议记进本子，并在回复末尾一句话告诉用户。

纪律：
- 一次只提一条，提完就停。用户不点就算了，**绝不追问、绝不重复提**。
- 只在用户主动开口的对话里提，**永远不主动发起会话**。
- 拿不准是「一次性要求」还是「长期判断」时，倾向于不提。宁可漏记，不要吵。
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python -m pytest tests/test_media_lesson_assistant.py -v`
Expected: PASS，5 passed

- [ ] **Step 8: 跑全量**

Run: `python -m pytest -q`
Expected: 415 passed，0 failed

- [ ] **Step 9: 冒烟**

起 dev server（`port=8013`），开 `/media/assistant`：
- 对助手说「这个开头太软了，我们的人不吃铺垫」
- 确认它调了 `propose_lesson`、助手页出现待确认卡
- 点「确认」→ 去 `/media/lessons` 看条目是否落库、`source` 是 `assistant`
- 回助手页点「撤销」→ 条目消失

本地若无可用模型 key，则跳过 AI 那一段，只手工构造 pending 记录验证确认/撤销两条路径，并在报告里如实说明冒烟覆盖到哪一步。

- [ ] **Step 10: 提交**

```bash
git add app/services/media_agent_tools.py app/services/media_assistant.py \
        tests/test_media_lesson_assistant.py
git commit -m "feat: 助手对话沉淀教训/红线

用户随口一句纠正是浓度最高的知识来源，现在 100% 在滑动窗口里蒸发。
propose_lesson 归 _CORE：只落 pending 出待确认卡，人点确认才写库，可撤。
revert 直接复用 created_table/created_id 通用分支，不另造撤销机制。
主动性取保守策略——只在用户开口的对话里、只在否定判断时、只提一次。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 收工检查

- [ ] `python -m pytest -q` 全绿（预期 415 passed）
- [ ] 真机验收清单（写进 `docs/进展与路线图.md` 的「打磨/观察」段）：
  - 教训匹配准度——`trigger_context` 用字面重合，实际选题里能不能命中
  - 助手主动提议的频率——是太吵还是漏太多，按手感调 `MEDIA_ASSISTANT_SYSTEM` 一句话
  - 两周后看 `hit_count == 0` 的条目，判断是 trigger 写歪了还是这条没必要
- [ ] 更新 `docs/进展与路线图.md`：记本轮做了什么 + 项目②③④ 的待办
- [ ] 用 `superpowers:finishing-a-development-branch` 决定合并方式

## 已知限制（照搬 spec §10，实施时不要试图“顺手解决”）

1. **匹配是字面重合不是语义**。「方法论」匹配得上「方法」，匹配不上「套路」。UI 已给提示，不在本轮引入向量。
2. **红线上限 2 条**。第 3 条能存但不注入，这是有意的设计压力。
3. **只有 `write_script` 消费本子**。`revise_draft` / `critique_draft` / 推选题都还看不到，验证有效后单独一轮铺开。
4. **有效性两周后才看得出来**。上线后第一件事是观察 `hit_count`，不是加功能。
