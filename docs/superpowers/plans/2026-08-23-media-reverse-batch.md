# 视频批量粘链接（反向入库）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 反向入库加"一次贴多条链接（最多 10）排队后台跑"，逐条串行调现有 `reverse_ingest`，精确 url 跳过已入库。

**Architecture:** 新建独立后台跑器模块 `media_reverse_batch.py`（仿 `media_batch`：内存 `_rev_jobs` + asyncio + 轮询），两个端点（起任务 / 查状态），反向 tab 加批量 UI。零迁移。

**Tech Stack:** Python 3、FastAPI、aiosqlite、pytest（纯函数 + tmp-DB_PATH 模块 fixture 异步测试 + FastAPI TestClient 路由测试）。

## Global Constraints

- 上限 **10 条**。去重 = 该人设已有同链接（`media_content.idea_reason` 精确字符串匹配）的 `video_reverse` 内容则跳过。
- 逐条**串行**跑（避免 ASR 限流/被抖音封）；**单条失败不中断**后续。
- 后台跑器仿 `media_batch`：模块级 `_rev_jobs` dict + `threading.Lock`，每人设一个活跃任务，已跑拒绝新起；进程内内存任务，离页不断、**服务器重启中断**。
- `first_url(text)` 找不到链接会**原样返回 text.strip()**（不是空串）——parse_urls 必须只保留 `http` 开头的结果。
- 测试：函数用 `get_db()` 则测试用 tmp-DB_PATH 模块 fixture（不用 make_db）；路由测试用 FastAPI TestClient + 签名 session cookie。
- 零迁移（不加表/列）。全程 TDD，`python -m pytest`。

---

### Task 1: 后台跑器模块 `media_reverse_batch.py`

**Files:**
- Create: `app/services/media_reverse_batch.py`
- Test: `tests/test_media_reverse_batch.py`

**Interfaces:**
- Produces（Task 2 依赖）：
  - `parse_urls(text: str, cap: int = 10) -> list[str]`
  - `start_reverse_batch(persona_id, urls, cfg, public_base, audio_dir, cookies_path=None) -> bool`
  - `get_reverse_status(persona_id) -> dict`（`{running, op:'reverse', done, total, results:[{url,ok,title,error}]}`）
  - `_run_reverse_batch(...)`（内部，逐条调 `reverse_ingest`）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_reverse_batch.py`：

```python
"""视频批量反向入库：parse_urls 纯函数 + 后台跑器。"""
import asyncio
from pathlib import Path

import pytest

from app.database import init_db
import app.database as _db_mod
import app.services.media_reverse_batch as mrb


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rev_batch_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_parse_urls_extracts_dedups_drops_nonurl():
    text = ("https://v.douyin.com/a/\n"
            "看这个 https://v.douyin.com/b/ 不错\n"
            "\n"
            "https://v.douyin.com/a/\n"          # 重复
            "没有链接的一行")                      # first_url 原样返回，非 http 丢弃
    assert mrb.parse_urls(text) == ["https://v.douyin.com/a/", "https://v.douyin.com/b/"]


def test_parse_urls_caps_at_10():
    text = "\n".join(f"https://v.douyin.com/{i}/" for i in range(15))
    assert len(mrb.parse_urls(text, cap=10)) == 10


def test_parse_urls_empty_or_no_url():
    assert mrb.parse_urls("") == []
    assert mrb.parse_urls("就是文字\n没有链接") == []


def test_runner_processes_all_and_continues_on_failure(monkeypatch):
    async def go():
        async def fake_ingest(db, pid, url, cfg, public_base, audio_dir, cookies_path=None):
            if url == "u2":
                raise RuntimeError("boom")     # 中间一条炸
            return {"ok": True, "content_id": "c", "title": "T-" + url, "error": ""}
        monkeypatch.setattr(mrb, "reverse_ingest", fake_ingest)
        assert mrb.start_reverse_batch("RB1", ["u1", "u2", "u3"], {}, "http://x", Path(".")) is True
        for _ in range(100):
            if not mrb.get_reverse_status("RB1")["running"]:
                break
            await asyncio.sleep(0.02)
        st = mrb.get_reverse_status("RB1")
        assert st["running"] is False and st["done"] == 3
        assert [r["ok"] for r in st["results"]] == [True, False, True]   # 失败不中断
        assert st["results"][0]["title"] == "T-u1"
    asyncio.run(go())


def test_start_rejected_when_already_running(monkeypatch):
    async def go():
        async def slow_ingest(db, pid, url, cfg, public_base, audio_dir, cookies_path=None):
            await asyncio.sleep(0.1)
            return {"ok": True, "title": "x", "error": ""}
        monkeypatch.setattr(mrb, "reverse_ingest", slow_ingest)
        assert mrb.start_reverse_batch("RB2", ["a"], {}, "http://x", Path(".")) is True
        assert mrb.start_reverse_batch("RB2", ["b"], {}, "http://x", Path(".")) is False  # 已在跑
        for _ in range(100):
            if not mrb.get_reverse_status("RB2")["running"]:
                break
            await asyncio.sleep(0.02)
    asyncio.run(go())


def test_status_empty_when_no_job():
    st = mrb.get_reverse_status("NOJOB")
    assert st["running"] is False and st["total"] == 0 and st["results"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_reverse_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.media_reverse_batch'`

- [ ] **Step 3: 写实现**

新建 `app/services/media_reverse_batch.py`：

```python
"""视频批量反向入库：解析链接 + 后台跑器（仿 media_batch）。逐条串行调 reverse_ingest。"""
import asyncio
import threading
from pathlib import Path

from app.database import get_db
from app.services.video_fetch import first_url
from app.services.media_reverse import reverse_ingest


def parse_urls(text: str, cap: int = 10) -> list:
    """按行拆，每行抠链接（支持贴整段分享文案），丢空/非链接行，保序去重，截断到 cap。
    注意：first_url 找不到链接会原样返回 text.strip()，故只保留 http 开头的结果。"""
    out, seen = [], set()
    for line in (text or "").splitlines():
        u = first_url(line).strip()
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= cap:
            break
    return out


# ─────────────── 后台跑器（每人设一个活跃任务·内存进度） ───────────────
_rev_jobs = {}
_rev_lock = threading.Lock()


def get_reverse_status(persona_id):
    with _rev_lock:
        j = _rev_jobs.get(persona_id)
        if not j:
            return {"running": False, "op": "reverse", "done": 0, "total": 0, "results": []}
        d = dict(j)
        d["results"] = list(j["results"])   # 别把在跑的 list 引用交出去
        return d


def start_reverse_batch(persona_id, urls, cfg, public_base, audio_dir, cookies_path=None) -> bool:
    us = [str(u) for u in (urls or []) if str(u).strip()]
    if not us:
        return False
    with _rev_lock:
        j = _rev_jobs.get(persona_id)
        if j and j.get("running"):
            return False
        _rev_jobs[persona_id] = {"op": "reverse", "done": 0, "total": len(us),
                                 "running": True, "results": []}
    asyncio.create_task(_run_reverse_batch(persona_id, us, cfg, public_base, audio_dir, cookies_path))
    return True


async def _run_reverse_batch(persona_id, urls, cfg, public_base, audio_dir, cookies_path):
    try:
        for url in urls:
            db = await get_db()
            try:
                r = await reverse_ingest(db, persona_id, url, cfg, public_base,
                                         audio_dir, cookies_path=cookies_path)
            except Exception as e:
                r = {"ok": False, "title": "", "error": str(e) or "入库出错"}
            finally:
                await db.close()
            with _rev_lock:
                j = _rev_jobs.get(persona_id)
                if j:
                    j["results"].append({"url": url, "ok": bool(r.get("ok")),
                                         "title": r.get("title", ""), "error": r.get("error", "")})
                    j["done"] += 1
    finally:
        with _rev_lock:
            j = _rev_jobs.get(persona_id)
            if j:
                j["running"] = False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_reverse_batch.py -v`
Expected: PASS（6 测全绿）

- [ ] **Step 5: 提交**

```bash
git add app/services/media_reverse_batch.py tests/test_media_reverse_batch.py
git commit -m "feat(media): 视频批量反向入库后台跑器(parse_urls+_rev_jobs串行)"
```

---

### Task 2: 端点 — 起批量任务 + 查状态

**Files:**
- Modify: `app/api/media.py`（import 区加一行；`@router.post("/media/reverse-ingest")` 那段附近加两个新路由）
- Test: `tests/test_media_reverse_batch_routes.py`（新建）

**Interfaces:**
- Consumes: `parse_urls`、`start_reverse_batch`、`get_reverse_status`（Task 1）；模块常量 `ASR_PUBLIC_DIR`、`BASE_DIR`、函数 `_load_config`、`_current_persona_id`（media.py 已有）。
- Produces: `POST /media/reverse/batch`（Form `urls`）→ `{ok, started, queued, skipped, running?, error?}`；`GET /media/reverse/batch-status` → status dict。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_media_reverse_batch_routes.py`：

```python
"""视频批量反向入库 路由：去重 + 校验（TestClient）。"""
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
    c.cookies.set("media_persona", "RP")     # 固定当前人设
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rev_batch_route_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())

    async def seed():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('RP','人设','一句话','涨粉','active')")
            # 已入库一条 idea_reason=urlA 的 video_reverse 内容
            await db.execute(
                "INSERT INTO media_content (id,persona_id,title,stage,idea_source,idea_reason) "
                "VALUES ('RC1','RP','旧','published','video_reverse','https://v.douyin.com/A/')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    yield
    _db_mod.DB_PATH = orig


def _creds(monkeypatch):
    monkeypatch.setattr(media_api, "_load_config", lambda: {"douyin_asr": {"api_key": "k"}})


def test_batch_dedup_and_start(monkeypatch):
    _creds(monkeypatch)
    captured = {}
    def spy_start(pid, urls, cfg, public_base, audio_dir, cookies_path=None):
        captured["pid"] = pid; captured["urls"] = list(urls); return True
    monkeypatch.setattr(media_api, "start_reverse_batch", spy_start)
    r = _client().post("/media/reverse/batch",
                       data={"urls": "https://v.douyin.com/A/\nhttps://v.douyin.com/B/"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["started"] is True and d["skipped"] == 1 and d["queued"] == 1
    assert captured["pid"] == "RP"
    assert captured["urls"] == ["https://v.douyin.com/B/"]   # A 已入库被跳过


def test_batch_all_duplicate_not_started(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setattr(media_api, "start_reverse_batch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该起任务")))
    r = _client().post("/media/reverse/batch", data={"urls": "https://v.douyin.com/A/"})
    d = r.json()
    assert d["ok"] and d["started"] is False and d["skipped"] == 1 and d["queued"] == 0


def test_batch_no_valid_url_error(monkeypatch):
    _creds(monkeypatch)
    r = _client().post("/media/reverse/batch", data={"urls": "就是文字\n没有链接"})
    assert r.json()["ok"] is False


def test_batch_no_creds_error(monkeypatch):
    monkeypatch.setattr(media_api, "_load_config", lambda: {})
    r = _client().post("/media/reverse/batch", data={"urls": "https://v.douyin.com/B/"})
    assert r.json()["ok"] is False


def test_batch_status_shape():
    d = _client().get("/media/reverse/batch-status").json()
    assert "running" in d and "results" in d
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_reverse_batch_routes.py -v`
Expected: FAIL（404 / 路由不存在）

- [ ] **Step 3: 写实现**

**3a.** `app/api/media.py` import 区（`from app.services.media_reverse import reverse_ingest` 那行附近）加：
```python
from app.services.media_reverse_batch import parse_urls, start_reverse_batch, get_reverse_status
```

**3b.** 在 `media_reverse_ingest`（`@router.post("/media/reverse-ingest")` 那段）之后加两个路由：
```python
@router.post("/media/reverse/batch")
async def media_reverse_batch(request: Request, urls: str = Form("")):
    """功能C批量：贴多条链接→精确 url 去重→后台逐条反向入库。"""
    cfg = (_load_config().get("douyin_asr") or {})
    if not cfg.get("api_key") and not (cfg.get("app_id") and cfg.get("access_key")):
        return JSONResponse({"ok": False, "error": "未配置豆包 ASR 凭证，去设置页填"})
    items = parse_urls(urls, 10)
    if not items:
        return JSONResponse({"ok": False, "error": "没有有效链接"})
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        cur = await db.execute(
            "SELECT idea_reason FROM media_content "
            "WHERE persona_id=? AND idea_source='video_reverse'", (pid,))
        seen = {row["idea_reason"] for row in await cur.fetchall()}
    finally:
        await db.close()
    queued = [u for u in items if u not in seen]
    skipped = len(items) - len(queued)
    if not queued:
        return JSONResponse({"ok": True, "started": False, "queued": 0, "skipped": skipped})
    public_base = (cfg.get("public_base") or str(request.base_url)).rstrip("/")
    cookies_file = BASE_DIR / "data" / "douyin_cookies.txt"
    cookies_path = cookies_file if cookies_file.exists() else None
    started = start_reverse_batch(pid, queued, cfg, public_base, ASR_PUBLIC_DIR, cookies_path)
    if not started:
        return JSONResponse({"ok": True, "started": False, "running": True,
                             "error": "已有批量任务在跑，等它跑完再起"})
    return JSONResponse({"ok": True, "started": True, "queued": len(queued), "skipped": skipped})


@router.get("/media/reverse/batch-status")
async def media_reverse_batch_status(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
    finally:
        await db.close()
    if not pid:
        return JSONResponse({"running": False, "op": "reverse", "done": 0, "total": 0, "results": []})
    return JSONResponse(get_reverse_status(pid))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_reverse_batch_routes.py -v`
Expected: PASS（5 测全绿）

- [ ] **Step 5: 提交**

```bash
git add app/api/media.py tests/test_media_reverse_batch_routes.py
git commit -m "feat(media): 视频批量反向入库端点(去重+起任务/查状态)"
```

---

### Task 3: 前端 — 反向 tab 批量入库 UI

**Files:**
- Modify: `app/templates/media_board.html`（`#rev-overlay` 的 `.panel` 内单条按钮之后加批量区；`<script>` 内加 JS）

**Interfaces:**
- Consumes: `POST /media/reverse/batch`、`GET /media/reverse/batch-status`（Task 2）。

- [ ] **Step 1: 加批量 UI**

在 `app/templates/media_board.html` 的 `#rev-overlay` 里，单条按钮
`<button onclick="reverseIngest()" id="rev-btn" ...>开始（可能要 1 分钟）</button>`
这一行**之后**（`</div>` 关闭 panel 之前）加：
```html
    <div style="margin-top:14px; padding-top:12px; border-top:1px solid var(--border)">
      <p style="font-size:12.5px; color:var(--ink-3); margin-bottom:8px">或一次贴多条（一行一条，最多 10 条，支持整段分享文案）：</p>
      <textarea id="rev-batch" rows="5" style="width:100%; margin-bottom:8px" placeholder="https://v.douyin.com/aaa/&#10;https://v.douyin.com/bbb/"></textarea>
      <button onclick="startReverseBatch()" id="rev-batch-btn" class="btn primary" style="width:100%; justify-content:center">批量入库（后台跑，可离开）</button>
      <div id="rev-batch-status" style="font-size:12.5px; margin-top:8px"></div>
    </div>
```

- [ ] **Step 2: 加 JS**

在 `media_board.html` 的 `<script>` 里、`reverseIngest` 函数之后加：
```javascript
async function startReverseBatch(){
  const btn=document.getElementById('rev-batch-btn'), st=document.getElementById('rev-batch-status');
  const text=document.getElementById('rev-batch').value.trim();
  if(!text){ st.textContent='先贴链接'; return; }
  btn.disabled=true;
  const fd=new FormData(); fd.append('urls', text);
  try{
    const d=await (await fetch('/media/reverse/batch',{method:'POST',body:fd})).json();
    if(!d.ok){ st.textContent='失败：'+(d.error||''); btn.disabled=false; return; }
    if(!d.started){
      st.textContent = d.skipped ? ('这些链接都已入库过（跳过 '+d.skipped+' 条）') : (d.error||'未起任务');
      btn.disabled=false; return;
    }
    st.textContent='已在后台开始：入库 '+d.queued+' 条'+(d.skipped?('（跳过 '+d.skipped+' 条已入库）'):'')+'，可离开这页';
    pollReverseBatch();
  }catch(e){ st.textContent='请求失败：'+e; btn.disabled=false; }
}
let _revTimer=null;
async function pollReverseBatch(){
  const st=document.getElementById('rev-batch-status'), btn=document.getElementById('rev-batch-btn');
  if(!st) return;
  try{
    const d=await (await fetch('/media/reverse/batch-status')).json();
    if(d.running){ st.textContent='入库中… '+d.done+'/'+d.total+'（后台跑，可离开这页）'; _revTimer=setTimeout(pollReverseBatch,3000); }
    else if(d.total){
      const rows=(d.results||[]).map(function(r){
        return r.ok ? ('✅ '+((r.title||r.url)).replace(/</g,'&lt;'))
                    : ('❌ '+((r.error||'失败')+'（'+r.url+'）').replace(/</g,'&lt;')); }).join('<br>');
      st.innerHTML='完成 '+d.done+'/'+d.total+'：<br>'+rows+'<br><a href="/media/legacy" style="color:var(--ai)">→ 去标爆款</a>';
      btn.disabled=false;
    }
  }catch(e){}
}
document.addEventListener('DOMContentLoaded', pollReverseBatch);
```

- [ ] **Step 3: 全套回归 + 冒烟移交**

Run: `python -m pytest -q`
Expected: 全套 PASS（本轮新增全绿，无回归）

浏览器冒烟由 controller 亲跑（实现者无需起服务器/开浏览器）：反向 tab 贴 2 条链接 → 批量入库 → 看进度轮询 + 逐条结果 + 去重提示；模板渲染无 Jinja/JS 报错。

- [ ] **Step 4: 提交**

```bash
git add app/templates/media_board.html
git commit -m "feat(media): 反向tab加批量粘链接入库UI(textarea+轮询进度/逐条结果)"
```

---

## Self-Review

**Spec coverage:**
- §1 后台跑器（_rev_jobs/start/status/_run 串行/失败不中断）→ Task 1 ✅
- §2 parse_urls（抠链接/去重/截10/非url丢弃）→ Task 1 ✅
- §3 端点（POST batch 去重 + GET status）→ Task 2 ✅
- §4 前端（textarea+按钮+轮询/逐条结果）→ Task 3 ✅
- §测试（parse_urls/跑器/端点去重与校验）→ Task 1、2 各测；前端冒烟 Task 3 ✅
- §不在本轮（全局条/视频指纹/并发/持久化）→ 计划未越界 ✅

**Placeholder scan:** 无 TBD/TODO；每步含真实代码与命令。✅

**Type consistency:**
- `parse_urls(text, cap=10) -> list` Task 1 定义、Task 2 用 `parse_urls(urls,10)`，一致。✅
- `start_reverse_batch(persona_id, urls, cfg, public_base, audio_dir, cookies_path=None) -> bool` Task 1 定义、Task 2 传 `(pid, queued, cfg, public_base, ASR_PUBLIC_DIR, cookies_path)`，参数序一致。✅
- `get_reverse_status(pid)` 返 `{running,op,done,total,results:[{url,ok,title,error}]}`；Task 2 status 路由透传、Task 3 JS 读 `d.running/d.done/d.total/d.results[].ok/.title/.url/.error`，字段一致。✅
- POST batch 返 `{ok,started,queued,skipped,running?,error?}`；Task 3 JS 读 `d.ok/d.started/d.skipped/d.queued/d.error`，一致。✅
- `first_url` 原样返回非链接文本 → parse_urls 用 `startswith("http")` 过滤，测试 `test_parse_urls_empty_or_no_url` 覆盖。✅
