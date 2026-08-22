# 复盘页助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（inline）。Steps use checkbox (`- [ ]`)。

**Goal:** 助手加复盘能力（读复盘/跑 L2·L3 走待确认卡）+ 对话框抽共享片段嵌进复盘页。

**Architecture:** 复盘读工具进 _READ、跑工具进 _CORE(stage)、apply_action 加 run_l2/run_l3 分支；对话框抽 `_media_assistant_box.html` 自包含片段（JS 载 history+pending），助手页与复盘页都 include。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。

## Global Constraints

- 读工具无需确认、回 scoped 摘要；跑工具只 stage(pending action_type=run_l2/run_l3)，确认后 apply 里同步跑 run_l2_cycle/run_l3_review(force=True)、reversible=0（revert 靠现有 reversible 守卫自动拒绝）。
- run_l2_cycle/run_l3_review 签名：`(db, persona_id, model="auto", force=False)`。
- 对话框片段自包含：JS init 载 `/media/assistant/history` + `/media/assistant/pending`，不依赖服务端 msgs。
- 不改 L2/L3 分析逻辑/门槛。测试：读/跑工具用 tmp-DB_PATH 模块 fixture（工具内部 get_db）；apply 用 make_db(传 db)+monkeypatch run_l2/run_l3。改模板 Edit/Write，JS 不塞 SVG。reseed 删 persona 前先删子表。
- 跑 pytest：`cd /d/GAGA-5-25/ai-pm && python -m pytest ... ; echo EXIT=${PIPESTATUS[0]}`（cwd 每次重置，先 cd）。假挂 `taskkill //F //IM python.exe`；pytest 疑似卡过 60s 多半已跑完，taskkill 后看输出。

---

### Task 1: 复盘读工具（_READ +4）

**Files:** Modify `app/services/media_agent_tools.py`；Test `tests/test_media_agent_review_read.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_agent_review_read.py
"""复盘读工具：列/读 周期+阶段复盘。"""
import asyncio, pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rr_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_review_cycle WHERE persona_id='RA'")
        await db.execute("DELETE FROM media_phase_review WHERE persona_id='RA'")
        await db.execute("DELETE FROM media_persona WHERE id='RA'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('RA','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_review_cycle (id,persona_id,level,seq,patterns,advisory) "
                         "VALUES ('CY1','RA','L2',1,'规律甲','建议甲')")
        await db.execute("INSERT INTO media_phase_review (id,persona_id,seq,phase_reco,phase_reason) "
                         "VALUES ('PR1','RA',1,'advance','该进阶段了')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_list_and_read_cycle():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("list_cycles", {}, "RA")
        assert "CY1" in out
        out = await mat.dispatch_media_tool("read_cycle", {"id": "CY1"}, "RA")
        assert "规律甲" in out
    asyncio.run(go())


def test_list_and_read_phase():
    _seed()
    async def go():
        out = await mat.dispatch_media_tool("list_phase_reviews", {}, "RA")
        assert "PR1" in out
        out = await mat.dispatch_media_tool("read_phase_review", {"id": "PR1"}, "RA")
        assert "该进阶段了" in out or "advance" in out
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_agent_review_read.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_agent_tools.py` 顶部 import 加：
```python
from app.services.media_review_cycle import list_cycles as _list_cycles, get_cycle as _get_cycle
from app.services.media_phase_review import list_phase_reviews as _list_phase, get_phase_review as _get_phase
```
加 4 个读工具：
```python
async def _tool_list_cycles(args, pid):
    db = await get_db()
    try:
        rows = await _list_cycles(db, pid)
    finally:
        await db.close()
    if not rows:
        return "（还没有周期复盘 L2）"
    return "\n".join(f"[{r['id']}] 第{r.get('seq','?')}轮 · {str(r.get('created_at',''))[:10]}" for r in rows[:30])


async def _tool_read_cycle(args, pid):
    db = await get_db()
    try:
        r = await _get_cycle(db, (args or {}).get("id", ""))
    finally:
        await db.close()
    if not r or r.get("persona_id") != pid:
        return "（找不到这轮复盘，或不属于当前人设）"
    return (f"第{r.get('seq','?')}轮周期复盘\n规律：{str(r.get('patterns') or '')[:800]}\n"
            f"建议：{str(r.get('advisory') or '')[:500]}")


async def _tool_list_phase_reviews(args, pid):
    db = await get_db()
    try:
        rows = await _list_phase(db, pid)
    finally:
        await db.close()
    if not rows:
        return "（还没有阶段复盘 L3）"
    return "\n".join(f"[{r['id']}] 第{r.get('seq','?')}轮 · {r.get('phase_reco','')} · {str(r.get('created_at',''))[:10]}" for r in rows[:30])


async def _tool_read_phase_review(args, pid):
    db = await get_db()
    try:
        r = await _get_phase(db, (args or {}).get("id", ""))
    finally:
        await db.close()
    if not r or r.get("persona_id") != pid:
        return "（找不到这轮阶段复盘，或不属于当前人设）"
    return (f"第{r.get('seq','?')}轮阶段复盘\n建议：{r.get('phase_reco','')}｜{str(r.get('phase_reason') or '')[:500]}")
```
把它们加进 `_READ` dict：
```python
    "list_cycles": _tool_list_cycles, "read_cycle": _tool_read_cycle,
    "list_phase_reviews": _tool_list_phase_reviews, "read_phase_review": _tool_read_phase_review,
```
schemas 追加：
```python
MEDIA_TOOL_SCHEMAS += [
    _schema("list_cycles", "列出周期复盘(L2)历轮。"),
    _schema("read_cycle", "读某轮周期复盘的规律/建议。", {"id": {"type": "string"}}, ["id"]),
    _schema("list_phase_reviews", "列出阶段复盘(L3)历轮。"),
    _schema("read_phase_review", "读某轮阶段复盘的阶段建议。", {"id": {"type": "string"}}, ["id"]),
]
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_agent_tools.py tests/test_media_agent_review_read.py && git commit -m "feat(media): 助手复盘读工具(列/读 周期L2+阶段L3复盘)"
```

---

### Task 2: 复盘跑工具（_CORE stage）+ apply_action run 分支

**Files:** Modify `app/services/media_agent_tools.py`、`app/services/media_assistant.py`；Test `tests/test_media_agent_review_run.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_agent_review_run.py
"""复盘跑工具：stage pending + apply 真跑。"""
import asyncio, pytest
from tests.media_helpers import make_db
from app.services import media_assistant as ma


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    await db.commit()
    return db


def test_apply_run_l2_calls_runner(monkeypatch):
    called = {}
    async def fake_l2(db, persona_id, model="auto", force=False):
        called["l2"] = (persona_id, force)
        return {"ok": True}
    import app.services.media_review_cycle as mrc
    monkeypatch.setattr(mrc, "run_l2_cycle", fake_l2)

    async def go():
        db = await _seed()
        aid = await ma.log_action(db, "A", "run_l2", "media_review_cycle", "",
                                  after={"summary": "跑周期复盘"}, status="pending")
        assert await ma.apply_action(db, aid) is True
        assert called["l2"] == ("A", True)                      # force=True
        cur = await db.execute("SELECT status,reversible FROM media_assistant_action WHERE id=?", (aid,))
        row = dict(await cur.fetchone())
        assert row["status"] == "applied" and row["reversible"] == 0
        assert await ma.revert_action(db, aid) is False         # 不可撤
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_agent_review_run.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_agent_tools.py` 加两个 stage 工具（复用已有 `_core_stage`）：
```python
async def _tool_run_cycle_review(args, pid):
    return await _core_stage(pid, "run_l2", "media_review_cycle", "",
                             {"summary": "跑一轮周期复盘 L2（会花 AI 费用）"})


async def _tool_run_phase_review(args, pid):
    return await _core_stage(pid, "run_l3", "media_phase_review", "",
                             {"summary": "跑一轮阶段复盘 L3（会花 AI 费用）"})
```
加进 `_CORE` dict：
```python
    "run_cycle_review": _tool_run_cycle_review, "run_phase_review": _tool_run_phase_review,
```
schemas 追加（在 _CORE schemas 那批里）：
```python
    _schema("run_cycle_review", "跑一轮周期复盘 L2（需人确认，会花 AI 费用）。"),
    _schema("run_phase_review", "跑一轮阶段复盘 L3（需人确认，会花 AI 费用）。"),
```
`app/services/media_assistant.py` 的 `apply_action`，在最后 `else: return False` 之前加两分支：
```python
    elif at == "run_l2":
        from app.services.media_review_cycle import run_l2_cycle
        await run_l2_cycle(db, pid, force=True)
        reversible = 0
    elif at == "run_l3":
        from app.services.media_phase_review import run_l3_review
        await run_l3_review(db, pid, force=True)
        reversible = 0
```

- [ ] **Step 4: GREEN** → PASS（1 passed）；回归 `python -m pytest tests/test_media_assistant_apply.py tests/test_media_agent_core_tools.py -q`

- [ ] **Step 5: Commit**
```bash
git add app/services/media_agent_tools.py app/services/media_assistant.py tests/test_media_agent_review_run.py && git commit -m "feat(media): 助手复盘跑工具(L2/L3 stage待确认)+apply_action run_l2/run_l3分支(force不可撤)"
```

---

### Task 3: 对话历史端点

**Files:** Modify `app/api/media.py`；Test `tests/test_media_assistant_history.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_assistant_history.py
"""助手对话历史端点。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hist_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_history():
    async def seed():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='HP'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('HP','嘉','x','涨粉','active')")
        import uuid
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content) "
                         "VALUES (?, 'HP','user','你好')", (str(uuid.uuid4()),))
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content) "
                         "VALUES (?, 'HP','assistant','在的')", (str(uuid.uuid4()),))
        await db.commit(); await db.close()
    asyncio.run(seed())
    r = _client().get("/media/assistant/history")
    d = r.json()
    assert [m["role"] for m in d["messages"]] == ["user", "assistant"]
    assert d["messages"][1]["content"] == "在的"
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_assistant_history.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py` 在 `assistant_pending` 路由附近加：
```python
@router.get("/media/assistant/history")
async def assistant_history(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        msgs = []
        if pid:
            cur = await db.execute(
                "SELECT role,content FROM media_assistant_message WHERE persona_id=? "
                "ORDER BY created_at LIMIT 40", (pid,))
            msgs = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return JSONResponse({"messages": msgs})
```

- [ ] **Step 4: GREEN** → PASS（1 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_assistant_history.py && git commit -m "feat(media): 助手对话历史端点/media/assistant/history(嵌入框自包含用)"
```

---

### Task 4: 对话框抽共享片段 + 助手页/复盘页两处嵌

**Files:** Create `app/templates/_media_assistant_box.html`；Modify `app/templates/media_assistant.html`、`app/templates/media_review_home.html`

- [ ] **Step 1: 建共享片段 `_media_assistant_box.html`**（自包含：JS init 载 history+pending）
```html
<div id="chat" style="border:1px solid var(--border); border-radius:10px; padding:12px; min-height:280px; max-height:55vh; overflow-y:auto; font-size:13.5px; line-height:1.6">
  <div style="color:var(--ink-3)">加载中…</div>
</div>
<div id="ast-pending" style="margin:10px 0"></div>
<div style="display:flex; gap:8px; margin-top:10px">
  <input id="ast-input" placeholder="跟助手说…（回车发送）" style="flex:1; padding:9px 12px" onkeydown="if(event.key==='Enter')astSend()">
  <button onclick="astSend()" id="ast-btn" class="btn primary">发送</button>
</div>
<div id="ast-status" style="font-size:12px; color:var(--ai); margin-top:6px; min-height:16px"></div>
<script>
const chat=document.getElementById('chat');
function bubble(who,text){ const d=document.createElement('div'); d.style.margin='8px 0';
  d.innerHTML='<b style="color:'+(who==='助手'?'var(--ai)':'var(--ink-1)')+'">'+who+'：</b>'+(text||'').replace(/</g,'&lt;'); chat.appendChild(d); chat.scrollTop=chat.scrollHeight; }
async function initHistory(){
  try{
    const d=await (await fetch('/media/assistant/history')).json();
    chat.innerHTML='';
    if(!d.messages || !d.messages.length){ chat.innerHTML='<div style="color:var(--ink-3)">还没聊过。试试："列一下最近的周期复盘" 或 "针对某条写下一条"。</div>'; return; }
    d.messages.forEach(function(m){ bubble(m.role==='assistant'?'助手':'我', m.content); });
  }catch(e){ chat.innerHTML=''; }
}
async function astSend(){
  const inp=document.getElementById('ast-input'); const msg=inp.value.trim(); if(!msg) return;
  const btn=document.getElementById('ast-btn'), st=document.getElementById('ast-status');
  if(chat.querySelector('div[style*="ink-3"]') && chat.children.length===1) chat.innerHTML='';
  bubble('我',msg); inp.value=''; btn.disabled=true; st.textContent='助手在想（可能会调工具）…';
  try{
    const fd=new FormData(); fd.append('message',msg);
    const r=await fetch('/media/assistant/ask',{method:'POST',body:fd});
    const txt=await r.text(); let d; try{ d=JSON.parse(txt); }catch(_){ st.textContent='没返回内容，稍后重试'; btn.disabled=false; return; }
    if(d.ok){ bubble('助手',d.reply); st.textContent=(d.steps&&d.steps.length?('调了：'+d.steps.join('、')+' · '):'')+'花费 $'+(d.cost||0).toFixed(4); loadPending(); }
    else st.textContent='失败：'+(d.error||'');
  }catch(e){ st.textContent='请求失败：'+e; }
  btn.disabled=false;
}
async function loadPending(){
  const box=document.getElementById('ast-pending');
  try{
    const d=await (await fetch('/media/assistant/pending')).json();
    if(!d.pending || !d.pending.length){ box.innerHTML=''; return; }
    box.innerHTML='<div style="font-size:12px;color:var(--ink-3);margin-bottom:4px">待确认（点确认才执行）</div>'+
      d.pending.map(function(p){
        return '<div style="border:1px solid var(--ai);border-radius:8px;padding:10px;margin:6px 0;background:var(--ai-soft)">'+
          '<div style="font-size:13px;margin-bottom:6px">'+(p.summary||'').replace(/</g,'&lt;')+'</div>'+
          '<button class="btn primary" style="font-size:12px;padding:4px 12px" onclick="confirmAction(\''+p.id+'\',\'apply\')">确认</button> '+
          '<button class="btn" style="font-size:12px;padding:4px 12px" onclick="confirmAction(\''+p.id+'\',\'cancel\')">取消</button></div>';
      }).join('');
  }catch(e){}
}
async function confirmAction(id, act){
  await fetch('/media/assistant/action/'+id+'/'+act,{method:'POST'});
  loadPending();
}
document.addEventListener('DOMContentLoaded', function(){ initHistory(); loadPending(); });
</script>
```

- [ ] **Step 2: 助手页改 include** — `media_assistant.html`：把第 15–63 行（`<div id="chat">` 到 `</script>`）整段换成：
```html
  {% include "_media_assistant_box.html" %}
</div>
```
（保留 7–14 行的 `{% block content %}`+标题+改动记录入口；第 14 行描述改成：`让它查内容/选题/打法库/复盘，建选题·续集·脚本草稿，或标爆款/删除/采纳入库/跑复盘——核心动作会出待确认卡，点确认才执行。`）。注意原第 8 行 `<div ...max-width:820px>` 和其闭合 `</div>`（原 26 行）要保留包住 include。

- [ ] **Step 3: 复盘页嵌入** — `media_review_home.html`：在 `{{ shell.step_nav(...) }}` 之前加：
```html
<div style="max-width:820px; margin:22px auto 0">
  <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px">
    <h2 class="pname" style="margin:0; font-size:17px">🤖 复盘助手</h2>
    <span style="font-size:12px; color:var(--ink-3)">问它复盘规律，或让它跑一轮周期/阶段复盘（会出待确认卡）</span>
  </div>
  {% include "_media_assistant_box.html" %}
</div>
```

- [ ] **Step 4: 全套回归 + 浏览器冒烟（controller 亲跑）** — `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient + 真机）：助手页 include 渲染、history 载入；复盘页出现"🤖 复盘助手"框；（真机）复盘页对话"列一下周期复盘"→调 list_cycles；"跑一轮周期复盘"→出待确认卡→确认→L2 生成。无 Jinja/500/console。

- [ ] **Step 5: Commit**
```bash
git add app/templates/_media_assistant_box.html app/templates/media_assistant.html app/templates/media_review_home.html && git commit -m "feat(media): 助手对话框抽共享片段(自包含载history)+助手页改include+复盘页嵌入"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 读工具→T1；§4 跑工具+apply→T2；§5 history 端点→T3；§6 片段+两处嵌→T4。
- **类型一致：** 读工具用 `_list_cycles/_get_cycle/_list_phase/_get_phase`(import 别名)→返 dict；跑工具 `_core_stage`(Phase2 已有)落 pending(run_l2/run_l3)→apply_action 新分支 `run_l2_cycle/run_l3_review(force=True)`(签名 db,persona_id,model,force)；history 端点返 `{messages:[{role,content}]}`→片段 initHistory 消费；pending/ask/apply/cancel 端点(Phase2 已有)→片段复用。
- **纪律：** 读回摘要不倒全量；跑走待确认卡（秒回 stage，apply 同步跑不塞聊天回合）；run reversible=0 revert 靠现有守卫拒。
- **无占位：** 每 step 完整代码。T4 助手页替换用行号+锚点(`<div id="chat">`…`</script>`)精确定位，描述行同步更新。
