# 打法库🅓 接写稿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 写口播脚本时 AI 从共享打法库挑最贴选题的一条打法当骨架注入，写完显示"用了《X》·理由"，人可换一条或不用打法重写。

**Architecture:** 匹配(`match_playbook`)是独立 AI 小调用，整库只在此出现；写稿(`write_script`)prompt 只注入匹配到的那一条。零 schema 变更（`used_playbook_ids` 列已存在）。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。AI 走 `ask_ai`。

## Global Constraints

- **注意力纪律（最高优先级）**：写稿 AI prompt 里最多出现一条打法，绝不整库注入。整库只在隔离的 `match_playbook` 调用里出现一次。匹配不到/库空→写稿不注入任何打法。
- **零 schema 变更**：`media_content.used_playbook_ids` 列已存在；`media_playbook` 表已建。无 migration。
- **诚实+成本可见**：匹配不合适返 None 不硬凑；调 AI 记 `log_injection`。
- **测试用 `make_db()`**（内存 DB，已应用 SCHEMA+MIGRATIONS，FK 约束开着，seed persona 在前，用独立 persona id）。改模板用 Edit/Write。JS 不塞 SVG 进字符串。
- **不动**：打法库建/采纳/status/共享逻辑、写稿其他注入(人设/原料/角度/证据)、决策引擎。
- 跑 pytest 用 `python -m pytest ...; echo EXIT=${PIPESTATUS[0]}`；假挂 `taskkill //F //IM python.exe`。shell 的 cwd 每次会重置到 `D:\GAGA-5-25`，每条 bash 先 `cd /d/GAGA-5-25/ai-pm &&`。

---

### Task 1: `match_playbook` 隔离匹配

**Files:** Modify `app/services/media_ai.py`（在 `mine_structure` 之后加）；Test `tests/test_media_playbook_match.py`（新）

**Interfaces:**
- Produces: `async match_playbook(db, content: dict, model="auto") -> dict`，返回 `{"ok":True, "playbook": {"id","name","structure","when_to_use","status","reason"} 或 None, "cost", "model"}`。`content` 是 media_content 行 dict（用到 id/title/puzzle/idea_reason）。
- Consumes: 本文件已有 `ask_ai`、`extract_json`、`log_injection`。

- [ ] **Step 1: 失败测试**

```python
# tests/test_media_playbook_match.py
"""打法库→选题 隔离匹配。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_ai


def _content(title="老板买了AI用不起来"):
    return {"id": "C1", "title": title, "puzzle": "为什么？", "idea_reason": "受众焦虑"}


def _stub(resp, calls):
    async def go(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        calls.append(prompt)
        return {"response": resp, "model": "deepseek", "tokens": 5, "cost": 0.0}
    return go


def test_match_hit(monkeypatch):
    db = make_db_sync()
    calls = []
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"PB1","reason":"命中焦虑受众"}', calls))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"]["id"] == "PB1" and r["playbook"]["reason"] == "命中焦虑受众"
    assert r["playbook"]["name"] == "痛点自曝法"
    asyncio.run(db.close())


def test_match_none_when_unsuitable(monkeypatch):
    db = make_db_sync()
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"","reason":""}', []))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"] is None
    asyncio.run(db.close())


def test_match_bogus_id(monkeypatch):
    db = make_db_sync()
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"NOPE","reason":"x"}', []))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"] is None
    asyncio.run(db.close())


def test_match_empty_pool_no_ai_call(monkeypatch):
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MPB','嘉','x','涨粉','active')")  # 无 playbook
        await db.commit()
        return db
    db = asyncio.run(go())
    called = []
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"PB1"}', called))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"] is None and called == []   # 池空不调 AI
    asyncio.run(db.close())


def make_db_sync():
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MPB','嘉','x','涨粉','active')")
        for pid, name, wtu in (("PB1", "痛点自曝法", "焦虑/踩坑类选题"),
                               ("PB2", "数据打脸法", "反常识类选题")):
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,when_to_use,structure,status) "
                             "VALUES (?,?,?,?, '抛→转→收','validating')", (pid, "MPB", name, wtu))
        await db.commit()
        return db
    return asyncio.run(go())
```

- [ ] **Step 2: RED** — `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_media_playbook_match.py -v; echo EXIT=${PIPESTATUS[0]}` → FAIL（`match_playbook` 不存在）

- [ ] **Step 3: 实现** — `app/services/media_ai.py`，在 `mine_structure` 函数之后加：

```python
MATCH_PLAYBOOK_SYSTEM = """给这条选题从「打法清单」里挑最贴的一条打法。
诚实：只有真的贴才挑；都不合适就返回空 playbook_id，别硬凑。只挑一条。
playbook_id 必须是清单里出现过的 id（方括号里那个），或空串。reason 一句话说为什么这条选题适合这个打法。
只输出严格 JSON：{"playbook_id":"","reason":""}"""


async def match_playbook(db, content: dict, model: str = "auto") -> dict:
    """从共享打法库挑最贴这条选题的一条。整库只在这里出现，绝不进写稿 prompt。
    池空不调 AI。返回 {ok, playbook:{...}|None, cost, model}。"""
    cur = await db.execute(
        "SELECT id,name,when_to_use,structure,status FROM media_playbook "
        "WHERE status IN ('proven','validating')")
    pool = [dict(r) for r in await cur.fetchall()]
    if not pool:
        return {"ok": True, "playbook": None, "cost": 0, "model": ""}
    menu = "\n".join(
        f"[{p['id']}] {p['name']}｜适用:{p['when_to_use']}｜结构:{p['structure']}" for p in pool)
    q = (f"选题：{content.get('title','')}\n谜题：{content.get('puzzle','')}\n"
         f"为什么做：{content.get('idea_reason','')}\n\n打法清单：\n{menu}")
    result = await ask_ai(q, model=model, task_type="media_topic",
                          system_prompt=MATCH_PLAYBOOK_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    await log_injection(db, content.get("id", ""), "match_playbook", [], result.get("tokens", 0))
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "playbook": None, "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object") or {}
    pid = (obj.get("playbook_id") or "").strip()
    by_id = {p["id"]: p for p in pool}
    if pid not in by_id:
        return {"ok": True, "playbook": None,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    p = by_id[pid]
    return {"ok": True, "cost": result.get("cost", 0), "model": result.get("model", ""),
            "playbook": {"id": p["id"], "name": p["name"], "structure": p["structure"],
                         "when_to_use": p["when_to_use"], "status": p["status"],
                         "reason": (obj.get("reason") or "").strip()}}
```

- [ ] **Step 4: GREEN** — 同命令 → PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
cd /d/GAGA-5-25/ai-pm && git add app/services/media_ai.py tests/test_media_playbook_match.py && git commit -m "feat(media): match_playbook隔离匹配打法(池空不调AI/瞎编id当没匹配)"
```

---

### Task 2: `write_script` 注入打法骨架

**Files:** Modify `app/services/media_ai.py`（`write_script`）；Test `tests/test_media_playbook_inject.py`（新）

**Interfaces:**
- Consumes: `match_playbook`（Task 1）。
- Produces: `write_script(db, content_id, mode="full", model="auto", hint="", playbook_id="") -> dict`；结果 dict 新增 `"playbook": {"id","name","reason","status"}|None`。`playbook_id`：`""`=自动匹配；`"none"`=不用打法；`"<id>"`=用指定那条。

- [ ] **Step 1: 失败测试**

```python
# tests/test_media_playbook_inject.py
"""write_script 注入一条打法骨架。"""
import asyncio
import json
from tests.media_helpers import make_db
from app.services import media_ai


def _setup():
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('WP','嘉','帮企业落地AI','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source) "
                         "VALUES ('WC','WP','老板买AI用不起来','为什么','idea','manual')")
        for pid, name in (("PB1", "痛点自曝法"), ("PB2", "数据打脸法")):
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,when_to_use,structure,status) "
                             "VALUES (?,?,?, '焦虑选题','前3秒抛痛点→自曝→给法','proven')", (pid, "WP", name))
        await db.commit()
        return db
    return asyncio.run(go())


def _capture_write(captured):
    """替 write 那次 ask_ai：记录 prompt，返回固定脚本。"""
    async def go(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        captured["prompt"] = prompt
        return {"response": "这是脚本正文。", "model": "deepseek", "tokens": 9, "cost": 0.0}
    return go


def _fixed_match(pb):
    async def go(db, content, model="auto"):
        return {"ok": True, "playbook": pb, "cost": 0, "model": ""}
    return go


def _pb(pid="PB1", name="痛点自曝法"):
    return {"id": pid, "name": name, "structure": "前3秒抛痛点→自曝→给法",
            "when_to_use": "焦虑选题", "status": "proven", "reason": "命中焦虑受众"}


def test_auto_match_injects_and_records(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    monkeypatch.setattr(media_ai, "match_playbook", _fixed_match(_pb()))
    r = asyncio.run(media_ai.write_script(db, "WC"))
    assert r["ok"] and r["playbook"]["id"] == "PB1"
    assert "【打法骨架】" in cap["prompt"] and "痛点自曝法" in cap["prompt"]
    assert "数据打脸法" not in cap["prompt"]     # 只注一条
    cur = asyncio.run(db.execute("SELECT used_playbook_ids FROM media_content WHERE id='WC'"))
    row = asyncio.run(cur.fetchone())
    assert json.loads(row["used_playbook_ids"]) == ["PB1"]
    asyncio.run(db.close())


def test_none_skips(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    called = []
    async def _spy(db, content, model="auto"):
        called.append(1); return {"ok": True, "playbook": _pb()}
    monkeypatch.setattr(media_ai, "match_playbook", _spy)
    r = asyncio.run(media_ai.write_script(db, "WC", playbook_id="none"))
    assert r["playbook"] is None and called == []       # 不匹配
    assert "【打法骨架】" not in cap["prompt"]
    cur = asyncio.run(db.execute("SELECT used_playbook_ids FROM media_content WHERE id='WC'"))
    assert json.loads((asyncio.run(cur.fetchone()))["used_playbook_ids"]) == []
    asyncio.run(db.close())


def test_explicit_id_no_match_call(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    called = []
    async def _spy(db, content, model="auto"):
        called.append(1); return {"ok": True, "playbook": _pb()}
    monkeypatch.setattr(media_ai, "match_playbook", _spy)
    r = asyncio.run(media_ai.write_script(db, "WC", playbook_id="PB2"))
    assert r["playbook"]["id"] == "PB2" and called == []   # 指定了就不再匹配
    assert "数据打脸法" in cap["prompt"] and "痛点自曝法" not in cap["prompt"]
    asyncio.run(db.close())


def test_lean_no_inject(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    r = asyncio.run(media_ai.write_script(db, "WC", mode="lean"))
    assert r["playbook"] is None and "【打法骨架】" not in cap["prompt"]
    asyncio.run(db.close())
```

- [ ] **Step 2: RED** — `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_media_playbook_inject.py -v; echo EXIT=${PIPESTATUS[0]}` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_ai.py` 改 `write_script`：

3a. 签名加 `playbook_id`：

```python
async def write_script(db, content_id: str, mode: str = "full",
                       model: str = "auto", hint: str = "", playbook_id: str = "") -> dict:
```

3b. 在 `material_block` 计算之后、`parts = [context_text]` 之前，加确定打法的逻辑：

```python
    playbook = None
    if mode != "lean":
        if playbook_id == "none":
            playbook = None
        elif playbook_id:
            cur = await db.execute(
                "SELECT id,name,structure,when_to_use,status FROM media_playbook WHERE id=?",
                (playbook_id,))
            prow = await cur.fetchone()
            if prow:
                playbook = dict(prow)
                playbook["reason"] = "（手动指定）"
        else:
            m = await match_playbook(db, content, model=model)
            playbook = m.get("playbook")
```

3c. 在 `ang_block` 那段之后、`parts.append(f"【本条选题】{content['title']}")` 之前，插入打法骨架注入：

```python
    if playbook:
        parts.append(
            f"【打法骨架】{playbook['name']}（{playbook.get('when_to_use','')}）\n"
            f"{playbook.get('structure','')}\n"
            f"（按这个结构写，但别硬套；结构服务于内容，不是填空）")
```

3d. 持久化草稿的 UPDATE 加 `used_playbook_ids`（原语句只有 ai_draft/evidence_gap/authoring_stage）：

```python
    used_pb = [playbook["id"]] if playbook else []
    await db.execute(
        "UPDATE media_content SET ai_draft=?, evidence_gap=?, used_playbook_ids=?, "
        "authoring_stage='drafted', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (resp, gap_text, json.dumps(used_pb), content_id))
```

3e. 返回值加 `playbook` 字段（原 return 保留其余字段不变）：

```python
    pb_out = ({"id": playbook["id"], "name": playbook["name"],
               "reason": playbook.get("reason", ""), "status": playbook.get("status", "")}
              if playbook else None)
    return {"ok": True, "script": resp, "error": "", "gap": gap_text,
            "cost": result.get("cost", 0), "model": result.get("model", ""),
            "injected_count": len(all_injected), "playbook": pb_out}
```

（注：`json` 本文件顶部已 import。lean 模式 `used_pb=[]` 会写空——lean 本就是覆盖 ai_draft 的诊断模式，一致。）

- [ ] **Step 4: GREEN** — 同命令 → PASS（4 passed）。再跑一次 match 测试确保没回归：`python -m pytest tests/test_media_playbook_match.py tests/test_media_playbook_inject.py -q; echo EXIT=${PIPESTATUS[0]}`

- [ ] **Step 5: Commit**

```bash
cd /d/GAGA-5-25/ai-pm && git add app/services/media_ai.py tests/test_media_playbook_inject.py && git commit -m "feat(media): write_script注入匹配到的一条打法骨架+记used_playbook_ids+返回playbook字段"
```

---

### Task 3: 路由透传 `playbook_id`

**Files:** Modify `app/api/media.py`（`content_ai_script`，约 :1341）；Test `tests/test_media_ai_script_route.py`（新）

**Interfaces:**
- Consumes: `write_script(..., playbook_id=...)`（Task 2）。
- Produces: `POST /media/content/{cid}/ai-script` 接受可选表单 `playbook_id`（默认 `""`），透传给 `write_script`。

- [ ] **Step 1: 失败测试**

```python
# tests/test_media_ai_script_route.py
"""ai-script 路由透传 playbook_id。"""
import asyncio
import base64
import json
import pytest
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
    tmp = tmp_path_factory.mktemp("aiscript_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_route_passes_playbook_id(monkeypatch):
    captured = {}
    async def fake_write(db, cid, mode="full", model="auto", hint="", playbook_id=""):
        captured["playbook_id"] = playbook_id
        captured["cid"] = cid
        return {"ok": True, "script": "s", "playbook": None}
    monkeypatch.setattr(media_api, "write_script", fake_write)
    r = _client().post("/media/content/XX/ai-script",
                       data={"mode": "full", "playbook_id": "PB9"})
    assert r.status_code == 200
    assert captured["playbook_id"] == "PB9" and captured["cid"] == "XX"


def test_route_default_empty(monkeypatch):
    captured = {}
    async def fake_write(db, cid, mode="full", model="auto", hint="", playbook_id=""):
        captured["playbook_id"] = playbook_id
        return {"ok": True, "script": "s", "playbook": None}
    monkeypatch.setattr(media_api, "write_script", fake_write)
    r = _client().post("/media/content/XX/ai-script", data={"mode": "full"})
    assert r.status_code == 200 and captured["playbook_id"] == ""
```

- [ ] **Step 2: RED** — `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_media_ai_script_route.py -v; echo EXIT=${PIPESTATUS[0]}` → FAIL（route 不接 playbook_id → 传给 fake_write 的默认 "" 命中 test_route_passes_playbook_id 断言 "PB9" 失败）

- [ ] **Step 3: 实现** — `app/api/media.py`，`content_ai_script` 加 `playbook_id` 参并透传：

```python
@router.post("/media/content/{cid}/ai-script")
async def content_ai_script(cid: str, mode: str = Form("full"),
                            hint: str = Form(""), playbook_id: str = Form("")):
    db = await get_db()
    try:
        try:
            result = await write_script(db, cid, mode=mode, hint=hint, playbook_id=playbook_id)
        except Exception as e:
            log.exception("AI 写脚本失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)
```

- [ ] **Step 4: GREEN** — 同命令 → PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
cd /d/GAGA-5-25/ai-pm && git add app/api/media.py tests/test_media_ai_script_route.py && git commit -m "feat(media): ai-script路由透传playbook_id给write_script"
```

---

### Task 4: UI —— 展示"用了《X》"+换一条+不用打法重写

**Files:** Modify `app/templates/media_content.html`（脚本区 :180-217 + `aiScript` JS :408）；`app/api/media.py`（`content_detail` context 传打法库清单给下拉）

**Interfaces:**
- Consumes: `content_ai_script` 返回的 `d.playbook`（Task 2/3）。
- 下拉需要打法库清单：`content_detail` 传 `playbooks=[{id,name,status}...]`。

- [ ] **Step 1: `content_detail` 传打法库清单** — `app/api/media.py` 的 `content_detail`（约 :1070）。**注意：该函数在 `finally: await db.close()`（约 :1126-1127）里关了 db，而 `return _tpl(...)`（约 :1143）在 finally 之后——db 已关，不能在 return dict 里 `await list_playbooks(db)`。** 必须在 try 块内（db 还开着时）先加载到变量。

在 `latest_review = dict(drow) if drow else None`（约 :1125，紧挨 `finally:` 之前）之后加一行：

```python
        playbooks = await list_playbooks(db)
```

（`list_playbooks` 该文件已 `from app.services.media_playbook import list_playbooks, get_playbook`（:25）导入；上一轮多人设已把它改成不带 persona 参、返回全共享池 proven 在前。）

然后在 `return _tpl(...)` 的 context dict 里（`"is_reverse": ...` 那一项旁）加：

```python
                 "playbooks": playbooks,
```

- [ ] **Step 2: 脚本区加打法展示条** — `media_content.html`，在 `<div id="ai-status" class="ai-status-line"></div>`（:199）之后插入：

```html
      <div id="pb-bar" style="display:none; font-size:12.5px; margin:6px 0; padding:7px 10px; border:1px solid var(--border); border-radius:8px; background:var(--panel-2)">
        <span id="pb-text"></span>
        <span style="margin-left:8px">
          <select id="pb-pick" style="font-size:12px; padding:2px 6px">
            <option value="">换一条打法…</option>
            {% for p in playbooks %}<option value="{{ p.id }}">{{ p.name }}（{{ '已跑通' if p.status=='proven' else '验证中' }}）</option>{% endfor %}
          </select>
          <button type="button" onclick="pbSwap()" class="btn" style="font-size:12px; padding:2px 8px">用这条重写</button>
          <button type="button" onclick="aiScript('full','none')" class="btn" style="font-size:12px; padding:2px 8px">不用打法重写</button>
        </span>
      </div>
```

- [ ] **Step 3: 改 `aiScript` 接受 playbookId + 渲染打法条** — `media_content.html` 的 `aiScript` 函数（:408）改成：

```javascript
async function aiScript(mode, playbookId) {
  const box = document.getElementById('script-box');
  if (box.value.trim() && !confirm('会覆盖当前脚本，继续？')) return;
  const btns = [document.getElementById('btn-full'), document.getElementById('btn-lean')];
  const status = document.getElementById('ai-status');
  btns.forEach(b => b.disabled = true);
  status.style.display = 'block';
  status.textContent = mode === 'lean'
    ? 'AI 写作中（精简注入，仅人设身份）…'
    : 'AI 写作中（自动匹配打法+完整注入）…';
  try {
    const fd = new FormData();
    fd.append('mode', mode);
    if (playbookId) fd.append('playbook_id', playbookId);
    const r = await fetch('/media/content/{{ content.id }}/ai-script',
                          {method: 'POST', body: fd});
    const d = await r.json();
    if (d.ok) {
      box.value = d.script;
      status.textContent = '完成（' + d.model + '，注入 ' + d.injected_count
                         + ' 条资产，费用 $' + (d.cost || 0).toFixed(4) + '）。'
                         + (d.gap ? ' AI 标了缺真料：' + d.gap + '（可点上面「采访我补料」）。' : '')
                         + ' 改成你要念的话后点「保存脚本」即定稿。';
      renderPbBar(d.playbook);
    } else {
      status.textContent = '失败：' + (d.error || '未知错误');
    }
  } catch (e) {
    status.textContent = '请求失败：' + e;
  }
  btns.forEach(b => b.disabled = false);
}

function renderPbBar(pb) {
  const bar = document.getElementById('pb-bar');
  const txt = document.getElementById('pb-text');
  if (!pb) { bar.style.display = 'none'; return; }
  const tag = pb.status === 'proven' ? '已跑通' : '验证中';
  txt.textContent = '本次用了打法《' + pb.name + '》（' + tag + '）'
                  + (pb.reason ? ' · ' + pb.reason : '');
  bar.style.display = 'block';
}

function pbSwap() {
  const v = document.getElementById('pb-pick').value;
  if (!v) return;
  aiScript('full', v);
}
```

（注意 `btn-full` 的 onclick 保持 `aiScript('full')`——两参 JS 里 `playbookId` 为 undefined，`if(playbookId)` 跳过，等价原行为。escapeHtml 不需要：用 textContent 赋值。）

- [ ] **Step 4: 全套回归 + 浏览器冒烟（controller 亲跑）**

`cd /d/GAGA-5-25/ai-pm && python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient 渲染 + 真机）：内容详情页脚本区渲染无 Jinja/500；播一条 content + 2 条 playbook，模拟 `d.playbook` 非空时 `#pb-bar` 显示"用了《X》"、下拉列出打法、"不用打法重写"按钮在；真机 DeepSeek 点「AI 写脚本」看是否真匹配一条 + 稿子按骨架走 + 换/去掉即时生效。

- [ ] **Step 5: Commit**

```bash
cd /d/GAGA-5-25/ai-pm && git add app/api/media.py app/templates/media_content.html && git commit -m "feat(media): UI-写稿显示用了哪条打法+换一条下拉+不用打法重写"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 匹配→T1；§4 注入(参数/位置/记录/返回)→T2；§5 节奏(路由透传)→T3；§5 UI(展示/换/去掉)→T4；§6 测准(不用打法重写=A/B)→T4 按钮；§2 纪律(只注一条)→T2 断言"数据打脸法 not in prompt"；§7 零迁移→全程无 ALTER。
- **类型一致：** `match_playbook`(db,content,model)→dict{playbook|None}(T1) 被 T2 write_script 消费；write_script 返回加 `playbook` 字段(T2)→T3 fake 保留→T4 `d.playbook` 消费；`playbook_id` 三态("" / "none" / "<id>")T2 定义↔T3 透传↔T4 传参一致；`list_playbooks(db)` 单参(上一轮多人设改的)→T4 context。
- **无占位：** 每 step 完整代码/命令/期望。T1 测试用 `make_db_sync()` 辅助避免重复 async 样板。
