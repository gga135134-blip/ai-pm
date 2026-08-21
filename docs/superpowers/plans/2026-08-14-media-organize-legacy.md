# 老文案批量整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（inline）。Steps use checkbox (`- [ ]`).

**Goal:** 老文案批量整理——AI 逐条给每条一句摘要（另存 summary）+ 统一格式（改写正文，走助手留痕可撤），列表显示摘要。

**Architecture:** 复用助手动作日志（media_assistant_action）做格式改写的可撤。前端逐条编排守纪律。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。

## Global Constraints

- 逐条喂 AI（前端 for 循环，每条一次调用，绝不合并）。
- 摘要另存 `media_content.summary`（additive，不撤）；格式改写覆盖 `script`，走 `log_action`(action_type='organize_format', before={script}) 可撤。
- organize_content 一次调用返 `{summary, formatted}`；格式提示词约束"只重排别改内容别扩写别删信息"。
- 迁移：summary 走 MIGRATIONS(ALTER)。测试 make_db 应用。工具/路由测试若走 get_db()，用 tmp-DB_PATH 模块 fixture（见 tests/test_media_mine_review.py），不用 make_db。改模板 Edit/Write，JS 不塞 SVG。
- 跑 pytest：`cd /d/GAGA-5-25/ai-pm && python -m pytest ... ; echo EXIT=${PIPESTATUS[0]}`（cwd 每次重置，先 cd）。假挂 `taskkill //F //IM python.exe`。

---

### Task 1: DB（summary 列）

**Files:** Modify `app/database.py`；Test `tests/test_media_organize_schema.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_organize_schema.py
"""老文案整理 schema。"""
import asyncio
from tests.media_helpers import make_db


def test_content_has_summary():
    async def go():
        db = await make_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_content)")
            cols = {r["name"] for r in await cur.fetchall()}
            assert "summary" in cols
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_organize_schema.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/database.py` MIGRATIONS 末尾加：
```python
    "ALTER TABLE media_content ADD COLUMN summary TEXT DEFAULT ''",
```

- [ ] **Step 4: GREEN** → PASS

- [ ] **Step 5: Commit**
```bash
git add app/database.py tests/test_media_organize_schema.py && git commit -m "feat(media): 老文案整理-media_content加summary列"
```

---

### Task 2: organize_content AI

**Files:** Modify `app/services/media_ai.py`；Test `tests/test_media_organize_ai.py`（新）

**Interfaces:** `async organize_content(script, model="auto") -> dict` 返 `{ok, summary, formatted, cost, model}`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_organize_ai.py
"""organize_content：一次调用出摘要+排版。"""
import asyncio
from app.services import media_ai


def test_organize_returns_summary_and_formatted(monkeypatch):
    async def fake_ai(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": '{"summary":"讲员工用AI泄密的防范","formatted":"清理后的正文"}',
                "model": "x", "tokens": 5, "cost": 0.0}
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai)

    async def go():
        r = await media_ai.organize_content("很长的原始正文……")
        assert r["ok"] and r["summary"].startswith("讲员工") and r["formatted"] == "清理后的正文"
    asyncio.run(go())


def test_organize_empty_script():
    async def go():
        r = await media_ai.organize_content("   ")
        assert r["ok"] is False
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_organize_ai.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_ai.py` 末尾加（`ask_ai`/`extract_json`/`log_injection` 已 import）：
```python
ORGANIZE_SYSTEM = """给你一条口播/文案正文，做两件事：
1) summary：一句话说这条讲了啥（≤30字，抓核心，别客套）。
2) formatted：把正文清理成统一排版——合并被切碎的行、去掉残留序号、统一分段。**只重排版面，别改内容、别扩写、别删信息、别润色措辞。**
只输出严格 JSON：{"summary":"","formatted":""}"""


async def organize_content(script, model: str = "auto") -> dict:
    """逐条整理：一次调用出一句摘要 + 统一排版的正文。不写库——由路由落库。"""
    body = (script or "").strip()
    if not body:
        return {"ok": False, "summary": "", "formatted": "", "cost": 0, "model": ""}
    result = await ask_ai(body[:8000], model=model, task_type="media_topic",
                          system_prompt=ORGANIZE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "summary": "", "formatted": "", "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object") or {}
    summary = (obj.get("summary") or "").strip()
    formatted = (obj.get("formatted") or "").strip()
    if not summary and not formatted:
        return {"ok": False, "summary": "", "formatted": "", "error": "整理失败",
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    return {"ok": True, "summary": summary, "formatted": formatted,
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```
（`organize_content` 不记 log_injection——它不持 db；成本由路由侧展示即可，与 match_playbook 一致由调用方决定。）

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_ai.py tests/test_media_organize_ai.py && git commit -m "feat(media): organize_content一次调用出摘要+统一排版"
```

---

### Task 3: /organize 路由 + revert 扩展 + legacy_home summary

**Files:** Modify `app/api/media.py`、`app/services/media_assistant.py`；Test `tests/test_media_organize_route.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_organize_route.py
"""老文案整理路由 + 撤销还原。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.api.media as media_api
from app.services import media_assistant as ma


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("org_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id='OG'")
        await db.execute("DELETE FROM media_content WHERE persona_id='OG'")
        await db.execute("DELETE FROM media_persona WHERE id='OG'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('OG','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                         "VALUES ('OC','OG','老文案A','published','legacy_text','原始碎\n行\n正文')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_organize_updates_and_logs(monkeypatch):
    _seed()
    async def fake_org(script, model="auto"):
        return {"ok": True, "summary": "一句摘要", "formatted": "整理后的正文", "cost": 0, "model": "x"}
    monkeypatch.setattr(media_api, "organize_content", fake_org)
    r = _client().post("/media/content/OC/organize")
    assert r.status_code == 200 and r.json()["summary"] == "一句摘要"

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT summary,script FROM media_content WHERE id='OC'")
        row = dict(await cur.fetchone())
        assert row["summary"] == "一句摘要" and row["script"] == "整理后的正文"
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action "
                               "WHERE action_type='organize_format' AND target_id='OC'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(chk())


def test_revert_organize_restores_script():
    async def go():
        db = await get_db()
        # 直接建一条 organize_format 动作 + 改过的 script
        await db.execute("UPDATE media_content SET script='改过的' WHERE id='OC'")
        aid = await ma.log_action(db, "OG", "organize_format", "media_content", "OC",
                                  before={"script": "原来的"}, after={"script": "改过的"})
        await db.commit()
        ok = await ma.revert_action(db, aid)
        assert ok
        cur = await db.execute("SELECT script FROM media_content WHERE id='OC'")
        assert (await cur.fetchone())["script"] == "原来的"
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_organize_route.py -v` → FAIL

- [ ] **Step 3: 实现** —
`app/services/media_assistant.py` 的 `revert_action`，在 `elif a["action_type"] == "draft_script":` 之后加分支：
```python
    elif a["action_type"] == "organize_format":
        # 整理格式 → 还原 script
        await db.execute("UPDATE media_content SET script=? WHERE id=?",
                         (before.get("script", ""), a["target_id"]))
```
`app/api/media.py`：import 加 `organize_content`（与 `mine_from_transcript` 同处）+ 确保 `from app.services.media_assistant import ... log_action`（若未 import log_action 则加）。在 `content_mine_to_queue` 附近加路由：
```python
@router.post("/media/content/{cid}/organize")
async def content_organize(cid: str):
    """老文案整理：一句摘要(另存) + 统一格式(改写script·留痕可撤)。逐条调（前端编排）。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id,script FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        pid, script = row["persona_id"], row["script"] or ""
        if not script.strip():
            return JSONResponse({"ok": False, "error": "无正文"})
        try:
            res = await organize_content(script)
        except Exception as e:
            log.exception("整理失败")
            return JSONResponse({"ok": False, "error": str(e)})
        if not res.get("ok"):
            return JSONResponse({"ok": False, "error": res.get("error", "整理失败")})
        formatted = res.get("formatted") or script
        await log_action(db, pid, "organize_format", "media_content", cid,
                         before={"script": script}, after={"script": formatted})
        await db.execute("UPDATE media_content SET summary=?, script=? WHERE id=?",
                         (res.get("summary", ""), formatted, cid))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True, "summary": res.get("summary", "")})
```
`legacy_home` 的 SELECT 加 summary（模板显示用）：
```python
                "SELECT id,title,idea_source,is_winner,mined_signature_at,mined_essence_at,summary "
                "FROM media_content "
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py app/services/media_assistant.py tests/test_media_organize_route.py && git commit -m "feat(media): /organize整理路由(摘要+格式改写留痕)+revert加organize_format还原+legacy带summary"
```

---

### Task 4: UI（老文案页批量整理+摘要显示 + 复盘页摘要 + 动作名）

**Files:** Modify `app/templates/media_legacy.html`、`app/api/media_ui.py`、`app/templates/media_review_home.html`、`app/templates/media_assistant_actions.html`

- [ ] **Step 1: 老文案页批量整理按钮 + 摘要显示** — `media_legacy.html`：
每条标题那行下面（`</span>` 关闭标题 span 之后、`{% if it.is_winner %}` 标签那块附近）加摘要显示。找到每行 `<label ...>` 里标题 `<span style="flex:1">...</span>`，在其内标题链接后加：
```html
          {% if it.summary %}<div style="font-size:11.5px; color:var(--ink-3); margin-top:2px">{{ it.summary }}</div>{% endif %}
```
在现有批量按钮区（`批量挖记忆点`那一排 `<div>` 里）加一个按钮：
```html
      <button type="button" onclick="batchOrganize()" class="btn ai" style="font-size:12.5px">批量整理（摘要+排版）</button>
```
在现有 `<script>`（batchMine 那个）里加：
```javascript
async function batchOrganize(){
  const ids=[...document.querySelectorAll('input[name=content_ids]:checked')].map(c=>c.value);
  const prog=document.getElementById('mine-progress');
  if(!ids.length){ prog.textContent='先勾选老文案'; return; }
  let done=0, ok=0;
  for(const id of ids){
    prog.textContent='整理中… '+(++done)+'/'+ids.length;
    try{
      const r=await fetch('/media/content/'+id+'/organize',{method:'POST'});
      const txt=await r.text(); let d; try{ d=JSON.parse(txt); }catch(_){ continue; }
      if(d.ok) ok++;
    }catch(e){}
  }
  prog.innerHTML='整理完成 '+ok+'/'+ids.length+' 条（摘要+排版）→ 刷新看摘要，改坏了去 <a href="/media/assistant/actions">改动记录</a> 撤销';
}
```

- [ ] **Step 2: 复盘页显示摘要** — `app/api/media_ui.py` 的 `media_review_home`，把已发内容 SELECT 加 summary：
```python
                "SELECT id,title,stage,is_winner,published_at,created_at,summary FROM media_content "
```
`app/templates/media_review_home.html`：找到已发内容列表里渲染每条标题的地方，标题下加：
```html
        {% if c.summary %}<div style="font-size:11.5px; color:var(--ink-3); margin-top:2px">{{ c.summary }}</div>{% endif %}
```
（grep 该模板找到 `c.title` 那行，`c` 是循环变量名以模板实际为准。）

- [ ] **Step 3: 改动记录动作名** — `app/templates/media_assistant_actions.html`，动作名映射字典加 `'organize_format':'整理格式'`（找到 `{'create_topic':'建选题',...}` 那个 dict，加一项）。

- [ ] **Step 4: 全套回归 + 浏览器冒烟（controller 亲跑）** — `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient + 真机）：老文案页出现「批量整理」按钮；播一条含 summary 的内容→老文案页/复盘页标题下显示摘要；（真机）勾选→批量整理→摘要出现→改动记录有「整理格式」→撤销→正文还原。无 Jinja/500/console。

- [ ] **Step 5: Commit**
```bash
git add app/templates/media_legacy.html app/api/media_ui.py app/templates/media_review_home.html app/templates/media_assistant_actions.html && git commit -m "feat(media): 老文案页批量整理按钮+摘要显示+复盘页摘要+改动记录整理格式名"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 summary 列→T1；§4 organize_content→T2；§5 /organize 路由→T3；§6 revert 扩展→T3；§7 UI(legacy 按钮+摘要/复盘摘要)→T4；动作名→T4。
- **类型一致：** `organize_content(script)→{ok,summary,formatted}`(T2)→T3 路由消费；`log_action(...,'organize_format',...,before={script})`(T3)→`revert_action` organize_format 分支还原 script(T3)；legacy_home/review_home SELECT 加 summary→T4 模板 `it.summary`/`c.summary`。
- **纪律：** 逐条 fetch（T4 for 循环）；格式提示词约束只重排不改写。
- **无占位：** 每 step 完整代码。T4 模板插入点用 grep 定位（summary 显示行、动作名 dict），已说明变量名以模板实际为准。
