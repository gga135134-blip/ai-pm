# 媒体批量后台跑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（inline）。Steps use checkbox (`- [ ]`).

**Goal:** 批量整理/挖矿改成服务器后台跑（离页不断）+ 显眼的全局进度条。

**Architecture:** 复用 auto_runner 模式（模块级内存 _jobs + asyncio.create_task + Lock）。per-content 核心从现有路由抽到 media_batch 供路由与后台跑器共用。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。

## Global Constraints

- 每人设**一个活跃任务**；已跑拒绝新起。逐条循环、每条一次 AI 调用（守纪律）、做一条存一条。
- 内存任务：离页/切页不影响（同进程）；服务器重启中断（接受，已入库的保留）。
- **零 schema 变更**（落库表都已存在）。
- DRY：run_organize_one/run_mine_one 抽到 media_batch，单条路由(/organize、/mine-to-queue)与后台跑器都调它，行为不变（回归绿）。
- 测试：run_*_one 用 make_db(内存·传 db 进函数) + monkeypatch AI；后台跑器/端点用 tmp-DB_PATH 模块 fixture(因 _run_batch 内部 get_db)。改模板 Edit/Write，JS 不塞 SVG。
- 跑 pytest：`cd /d/GAGA-5-25/ai-pm && python -m pytest ... ; echo EXIT=${PIPESTATUS[0]}`（cwd 每次重置，先 cd）。假挂 `taskkill //F //IM python.exe`。

---

### Task 1: media_batch 核心（run_organize_one / run_mine_one）+ 路由重构

**Files:** Create `app/services/media_batch.py`；Modify `app/api/media.py`；Test `tests/test_media_batch_core.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_batch_core.py
"""per-content 核心：整理/挖矿逐条落库。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_batch as mb


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('P','嘉','x','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script,is_winner) "
                     "VALUES ('C1','P','老文案','published','legacy_text','碎\n行\n正文',1)")
    await db.commit()
    return db


def test_run_organize_one(monkeypatch):
    async def fake_org(script, model="auto"):
        return {"ok": True, "summary": "一句摘要", "formatted": "整理后", "cost": 0, "model": "x"}
    monkeypatch.setattr(mb, "organize_content", fake_org)

    async def go():
        db = await _seed()
        r = await mb.run_organize_one(db, "C1")
        assert r["ok"] and r["summary"] == "一句摘要"
        cur = await db.execute("SELECT summary,script FROM media_content WHERE id='C1'")
        row = dict(await cur.fetchone())
        assert row["summary"] == "一句摘要" and row["script"] == "整理后"
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action WHERE action_type='organize_format'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())


def test_run_mine_one_signature(monkeypatch):
    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": [{"content": "你要知道"}]}
    monkeypatch.setattr(mb, "mine_from_transcript", fake_mine)

    async def go():
        db = await _seed()
        r = await mb.run_mine_one(db, "C1", "signature")
        assert r["ok"] and r["added"] == 1
        cur = await db.execute("SELECT mined_signature_at FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["mined_signature_at"] is not None
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_batch_core.py -v` → FAIL

- [ ] **Step 3: 实现** — 新建 `app/services/media_batch.py`（先只放核心，后台跑器 Task 2 加）：
```python
# app/services/media_batch.py
"""媒体批量后台跑：per-content 核心（路由与后台跑器共用）+ 后台跑器（Task 2）。"""
from app.services.media_ai import organize_content, mine_from_transcript, mine_structure
from app.services.media_mine_queue import enqueue_candidates
from app.services.media_assistant import log_action


async def run_organize_one(db, cid) -> dict:
    """整理一条：摘要另存 + 格式改写(留痕可撤)。传入 db，由调用方管连接。"""
    cur = await db.execute("SELECT persona_id,script FROM media_content WHERE id=?", (cid,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在"}
    pid, script = row["persona_id"], row["script"] or ""
    if not script.strip():
        return {"ok": False, "error": "无正文"}
    res = await organize_content(script)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "整理失败")}
    formatted = res.get("formatted") or script
    await log_action(db, pid, "organize_format", "media_content", cid,
                     before={"script": script}, after={"script": formatted})
    await db.execute("UPDATE media_content SET summary=?, script=? WHERE id=?",
                     (res.get("summary", ""), formatted, cid))
    await db.commit()
    return {"ok": True, "summary": res.get("summary", "")}


async def run_mine_one(db, cid, kind, force=0) -> dict:
    """挖一条：kind=signature 从任意内容挖口头禅；essence 仅爆款挖素材+打法。"""
    cur = await db.execute(
        "SELECT persona_id,script,is_winner,mined_signature_at,mined_essence_at "
        "FROM media_content WHERE id=?", (cid,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在"}
    pid, script = row["persona_id"], row["script"] or ""
    if kind == "signature":
        if row["mined_signature_at"] and not force:
            return {"ok": True, "added": 0, "skipped": "already"}
        res = await mine_from_transcript(db, pid, script)
        added = await enqueue_candidates(db, pid, cid, "signature", res.get("signatures") or [])
        await db.execute("UPDATE media_content SET mined_signature_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
        await db.commit()
        return {"ok": True, "added": added, "skipped": ""}
    elif kind == "essence":
        if not row["is_winner"]:
            return {"ok": True, "added": 0, "skipped": "not_winner"}
        if row["mined_essence_at"] and not force:
            return {"ok": True, "added": 0, "skipped": "already"}
        res = await mine_from_transcript(db, pid, script)
        added = await enqueue_candidates(db, pid, cid, "material", res.get("materials") or [])
        st = await mine_structure(db, pid, script)
        if st.get("ok") and st.get("playbook"):
            added += await enqueue_candidates(db, pid, cid, "playbook", [st["playbook"]])
        await db.execute("UPDATE media_content SET mined_essence_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
        await db.commit()
        return {"ok": True, "added": added, "skipped": ""}
    return {"ok": False, "error": "kind 非法"}
```
`app/api/media.py`：import 加 `from app.services.media_batch import run_organize_one, run_mine_one`。把 `content_organize` 整个函数体换成调用：
```python
@router.post("/media/content/{cid}/organize")
async def content_organize(cid: str):
    """老文案整理：单条端点，逻辑在 media_batch.run_organize_one。"""
    db = await get_db()
    try:
        try:
            res = await run_organize_one(db, cid)
        except Exception as e:
            log.exception("整理失败")
            res = {"ok": False, "error": str(e)}
    finally:
        await db.close()
    return JSONResponse(res)
```
把 `content_mine_to_queue` 整个函数体换成：
```python
@router.post("/media/content/{cid}/mine-to-queue")
async def content_mine_to_queue(cid: str, kind: str = Form(...), force: int = Form(0)):
    """批量挖矿单条端点，逻辑在 media_batch.run_mine_one。"""
    db = await get_db()
    try:
        try:
            res = await run_mine_one(db, cid, kind, force)
        except Exception as e:
            log.exception("批量挖矿失败")
            res = {"ok": False, "error": str(e)}
    finally:
        await db.close()
    return JSONResponse(res)
```

- [ ] **Step 4: GREEN** — `python -m pytest tests/test_media_batch_core.py tests/test_media_organize_route.py tests/test_media_mine_to_queue.py -q` → 全 PASS（核心 2 + 两个路由回归绿）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_batch.py app/api/media.py tests/test_media_batch_core.py && git commit -m "feat(media): media_batch抽出run_organize_one/run_mine_one,单条路由重构调用(DRY)"
```

---

### Task 2: 后台跑器（_jobs / start_batch / get_status / _run_batch）

**Files:** Modify `app/services/media_batch.py`；Test `tests/test_media_batch_runner.py`（新）

- [ ] **Step 1: 失败测试**（tmp-DB_PATH fixture，因 _run_batch 内部 get_db）
```python
# tests/test_media_batch_runner.py
"""后台跑器：起任务/已跑拒绝/进度/完成。"""
import asyncio, pytest
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_batch as mb


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("batch_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_content WHERE persona_id='BP'")
        await db.execute("DELETE FROM media_persona WHERE id='BP'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('BP','嘉','x','涨粉','active')")
        for cid in ("B1", "B2"):
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                             "VALUES (?, 'BP',?, 'published','legacy_text','正文')", (cid, cid))
        await db.commit(); await db.close()
    asyncio.run(go())


def test_start_guard_and_progress(monkeypatch):
    _seed()
    async def fake_org(script, model="auto"):
        return {"ok": True, "summary": "s", "formatted": "f", "cost": 0, "model": "x"}
    monkeypatch.setattr(mb, "organize_content", fake_org)

    async def go():
        assert mb.get_status("BP") is None or mb.get_status("BP").get("running") is not True
        started = mb.start_batch("BP", "organize", ["B1", "B2"])
        assert started is True
        # 立刻再起：应被拒（同人设已有任务在跑）
        assert mb.start_batch("BP", "organize", ["B1"]) is False
        # 等它跑完
        for _ in range(60):
            st = mb.get_status("BP")
            if st and not st["running"]:
                break
            await asyncio.sleep(0.05)
        st = mb.get_status("BP")
        assert st["done"] == 2 and st["running"] is False and st["op"] == "organize"
    asyncio.run(go())

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT summary FROM media_content WHERE id='B1'")
        assert (await cur.fetchone())["summary"] == "s"
        await db.close()
    asyncio.run(chk())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_batch_runner.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_batch.py` 顶部 import 加：
```python
import asyncio
import threading
from app.database import get_db
```
文件末尾加：
```python
_jobs = {}
_lock = threading.Lock()
_OP_LABEL = {"organize": "整理", "mine_signature": "挖记忆点", "mine_essence": "挖精华"}


def get_status(persona_id):
    with _lock:
        j = _jobs.get(persona_id)
        return dict(j) if j else None


def start_batch(persona_id, op, content_ids) -> bool:
    if op not in ("organize", "mine_signature", "mine_essence"):
        return False
    ids = [str(c) for c in (content_ids or [])]
    if not ids:
        return False
    with _lock:
        j = _jobs.get(persona_id)
        if j and j.get("running"):
            return False
        _jobs[persona_id] = {"op": op, "op_label": _OP_LABEL.get(op, op),
                             "done": 0, "total": len(ids), "running": True, "ok_count": 0}
    asyncio.create_task(_run_batch(persona_id, op, ids))
    return True


async def _run_batch(persona_id, op, content_ids):
    try:
        for cid in content_ids:
            db = await get_db()
            try:
                if op == "organize":
                    r = await run_organize_one(db, cid)
                elif op == "mine_signature":
                    r = await run_mine_one(db, cid, "signature")
                elif op == "mine_essence":
                    r = await run_mine_one(db, cid, "essence")
                else:
                    r = {"ok": False}
            except Exception:
                r = {"ok": False}
            finally:
                await db.close()
            with _lock:
                j = _jobs.get(persona_id)
                if j:
                    j["done"] += 1
                    if r.get("ok"):
                        j["ok_count"] += 1
    finally:
        with _lock:
            j = _jobs.get(persona_id)
            if j:
                j["running"] = False
```

- [ ] **Step 4: GREEN** → PASS（1 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_batch.py tests/test_media_batch_runner.py && git commit -m "feat(media): 后台批量跑器(每人设一任务/已跑拒绝/内存进度/asyncio后台)"
```

---

### Task 3: 起/查端点

**Files:** Modify `app/api/media.py`；Test `tests/test_media_batch_routes.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_batch_routes.py
"""批量后台端点：起 + 查进度。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.services.media_batch as mb


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("br_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='RP'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('RP','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                         "VALUES ('RC','RP','t','published','legacy_text','正文')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_batch_start_and_status(monkeypatch):
    _seed()
    # 让 start_batch 不真起后台任务，只验端点连通 + 状态透传
    monkeypatch.setattr(mb, "start_batch", lambda pid, op, ids: True)
    monkeypatch.setattr("app.api.media.start_batch", lambda pid, op, ids: True)
    r = _client().post("/media/legacy/batch", data={"op": "organize", "content_ids": ["RC"]})
    assert r.status_code == 200 and r.json()["started"] is True

    monkeypatch.setattr("app.api.media.batch_get_status",
                        lambda pid: {"running": True, "op_label": "整理", "done": 1, "total": 3})
    r = _client().get("/media/legacy/batch-status")
    d = r.json()
    assert d["running"] is True and d["done"] == 1 and d["total"] == 3 and d["op"] == "整理"


def test_batch_empty_ids():
    _seed()
    r = _client().post("/media/legacy/batch", data={"op": "organize"})
    assert r.json()["ok"] is False
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_batch_routes.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py`：import 加
```python
from app.services.media_batch import start_batch, get_status as batch_get_status
```
加两路由（在 content_organize 附近）：
```python
@router.post("/media/legacy/batch")
async def legacy_batch(request: Request, op: str = Form(...),
                       content_ids: list[str] = Form([]), force: int = Form(0)):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
    finally:
        await db.close()
    if not pid:
        return JSONResponse({"ok": False, "error": "请先选人设"})
    if not content_ids:
        return JSONResponse({"ok": False, "error": "先勾选内容"})
    started = start_batch(pid, op, content_ids)
    return JSONResponse({"ok": True, "started": started,
                         "error": "" if started else "已有任务在跑，等它跑完"})


@router.get("/media/legacy/batch-status")
async def legacy_batch_status(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        st = batch_get_status(pid) if pid else None
    finally:
        await db.close()
    if not st:
        return JSONResponse({"running": False})
    return JSONResponse({"running": st.get("running", False), "op": st.get("op_label", ""),
                         "done": st.get("done", 0), "total": st.get("total", 0)})
```

- [ ] **Step 4: GREEN** → PASS（2 passed）；全套回归 `python -m pytest -q`

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_batch_routes.py && git commit -m "feat(media): 批量后台起/查端点(/media/legacy/batch + batch-status)"
```

---

### Task 4: 前端（老文案页起后台任务+轮询 + 全局显眼进度条）

**Files:** Modify `app/templates/media_legacy.html`、`app/templates/_media_shell.html`

- [ ] **Step 1: 老文案页按钮改起后台 + 轮询** — `media_legacy.html`：把 `batchMine`/`batchOrganize` 三个按钮的 onclick 改为起后台任务。替换现有三按钮的 JS（batchMine/batchOrganize 两函数）为：
```javascript
async function startBatch(op){
  const ids=[...document.querySelectorAll('input[name=content_ids]:checked')].map(c=>c.value);
  const prog=document.getElementById('mine-progress');
  if(!ids.length){ prog.textContent='先勾选老文案'; return; }
  const fd=new FormData(); fd.append('op',op); ids.forEach(id=>fd.append('content_ids',id));
  const r=await fetch('/media/legacy/batch',{method:'POST',body:fd});
  const d=await r.json();
  if(!d.ok){ prog.textContent='失败：'+(d.error||''); return; }
  if(!d.started){ prog.textContent='已有任务在跑，等它跑完再起'; return; }
  prog.textContent='已在后台开始，你可以切去别的页面，回来还能看进度';
  pollBatch();
}
let _batchTimer=null;
async function pollBatch(){
  const prog=document.getElementById('mine-progress');
  try{
    const d=await (await fetch('/media/legacy/batch-status')).json();
    if(d.running){ prog.textContent=d.op+'中… '+d.done+'/'+d.total+'（后台跑，可离开）';
      _batchTimer=setTimeout(pollBatch,2000); }
    else if(d.total){ prog.innerHTML='完成 '+d.done+'/'+d.total+' → 刷新看结果'; }
  }catch(e){}
}
document.addEventListener('DOMContentLoaded', pollBatch);  // 回到本页接着看进度
```
把三个按钮 onclick 改为：`onclick="startBatch('mine_signature')"` / `onclick="startBatch('mine_essence')"` / `onclick="startBatch('organize')"`（分别对应挖记忆点/挖精华/整理）。删掉旧的 batchMine/batchOrganize 函数（被 startBatch 取代）。

- [ ] **Step 2: 全局显眼进度条** — `_media_shell.html`：在 `{% macro media_shell(...) %}` 内、`<div class="pbar">` 之前插入一条醒目横条（默认隐藏）：
```html
<div id="ms-batch" style="display:none; margin:0 0 12px; padding:10px 14px; border-radius:10px;
     background:var(--ai-soft); border:1px solid var(--ai); color:var(--ai); font-size:13.5px; font-weight:600">
  🔄 <span id="ms-batch-txt">AI 处理中…</span>
</div>
```
在外壳已有的 `<script>` 里（fetch /media/ui/steps 那段附近）加一段轮询：
```javascript
(function(){
  var box=document.getElementById('ms-batch'), txt=document.getElementById('ms-batch-txt');
  if(!box) return;
  async function poll(){
    try{
      var d=await (await fetch('/media/legacy/batch-status')).json();
      if(d.running){ box.style.display='block'; txt.textContent='AI '+d.op+'中 '+d.done+'/'+d.total+'（后台跑，切页不断）'; setTimeout(poll,2000); }
      else{ box.style.display='none'; setTimeout(poll,5000); }
    }catch(e){ setTimeout(poll,5000); }
  }
  poll();
})();
```
（JS 不塞 SVG；用 🔄 emoji。）

- [ ] **Step 3: 全套回归 + 浏览器冒烟（controller 亲跑）** — `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient + 真机 DeepSeek）：老文案页勾选→点批量整理→返回"已在后台开始"；GET batch-status 返 running/进度；切到别的媒体页（如 /media/board）看顶部 `ms-batch` 横条显示"AI 整理中 X/Y"；跑完横条消失、老文案页刷新看摘要。无 Jinja/500/console。

- [ ] **Step 4: Commit**
```bash
git add app/templates/media_legacy.html app/templates/_media_shell.html && git commit -m "feat(media): 老文案批量改后台起+轮询,全局显眼进度条(任何媒体页可见·离页不断)"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3.1 跑器→T2；per-content 核心→T1；§3.2 端点→T3；单条路由重构→T1；§3.3 前端(按钮起后台+轮询)→T4；全局指示灯→T4。
- **类型一致：** `run_organize_one(db,cid)→{ok,summary/error}`、`run_mine_one(db,cid,kind,force)→{ok,added,skipped/error}`(T1)→T2 _run_batch 调、T1 路由调；`start_batch(pid,op,ids)→bool`、`get_status(pid)→dict|None`(T2)→T3 端点(import 别名 batch_get_status)；op 取值 organize/mine_signature/mine_essence 全程一致；batch-status 返 {running,op(=op_label),done,total}→T4 前端消费 d.op/d.done/d.total。
- **纪律：** _run_batch 逐条（每条一次 AI 调用）；内存任务离页不断、重启中断已在 spec 声明。
- **无占位：** 每 step 完整代码。T4 按钮 onclick 三个 op 映射已列明（signature/essence/organize）。
