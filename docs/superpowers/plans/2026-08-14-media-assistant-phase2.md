# AI 助手 Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。Steps use checkbox (`- [ ]`).

**Goal:** 给助手加核心动作（标爆款/删除/采纳素材·口头禅·打法），AI 只 stage→待确认卡→人点确认才执行；复用动作日志留痕，删除不可撤。

**Architecture:** 核心工具在 media_agent_tools 只 stage（log_action status='pending'）；media_assistant 加 apply/cancel/list_pending + revert 扩展；media.py 加 apply/cancel/pending 路由；助手页显示待确认卡。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。

## Global Constraints

- 核心工具**只 stage 不执行**：log_action(status='pending')，返"已拟好待确认"。
- **确认才执行**：apply_action 按 action_type 落真动作；标爆款/采纳类可撤(revert)；**删除 apply 后 reversible=0 不可撤**；playbook 归并 reversible=0。
- 采纳 source='assistant'（media_persona_trait/media_material/media_playbook）；采纳类 apply 时把新记录 id 存进 after_json['created_id'/'created_table'] 供撤销。
- 核心工具验证 target 属当前人设（mark_winner/delete_content）。零 schema 变更（status 加 pending/cancelled 取值）。
- 不动 Phase 1 查/改草稿工具。测试 apply/服务用 make_db(传 db 进函数)；路由用 tmp-DB_PATH 模块 fixture。改模板 Edit/Write，JS 不塞 SVG。reseed 删 persona 前先删子表。
- 跑 pytest：`cd /d/GAGA-5-25/ai-pm && python -m pytest ... ; echo EXIT=${PIPESTATUS[0]}`（cwd 每次重置，先 cd）。假挂 `taskkill //F //IM python.exe`。

---

### Task 1: media_assistant 基础扩展（log_action status + cancel + list_pending + 系统提示）

**Files:** Modify `app/services/media_assistant.py`；Test `tests/test_media_assistant_pending.py`（新）

**Interfaces:** `log_action(..., status='applied')`；`async cancel_action(db, action_id)->bool`；`async list_pending(db, persona_id)->list`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_pending.py
"""助手 pending：log status + cancel + list_pending。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.commit()
    return db


def test_log_pending_and_list():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "mark_winner", "media_content", "C1",
                                  after={"summary": "标爆款《X》", "content_id": "C1"}, status="pending")
        pend = await ma.list_pending(db, "A")
        assert len(pend) == 1 and pend[0]["id"] == aid and pend[0]["status"] == "pending"
        await db.close()
    asyncio.run(go())


def test_cancel():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "mark_winner", "media_content", "C1",
                                  after={"summary": "x"}, status="pending")
        assert await ma.cancel_action(db, aid) is True
        cur = await db.execute("SELECT status FROM media_assistant_action WHERE id=?", (aid,))
        assert (await cur.fetchone())["status"] == "cancelled"
        assert await ma.list_pending(db, "A") == []
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_pending.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_assistant.py`：
`log_action` 签名加 `status="applied"`，INSERT 加 status 列：
```python
async def log_action(db, persona_id, action_type, target_table, target_id,
                     before=None, after=None, conversation_ref="", status="applied") -> str:
    aid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_assistant_action "
        "(id,persona_id,conversation_ref,action_type,target_table,target_id,before_json,after_json,status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (aid, persona_id, conversation_ref, action_type, target_table, target_id,
         json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False), status))
    await db.commit()
    return aid
```
文件末尾加：
```python
async def cancel_action(db, action_id) -> bool:
    cur = await db.execute("SELECT status FROM media_assistant_action WHERE id=?", (action_id,))
    r = await cur.fetchone()
    if not r or r["status"] != "pending":
        return False
    await db.execute("UPDATE media_assistant_action SET status='cancelled' WHERE id=?", (action_id,))
    await db.commit()
    return True


async def list_pending(db, persona_id) -> list:
    cur = await db.execute(
        "SELECT * FROM media_assistant_action WHERE persona_id=? AND status='pending' "
        "ORDER BY created_at", (persona_id,))
    return [dict(r) for r in await cur.fetchall()]
```
更新 `MEDIA_ASSISTANT_SYSTEM`：把结尾"采纳进库/删除这类核心动作你现在不能做（让用户去对应页面点）。"整句换成：
```
你也能做核心动作：标爆款、删除内容、把口头禅/素材/打法采纳进库。但这些你只是"拟"——系统会生成待确认卡，用户点确认后才真执行，你不用等结果，告诉用户"已拟好，去确认卡点确认"即可。删除内容确认后不可撤，涉及删除务必先说清楚是哪条。
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_assistant.py tests/test_media_assistant_pending.py && git commit -m "feat(media): 助手log_action加status参+cancel_action+list_pending+系统提示允许核心动作(待确认)"
```

---

### Task 2: apply_action + revert 扩展

**Files:** Modify `app/services/media_assistant.py`；Test `tests/test_media_assistant_apply.py`（新）

**Interfaces:** `async apply_action(db, action_id)->bool`；`revert_action` 加 mark_winner/adopt_* 分支 + reversible 守卫。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_apply.py
"""apply_action 各类型 + 撤销。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                     "VALUES ('C1','A','内容甲','published',0)")
    await db.commit()
    return db


def test_apply_mark_winner_and_revert():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "mark_winner", "media_content", "C1",
                                  after={"summary": "标爆款", "content_id": "C1"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["is_winner"] == 1
        assert await ma.revert_action(db, aid) is True          # 可撤
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["is_winner"] == 0
        await db.close()
    asyncio.run(go())


def test_apply_adopt_signature_and_revert():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "adopt_signature", "media_persona_trait", "",
                                  after={"summary": "口头禅", "content": "你要知道"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait "
                               "WHERE persona_id='A' AND dimension='signature'")
        assert (await cur.fetchone())["c"] == 1
        assert await ma.revert_action(db, aid) is True          # 删掉写入的记录
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait WHERE persona_id='A'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(go())


def test_apply_delete_content_irreversible():
    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "delete_content", "media_content", "C1",
                                  after={"summary": "删除《内容甲》"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        cur = await db.execute("SELECT COUNT(*) c FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["c"] == 0
        assert await ma.revert_action(db, aid) is False         # 不可撤
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_apply.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_assistant.py` 加 `apply_action`：
```python
async def apply_action(db, action_id) -> bool:
    cur = await db.execute("SELECT * FROM media_assistant_action WHERE id=?", (action_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "pending":
        return False
    a = dict(row)
    at, pid, tid = a["action_type"], a["persona_id"], a["target_id"]
    after = json.loads(a["after_json"] or "{}")
    before, reversible = {}, 1
    if at == "mark_winner":
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id=?", (tid,))
        r = await cur.fetchone()
        before = {"is_winner": (r["is_winner"] if r else 0)}
        await db.execute("UPDATE media_content SET is_winner=1 WHERE id=?", (tid,))
    elif at == "delete_content":
        await db.execute("DELETE FROM media_metrics WHERE publish_id IN "
                         "(SELECT id FROM media_publish WHERE content_id=?)", (tid,))
        for tbl in ("media_publish", "media_review", "media_case",
                    "media_evidence", "media_angle", "media_draft_review"):
            await db.execute(f"DELETE FROM {tbl} WHERE content_id=?", (tid,))
        await db.execute("DELETE FROM media_content WHERE id=?", (tid,))
        reversible = 0
    elif at == "adopt_signature":
        content = (after.get("content") or "").strip()
        nid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?, 'signature',?,?, 'assistant',?,3,'')",
            (nid, pid, content, (after.get("brief") or content)[:30], (after.get("evidence") or "").strip()))
        after["created_id"], after["created_table"] = nid, "media_persona_trait"
    elif at == "adopt_material":
        detail = (after.get("content") or "").strip()
        nid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_material (id,persona_id,type,title,detail,brief,source) "
            "VALUES (?,?,?,?,?,?, 'assistant')",
            (nid, pid, (after.get("type") or "story"), detail[:40], detail,
             (after.get("brief") or detail)[:30]))
        after["created_id"], after["created_table"] = nid, "media_material"
    elif at == "adopt_playbook":
        name = (after.get("name") or "").strip()
        sim = (after.get("similar_to") or "").strip()
        merged = False
        if sim:
            cur = await db.execute("SELECT id,evidence FROM media_playbook WHERE persona_id=? AND name=?", (pid, sim))
            ex = await cur.fetchone()
            if ex:
                new_ev = ((ex["evidence"] or "") + "\n---\n" + (after.get("evidence") or "")).strip()
                await db.execute("UPDATE media_playbook SET evidence=? WHERE id=?", (new_ev, ex["id"]))
                merged, reversible = True, 0
        if not merged:
            nid = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO media_playbook "
                "(id,persona_id,name,structure,when_to_use,evidence,source,status) "
                "VALUES (?,?,?,?,?,?, 'assistant','validating')",
                (nid, pid, name, (after.get("structure") or "").strip(),
                 (after.get("when_to_use") or "").strip(), (after.get("evidence") or "").strip()))
            after["created_id"], after["created_table"] = nid, "media_playbook"
    else:
        return False
    await db.execute(
        "UPDATE media_assistant_action SET status='applied', reversible=?, before_json=?, after_json=? WHERE id=?",
        (reversible, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), action_id))
    await db.commit()
    return True
```
改 `revert_action`：在读出 row 后、分支前加 reversible 守卫；加 mark_winner/adopt_* 分支。把现有 `revert_action` 里 `a = dict(row)` 之后加：
```python
    if not row["reversible"]:
        return False
```
在现有 `elif a["action_type"] == "organize_format":` 分支之后、`else:` 之前加：
```python
    elif a["action_type"] == "mark_winner":
        await db.execute("UPDATE media_content SET is_winner=? WHERE id=?",
                         (before.get("is_winner", 0), a["target_id"]))
    elif a["action_type"] in ("adopt_signature", "adopt_material", "adopt_playbook"):
        after = json.loads(a["after_json"] or "{}")
        tbl, nid = after.get("created_table"), after.get("created_id")
        if not tbl or not nid:
            return False
        await db.execute(f"DELETE FROM {tbl} WHERE id=?", (nid,))
```

- [ ] **Step 4: GREEN** → PASS（3 passed）；回归 `python -m pytest tests/test_media_assistant_service.py tests/test_media_assistant_actions_route.py -q`（Phase 1 撤销不受影响）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_assistant.py tests/test_media_assistant_apply.py && git commit -m "feat(media): apply_action(标爆款/删除/采纳三库)+revert扩展(可撤类还原/删除不可撤)"
```

---

### Task 3: 核心 stage 工具（media_agent_tools _CORE）

**Files:** Modify `app/services/media_agent_tools.py`；Test `tests/test_media_agent_core_tools.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_agent_core_tools.py
"""核心 stage 工具：只落 pending 不执行，验 target 属人设。用 tmp-DB_PATH fixture(工具内部 get_db)。"""
import asyncio, pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("core_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id IN ('A','B')")
        await db.execute("DELETE FROM media_content WHERE persona_id IN ('A','B')")
        await db.execute("DELETE FROM media_persona WHERE id IN ('A','B')")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('A','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('B','别人','y','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                         "VALUES ('C1','A','内容甲','published',0)")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage) "
                         "VALUES ('C2','B','别人内容','published')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_mark_winner_stages_pending_not_execute():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("mark_winner", {"content_id": "C1"}, "A")
        assert "确认" in out
        db = await get_db()
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["is_winner"] == 0        # 没执行
        cur = await db.execute("SELECT status,action_type FROM media_assistant_action WHERE persona_id='A'")
        row = dict(await cur.fetchone())
        assert row["status"] == "pending" and row["action_type"] == "mark_winner"
        await db.close()
    asyncio.run(go())


def test_mark_winner_rejects_other_persona():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("mark_winner", {"content_id": "C2"}, "A")  # C2 属 B
        assert "找不到" in out or "不属于" in out
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action WHERE persona_id='A'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_agent_core_tools.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_agent_tools.py`：import 加 `from app.services.media_assistant import log_action`（若已因 write 工具引入则复用）。加核心 stage：
```python
async def _core_stage(pid, action_type, target_table, target_id, after):
    db = await get_db()
    try:
        await log_action(db, pid, action_type, target_table, target_id, after=after, status="pending")
    finally:
        await db.close()
    return "已拟好：" + after.get("summary", "") + "。去助手页待确认卡点「确认」才执行。"


async def _tool_mark_winner(args, pid):
    cid = (args or {}).get("content_id", "")
    db = await get_db()
    try:
        cur = await db.execute("SELECT title FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        return "（找不到这条内容，或不属于当前人设）"
    return await _core_stage(pid, "mark_winner", "media_content", cid,
                             {"summary": f"把《{row['title']}》标为爆款", "content_id": cid})


async def _tool_delete_content(args, pid):
    cid = (args or {}).get("content_id", "")
    db = await get_db()
    try:
        cur = await db.execute("SELECT title FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        return "（找不到这条内容，或不属于当前人设）"
    return await _core_stage(pid, "delete_content", "media_content", cid,
                             {"summary": f"删除《{row['title']}》（确认后不可撤）", "content_id": cid})


async def _tool_adopt_signature(args, pid):
    content = ((args or {}).get("content") or "").strip()
    if not content:
        return "（要采纳的口头禅内容为空）"
    return await _core_stage(pid, "adopt_signature", "media_persona_trait", "",
                             {"summary": f"把口头禅「{content[:20]}」加进人设",
                              "content": content, "evidence": (args or {}).get("evidence", "")})


async def _tool_adopt_material(args, pid):
    content = ((args or {}).get("content") or "").strip()
    if not content:
        return "（要采纳的素材内容为空）"
    return await _core_stage(pid, "adopt_material", "media_material", "",
                             {"summary": f"把素材「{content[:20]}」存进原料库",
                              "type": (args or {}).get("type", "story"), "content": content,
                              "brief": (args or {}).get("brief", ""), "evidence": (args or {}).get("evidence", "")})


async def _tool_adopt_playbook(args, pid):
    name = ((args or {}).get("name") or "").strip()
    if not name:
        return "（打法名为空）"
    a = args or {}
    return await _core_stage(pid, "adopt_playbook", "media_playbook", "",
                             {"summary": f"把打法《{name}》采纳进打法库", "name": name,
                              "structure": a.get("structure", ""), "when_to_use": a.get("when_to_use", ""),
                              "evidence": a.get("evidence", ""), "similar_to": a.get("similar_to", "")})


_CORE = {
    "mark_winner": _tool_mark_winner, "delete_content": _tool_delete_content,
    "adopt_signature": _tool_adopt_signature, "adopt_material": _tool_adopt_material,
    "adopt_playbook": _tool_adopt_playbook,
}

MEDIA_TOOL_SCHEMAS += [
    _schema("mark_winner", "把某条内容标为爆款（需人确认）。", {"content_id": {"type": "string"}}, ["content_id"]),
    _schema("delete_content", "删除某条内容（需人确认，确认后不可撤）。", {"content_id": {"type": "string"}}, ["content_id"]),
    _schema("adopt_signature", "把一句口头禅采纳进人设（需人确认）。",
            {"content": {"type": "string"}, "evidence": {"type": "string"}}, ["content"]),
    _schema("adopt_material", "把一条素材采纳进原料库（需人确认）。",
            {"type": {"type": "string"}, "content": {"type": "string"},
             "brief": {"type": "string"}, "evidence": {"type": "string"}}, ["content"]),
    _schema("adopt_playbook", "把一个打法采纳进打法库（需人确认）。",
            {"name": {"type": "string"}, "structure": {"type": "string"},
             "when_to_use": {"type": "string"}, "evidence": {"type": "string"},
             "similar_to": {"type": "string"}}, ["name"]),
]
```
dispatch 改：`fn = _READ.get(name) or _WRITE.get(name) or _CORE.get(name)`。

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_agent_tools.py tests/test_media_agent_core_tools.py && git commit -m "feat(media): 助手核心stage工具(标爆款/删除/采纳素材口头禅打法·只落pending待确认)"
```

---

### Task 4: 端点（apply/cancel/pending）+ assistant_ask pending_count

**Files:** Modify `app/api/media.py`；Test `tests/test_media_assistant_confirm_routes.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_confirm_routes.py
"""确认/取消/待确认清单路由。"""
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
    tmp = tmp_path_factory.mktemp("cf_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_pending():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id='CF'")
        await db.execute("DELETE FROM media_content WHERE persona_id='CF'")
        await db.execute("DELETE FROM media_persona WHERE id='CF'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('CF','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                         "VALUES ('CFC','CF','内容','published',0)")
        aid = await ma.log_action(db, "CF", "mark_winner", "media_content", "CFC",
                                  after={"summary": "标爆款《内容》", "content_id": "CFC"}, status="pending")
        await db.close()
        return aid
    return asyncio.run(go())


def test_pending_then_apply():
    aid = _seed_pending()
    r = _client().get("/media/assistant/pending")
    d = r.json()
    assert d["pending"] and d["pending"][0]["id"] == aid and "标爆款" in d["pending"][0]["summary"]
    r = _client().post(f"/media/assistant/action/{aid}/apply")
    assert r.status_code == 200 and r.json()["ok"] is True

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='CFC'")
        assert (await cur.fetchone())["is_winner"] == 1
        await db.close()
    asyncio.run(chk())


def test_cancel_route():
    aid = _seed_pending()
    r = _client().post(f"/media/assistant/action/{aid}/cancel")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = _client().get("/media/assistant/pending")
    assert r.json()["pending"] == []
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_confirm_routes.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py`：import 处（`from app.services.media_assistant import ...`）加 `apply_action, cancel_action, list_pending`。加路由（`assistant_action_revert` 附近）：
```python
@router.post("/media/assistant/action/{aid}/apply")
async def assistant_action_apply(aid: str):
    db = await get_db()
    try:
        ok = await apply_action(db, aid)
    finally:
        await db.close()
    return JSONResponse({"ok": ok})


@router.post("/media/assistant/action/{aid}/cancel")
async def assistant_action_cancel(aid: str):
    db = await get_db()
    try:
        ok = await cancel_action(db, aid)
    finally:
        await db.close()
    return JSONResponse({"ok": ok})


@router.get("/media/assistant/pending")
async def assistant_pending(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        items = await list_pending(db, pid) if pid else []
    finally:
        await db.close()
    out = []
    for a in items:
        after = json.loads(a["after_json"] or "{}")
        out.append({"id": a["id"], "action_type": a["action_type"],
                    "summary": after.get("summary", a["action_type"])})
    return JSONResponse({"pending": out})
```
`assistant_ask` 的 return（现 `return JSONResponse({"ok": True, "reply": reply, "steps": steps, "cost": ...})`）——在存完 assistant 消息后、return 前加统计并入返回：
```python
    db = await get_db()
    try:
        pend = await list_pending(db, pid)
    finally:
        await db.close()
    return JSONResponse({"ok": True, "reply": reply, "steps": steps,
                         "cost": result.get("cost", 0), "pending_count": len(pend)})
```

- [ ] **Step 4: GREEN** → PASS（2 passed）；全套回归 `python -m pytest -q`

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_assistant_confirm_routes.py && git commit -m "feat(media): 助手apply/cancel/pending路由+assistant_ask返pending_count"
```

---

### Task 5: 助手页待确认卡 UI

**Files:** Modify `app/templates/media_assistant.html`

- [ ] **Step 1: 待确认卡区 + JS** — grep `media_assistant.html` 找到聊天区（`#chat`）与输入区之间，插入待确认卡容器：
```html
  <div id="ast-pending" style="margin:10px 0"></div>
```
在页面脚本里加（复用现有 astSend 之后；页面加载 + 每次发消息后刷新）：
```html
<script>
async function loadPending(){
  const box=document.getElementById('ast-pending');
  try{
    const d=await (await fetch('/media/assistant/pending')).json();
    if(!d.pending || !d.pending.length){ box.innerHTML=''; return; }
    box.innerHTML='<div style="font-size:12px;color:var(--ink-3);margin-bottom:4px">待确认（点确认才执行）</div>'+
      d.pending.map(function(p){
        return '<div style="border:1px solid var(--ai);border-radius:8px;padding:10px;margin:6px 0;background:var(--ai-soft)">'+
          '<div style="font-size:13px;margin-bottom:6px">'+p.summary.replace(/</g,'&lt;')+'</div>'+
          '<button class="btn primary" style="font-size:12px;padding:4px 12px" onclick="confirmAction(\''+p.id+'\',\'apply\')">确认</button> '+
          '<button class="btn" style="font-size:12px;padding:4px 12px" onclick="confirmAction(\''+p.id+'\',\'cancel\')">取消</button></div>';
      }).join('');
  }catch(e){}
}
async function confirmAction(id, act){
  await fetch('/media/assistant/action/'+id+'/'+act,{method:'POST'});
  loadPending();
}
document.addEventListener('DOMContentLoaded', loadPending);
</script>
```
并在现有 `astSend` 成功回调末尾加一行 `loadPending();`（发完消息刷新待确认卡；grep 找到 astSend 里 bubble('助手',...) 之后）。

- [ ] **Step 2: 全套回归 + 浏览器冒烟（controller 亲跑）** — `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient + 真机 DeepSeek）：seed 一条 pending → 助手页 loadPending 渲染卡 → 点确认 apply 生效（is_winner 改）→ 卡消失；真机对话"把《X》标爆款"→出卡→确认→生效。无 Jinja/500/console。

- [ ] **Step 3: Commit**
```bash
git add app/templates/media_assistant.html && git commit -m "feat(media): 助手页待确认卡(确认/取消·发消息与加载时刷新)"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 确认机制→T1(pending/cancel/list)+T2(apply)+T4(路由)；§4 服务扩展→T1+T2；§5 核心工具→T3；§6 端点→T4、UI→T5；系统提示→T1。
- **类型一致：** `log_action(...,status)`(T1)→T3 stage 传 pending；`apply_action`/`cancel_action`/`list_pending`(T1/T2)→T4 路由；`revert_action` 加分支+reversible 守卫(T2)；stage after_json 存 summary+params→apply_action 读 params 执行+回填 created_id/table→revert 读 created_* 删记录（三处一致）。
- **纪律：** 核心工具只 stage 不执行（T3 断言 is_winner 未改）；验 target 属人设（T3 拒 C2）。删除 reversible=0（T2 断言 revert False）。
- **无占位：** 每 step 完整代码。T3 测试用 tmp-DB_PATH 模块 fixture(与 Phase 1 工具测试一致，工具内部真 get_db)，_seed 先删子表再删 persona(FK)。
