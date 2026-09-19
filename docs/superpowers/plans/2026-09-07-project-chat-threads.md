# 项目对话分线 + 从对话沉淀 实现计划（核心三片）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"总 AI 对话"从一条大流改造成按项目分线、每轮现读该项目全量内容、并能把决策/待办/要点一键沉淀进项目且双向留痕。

**Architecture:** 复用 `messages.project_id` 做分线键（项目线=该 pid，综合线=NULL）；进项目线时每轮用 `build_project_chat_context(project_id)` 现读项目全量注入；沉淀走新表 `chat_captures` 记回指，参照现有 `media_assistant_action` 的可撤销/回指模式。

**Tech Stack:** FastAPI + Jinja2 + aiosqlite（SQLite/WAL）；测试用 pytest + `fastapi.testclient.TestClient`；AI 调用在测试里 monkeypatch。

## Global Constraints

- 建表：新表加进 `app/database.py` 的 `SCHEMA`；新列 append 到 `MIGRATIONS`（try/except 吞"列已存在"）。逐字对齐现有风格。
- 每个 DB 连接来自 `await get_db()`（`row_factory=aiosqlite.Row`，外键已开），用完 `await db.close()`。
- 项目引用解析统一用 `master_ai._resolve_project(ref)`（吃 `P003`/`3`/项目名/UUID → project_id）。
- 诚信红线：动作失败在 `reply` 如实报错，不假装成功；沿用 `_extract_action_json` 稳健解析。
- 测试鉴权：带签名 `session` cookie（见各测试的 `_client()`）；DB 用临时库（fixture 换 `app.database.DB_PATH` 后 `init_db()`）。
- 提交信息结尾加：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- 语音（spec 片 4）不在本计划内，另起 `2026-09-07-project-chat-voice.md`。

## File Structure

- Modify `app/database.py` — SCHEMA 增 `chat_captures` 表。
- Modify `app/services/project_context.py` — 增 `build_project_chat_context(project_id)`（描述+任务状态+决策+核心档+知识库导航）。
- Modify `app/services/master_ai.py` — `master_chat(...project_id)`、`_get_recent_history(limit, project_id)`、按线选上下文、返回 `capture_suggestion`、`MASTER_SYSTEM` 增沉淀提示。
- Create `app/services/chat_capture.py` — 沉淀落库 + 回指查询。
- Modify `app/api/chat.py` — `/chat` 分线渲染、`/chat/ask` 带 project、`/chat/clear` 按线、新增 `/chat/capture`。
- Modify `app/templates/chat.html` — 左栏项目线列表 + 选中线视图 + 沉淀卡片 + 📌 标记。
- Modify `app/templates/decisions.html` — 有回指的决策显示【看当初讨论】。
- Test: `tests/test_chat_threads.py`、`tests/test_project_chat_context.py`、`tests/test_chat_capture.py`。

---

## 片 1 — 对话分线

### Task 1: master_chat 支持 project_id、历史按线过滤

**Files:**
- Modify: `app/services/master_ai.py`（`_get_recent_history`、`master_chat`）
- Test: `tests/test_chat_threads.py`

**Interfaces:**
- Produces: `async _get_recent_history(limit:int=12, project_id:str|None=None) -> str`；`async master_chat(message:str, sender:str, model:str="auto", project_id:str|None=None) -> dict`（dict 含 `reply, action, action_result, model, cost`，本片不改这些键）。
- Consumes: `_resolve_project`、`get_context_for_master`、`ask_ai`、`_extract_action_json`（均已存在）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chat_threads.py
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.services.master_ai as m


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("chat_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _fake_ask(monkeypatch, capture=None):
    async def fake(prompt, model, task_type, system_prompt):
        if capture is not None:
            capture["prompt"] = prompt
        return {"response": '{"action":"chat","params":{},"reply":"收到"}',
                "model": "x", "tokens": 1, "cost": 0.0}
    monkeypatch.setattr(m, "ask_ai", fake)


def test_history_scoped_by_line(monkeypatch):
    _fake_ask(monkeypatch)
    async def seed():
        db = await get_db()
        await db.execute("DELETE FROM messages WHERE channel='master_ai'")
        await db.execute("DELETE FROM projects WHERE id IN ('PA','PB')")
        for pid in ("PA", "PB"):
            await db.execute("INSERT INTO projects (id,name,status,code) VALUES (?,?,'active',?)",
                             (pid, pid, pid))
        await db.commit(); await db.close()
    asyncio.run(seed())
    asyncio.run(m.master_chat("A 线的事", "Gaga", "auto", project_id="PA"))
    asyncio.run(m.master_chat("B 线的事", "Gaga", "auto", project_id="PB"))
    hist = asyncio.run(m._get_recent_history(project_id="PA"))
    assert "A 线的事" in hist and "B 线的事" not in hist


def test_message_written_with_project_id(monkeypatch):
    _fake_ask(monkeypatch)
    asyncio.run(m.master_chat("挂到 PA", "Gaga", "auto", project_id="PA"))
    async def chk():
        db = await get_db()
        cur = await db.execute(
            "SELECT COUNT(*) c FROM messages WHERE channel='master_ai' AND project_id='PA' AND direction='in' AND content LIKE '%挂到 PA%'")
        n = (await cur.fetchone())["c"]; await db.close(); return n
    assert asyncio.run(chk()) == 1
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_threads.py -v`
Expected: FAIL（`master_chat` 不接受 `project_id` / 历史未过滤）。

- [ ] **Step 3: 改 `_get_recent_history` 支持按线过滤**

把 `app/services/master_ai.py` 的 `_get_recent_history` 改成：

```python
async def _get_recent_history(limit: int = 12, project_id: str | None = None) -> str:
    """读最近的对话历史（按线）。project_id=None 读综合线（project_id IS NULL）。"""
    db = await get_db()
    try:
        if project_id:
            cursor = await db.execute(
                "SELECT content, direction FROM messages WHERE channel = 'master_ai' AND project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT content, direction FROM messages WHERE channel = 'master_ai' AND project_id IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    rows.reverse()
    lines = []
    for r in rows:
        who = "董事会" if r["direction"] == "in" else "你(总AI)"
        lines.append(f"{who}: {r['content']}")
    return "\n".join(lines)
```

- [ ] **Step 4: 改 `master_chat` 签名与写库**

`master_chat` 签名改为 `async def master_chat(message: str, sender: str, model: str = "auto", project_id: str | None = None) -> dict:`。
把读历史那行改为 `history = await _get_recent_history(project_id=project_id)`。
两处 `INSERT INTO messages (... project_id ...)` 把 `project_id` 从 `NULL` 改为参数（用命名占位）——两条 INSERT 分别改为：

```python
# 用户消息
await db.execute(
    "INSERT INTO messages (id, project_id, task_id, channel, content, direction, created_at) "
    "VALUES (?, ?, NULL, 'master_ai', ?, 'in', ?)",
    (msg_id, project_id, f"[{sender}] {message}", now),
)
# AI 回复
await db.execute(
    "INSERT INTO messages (id, project_id, task_id, channel, content, direction, created_at) "
    "VALUES (?, ?, NULL, 'master_ai', ?, 'out', ?)",
    (ai_msg_id, project_id, full_reply, now2),
)
```

- [ ] **Step 5: 跑，确认通过**

Run: `python -m pytest tests/test_chat_threads.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/master_ai.py tests/test_chat_threads.py
git commit -m "feat(chat): master_chat 按项目线分流消息与历史"
```

---

### Task 2: /chat 分线渲染、/chat/ask 带线、/chat/clear 按线

**Files:**
- Modify: `app/api/chat.py`
- Test: `tests/test_chat_threads.py`（续写）

**Interfaces:**
- Consumes: `master_chat(..., project_id)`（Task 1）、`_resolve_project`。
- Produces: `GET /chat?project=<code|id|空>` 渲染左栏项目线 + 当前线消息（模板变量 `lines`、`current`、`messages`）；`POST /chat/ask`（Form 增 `project`）；`POST /chat/clear`（Form 增 `project`，按线删）。

- [ ] **Step 1: 写失败测试**

```python
def test_ask_and_clear_scoped(monkeypatch):
    _fake_ask(monkeypatch)
    c = _client()
    r = c.post("/chat/ask", data={"message": "PA 你好", "sender": "Gaga", "model": "auto", "project": "PA"})
    assert r.status_code == 200 and r.json()["reply"]
    # 清 PB 不影响 PA
    c.post("/chat/clear", data={"project": "PB"})
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM messages WHERE channel='master_ai' AND project_id='PA'")
        n = (await cur.fetchone())["c"]; await db.close(); return n
    assert asyncio.run(chk()) >= 2


def test_chat_page_lists_project_lines():
    r = _client().get("/chat")
    assert r.status_code == 200
    assert "综合线" in r.text  # 左栏含综合线入口
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_threads.py -v`
Expected: FAIL（`/chat/ask` 不认 `project`；页面无"综合线"）。

- [ ] **Step 3: 改 `app/api/chat.py`**

`chat_ask` 增 `project` 表单并解析：

```python
from app.services.master_ai import master_chat, _resolve_project

@router.post("/chat/ask")
async def chat_ask(message: str = Form(...), sender: str = Form("Gaga"),
                   model: str = Form("auto"), project: str = Form("")):
    pid = await _resolve_project(project) if project else None
    try:
        result = await master_chat(message, sender, model, project_id=pid)
        return JSONResponse({
            "reply": result["reply"], "action": result.get("action", "chat"),
            "model": result.get("model", ""), "cost": result.get("cost", 0),
            "capture": result.get("capture_suggestion"),  # 片 3 用；此刻恒 None
        })
    except Exception as e:
        import logging; logging.getLogger(__name__).exception("chat_ask failed")
        return JSONResponse({"reply": f"❌ 总 AI 内部出错：{type(e).__name__}: {e}\n（已记录，可重试）",
                             "action": "chat", "cost": 0, "model": "error"})
```

`chat_page` 改为查活跃项目线 + 当前线消息：

```python
@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, project: str = ""):
    from app.api.projects import ensure_project_codes
    await ensure_project_codes()
    pid = await _resolve_project(project) if project else None
    db = await get_db()
    try:
        cur = await db.execute("SELECT id, code, name FROM projects WHERE status='active' ORDER BY code")
        lines = [dict(r) for r in await cur.fetchall()]
        if pid:
            cur = await db.execute(
                "SELECT * FROM messages WHERE channel='master_ai' AND project_id=? ORDER BY created_at ASC LIMIT 100", (pid,))
        else:
            cur = await db.execute(
                "SELECT * FROM messages WHERE channel='master_ai' AND project_id IS NULL ORDER BY created_at ASC LIMIT 100")
        messages = [dict(r) for r in await cur.fetchall()]
        current = next((l for l in lines if l["id"] == pid), None)
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "chat.html",
        {"request": request, "messages": messages, "lines": lines,
         "current": current, "current_pid": pid or ""})
```

`chat_clear` 按线删：

```python
@router.post("/chat/clear")
async def chat_clear(project: str = Form("")):
    pid = await _resolve_project(project) if project else None
    db = await get_db()
    try:
        if pid:
            await db.execute("DELETE FROM messages WHERE channel='master_ai' AND project_id=?", (pid,))
        else:
            await db.execute("DELETE FROM messages WHERE channel='master_ai' AND project_id IS NULL")
        await db.commit()
    finally:
        await db.close()
    from fastapi.responses import RedirectResponse
    dest = f"/chat?project={project}" if project else "/chat"
    return RedirectResponse(dest, status_code=303)
```

（删除已废弃的 `/chat/send` 端点及其 import，若无引用。用 `grep -rn "/chat/send" app` 确认无模板引用后再删。）

- [ ] **Step 4: 跑，确认通过**

Run: `python -m pytest tests/test_chat_threads.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/api/chat.py tests/test_chat_threads.py
git commit -m "feat(chat): /chat 按项目线渲染，ask/clear 带线"
```

---

### Task 3: chat.html 左栏项目线 + 选中线视图

**Files:**
- Modify: `app/templates/chat.html`
- Test: `tests/test_chat_threads.py`（`test_chat_page_lists_project_lines` 已覆盖渲染；本任务加一条断言当前线标题）

**Interfaces:**
- Consumes: 模板变量 `lines`(list of {id,code,name})、`current`、`current_pid`、`messages`。
- Produces: 左栏每条项目线 `<a href="/chat?project={{code}}">`；综合线 `<a href="/chat">`；发送表单隐藏域 `project` = `current_pid`；清空表单 `project` = `current_pid`。

- [ ] **Step 1: 加断言（先失败）**

```python
def test_chat_page_shows_current_line_title():
    r = _client().get("/chat?project=PA")
    assert r.status_code == 200
    assert 'name="project"' in r.text  # 表单带线
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_threads.py::test_chat_page_shows_current_line_title -v`
Expected: FAIL。

- [ ] **Step 3: 改模板**

在 `chat.html` 的 `.chat-shell` 外层包一个两栏布局：左栏渲染 `lines`（高亮 `current_pid`）+ 综合线入口；右栏是原有消息区。发送表单 `<form id="chat-form">` 内加 `<input type="hidden" name="project" value="{{ current_pid }}">`；清空表单 `<form method="POST" action="/chat/clear">` 内加同样隐藏域；JS 的 `fetch('/chat/ask')` 的 `FormData` 追加 `fd.append('project', document.querySelector('#chat-form [name=project]').value);`。左栏最小实现：

```html
<div style="display:flex; gap:14px; height:calc(100vh - 100px);">
  <div style="width:210px; flex:none; overflow-y:auto; border-right:1px solid var(--border); padding-right:10px;">
    <a href="/chat" class="{{ 'on' if not current_pid else '' }}" style="display:block; padding:7px 9px; border-radius:8px; font-size:13px; color:var(--ink-2); text-decoration:none;">综合线</a>
    {% for l in lines %}
    <a href="/chat?project={{ l.code }}" class="{{ 'on' if l.id == current_pid else '' }}" style="display:block; padding:7px 9px; border-radius:8px; font-size:13px; color:var(--ink-2); text-decoration:none;">[{{ l.code }}] {{ l.name }}</a>
    {% endfor %}
  </div>
  <div class="chat-shell" style="flex:1; height:auto;">
    <!-- 原有 chat-head / chat-messages / chatbar 保持不变，仅在两处表单加隐藏域 project -->
  </div>
</div>
```

（`.side a.on` 的高亮样式已在 base.html 定义，可直接用 `class="on"`。）

- [ ] **Step 4: 跑，确认通过 + 浏览器验证**

Run: `python -m pytest tests/test_chat_threads.py -v`
Expected: PASS。
浏览器：`preview_start name=ai-pm` → 开 `/chat` 与 `/chat?project=P00x`，确认左栏切换、发送落到对应线、清空只清当前线（read_console_messages 无报错）。

- [ ] **Step 5: 提交**

```bash
git add app/templates/chat.html tests/test_chat_threads.py
git commit -m "feat(chat): 左栏项目线切换 UI"
```

---

## 片 2 — 每轮现读项目全量上下文

### Task 4: build_project_chat_context

**Files:**
- Modify: `app/services/project_context.py`
- Test: `tests/test_project_chat_context.py`

**Interfaces:**
- Produces: `async build_project_chat_context(project_id: str) -> str`——返回含 `## 项目背景`(编号/名称/描述/状态) + `## 任务清单`(每条 `标题 · 状态 · 负责人`) + `## 决策记录`(每条 `标题：决定`) + 复用核心档/知识库导航的文本块。空 project_id 返回 `""`。
- Consumes: `get_db`；可内部调用现有 `build_project_context(project_id, "", "")` 取核心档/知识库块后再拼任务与决策。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_project_chat_context.py
import asyncio, pytest
from app.database import get_db, init_db
import app.database as _db_mod
from app.services.project_context import build_project_chat_context


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pcc_db") / "t.db"
    orig = _db_mod.DB_PATH; _db_mod.DB_PATH = tmp
    asyncio.run(init_db()); yield; _db_mod.DB_PATH = orig


def test_context_includes_tasks_and_decisions():
    async def seed():
        db = await get_db()
        await db.execute("INSERT INTO projects (id,name,description,status,code) VALUES ('PC','短视频','做起来','active','P077')")
        await db.execute("INSERT INTO tasks (id,project_id,title,status,assignee) VALUES ('T1','PC','拍脚本','pending','ai')")
        await db.execute("INSERT INTO decisions (id,project_id,title,decision,made_by) VALUES ('D1','PC','用DeepSeek','日常走DeepSeek','Gaga')")
        await db.commit(); await db.close()
    asyncio.run(seed())
    ctx = asyncio.run(build_project_chat_context("PC"))
    assert "P077" in ctx and "做起来" in ctx
    assert "拍脚本" in ctx and "pending" in ctx
    assert "用DeepSeek" in ctx


def test_empty_project_returns_blank():
    assert asyncio.run(build_project_chat_context("")) == ""
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_project_chat_context.py -v`
Expected: FAIL（函数不存在）。

- [ ] **Step 3: 实现**

在 `app/services/project_context.py` 末尾加：

```python
_STATUS_CN = {"pending": "待办", "running": "执行中", "done": "完成",
              "blocked": "阻塞", "reviewing": "待审", "failed": "失败"}


async def build_project_chat_context(project_id: str) -> str:
    """总 AI 对话线用：项目全量现读（描述+任务状态+决策+核心档+知识库导航）。"""
    if not project_id:
        return ""
    base = await build_project_context(project_id, "", "")  # 描述/核心档/知识库导航
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT title, status, assignee FROM tasks WHERE project_id=? ORDER BY priority ASC, created_at ASC", (project_id,))
        tasks = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT title, decision FROM decisions WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        decisions = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    blocks = [base] if base else []
    if tasks:
        lines = ["\n## 任务清单"]
        for t in tasks:
            who = t["assignee"] or "未指派"
            lines.append(f"- {t['title']} · {_STATUS_CN.get(t['status'], t['status'])} · {who}")
        blocks.append("\n".join(lines))
    if decisions:
        lines = ["\n## 决策记录"]
        for d in decisions:
            lines.append(f"- {d['title']}：{d['decision']}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)
```

- [ ] **Step 4: 跑，确认通过**

Run: `python -m pytest tests/test_project_chat_context.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/project_context.py tests/test_project_chat_context.py
git commit -m "feat(chat): build_project_chat_context 现读项目全量"
```

---

### Task 5: master_chat 按线选上下文

**Files:**
- Modify: `app/services/master_ai.py`（`master_chat` 里 `context = ...`）
- Test: `tests/test_chat_threads.py`（续写，用 `_fake_ask` 抓 prompt）

**Interfaces:**
- Consumes: `build_project_chat_context`（Task 4）、`get_context_for_master`。
- Produces: 进项目线时 prompt 含该项目全量；综合线时用全局摘要。

- [ ] **Step 1: 写失败测试**

```python
def test_project_content_reaches_prompt(monkeypatch):
    cap = {}
    _fake_ask(monkeypatch, capture=cap)
    async def seed():
        db = await get_db()
        await db.execute("INSERT OR IGNORE INTO projects (id,name,description,status,code) VALUES ('PA','A项目','独特描述XYZ','active','PA')")
        await db.execute("INSERT INTO tasks (id,project_id,title,status,assignee) VALUES ('TT1','PA','独特任务QQ','pending','ai')")
        await db.commit(); await db.close()
    asyncio.run(seed())
    asyncio.run(m.master_chat("看看进展", "Gaga", "auto", project_id="PA"))
    assert "独特描述XYZ" in cap["prompt"] and "独特任务QQ" in cap["prompt"]
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_threads.py::test_project_content_reaches_prompt -v`
Expected: FAIL（项目全量未进 prompt）。

- [ ] **Step 3: 改 `master_chat` 的上下文选择**

把 `context = await get_context_for_master()` 改为按线选：

```python
from app.services.project_context import build_project_chat_context
if project_id:
    proj_ctx = await build_project_chat_context(project_id)
    global_ctx = await get_context_for_master()
    context = f"{proj_ctx}\n\n（全局概览）\n{global_ctx}"
else:
    context = await get_context_for_master()
```

- [ ] **Step 4: 跑，确认通过（含全量回归）**

Run: `python -m pytest tests/test_chat_threads.py tests/test_project_chat_context.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/master_ai.py tests/test_chat_threads.py
git commit -m "feat(chat): 项目线每轮注入项目全量上下文"
```

---

## 片 3 — 从对话沉淀 + 双向留痕

### Task 6: chat_captures 表 + chat_capture 服务

**Files:**
- Modify: `app/database.py`（SCHEMA 增表）
- Create: `app/services/chat_capture.py`
- Test: `tests/test_chat_capture.py`

**Interfaces:**
- Produces:
  - `async capture_from_message(message_id:str, project_id:str|None, kind:str, fields:dict, sender:str) -> dict` — kind∈{decision,task,note}，写目标表 + `chat_captures`，返回 `{"target_table":..., "target_id":...}`。
    - decision.fields: `{title, decision, reason?}`；task.fields: `{title, description?, assignee?}`；note.fields: `{title, content?, tags?}`。
  - `async captures_for_message(message_id:str) -> list[dict]`（每项 `{kind,target_table,target_id}`）。
  - `async source_message_for(target_table:str, target_id:str) -> str|None`。
- Consumes: `get_db`。

- [ ] **Step 1: SCHEMA 增表**

在 `app/database.py` 的 `SCHEMA` 末尾（`media_assistant_message` 之后、`"""` 之前）加：

```sql
CREATE TABLE IF NOT EXISTS chat_captures (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    project_id TEXT,
    kind TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_chat_capture.py
import asyncio, pytest
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import chat_capture as cc


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cap_db") / "t.db"
    orig = _db_mod.DB_PATH; _db_mod.DB_PATH = tmp
    asyncio.run(init_db()); yield; _db_mod.DB_PATH = orig


def test_capture_decision_and_backrefs():
    async def go():
        db = await get_db()
        await db.execute("INSERT OR IGNORE INTO projects (id,name,status,code) VALUES ('PZ','Z','active','PZ')")
        await db.execute("INSERT INTO messages (id,project_id,channel,content,direction) VALUES ('MZ','PZ','master_ai','聊到用DeepSeek','out')")
        await db.commit(); await db.close()
    asyncio.run(go())
    res = asyncio.run(cc.capture_from_message(
        "MZ", "PZ", "decision", {"title": "用DeepSeek", "decision": "日常走DeepSeek", "reason": "省钱"}, "Gaga"))
    assert res["target_table"] == "decisions"
    # 决策表真的写了，made_by=发送人
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT made_by,title FROM decisions WHERE id=?", (res["target_id"],))
        row = await cur.fetchone(); await db.close(); return row
    row = asyncio.run(chk())
    assert row["made_by"] == "Gaga" and row["title"] == "用DeepSeek"
    # 双向回指
    caps = asyncio.run(cc.captures_for_message("MZ"))
    assert any(c["target_id"] == res["target_id"] for c in caps)
    assert asyncio.run(cc.source_message_for("decisions", res["target_id"])) == "MZ"


def test_capture_task_and_note():
    async def go():
        db = await get_db()
        await db.execute("INSERT INTO messages (id,project_id,channel,content,direction) VALUES ('MT','PZ','master_ai','待办','in')")
        await db.commit(); await db.close()
    asyncio.run(go())
    t = asyncio.run(cc.capture_from_message("MT", "PZ", "task", {"title": "联系渠道商"}, "Gaga"))
    assert t["target_table"] == "tasks"
    n = asyncio.run(cc.capture_from_message("MT", "PZ", "note", {"title": "报价", "content": "两万"}, "Gaga"))
    assert n["target_table"] == "notes"
```

- [ ] **Step 3: 跑，确认失败**

Run: `python -m pytest tests/test_chat_capture.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 4: 实现 `app/services/chat_capture.py`**

```python
"""从对话沉淀到项目：写目标表 + chat_captures 回指。参照 media_assistant_action 的留痕思路。"""
import uuid
from datetime import datetime
from app.database import get_db

_TABLE = {"decision": "decisions", "task": "tasks", "note": "notes"}


async def capture_from_message(message_id: str, project_id: str | None, kind: str,
                               fields: dict, sender: str) -> dict:
    if kind not in _TABLE:
        raise ValueError(f"未知沉淀类型: {kind}")
    target_table = _TABLE[kind]
    tid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        if kind == "decision":
            await db.execute(
                "INSERT INTO decisions (id, project_id, title, context, decision, reason, made_by, created_at) "
                "VALUES (?, ?, ?, '', ?, ?, ?, ?)",
                (tid, project_id, fields.get("title", ""), fields.get("decision", ""),
                 fields.get("reason", ""), sender, now))
        elif kind == "task":
            await db.execute(
                "INSERT INTO tasks (id, project_id, title, description, assignee, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (tid, project_id, fields.get("title", ""), fields.get("description", ""),
                 fields.get("assignee", sender), now, now))
        else:  # note
            await db.execute(
                "INSERT INTO notes (id, title, content, project_id, tags, source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'chat_capture', ?, ?)",
                (tid, fields.get("title", ""), fields.get("content", ""), project_id,
                 fields.get("tags", ""), now, now))
        cap_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO chat_captures (id, message_id, project_id, kind, target_table, target_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cap_id, message_id, project_id, kind, target_table, tid, now))
        await db.commit()
    finally:
        await db.close()
    return {"target_table": target_table, "target_id": tid}


async def captures_for_message(message_id: str) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT kind, target_table, target_id FROM chat_captures WHERE message_id=?", (message_id,))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def source_message_for(target_table: str, target_id: str) -> str | None:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT message_id FROM chat_captures WHERE target_table=? AND target_id=? LIMIT 1",
            (target_table, target_id))
        row = await cur.fetchone()
        return row["message_id"] if row else None
    finally:
        await db.close()
```

（注意：`tasks.project_id` NOT NULL —— 沉淀 task 时 `project_id` 不可为空。综合线沉淀 task 时前端须先选一个项目；后端在 Task 7 里校验。）

- [ ] **Step 5: 跑，确认通过**

Run: `python -m pytest tests/test_chat_capture.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/database.py app/services/chat_capture.py tests/test_chat_capture.py
git commit -m "feat(chat): chat_captures 表 + 沉淀落库与双向回指"
```

---

### Task 7: POST /chat/capture 端点

**Files:**
- Modify: `app/api/chat.py`
- Test: `tests/test_chat_capture.py`（续写，用 TestClient）

**Interfaces:**
- Consumes: `chat_capture.capture_from_message`、`_resolve_project`。
- Produces: `POST /chat/capture`（Form: `message_id, project, kind, title, content?, decision?, reason?`）→ JSON `{ok, target_table, target_id}`；task 且无项目 → `{ok:false, error}`。

- [ ] **Step 1: 写失败测试**

```python
import base64, json
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret

def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app); c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user":"t"}).encode())).decode())
    return c

def test_capture_endpoint_decision():
    async def go():
        db = await get_db()
        await db.execute("INSERT OR IGNORE INTO projects (id,name,status,code) VALUES ('PZ','Z','active','PZ')")
        await db.execute("INSERT OR IGNORE INTO messages (id,project_id,channel,content,direction) VALUES ('ME','PZ','master_ai','x','out')")
        await db.commit(); await db.close()
    asyncio.run(go())
    r = _client().post("/chat/capture", data={"message_id":"ME","project":"PZ","kind":"decision","title":"定了","decision":"就这么办"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["target_table"] == "decisions"

def test_capture_task_without_project_rejected():
    r = _client().post("/chat/capture", data={"message_id":"ME","project":"","kind":"task","title":"没项目的待办"})
    assert r.json()["ok"] is False and "项目" in r.json()["error"]
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_capture.py -v`
Expected: FAIL（无 `/chat/capture`）。

- [ ] **Step 3: 加端点**

```python
from app.services import chat_capture

@router.post("/chat/capture")
async def chat_capture_ep(message_id: str = Form(...), project: str = Form(""),
                          kind: str = Form(...), sender: str = Form("Gaga"),
                          title: str = Form(""), content: str = Form(""),
                          decision: str = Form(""), reason: str = Form("")):
    pid = await _resolve_project(project) if project else None
    if kind == "task" and not pid:
        return JSONResponse({"ok": False, "error": "沉淀为任务须先选一个项目"})
    fields = {"decision": {"title": title, "decision": decision, "reason": reason},
              "task": {"title": title, "description": content},
              "note": {"title": title, "content": content}}.get(kind)
    if fields is None:
        return JSONResponse({"ok": False, "error": f"未知类型 {kind}"})
    try:
        res = await chat_capture.capture_from_message(message_id, pid, kind, fields, sender)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        import logging; logging.getLogger(__name__).exception("chat_capture failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})
```

- [ ] **Step 4: 跑，确认通过**

Run: `python -m pytest tests/test_chat_capture.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/api/chat.py tests/test_chat_capture.py
git commit -m "feat(chat): POST /chat/capture 沉淀端点"
```

---

### Task 8: MASTER_SYSTEM 产出 capture_suggestion 并回传

**Files:**
- Modify: `app/services/master_ai.py`（`MASTER_SYSTEM` 文案 + `master_chat` 解析回传）
- Test: `tests/test_chat_threads.py`（续写）

**Interfaces:**
- Produces: `master_chat` 返回值增 `capture_suggestion`（`{kind,title,content?,decision?,reason?}` 或 `None`），取自 AI JSON 顶层同名字段（`_extract_action_json` 已把整对象返回，直接读 `parsed.get("capture_suggestion")`）。

- [ ] **Step 1: 写失败测试**

```python
def test_capture_suggestion_passed_through(monkeypatch):
    async def fake(prompt, model, task_type, system_prompt):
        return {"response": '{"action":"chat","params":{},"reply":"好",'
                            '"capture_suggestion":{"kind":"decision","title":"用DeepSeek","decision":"日常走DeepSeek"}}',
                "model": "x", "tokens": 1, "cost": 0.0}
    monkeypatch.setattr(m, "ask_ai", fake)
    out = asyncio.run(m.master_chat("就用DeepSeek吧", "Gaga", "auto", project_id="PA"))
    assert out["capture_suggestion"]["kind"] == "decision"
    assert out["capture_suggestion"]["title"] == "用DeepSeek"
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_threads.py::test_capture_suggestion_passed_through -v`
Expected: FAIL（返回无该键）。

- [ ] **Step 3: 实现**

`master_chat` 的 `return {...}` 增一行 `"capture_suggestion": parsed.get("capture_suggestion"),`。
`MASTER_SYSTEM` 里"可执行动作"说明后，加一段（放在 `规则：` 之前）：

```python
# 追加到 MASTER_SYSTEM 字符串中：
"""

关于"待沉淀卡片"（重要）：
- 当这一轮里董事会明确定了一个决策、或冒出一个该记下来的待办/要点时，你可以在返回的 JSON 顶层附一个 "capture_suggestion" 字段，供前端渲染成"要不要记下"的卡片（董事会点了才真入库，你不要自己当成已入库）：
  "capture_suggestion": {"kind": "decision|task|note", "title": "...", "decision": "决策内容（kind=decision 时）", "content": "内容（task/note 时）", "reason": "原因（可选）"}
- 只有确实成型时才附；还在讨论、没定论就不要附。一轮最多附一个，挑最该记的。
"""
```

- [ ] **Step 4: 跑，确认通过**

Run: `python -m pytest tests/test_chat_threads.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/master_ai.py tests/test_chat_threads.py
git commit -m "feat(chat): 总AI 产出 capture_suggestion 待沉淀卡片"
```

---

### Task 9: 前端沉淀卡片 + 📌 标记 + 决策日志【看当初讨论】

**Files:**
- Modify: `app/templates/chat.html`（渲染 `capture` 卡片 + 每条消息的 📌）
- Modify: `app/api/chat.py`（`chat_page` 给消息附 `captures`）
- Modify: `app/templates/decisions.html` + `app/api/projects.py` 或 decisions 路由（给决策附 `source_message_id`）
- Test: `tests/test_chat_capture.py`（页面断言）

**Interfaces:**
- Consumes: `chat_capture.captures_for_message`、`source_message_for`；Task 7 的 `/chat/capture`；Task 8 的 `capture`。
- Produces: 对话线里被沉淀过的消息渲染 📌「已记入…」；AI 回复带 `capture` 时渲染卡片（记下 → POST `/chat/capture`）；决策日志有回指的显示【看当初讨论】链到 `/chat?project=<code>`。

- [ ] **Step 1: 写失败测试**

```python
def test_decisions_page_shows_discussion_link():
    # 复用 test_capture_endpoint_decision 造的 ME→decision 回指
    async def go():
        db = await get_db()
        await db.execute("INSERT OR IGNORE INTO projects (id,name,status,code) VALUES ('PZ','Z','active','PZ')")
        await db.execute("INSERT OR IGNORE INTO messages (id,project_id,channel,content,direction) VALUES ('ME','PZ','master_ai','x','out')")
        await db.commit(); await db.close()
    asyncio.run(go())
    _client().post("/chat/capture", data={"message_id":"ME","project":"PZ","kind":"decision","title":"定了看讨论","decision":"x"})
    r = _client().get("/decisions")
    assert r.status_code == 200 and "看当初讨论" in r.text
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_chat_capture.py::test_decisions_page_shows_discussion_link -v`
Expected: FAIL（决策页无该链接）。

- [ ] **Step 3: 后端给决策附回指**

在渲染 `/decisions` 的路由（`grep -n "decisions.html" app/api/*.py` 定位）里，取到 `decisions` 后为每条补 `source_message_id`：

```python
from app.services.chat_capture import source_message_for
for d in decisions:
    d["source_message_id"] = await source_message_for("decisions", d["id"])
```

`decisions.html` 在每条决策卡（AI/人工两处）的元信息区加：

```html
{% if d.source_message_id %}<a href="/chat?project={{ d.project_code or '' }}" style="font-size:11px;color:var(--ai);text-decoration:none">看当初讨论 →</a>{% endif %}
```

（若决策卡当前没 `project_code`，在路由里给每条决策补 `d["project_code"]` = 该 project 的 code；`project_id` 为空时链到 `/chat`。）

- [ ] **Step 4: 对话页消息附 captures + 卡片 + 📌**

`chat_page` 里给每条 message 补 `m["captures"] = await captures_for_message(m["id"])`。`chat.html`：
- AI 气泡下，若 JS 收到的 `data.capture` 非空，渲染"待沉淀卡片"（记下/改改/忽略）；【记下】= `fetch('/chat/capture', {form: message_id=当前AI消息id, project=current_pid, kind, title, ...})`，成功后把卡片替换成 📌「已记入」。
- 服务端已存在的消息，若 `msg.captures` 非空，在气泡下渲染 📌「已记入{{kind}}」。

（AI 消息 id：Task 1 里 AI 回复已入库有 `ai_msg_id`；`/chat/ask` 的 JSON 需把它带回前端做卡片的 `message_id`。为此在 Task 1 的返回 dict 里补 `"ai_message_id": ai_msg_id`，并在 `/chat/ask` JSON 透传 `ai_message_id`。此改动小，随本步一起做并加断言：`assert r.json()["ai_message_id"]`。）

- [ ] **Step 5: 跑，确认通过 + 浏览器验证**

Run: `python -m pytest tests/test_chat_capture.py tests/test_chat_threads.py -v`
Expected: PASS。
浏览器：项目线里发一句能触发决策的话 → 出现待沉淀卡片 →【记下】→ 该消息变 📌，`/decisions` 出现该决策且有【看当初讨论】。

- [ ] **Step 6: 提交**

```bash
git add app/api/chat.py app/api/projects.py app/templates/chat.html app/templates/decisions.html tests/
git commit -m "feat(chat): 沉淀卡片、消息📌标记、决策日志看当初讨论"
```

---

## Self-Review 记录

- **Spec 覆盖**：分线(片1/Task1-3)、每轮现读全量(片2/Task4-5)、沉淀+双向留痕(片3/Task6-9)、决策 made_by=发送人(Task6)、task 无项目校验(Task6 注/Task7)、综合线用全局摘要+立项不搬运历史(Task5，历史天然不搬)。语音(片4)已声明另立计划。✅
- **占位符**：无 TBD/TODO；每个改动步给了真实代码与命令。
- **类型一致**：`master_chat(...project_id)`、`build_project_chat_context(project_id)`、`capture_from_message(message_id,project_id,kind,fields,sender)`、`captures_for_message`、`source_message_for` 全程签名一致；`chat_captures` 列名在建表/服务/查询处一致。
- **待执行时确认**：Task9 需先 `grep` 定位 `/decisions` 渲染路由与 `project_code` 来源；删 `/chat/send` 前先确认无模板引用。
