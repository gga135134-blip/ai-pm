# 自媒体多人设总览 + 人设工作区隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 `/media` 改成人设总览页、看板搬 `/media/board`，用 cookie 记住"当前人设"替换 15 处硬编码，让每个人设独立工作区；打法库改共享全公司一池，原料库留 `scope` 共享接口。

**Architecture:** 数据层本就按 `persona_id` 隔离（零改动）。新增 `_current_persona_id(request, db)` 读 cookie 选人设，替换 `_first_persona_id` 调用。总览页聚合各人设概况。打法库查询去掉 persona 过滤=共享。原料库加 `scope` 列，读取 honor `scope='shared'`（现无 shared 行=行为不变）。一份代码所有人设共享每个功能。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。

## Global Constraints

- **AI 注意力纪律**：默认全隔离，只有打法库明确共享；原料库留接口但本轮零 shared 行（行为不变）。
- **零 AI 逻辑改动**：只改"取哪个 persona_id"这一层，AI/写稿/决策不碰。
- **迁移**：`media_material ADD COLUMN scope` 走 MIGRATIONS(idempotent ALTER，重启自动跑)；无新表。测试 DB `make_db()`/`init_db()` 自动应用。
- **改模板用 Edit/Write**（PowerShell -replace 会毁中文）。JS 不塞 SVG 进字符串。红 `var(--down)` 绿 `var(--up)`/`var(--success)`。
- **cookie 名** `media_persona`，属性 `httponly`+`samesite=lax`+`max_age=31536000`。
- 跑 pytest 假挂 `taskkill //F //IM python.exe`；看真退出码 `echo EXIT=${PIPESTATUS[0]}`。
- **单人设回归铁律**：只有一个人设时，总览/看板/子页行为必须与改造前一致（`_current_persona_id` 无 cookie 回落第一个）。

---

### Task 1: `_current_persona_id` helper + `enter` cookie 路由

**Files:**
- Modify: `app/api/media.py`（helper 加在 `_first_persona_id` 之后 :74；enter 路由加在 persona_create 之后 :107）
- Test: `tests/test_media_persona_switch.py`（新）

**Interfaces:**
- Produces: `async _current_persona_id(request, db) -> str | None`（读 cookie `media_persona`→校验人设存在→回落第一个 active→None）；`GET /media/persona/{pid}/enter`（设 cookie + 302 到 `/media/board`）。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_persona_switch.py
"""多人设：当前人设 cookie 选择 + enter 路由。"""
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
from app.api.media import _current_persona_id


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("psw_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_two():
    async def go():
        db = await get_db()
        try:
            for pid, nm in (("PA", "甲设"), ("PB", "乙设")):
                await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
                await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                                 "VALUES (?,?,?, '涨粉','active')", (pid, nm, "x"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


class _Req:
    def __init__(self, cookie=None):
        self.cookies = {"media_persona": cookie} if cookie else {}


def test_current_persona_id_cookie_hit_miss_fallback():
    _seed_two()

    async def go():
        db = await get_db()
        try:
            assert await _current_persona_id(_Req("PB"), db) == "PB"      # 命中
            assert await _current_persona_id(_Req("nope"), db) in ("PA", "PB")  # 无效→回落
            assert await _current_persona_id(_Req(None), db) in ("PA", "PB")    # 无cookie→回落
        finally:
            await db.close()
    asyncio.run(go())


def test_enter_sets_cookie_and_redirects():
    _seed_two()
    r = _client().get("/media/persona/PB/enter", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/media/board"
    assert "media_persona=PB" in r.headers.get("set-cookie", "")
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_persona_switch.py -v` → FAIL（`_current_persona_id` 不存在 / enter 404）

- [ ] **Step 3: 实现** — `app/api/media.py`，在 `_first_persona_id`（:74 结束）之后加：
```python
async def _current_persona_id(request, db) -> str | None:
    """当前人设：读 cookie media_persona→校验存在→回落第一个 active。"""
    pid = request.cookies.get("media_persona")
    if pid:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        if await cur.fetchone():
            return pid
    return await _first_persona_id(db)
```
在 `persona_create`（:107 return 之后、下一个路由之前）加 enter 路由：
```python
@router.get("/media/persona/{pid}/enter")
async def persona_enter(pid: str):
    """选中人设：设 cookie 后进它的看板。无效 pid 回总览。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        ok = await cur.fetchone() is not None
    finally:
        await db.close()
    if not ok:
        return RedirectResponse("/media", status_code=302)
    resp = RedirectResponse("/media/board", status_code=302)
    resp.set_cookie("media_persona", pid, max_age=31536000,
                    httponly=True, samesite="lax")
    return resp
```

- [ ] **Step 4: GREEN** — 同命令 → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_persona_switch.py
git commit -m "feat(media): _current_persona_id(cookie选人设)+persona enter路由"
```

---

### Task 2: 人设总览页 + 路由重排（/media=总览, 看板→/media/board）

**Files:**
- Create: `app/services/media_overview.py`；`app/templates/media_overview.html`
- Modify: `app/api/media.py`（board 路由改 path + 用 _current；新增 overview 路由；persona_create 改 redirect+set cookie）
- Test: `tests/test_media_overview.py`（新）

**Interfaces:**
- Consumes: `_current_persona_id`（Task 1）。
- Produces: `async persona_overview(db) -> list[dict]`（每人设 `{id,name,one_liner,current_phase,total,published,winners,accounts}`）；`GET /media`→总览；`GET /media/board`→看板。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_overview.py
"""人设总览页 + 聚合。"""
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
from app.services.media_overview import persona_overview


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ov_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona")
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('OA','甲','定位甲','涨粉','active')")
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('OB','乙','定位乙','冷启动','active')")
            await db.execute("INSERT INTO media_account (id,persona_id,platform,account_name) "
                             "VALUES ('AC','OA','抖音','嘉姐')")
            # OA: 2 条内容，1 已发，1 已发爆款
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CA1','OA','a1','published',1)")
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CA2','OA','a2','idea',0)")
            # OB: 1 条 idea
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CB1','OB','b1','idea',0)")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_persona_overview_aggregates_per_persona():
    _seed()

    async def go():
        db = await get_db()
        try:
            rows = {r["id"]: r for r in await persona_overview(db)}
            assert rows["OA"]["total"] == 2 and rows["OA"]["published"] == 1 and rows["OA"]["winners"] == 1
            assert "抖音" in " ".join(a["platform"] for a in rows["OA"]["accounts"])
            assert rows["OB"]["total"] == 1 and rows["OB"]["published"] == 0 and rows["OB"]["winners"] == 0
            assert rows["OB"]["accounts"] == []           # 账号不串
        finally:
            await db.close()
    asyncio.run(go())


def test_media_root_is_overview_and_board_uses_cookie():
    _seed()
    c = _client()
    r = c.get("/media")
    assert r.status_code == 200 and "定位甲" in r.text and "定位乙" in r.text  # 两人设都列
    c.cookies.set("media_persona", "OB")
    r = c.get("/media/board")
    assert r.status_code == 200 and "乙" in r.text        # 看板显示 cookie 指定的人设
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_overview.py -v` → FAIL

- [ ] **Step 3a: 聚合服务** — `app/services/media_overview.py`（新）：
```python
"""人设总览聚合：每人设概况（内容数/账号）。"""


async def persona_overview(db) -> list:
    cur = await db.execute("SELECT * FROM media_persona ORDER BY created_at")
    personas = [dict(r) for r in await cur.fetchall()]
    out = []
    for p in personas:
        cur = await db.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN stage='published' THEN 1 ELSE 0 END) published, "
            "SUM(CASE WHEN is_winner=1 THEN 1 ELSE 0 END) winners "
            "FROM media_content WHERE persona_id=?", (p["id"],))
        c = await cur.fetchone()
        cur = await db.execute(
            "SELECT platform, account_name FROM media_account WHERE persona_id=?", (p["id"],))
        accounts = [dict(r) for r in await cur.fetchall()]
        out.append({"id": p["id"], "name": p["name"], "one_liner": p.get("one_liner", ""),
                    "current_phase": p.get("current_phase", ""),
                    "total": c["total"] or 0, "published": c["published"] or 0,
                    "winners": c["winners"] or 0, "accounts": accounts})
    return out
```

- [ ] **Step 3b: 路由重排** — `app/api/media.py`：
  1. import 顶部加 `from app.services.media_overview import persona_overview`。
  2. board 路由（:706 `@router.get("/media"...)`）：把装饰器 path 从 `"/media"` 改成 `"/media/board"`，函数体内 `pid = await _first_persona_id(db)` 改 `pid = await _current_persona_id(request, db)`。
  3. 在 board 之前新增总览路由：
```python
@router.get("/media/overview", response_class=HTMLResponse)
@router.get("/media", response_class=HTMLResponse)
async def media_overview_page(request: Request):
    db = await get_db()
    try:
        rows = await persona_overview(db)
    finally:
        await db.close()
    if not rows:
        return RedirectResponse("/media/persona", status_code=302)   # 零人设→引导建
    return _tpl(request, "media_overview.html", {"personas": rows})
```
  4. `persona_create`（:107）改：设 cookie + 进 board（新建即成当前人设）：
```python
    resp = RedirectResponse("/media/board", status_code=302)
    resp.set_cookie("media_persona", pid, max_age=31536000, httponly=True, samesite="lax")
    return resp
```
（替换原 `return RedirectResponse(f"/media/persona/{pid}", status_code=302)`）

- [ ] **Step 3c: 总览模板** — `app/templates/media_overview.html`（新）：
```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}自媒体 · 人设总览{% endblock %}
{% block content %}
<style>
  #np-overlay{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:50; align-items:center; justify-content:center; padding:16px; }
  #np-overlay.open{ display:flex; }
  #np-overlay .panel{ background:var(--bg); border:1px solid var(--border); border-radius:var(--r); box-shadow:var(--shadow-lg); padding:18px; width:100%; max-width:420px; }
  .pcard{ display:block; border:1px solid var(--border); border-radius:var(--r); padding:16px 18px; margin-bottom:12px; background:var(--panel); text-decoration:none; color:inherit; }
  .pcard:hover{ border-color:var(--accent); }
  .pcard .nm{ font-size:17px; font-weight:600; }
  .pcard .meta{ font-size:13px; color:var(--ink-3); margin-top:6px; display:flex; gap:14px; flex-wrap:wrap; }
</style>
<div style="max-width:720px; margin:0 auto">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px">
    <h2 style="margin:0">人设总览</h2>
    <button onclick="document.getElementById('np-overlay').classList.add('open')" class="btn primary" style="padding:6px 14px; font-size:13px">＋ 新建人设</button>
  </div>
  {% for p in personas %}
  <a class="pcard" href="/media/persona/{{ p.id }}/enter">
    <div class="nm">{{ p.name }} <span class="pill run" style="font-size:11px">{{ p.current_phase }}期</span></div>
    {% if p.one_liner %}<div style="font-size:13px; color:var(--ink-2); margin-top:4px">{{ p.one_liner }}</div>{% endif %}
    <div class="meta">
      <span>内容 {{ p.total }} · 已发 {{ p.published }} · 爆款 {{ p.winners }}</span>
      {% if p.accounts %}<span>{% for a in p.accounts %}{{ a.platform }}{% if a.account_name %} @{{ a.account_name }}{% endif %}{% if not loop.last %}、{% endif %}{% endfor %}</span>
      {% else %}<span style="color:var(--ink-3)">未绑账号</span>{% endif %}
    </div>
  </a>
  {% else %}<div class="empty">还没有人设，点右上「＋ 新建人设」开始。</div>{% endfor %}
</div>
<div id="np-overlay" onclick="if(event.target===this) this.classList.remove('open')">
  <div class="panel">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px">
      <b>新建人设</b>
      <button onclick="document.getElementById('np-overlay').classList.remove('open')" class="iconbtn">{{ ic.icon('x') }}</button>
    </div>
    <form method="post" action="/media/persona">
      <input name="name" placeholder="人设名字（如 同事-An）" required style="width:100%; padding:8px; margin-bottom:8px">
      <input name="one_liner" placeholder="一句话定位（可留空）" style="width:100%; padding:8px; margin-bottom:12px">
      <button type="submit" class="btn primary" style="width:100%">建好，进工作区</button>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 4: GREEN** — `python -m pytest tests/test_media_overview.py -v` → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_overview.py app/templates/media_overview.html app/api/media.py tests/test_media_overview.py
git commit -m "feat(media): /media改人设总览页+看板搬/media/board+新建人设即设为当前"
```

---

### Task 3: 其余 12 处 `_first_persona_id` → `_current_persona_id`

**Files:** Modify `app/api/media.py`（12 处；5 处 POST 路由补 `request: Request` 参数）
**Interfaces:** Consumes `_current_persona_id`（Task 1）。

改造清单（board=Task2、playbook_home=Task4 不在此列）。逐个把 `pid = await _first_persona_id(db)` 改成 `pid = await _current_persona_id(request, db)`：

| 函数 | 是否已有 request |
|---|---|
| persona_home | 有 |
| materials_home | 有 |
| audience_home | 有 |
| audience_draft | **无，补 `request: Request,` 作首参** |
| anchor_home | 有 |
| anchor_draft | **无，补** |
| topics_home | 有 |
| media_reverse_ingest | 有 |
| legacy_home | 有 |
| topics_ai_recommend | **无，补** |
| topics_tag | **无，补** |
| topics_rank | **无，补** |

- [ ] **Step 1: 失败测试** — `tests/test_media_persona_switch.py` 追加（验证子页认 cookie 人设）：
```python
def test_subpages_respect_current_persona():
    _seed_two()
    c = _client()
    c.cookies.set("media_persona", "PB")

    async def seed_mat():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status) "
                             "VALUES ('MB','PB','story','乙的料','乙的料正文','active')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_mat())
    r = c.get("/media/materials")
    assert r.status_code == 200 and "乙的料" in r.text   # 原料库认当前人设PB
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_persona_switch.py::test_subpages_respect_current_persona -v` → FAIL（materials_home 还在取第一个 PA，看不到 PB 的料）

- [ ] **Step 3: 实现** — 对上表每个函数：
  - 5 个"无 request"的：在函数签名首位加 `request: Request,`（如 `async def audience_draft(request: Request, answers: str = Form("")):`）。
  - 全部 12 个：函数体 `_first_persona_id(db)` → `_current_persona_id(request, db)`。
  用 grep 核对：`grep -n "_first_persona_id" app/api/media.py` 结果应只剩 `_first_persona_id` 的定义(:69)、`_current_persona_id` 内部回落调用、board(Task2已改用_current)、playbook_home(Task4改)。若 board/playbook 尚未改到，允许它们暂留。

- [ ] **Step 4: GREEN** — `python -m pytest tests/test_media_persona_switch.py -v` → PASS。再跑全套 `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿（单人设回归不破）。

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_persona_switch.py
git commit -m "feat(media): 12处子页路由改用_current_persona_id(认cookie当前人设)"
```

---

### Task 4: 打法库共享（全公司一池）

**Files:**
- Modify: `app/services/media_playbook.py`（list_playbooks 去 persona 参数）；`app/api/media.py`（playbook_home、mine 名单查询、adopt-playbook 归并查询）
- Test: `tests/test_media_playbook_shared.py`（新）

**Interfaces:** `async list_playbooks(db) -> list`（去掉 persona_id 参数，返全部 proven 在前）。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_playbook_shared.py
"""打法库共享：跨人设一池 + similar_to 跨池归并。"""
import asyncio
from tests.media_helpers import make_db
from app.services.media_playbook import list_playbooks


def test_list_playbooks_is_global():
    async def go():
        db = await make_db()
        try:
            for pid in ("SPA", "SPB"):
                await db.execute("INSERT INTO media_persona (id,name,current_phase,status) "
                                 "VALUES (?,?, '涨粉','active')", (pid, pid))
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('P1','SPA','痛点法','proven')")
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('P2','SPB','悬念法','validating')")
            await db.commit()
            rows = await list_playbooks(db)          # 不传 persona
            names = [r["name"] for r in rows]
            assert "痛点法" in names and "悬念法" in names   # 两人设的都在
            assert names[0] == "痛点法"                     # proven 在前
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_playbook_shared.py -v` → FAIL（list_playbooks 还要 persona_id 参数）

- [ ] **Step 3: 实现**
  1. `app/services/media_playbook.py`：`list_playbooks` 去掉 persona 过滤：
```python
async def list_playbooks(db) -> list:
    cur = await db.execute(
        "SELECT * FROM media_playbook "
        "ORDER BY CASE status WHEN 'proven' THEN 0 ELSE 1 END, created_at DESC")
    return [dict(r) for r in await cur.fetchall()]
```
  2. `app/api/media.py` `playbook_home`（:872）改成不依赖 persona：
```python
@router.get("/media/playbook", response_class=HTMLResponse)
async def playbook_home(request: Request):
    db = await get_db()
    try:
        pbs = await list_playbooks(db)
    finally:
        await db.close()
    return _tpl(request, "media_playbook.html", {"playbooks": pbs})
```
  3. mine 路由名单查询（:1094）去掉 persona 过滤（共享池收敛）：
```python
                cur = await db.execute(
                    "SELECT name FROM media_playbook "
                    "WHERE status IN ('validating','proven')")
```
  （删掉原 `WHERE persona_id=? AND ...` 和它的 `(row["persona_id"],)` 参数——`db.execute(...)` 单参。）
  4. adopt-playbook 归并查询（:1149）去掉 persona 过滤：
```python
            cur = await db.execute(
                "SELECT id,evidence FROM media_playbook WHERE name=?",
                (similar_to.strip(),))
```
  （原 `WHERE persona_id=? AND name=?` 两参 → 只留 name 一参。新建时 persona_id 仍记来源，不改。）

- [ ] **Step 4: GREEN** — `python -m pytest tests/test_media_playbook_shared.py tests/test_media_playbook_mine.py tests/test_media_playbook_routes.py -v` → 全 PASS。
  注意：`test_media_playbook_mine.py::test_adopt_playbook_new_then_merge` 仍应过（同人设归并现在跨池仍命中同名）。

- [ ] **Step 5: Commit**
```bash
git add app/services/media_playbook.py app/api/media.py tests/test_media_playbook_shared.py
git commit -m "feat(media): 打法库改共享全公司一池(list/mine名单/adopt归并去persona过滤,persona_id留作来源)"
```

---

### Task 5: 原料库 `scope` 共享接口（留缝不装门）

**Files:**
- Modify: `app/database.py`（MIGRATIONS 加 scope 列）；`app/api/media.py`（:333, :966 两处读取）；`app/services/media_ai.py`（:371, :807 两处读取）
- Test: `tests/test_media_material_scope.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_material_scope.py
"""原料库 scope 接口：默认隔离，shared 跨人设可见。"""
import asyncio
from tests.media_helpers import make_db


def test_scope_column_and_shared_visibility():
    async def go():
        db = await make_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_material)")
            assert "scope" in {r["name"] for r in await cur.fetchall()}   # 列存在
            for pid in ("MA", "MB"):
                await db.execute("INSERT INTO media_persona (id,name,current_phase,status) "
                                 "VALUES (?,?, '涨粉','active')", (pid, pid))
            # MA 一条私有、一条公司级
            await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status,scope) "
                             "VALUES ('m1','MA','story','私料','x','active','persona')")
            await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status,scope) "
                             "VALUES ('m2','MA','story','公司料','x','active','shared')")
            await db.commit()
            # MB 视角：私有条款 (persona_id=MB OR scope=shared) → 只看到公司料
            cur = await db.execute(
                "SELECT title FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active'",
                ("MB",))
            titles = {r["title"] for r in await cur.fetchall()}
            assert "公司料" in titles and "私料" not in titles
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_material_scope.py -v` → FAIL（无 scope 列）

- [ ] **Step 3: 实现**
  1. `app/database.py` MIGRATIONS 末尾加：
```python
    "ALTER TABLE media_material ADD COLUMN scope TEXT DEFAULT 'persona'",
```
  2. 4 处读取加共享条款（**括号包 OR，AND 在外**）：
    - `app/api/media.py:333`：`"SELECT * FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active' "`
    - `app/api/media.py:966`：`"SELECT * FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active'"`
    - `app/services/media_ai.py:371`：`"SELECT brief,title FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active' "`
    - `app/services/media_ai.py:807`：`"SELECT id,brief,title,use_count FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active'"`
    （参数不变，仍传单个 persona_id；`scope='shared'` 是字面量。）

- [ ] **Step 4: GREEN** — `python -m pytest tests/test_media_material_scope.py -v` → PASS。全套回归 `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿（现无 shared 行→行为不变）。

- [ ] **Step 5: Commit**
```bash
git add app/database.py app/api/media.py app/services/media_ai.py tests/test_media_material_scope.py
git commit -m "feat(media): 原料库加scope列+4处读取honor scope=shared(留公司级共享接口,本轮零shared行)"
```

---

### Task 6: 看板头部面包屑「← 全部人设 ｜ 当前：X」

**Files:** Modify `app/templates/media_board.html`（页头 :39 之前加面包屑）

- [ ] **Step 1: 加面包屑** — `media_board.html`，在 `<!-- ===== 页头 -->` 的 `<div style="margin-bottom:18px">`（:39）**内部最上方**加：
```html
  <a href="/media" style="font-size:12.5px; color:var(--ink-3); text-decoration:none">← 全部人设</a>
  <span style="font-size:12.5px; color:var(--ink-3)"> ｜ 当前：{{ persona.name }}</span>
```
（放在 `<div class="pname">{{ persona.name }}</div>` 之前。sub-pages 已有 base.html topbar 的「自媒体」面包屑指向 /media=总览，无需逐页改。）

- [ ] **Step 2: 冒烟 + 全套回归** — controller 亲跑：临时端口 8011、签名 cookie 登录，播 2 人设：
  - `GET /media` 列两张人设卡（各带内容数/账号）。
  - 点甲卡 `GET /media/persona/{A}/enter` → 302 /media/board 且 set-cookie。
  - `GET /media/board` 顶部有「← 全部人设 ｜ 当前：甲」。
  - 换 cookie 到乙 → /media/board 显示乙、原料库/受众/锚点只乙数据。
  - `GET /media/playbook` 两人设打法都在（共享）。
  全套 `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。

- [ ] **Step 3: Commit**
```bash
git add app/templates/media_board.html
git commit -m "feat(media): 看板头部加「←全部人设｜当前」面包屑"
```

---

## Self-Review 记录

- **Spec 覆盖：** 导航重排→T2；`_current_persona_id`+enter→T1；15处替换→board(T2)+12处(T3)+playbook_home(T4)；总览概况(定位/阶段/内容数/账号)→T2；新建人设→T2(persona_create改+弹窗)；打法库共享→T4；原料库scope接口→T5；面包屑→T6；迁移(material scope)→T5。底层统一=全程一份代码。
- **类型一致：** `_current_persona_id(request,db)`(T1)→T2/T3消费；`persona_overview(db)->list[dict{id,name,one_liner,current_phase,total,published,winners,accounts}]`(T2)↔模板字段一致；`list_playbooks(db)`去参(T4)↔T2无关(playbook_home自己调)。cookie 名 `media_persona` 全任务一致。
- **无占位：** 每 step 完整代码/命令/期望。改现有用行号+"原→改"精确定位。
- **单人设回归：** T3/T5 Step4 均跑全套；`_current_persona_id` 无 cookie 回落第一个=旧行为。
