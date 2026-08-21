# 自媒体 AI 助手 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。Steps use checkbox (`- [ ]`).

**Goal:** 每人设一个对话式 AI 助手，Phase 1 = 查 + 改草稿（建选题/写下一条续集/写脚本草稿/匹配打法）+ 全程留痕可撤 + 对话页，入口在看板下方。

**Architecture:** 复用 `run_agent_loop`（小幅向后兼容扩展）挂一套媒体工具集，绑当前人设。改类动作记 `media_assistant_action` 日志、可撤。对话历史存 `media_assistant_message`。核心/入库/删除留作 Phase 2。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS；AI 走 DeepSeek 函数调用（`run_agent_loop`）。

## Global Constraints

- **分层留痕**：查=不留痕；改草稿=即时执行+记 `applied` 动作日志+可撤；核心/入库/删除=Phase 2 不做。
- **每人设 scoped**：工具 ctx=persona_id；独享数据只查本人设；`list_playbooks` 回共享全部；`list_materials` 含 `scope='shared'`。
- **注意力纪律**：`list_*` 回清单/摘要不回全文；`read_content` 才回单条全文；写稿/续集只注一条打法（沿用现有 match）。
- **复用**：建选题落 `media_topic`(status pool, source='assistant')；续集落 `media_content`(idea, idea_source='assistant', parent_content_id)；脚本草稿走 `write_script`（写 ai_draft 不碰 script）；匹配走 `match_playbook`。
- **迁移**：parent_content_id 走 MIGRATIONS(ALTER)；两张新表走 SCHEMA。测试 make_db() 应用两者，FK 开着，独立 persona id。改模板用 Edit/Write，JS 不塞 SVG。
- 跑 pytest：`cd /d/GAGA-5-25/ai-pm && python -m pytest ... ; echo EXIT=${PIPESTATUS[0]}`（cwd 每次重置，每条 bash 先 cd）。假挂 `taskkill //F //IM python.exe`。测试里嵌 asyncio 用 `async def` 辅助函数 + `await`，别在运行的 loop 里再 `asyncio.run`。reseed 删 persona 前先删其子表（FK）。

---

### Task 1: DB（parent_content_id + 两张新表）

**Files:** Modify `app/database.py`；Test `tests/test_media_assistant_schema.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_schema.py
"""助手 schema。"""
import asyncio
from tests.media_helpers import make_db


def _cols(t):
    async def go():
        db = await make_db()
        try:
            cur = await db.execute(f"PRAGMA table_info({t})")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    return asyncio.run(go())


def test_content_has_parent():
    assert "parent_content_id" in _cols("media_content")


def test_action_table():
    assert {"id", "persona_id", "conversation_ref", "action_type", "target_table",
            "target_id", "before_json", "after_json", "status", "reversible",
            "created_at"} <= _cols("media_assistant_action")


def test_message_table():
    assert {"id", "persona_id", "role", "content", "cost", "created_at"} <= _cols("media_assistant_message")
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_schema.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/database.py`：MIGRATIONS 末尾加
```python
    "ALTER TABLE media_content ADD COLUMN parent_content_id TEXT DEFAULT ''",
```
SCHEMA 末尾（结束 `"""` 前）加两张表：
```sql
CREATE TABLE IF NOT EXISTS media_assistant_action (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    conversation_ref TEXT DEFAULT '',
    action_type TEXT NOT NULL,
    target_table TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    before_json TEXT DEFAULT '',
    after_json TEXT DEFAULT '',
    status TEXT DEFAULT 'applied',
    reversible INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_assistant_message (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    content TEXT DEFAULT '',
    cost REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: GREEN** → PASS（3 passed）

- [ ] **Step 5: Commit**
```bash
git add app/database.py tests/test_media_assistant_schema.py && git commit -m "feat(media): 助手-media_content加parent_content_id+动作日志表+对话消息表"
```

---

### Task 2: 助手服务（留痕 + 撤销 + 系统提示）

**Files:** Create `app/services/media_assistant.py`；Test `tests/test_media_assistant_service.py`（新）

**Interfaces:**
- `async log_action(db, persona_id, action_type, target_table, target_id, before=None, after=None, conversation_ref="") -> str`（返 action id）
- `async list_actions(db, persona_id) -> list`（applied 在前，按时间倒序）
- `async revert_action(db, action_id) -> bool`（按 target_table/action_type 回滚：create 类删记录，draft_script 还原 ai_draft；置 status='reverted'）
- `MEDIA_ASSISTANT_SYSTEM`（str 系统提示）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_service.py
"""助手留痕 + 撤销。"""
import asyncio, json
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.commit()
    return db


def test_log_and_list():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "create_topic", "media_topic", "T1",
                                  after={"title": "选题甲"})
        acts = await ma.list_actions(db, "A")
        assert len(acts) == 1 and acts[0]["id"] == aid and acts[0]["status"] == "applied"
        await db.close()
    asyncio.run(go())


def test_revert_create_deletes_row():
    async def go():
        db = await _seed()
        await db.execute("INSERT INTO media_topic (id,persona_id,title,source,status) "
                         "VALUES ('T1','A','选题甲','assistant','pool')")
        await db.commit()
        aid = await ma.log_action(db, "A", "create_topic", "media_topic", "T1",
                                  after={"title": "选题甲"})
        ok = await ma.revert_action(db, aid)
        assert ok
        cur = await db.execute("SELECT COUNT(*) c FROM media_topic WHERE id='T1'")
        assert (await cur.fetchone())["c"] == 0
        cur = await db.execute("SELECT status FROM media_assistant_action WHERE id=?", (aid,))
        assert (await cur.fetchone())["status"] == "reverted"
        await db.close()
    asyncio.run(go())


def test_revert_draft_restores_ai_draft():
    async def go():
        db = await _seed()
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,ai_draft) "
                         "VALUES ('C1','A','标题','idea','新草稿')")
        await db.commit()
        aid = await ma.log_action(db, "A", "draft_script", "media_content", "C1",
                                  before={"ai_draft": "旧草稿"}, after={"ai_draft": "新草稿"})
        await ma.revert_action(db, aid)
        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["ai_draft"] == "旧草稿"
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_service.py -v` → FAIL

- [ ] **Step 3: 实现**
```python
# app/services/media_assistant.py
"""自媒体助手：动作留痕 + 撤销 + 系统提示。核心/入库动作留作 Phase 2。"""
import json
import uuid

MEDIA_ASSISTANT_SYSTEM = """你是这个自媒体人设的 AI 助手。你能查内容/选题/打法库/原料库，能建选题、写下一条续集、写脚本草稿、匹配打法。
纪律：只在需要时调工具；查东西回清单/摘要，别一次性铺开全部。写稿/续集只用最贴的一条打法当骨架，别堆。
你只做"草稿/可逆"的事——建选题、续集、脚本草稿都是草稿，人还会定稿；采纳进库/删除这类核心动作你现在不能做（让用户去对应页面点）。
做完把你做了什么、建了哪条、简明告诉用户。"""


async def log_action(db, persona_id, action_type, target_table, target_id,
                     before=None, after=None, conversation_ref="") -> str:
    aid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_assistant_action "
        "(id,persona_id,conversation_ref,action_type,target_table,target_id,before_json,after_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (aid, persona_id, conversation_ref, action_type, target_table, target_id,
         json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False)))
    await db.commit()
    return aid


async def list_actions(db, persona_id) -> list:
    cur = await db.execute(
        "SELECT * FROM media_assistant_action WHERE persona_id=? "
        "ORDER BY CASE status WHEN 'applied' THEN 0 ELSE 1 END, created_at DESC",
        (persona_id,))
    return [dict(r) for r in await cur.fetchall()]


async def revert_action(db, action_id) -> bool:
    cur = await db.execute("SELECT * FROM media_assistant_action WHERE id=?", (action_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "applied":
        return False
    a = dict(row)
    before = json.loads(a["before_json"] or "{}")
    if a["action_type"] in ("create_topic", "write_next"):
        # 建类 → 删掉建的记录
        await db.execute(f"DELETE FROM {a['target_table']} WHERE id=?", (a["target_id"],))
    elif a["action_type"] == "draft_script":
        # 草稿类 → 还原 ai_draft
        await db.execute("UPDATE media_content SET ai_draft=? WHERE id=?",
                         (before.get("ai_draft", ""), a["target_id"]))
    else:
        return False
    await db.execute("UPDATE media_assistant_action SET status='reverted' WHERE id=?", (action_id,))
    await db.commit()
    return True
```

- [ ] **Step 4: GREEN** → PASS（3 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_assistant.py tests/test_media_assistant_service.py && git commit -m "feat(media): 助手动作留痕log/list+撤销(建类删/草稿还原)+系统提示"
```

---

### Task 3: 媒体工具 · 查类（schemas + dispatch 骨架）

**Files:** Create `app/services/media_agent_tools.py`；Test `tests/test_media_agent_read_tools.py`（新）

**Interfaces:** `MEDIA_TOOL_SCHEMAS`（list，本任务先放查类）；`async dispatch_media_tool(name, args, persona_id) -> str`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_agent_read_tools.py
"""媒体查类工具：scoped + 共享可见 + 不回全文。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_agent_tools as mat


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('B','别人','y','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,script) "
                     "VALUES ('C1','A','我的内容','published','很长的转写正文……')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage) "
                     "VALUES ('C2','B','别人内容','idea')")
    # 打法库共享（persona_id 不同也应都可见）
    await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) VALUES ('P1','A','痛点法','proven')")
    await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) VALUES ('P2','B','悬念法','validating')")
    # 原料库：本人设 + 共享
    await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status) "
                     "VALUES ('M1','A','story','我的料','x','active')")
    await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status,scope) "
                     "VALUES ('M2','B','story','公司料','y','active','shared')")
    await db.commit()
    return db


def test_list_contents_scoped_no_fulltext():
    async def go():
        db = await _seed()
        out = await mat.dispatch_media_tool("list_contents", {}, "A")
        assert "我的内容" in out and "别人内容" not in out       # 只本人设
        assert "很长的转写正文" not in out                       # 不回全文
        await db.close()
    asyncio.run(go())


def test_read_content_returns_fulltext():
    async def go():
        db = await _seed()
        out = await mat.dispatch_media_tool("read_content", {"id": "C1"}, "A")
        assert "很长的转写正文" in out
        await db.close()
    asyncio.run(go())


def test_list_playbooks_shows_shared_all():
    async def go():
        db = await _seed()
        out = await mat.dispatch_media_tool("list_playbooks", {}, "A")
        assert "痛点法" in out and "悬念法" in out               # 共享全部
        await db.close()
    asyncio.run(go())


def test_list_materials_includes_shared():
    async def go():
        db = await _seed()
        out = await mat.dispatch_media_tool("list_materials", {}, "A")
        assert "我的料" in out and "公司料" in out               # 本人设 + shared
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_agent_read_tools.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_agent_tools.py`（工具用自己的 db 连接，与 run_agent_loop 解耦）：
```python
# app/services/media_agent_tools.py
"""自媒体助手工具集：查类 + 改草稿类。ctx=persona_id。每个工具返回给 agent 的文本。"""
from app.database import get_db


async def _tool_list_contents(args, pid):
    stage = (args or {}).get("stage")
    db = await get_db()
    try:
        if stage:
            cur = await db.execute(
                "SELECT id,title,stage FROM media_content WHERE persona_id=? AND stage=? "
                "ORDER BY updated_at DESC LIMIT 50", (pid, stage))
        else:
            cur = await db.execute(
                "SELECT id,title,stage FROM media_content WHERE persona_id=? "
                "ORDER BY updated_at DESC LIMIT 50", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    if not rows:
        return "（该人设暂无内容）"
    return "\n".join(f"[{r['id']}] {r['title']}（{r['stage']}）" for r in rows)


async def _tool_read_content(args, pid):
    cid = (args or {}).get("id", "")
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT title,puzzle,script,ai_draft,stage FROM media_content "
            "WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        return "（找不到这条内容，或不属于当前人设）"
    r = dict(row)
    body = r["script"] or r["ai_draft"] or "（暂无正文/脚本）"
    return f"标题：{r['title']}\n谜题：{r['puzzle']}\n阶段：{r['stage']}\n正文：\n{body}"


async def _tool_list_topics(args, pid):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id,title FROM media_topic WHERE persona_id=? AND status='pool' "
            "ORDER BY created_at DESC LIMIT 50", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"[{r['id']}] {r['title']}" for r in rows) or "（选题池为空）"


async def _tool_list_playbooks(args, pid):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT name,when_to_use,status FROM media_playbook "
            "ORDER BY CASE status WHEN 'proven' THEN 0 ELSE 1 END, created_at DESC")
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    if not rows:
        return "（打法库为空）"
    return "\n".join(f"{r['name']}｜适用:{r['when_to_use']}｜{r['status']}" for r in rows)


async def _tool_list_materials(args, pid):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT type,title,brief FROM media_material "
            "WHERE (persona_id=? OR scope='shared') AND status='active' LIMIT 60", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"[{r['type']}] {r['title']}：{r['brief'] or ''}" for r in rows) or "（原料库为空）"


async def _tool_list_audiences(args, pid):
    db = await get_db()
    try:
        cur = await db.execute("SELECT segment,anxiety FROM media_audience WHERE persona_id=? LIMIT 40", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"{r['segment']}：{r['anxiety'] or ''}" for r in rows) or "（暂无受众）"


async def _tool_list_anchors(args, pid):
    db = await get_db()
    try:
        cur = await db.execute("SELECT name,type,status FROM media_anchor WHERE persona_id=? LIMIT 40", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"{r['name']}（{r['type']}·{r['status']}）" for r in rows) or "（暂无锚点）"


_READ = {
    "list_contents": _tool_list_contents, "read_content": _tool_read_content,
    "list_topics": _tool_list_topics, "list_playbooks": _tool_list_playbooks,
    "list_materials": _tool_list_materials, "list_audiences": _tool_list_audiences,
    "list_anchors": _tool_list_anchors,
}


def _schema(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


MEDIA_TOOL_SCHEMAS = [
    _schema("list_contents", "列出当前人设的内容（标题+阶段，不含正文）。可选 stage 筛选。",
            {"stage": {"type": "string", "description": "可选：idea/scripted/.../published"}}),
    _schema("read_content", "读某条内容的标题/谜题/正文（点名某条时用）。", {"id": {"type": "string"}}, ["id"]),
    _schema("list_topics", "列出当前人设选题池里的待选选题。"),
    _schema("list_playbooks", "列出打法库（全公司共享）。"),
    _schema("list_materials", "列出原料库（本人设 + 公司共享料）。"),
    _schema("list_audiences", "列出当前人设的受众。"),
    _schema("list_anchors", "列出当前人设的锚点。"),
]


async def dispatch_media_tool(name, args, persona_id):
    fn = _READ.get(name)
    if fn:
        return await fn(args, persona_id)
    return f"（未知工具 {name}）"
```
> 列名已按真实 schema 核对：media_audience=segment/anxiety、media_anchor=name/type/status、media_material=type/title/brief/scope、media_content=title/puzzle/script/ai_draft/stage。media_material 的 `scope` 列是多人设那轮加的。

- [ ] **Step 4: GREEN** → PASS（4 passed）。若某表列名不符导致报错，按真实列名调整查询（保持返回文本含标题/名称）。

- [ ] **Step 5: Commit**
```bash
git add app/services/media_agent_tools.py tests/test_media_agent_read_tools.py && git commit -m "feat(media): 助手查类工具(scoped内容/选题池/共享打法库/含shared原料库/受众/锚点)"
```

---

### Task 4: 媒体工具 · 改草稿类（+ 留痕）

**Files:** Modify `app/services/media_agent_tools.py`；Test `tests/test_media_agent_write_tools.py`（新）

**Interfaces:** 加 `create_topic`/`write_next`/`draft_script`/`match_playbook` 到 `_WRITE` + schemas；dispatch 先查 `_READ` 再 `_WRITE`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_agent_write_tools.py
"""媒体改草稿工具：落库 + 记 applied 日志。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_agent_tools as mat


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','企业AI落地','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,script) "
                     "VALUES ('C1','A','数据安全','published','讲了员工泄密，下一期聊怎么防')")
    await db.commit()
    return db


def test_create_topic_writes_pool_and_logs():
    async def go():
        db = await _seed()
        out = await mat.dispatch_media_tool("create_topic",
                {"title": "企业AI数据安全", "puzzle": "怎么既用AI又不泄密"}, "A")
        cur = await db.execute("SELECT COUNT(*) c FROM media_topic WHERE persona_id='A' AND source='assistant'")
        assert (await cur.fetchone())["c"] == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action "
                               "WHERE action_type='create_topic' AND status='applied'")
        assert (await cur.fetchone())["c"] == 1
        assert "企业AI数据安全" in out
        await db.close()
    asyncio.run(go())


def test_write_next_creates_content_with_parent(monkeypatch):
    async def fake_ai(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": '{"title":"数据安全下一集","puzzle":"怎么防","reason":"承接上期"}',
                "model": "x", "tokens": 1, "cost": 0}
    monkeypatch.setattr(mat, "ask_ai", fake_ai)

    async def go():
        db = await _seed()
        out = await mat.dispatch_media_tool("write_next", {"from_content_id": "C1"}, "A")
        cur = await db.execute("SELECT id,parent_content_id,stage,idea_source FROM media_content "
                               "WHERE persona_id='A' AND parent_content_id='C1'")
        row = dict(await cur.fetchone())
        assert row["stage"] == "idea" and row["idea_source"] == "assistant"
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action WHERE action_type='write_next'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_agent_write_tools.py -v` → FAIL

- [ ] **Step 3: 实现** — `media_agent_tools.py` 顶部加 import：
```python
import json, uuid
from app.services.media_assistant import log_action
from app.services.ai_router import ask_ai
```
加改类工具：
```python
async def _tool_create_topic(args, pid):
    a = args or {}
    title = (a.get("title") or "").strip()
    if not title:
        return "（建选题需要 title）"
    tid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_topic (id,persona_id,title,puzzle,source,reason,status) "
            "VALUES (?,?,?,?, 'assistant',?, 'pool')",
            (tid, pid, title, (a.get("puzzle") or "").strip(), (a.get("reason") or "").strip()))
        await log_action(db, pid, "create_topic", "media_topic", tid, after={"title": title})
    finally:
        await db.close()
    return f"已把选题「{title}」加进选题池（可在改动记录里撤销）。"


_NEXT_SYSTEM = """基于给的上一条内容（转写稿+结尾预告），拟这个人设的下一条选题。
只输出严格 JSON：{"title":"","puzzle":"","reason":"承接上期…"}"""


async def _tool_write_next(args, pid):
    cid = (args or {}).get("from_content_id", "")
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT title,script FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        src = await cur.fetchone()
        if not src:
            await db.close()
            return "（找不到源内容）"
        src = dict(src)
        result = await ask_ai(f"上一条标题：{src['title']}\n转写稿：\n{(src['script'] or '')[:4000]}",
                              model="auto", task_type="media_topic",
                              system_prompt=_NEXT_SYSTEM, json_mode=True)
        obj = {}
        try:
            obj = json.loads(result.get("response", "{}"))
        except Exception:
            obj = {}
        title = (obj.get("title") or f"{src['title']}（下一集）").strip()
        ncid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source,idea_reason,parent_content_id) "
            "VALUES (?,?,?,?, 'idea','assistant',?,?)",
            (ncid, pid, title, (obj.get("puzzle") or "").strip(), (obj.get("reason") or "").strip(), cid))
        await log_action(db, pid, "write_next", "media_content", ncid,
                         after={"title": title, "parent": cid})
    finally:
        await db.close()
    return f"已开出续集「{title}」（idea 阶段，承接《{src['title']}》；可在改动记录里撤销）。"


async def _tool_draft_script(args, pid):
    from app.services.media_ai import write_script
    cid = (args or {}).get("content_id", "")
    hint = (args or {}).get("hint", "")
    db = await get_db()
    try:
        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
        if not row:
            await db.close()
            return "（找不到这条内容）"
        before_draft = row["ai_draft"] or ""
        res = await write_script(db, cid, mode="full", hint=hint)
        if not res.get("ok"):
            await db.close()
            return f"（写稿失败：{res.get('error', '')}）"
        await log_action(db, pid, "draft_script", "media_content", cid,
                         before={"ai_draft": before_draft}, after={"ai_draft": res.get("script", "")})
    finally:
        await db.close()
    return "已写好脚本草稿（在内容的口播脚本区，未定稿；可撤销还原）。"


async def _tool_match_playbook(args, pid):
    from app.services.media_ai import match_playbook
    cid = (args or {}).get("content_id", "")
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
        if not row:
            await db.close()
            return "（找不到这条内容）"
        res = await match_playbook(db, dict(row))
    finally:
        await db.close()
    pb = res.get("playbook")
    return (f"最贴的打法：《{pb['name']}》——{pb.get('reason', '')}" if pb
            else "（没有匹配到合适的打法）")


_WRITE = {
    "create_topic": _tool_create_topic, "write_next": _tool_write_next,
    "draft_script": _tool_draft_script, "match_playbook": _tool_match_playbook,
}

MEDIA_TOOL_SCHEMAS += [
    _schema("create_topic", "往选题池加一条选题（草稿·可撤）。",
            {"title": {"type": "string"}, "puzzle": {"type": "string"}, "reason": {"type": "string"}}, ["title"]),
    _schema("write_next", "针对某条内容写下一条续集（建 idea 内容+记血缘·可撤）。",
            {"from_content_id": {"type": "string"}}, ["from_content_id"]),
    _schema("draft_script", "给某条内容写口播脚本草稿（不定稿·可撤）。",
            {"content_id": {"type": "string"}, "hint": {"type": "string"}}, ["content_id"]),
    _schema("match_playbook", "给某条内容匹配最贴的一条打法（读）。",
            {"content_id": {"type": "string"}}, ["content_id"]),
]
```
把 `dispatch_media_tool` 改成先查 `_READ` 再 `_WRITE`：
```python
async def dispatch_media_tool(name, args, persona_id):
    fn = _READ.get(name) or _WRITE.get(name)
    if fn:
        return await fn(args, persona_id)
    return f"（未知工具 {name}）"
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_agent_tools.py tests/test_media_agent_write_tools.py && git commit -m "feat(media): 助手改草稿工具(建选题/续集带血缘/脚本草稿/匹配打法)+全部记applied日志"
```

---

### Task 5: run_agent_loop 向后兼容扩展

**Files:** Modify `app/services/agent_tools.py`；Test `tests/test_agent_loop_custom_tools.py`（新）

**Interfaces:** `run_agent_loop(prompt, system, project_id=None, max_steps=18, on_step=None, tool_schemas=None, dispatch=None, ctx=None)`。

- [ ] **Step 1: 失败测试**（用假 client 验证自定义 dispatch+ctx 生效）
```python
# tests/test_agent_loop_custom_tools.py
"""run_agent_loop 支持自定义工具集/分发/ctx（向后兼容）。"""
import asyncio
import app.services.agent_tools as at


class _FakeMsg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeTC:
    def __init__(self, name):
        self.id = "tc1"
        self.function = type("F", (), {"name": name, "arguments": "{}"})()
    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": "{}"}}


class _FakeResp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]
        self.usage = type("U", (), {"total_tokens": 1, "prompt_tokens": 1, "completion_tokens": 1})()


def test_custom_dispatch_receives_ctx(monkeypatch):
    calls = []

    class _FakeCompletions:
        def __init__(self):
            self.n = 0
        async def create(self, **kw):
            self.n += 1
            if self.n == 1:
                return _FakeResp(_FakeMsg("", [_FakeTC("my_tool")]))
            return _FakeResp(_FakeMsg("完成"))

    class _FakeClient:
        def __init__(self):
            self.chat = type("Ch", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(at, "_get_tool_client", lambda: (_FakeClient(), "fake", "deepseek"))

    async def my_dispatch(name, args, ctx):
        calls.append((name, ctx))
        return "ok"

    async def go():
        r = await at.run_agent_loop("hi", system="s",
                                    tool_schemas=[{"type": "function", "function": {"name": "my_tool", "parameters": {"type": "object", "properties": {}}}}],
                                    dispatch=my_dispatch, ctx="PERSONA_A")
        return r
    r = asyncio.run(go())
    assert calls == [("my_tool", "PERSONA_A")] and "完成" in r["response"]
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_agent_loop_custom_tools.py -v` → FAIL

- [ ] **Step 3: 实现** — 改 `run_agent_loop` 签名与两处用点：
签名加 `tool_schemas=None, dispatch=None, ctx=None`。循环里：
```python
        resp = await client.chat.completions.create(
            model=model_name, messages=messages, tools=(tool_schemas or TOOL_SCHEMAS),
            tool_choice="auto", max_tokens=4096,
        )
```
调工具处：
```python
            _ctx = ctx if ctx is not None else project_id
            result = await (dispatch or _dispatch_tool)(tc.function.name, args, _ctx)
```

- [ ] **Step 4: GREEN** → PASS（1 passed）。回归：`python -m pytest tests/ -q -k "agent or project_ai or master" ` 确认现有 loop 调用未受影响。

- [ ] **Step 5: Commit**
```bash
git add app/services/agent_tools.py tests/test_agent_loop_custom_tools.py && git commit -m "feat(agent): run_agent_loop加tool_schemas/dispatch/ctx可选参(向后兼容)"
```

---

### Task 6: 对话端点 + 助手页路由

**Files:** Modify `app/api/media.py`；Test `tests/test_media_assistant_chat.py`（新）

**Interfaces:** `GET /media/assistant`（页）；`POST /media/assistant/ask`（Form message → 存消息+跑 agent+存回复，返 JSON {reply, steps, cost}）；`POST /media/assistant/clear`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_chat.py
"""助手对话端点：存消息 + 跑 agent。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.api.media as media_api


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ast_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_message WHERE persona_id='AS'")
        await db.execute("DELETE FROM media_persona WHERE id='AS'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('AS','嘉','x','涨粉','active')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_ask_stores_messages(monkeypatch):
    _seed()
    async def fake_loop(prompt, system, ctx=None, tool_schemas=None, dispatch=None, **kw):
        return {"response": "我建了一条选题", "model": "x", "tokens": 1, "cost": 0.01, "steps": [{"tool": "create_topic", "args": {}}]}
    monkeypatch.setattr(media_api, "run_agent_loop", fake_loop)
    r = _client().post("/media/assistant/ask", data={"message": "帮我建个选题"})
    assert r.status_code == 200 and "我建了一条选题" in r.json()["reply"]

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT role FROM media_assistant_message WHERE persona_id='AS' ORDER BY created_at")
        roles = [r["role"] for r in await cur.fetchall()]
        assert roles == ["user", "assistant"]
        await db.close()
    asyncio.run(chk())


def test_page_renders():
    _seed()
    r = _client().get("/media/assistant")
    assert r.status_code == 200
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_chat.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py` import 加：
```python
from app.services.agent_tools import run_agent_loop
from app.services.media_agent_tools import MEDIA_TOOL_SCHEMAS, dispatch_media_tool
from app.services.media_assistant import MEDIA_ASSISTANT_SYSTEM, list_actions, revert_action
from app.services.constitution import with_constitution
```
加路由：
```python
@router.get("/media/assistant", response_class=HTMLResponse)
async def assistant_page(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona, msgs = None, []
        if pid:
            cur = await db.execute("SELECT name,one_liner,current_phase FROM media_persona WHERE id=?", (pid,))
            prow = await cur.fetchone()
            persona = dict(prow) if prow else None
            cur = await db.execute("SELECT role,content FROM media_assistant_message "
                                   "WHERE persona_id=? ORDER BY created_at", (pid,))
            msgs = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_assistant.html", {"persona": persona, "msgs": msgs})


@router.post("/media/assistant/ask")
async def assistant_ask(request: Request, message: str = Form(...)):
    msg = message.strip()
    if not msg:
        return JSONResponse({"ok": False, "error": "空消息"})
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先选人设"})
        cur = await db.execute("SELECT name,one_liner,current_phase FROM media_persona WHERE id=?", (pid,))
        p = dict(await cur.fetchone())
        # 最近 10 条历史
        cur = await db.execute("SELECT role,content FROM media_assistant_message WHERE persona_id=? "
                               "ORDER BY created_at DESC LIMIT 10", (pid,))
        hist = list(reversed([dict(r) for r in await cur.fetchall()]))
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content) VALUES (?,?, 'user',?)",
                         (str(uuid.uuid4()), pid, msg))
        await db.commit()
    finally:
        await db.close()
    persona_line = f"【当前人设】{p['name']}｜{p['one_liner']}｜阶段：{p['current_phase']}"
    hist_text = "\n".join(f"{h['role']}：{h['content']}" for h in hist)
    prompt = (f"{persona_line}\n\n对话历史：\n{hist_text}\n\n──────\n用户：{msg}" if hist_text
              else f"{persona_line}\n\n用户：{msg}")
    try:
        result = await run_agent_loop(prompt, system=with_constitution(MEDIA_ASSISTANT_SYSTEM),
                                      tool_schemas=MEDIA_TOOL_SCHEMAS, dispatch=dispatch_media_tool, ctx=pid)
    except Exception as e:
        log.exception("助手对话失败")
        return JSONResponse({"ok": False, "error": str(e)})
    reply = result.get("response", "")
    db = await get_db()
    try:
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content,cost) "
                         "VALUES (?,?, 'assistant',?,?)",
                         (str(uuid.uuid4()), pid, reply, result.get("cost", 0)))
        await db.commit()
    finally:
        await db.close()
    steps = [s.get("tool") for s in result.get("steps", [])]
    return JSONResponse({"ok": True, "reply": reply, "steps": steps, "cost": result.get("cost", 0)})


@router.post("/media/assistant/clear")
async def assistant_clear(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if pid:
            await db.execute("DELETE FROM media_assistant_message WHERE persona_id=?", (pid,))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/assistant", status_code=303)
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_assistant_chat.py && git commit -m "feat(media): 助手对话端点(存消息+跑run_agent_loop媒体工具+记cost)+助手页路由+清空"
```

---

### Task 7: 改动记录页 + 撤销路由

**Files:** Modify `app/api/media.py`；Create `app/templates/media_assistant_actions.html`；Test `tests/test_media_assistant_actions_route.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_actions_route.py
"""助手改动记录页 + 撤销路由。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_assistant as ma


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("act_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_action():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='AA'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('AA','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_topic (id,persona_id,title,source,status) "
                         "VALUES ('TT','AA','助手建的选题','assistant','pool')")
        aid = await ma.log_action(db, "AA", "create_topic", "media_topic", "TT", after={"title": "助手建的选题"})
        await db.close()
        return aid
    return asyncio.run(go())


def test_actions_page_renders():
    _seed_action()
    r = _client().get("/media/assistant/actions")
    assert r.status_code == 200 and "助手建的选题" in r.text


def test_revert_route():
    aid = _seed_action()
    r = _client().post(f"/media/assistant/action/{aid}/revert", follow_redirects=False)
    assert r.status_code in (302, 303)
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_topic WHERE id='TT'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(chk())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_actions_route.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py` 加：
```python
@router.get("/media/assistant/actions", response_class=HTMLResponse)
async def assistant_actions(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        acts = await list_actions(db, pid) if pid else []
    finally:
        await db.close()
    return _tpl(request, "media_assistant_actions.html", {"actions": acts})


@router.post("/media/assistant/action/{aid}/revert")
async def assistant_action_revert(aid: str):
    db = await get_db()
    try:
        await revert_action(db, aid)
    finally:
        await db.close()
    return RedirectResponse("/media/assistant/actions", status_code=303)
```
新建 `app/templates/media_assistant_actions.html`：
```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}助手改动记录{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} <a href="/media/assistant" style="color:inherit;text-decoration:none">助手</a> {{ ic.icon('chevron') }} <b>改动记录</b></span>
{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  <h1 class="pname" style="margin:0 0 5px">🤖 助手改动记录</h1>
  <p style="font-size:13px;color:var(--ink-3);margin:0 0 16px">助手做过的每件事都在这，有问题点撤销即可回退。</p>
  {% for a in actions %}
  <div class="module" style="margin-top:10px">
    <div class="mh">
      <span class="ttl">{{ {'create_topic':'建选题','write_next':'写下一条','draft_script':'写脚本草稿'}.get(a.action_type, a.action_type) }}</span>
      <span class="tag" style="color:{{ 'var(--ink-3)' if a.status=='reverted' else 'var(--up)' }}">{{ '已撤销' if a.status=='reverted' else '已应用' }}</span>
    </div>
    <div class="inner" style="font-size:13px">
      <div style="color:var(--ink-3); font-size:12px">目标：{{ a.target_table }} · {{ a.target_id[:8] }} · {{ a.created_at }}</div>
      {% if a.status=='applied' and a.reversible %}
      <form method="post" action="/media/assistant/action/{{ a.id }}/revert" style="margin-top:6px"
            onsubmit="return confirm('撤销这个改动？')">
        <button type="submit" class="btn" style="font-size:12px; color:var(--down); border-color:var(--down)">撤销</button>
      </form>
      {% endif %}
    </div>
  </div>
  {% else %}
  <div class="empty" style="padding:34px 12px"><p style="color:var(--ink-3);font-size:13px">助手还没做过改动。</p></div>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py app/templates/media_assistant_actions.html tests/test_media_assistant_actions_route.py && git commit -m "feat(media): 助手改动记录页+撤销路由"
```

---

### Task 8: UI（助手页 + 看板入口 + 内容页标识）

**Files:** Create `app/templates/media_assistant.html`；Modify `app/templates/media_board.html`、`app/templates/media_content.html`、`app/api/media.py`（content_detail 传"助手改过"标记）

- [ ] **Step 1: 助手对话页 `media_assistant.html`**
```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}AI 助手{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} <b>AI 助手</b>{% if persona %} · {{ persona.name }}{% endif %}</span>
{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px">
    <h1 class="pname" style="margin:0">🤖 助手{% if persona %} · {{ persona.name }}{% endif %}</h1>
    <a href="/media/assistant/actions" style="font-size:12.5px; margin-left:auto">改动记录</a>
    <a href="/media" style="font-size:12.5px">← 全部人设</a>
  </div>
  <p style="font-size:12.5px;color:var(--ink-3);margin:0 0 12px">让它查内容/选题/打法库，或建选题、写下一条续集、写脚本草稿。核心动作（采纳入库/删除）暂时去对应页面点。</p>
  <div id="chat" style="border:1px solid var(--border); border-radius:10px; padding:12px; min-height:340px; max-height:60vh; overflow-y:auto; font-size:13.5px; line-height:1.6">
    {% for m in msgs %}
    <div style="margin:8px 0"><b style="color:{{ 'var(--ai)' if m.role=='assistant' else 'var(--ink-1)' }}">{{ '助手' if m.role=='assistant' else '我' }}：</b>{{ m.content }}</div>
    {% else %}<div style="color:var(--ink-3)">还没聊过。试试："帮我列一下已发的内容" 或 "针对某条写下一条"。</div>{% endfor %}
  </div>
  <div style="display:flex; gap:8px; margin-top:10px">
    <input id="ast-input" placeholder="跟助手说…（回车发送）" style="flex:1; padding:9px 12px" onkeydown="if(event.key==='Enter')astSend()">
    <button onclick="astSend()" id="ast-btn" class="btn primary">发送</button>
  </div>
  <div id="ast-status" style="font-size:12px; color:var(--ai); margin-top:6px; min-height:16px"></div>
</div>
<script>
const chat=document.getElementById('chat');
function bubble(who,text){ const d=document.createElement('div'); d.style.margin='8px 0';
  d.innerHTML='<b style="color:'+(who==='助手'?'var(--ai)':'var(--ink-1)')+'">'+who+'：</b>'+text.replace(/</g,'&lt;'); chat.appendChild(d); chat.scrollTop=chat.scrollHeight; }
async function astSend(){
  const inp=document.getElementById('ast-input'); const msg=inp.value.trim(); if(!msg) return;
  const btn=document.getElementById('ast-btn'), st=document.getElementById('ast-status');
  bubble('我',msg); inp.value=''; btn.disabled=true; st.textContent='助手在想（可能会调工具）…';
  try{
    const fd=new FormData(); fd.append('message',msg);
    const r=await fetch('/media/assistant/ask',{method:'POST',body:fd});
    const txt=await r.text(); let d; try{ d=JSON.parse(txt); }catch(_){ st.textContent='没返回内容，稍后重试'; btn.disabled=false; return; }
    if(d.ok){ bubble('助手',d.reply); st.textContent=(d.steps&&d.steps.length?('调了：'+d.steps.join('、')+' · '):'')+'花费 $'+(d.cost||0).toFixed(4); }
    else st.textContent='失败：'+(d.error||'');
  }catch(e){ st.textContent='请求失败：'+e; }
  btn.disabled=false;
}
</script>
{% endblock %}
```

- [ ] **Step 2: 看板入口** — grep `media_board.html` 找到步骤条（`shell.media_shell` 或步骤卡）之后，加一张助手入口卡：
```html
<a href="/media/assistant" class="card" style="display:flex; align-items:center; gap:10px; margin:12px 0; padding:12px 14px; text-decoration:none; color:inherit; border:1px solid var(--border); border-radius:10px">
  <span style="font-size:20px">🤖</span>
  <span><b style="font-size:13.5px">AI 助手</b><span style="color:var(--ink-3); font-size:12px; margin-left:8px">用一句话查/建/写下一条，改动都留痕可撤</span></span>
  <span style="margin-left:auto; color:var(--ink-3)">→</span>
</a>
```
（放在步骤条下方、内容看板主体上方。对齐现有 class；`.card` 无则用内联样式。）

- [ ] **Step 3: 内容详情"助手改过"标识** — `app/api/media.py` 的 `content_detail`：查这条是否有 applied 动作，传 `assistant_touched`：
```python
        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_assistant_action "
            "WHERE target_table='media_content' AND target_id=? AND status='applied'", (cid,))
        assistant_touched = (await cur.fetchone())["c"] > 0
```
把 `assistant_touched` 加进 content_detail 的模板 context。`media_content.html` 标题区（`<h1 ...>{{ content.title }}</h1>` 附近）加：
```html
{% if assistant_touched %}<span class="tag" style="color:var(--ai); margin-left:6px">🤖 助手参与</span>{% endif %}
```

- [ ] **Step 4: 全套回归 + 浏览器冒烟（controller 亲跑）** — `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient + 真机 DeepSeek）：看板出现助手入口卡 → 助手页可发消息 → （真机）让它"列出已发内容"看它调 list_contents、"针对X写下一条"看建出续集 → 改动记录出现该动作 → 撤销 → 续集消失。无 Jinja/500/console。

- [ ] **Step 5: Commit**
```bash
git add app/templates/media_assistant.html app/templates/media_board.html app/templates/media_content.html app/api/media.py && git commit -m "feat(media): 助手对话页+看板入口卡+内容页助手参与标识"
```

---

## Self-Review 记录

- **Spec 覆盖：** §4 引擎扩展→T5；§5 查工具→T3、改工具→T4；§6 数据→T1；§7 留痕/撤销→T2(服务)+T7(页/路由)；§8 对话页+入口→T6(端点)+T8(UI)；§8 内容页标识→T8。Phase 2（核心/确认）不在本 plan（spec §3/§10 明确）。
- **类型一致：** `log_action(db,pid,action_type,target_table,target_id,before,after,conversation_ref)`(T2)→T4 各写工具调；`revert_action`(T2)→T7 路由；`dispatch_media_tool(name,args,pid)`+`MEDIA_TOOL_SCHEMAS`(T3/T4)→T6 端点；`run_agent_loop(...,tool_schemas,dispatch,ctx)`(T5)→T6 调；`list_actions`(T2)→T7 页。
- **纪律：** 查工具回清单不回全文（T3 断言）；list_playbooks 共享全部、list_materials 含 shared（T3 断言）；续集/草稿只注一条打法（复用 write_script/match，未改）。
- **无占位：** 每 step 完整代码。查工具列名已按真实 schema 核对（audience=segment/anxiety 等，见 T3 note）。
