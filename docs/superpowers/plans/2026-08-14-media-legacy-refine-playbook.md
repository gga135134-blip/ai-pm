# 老文案/视频炼化 + 打法库🅐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把老文案/视频统一走"反向补录 media_content → 挖精华"，挖精华加第三桶「结构/打法」→ 新建打法库。本轮只建资产，不接决策引擎/写稿。

**Architecture:** 复用功能C（反向视图+挖精华前两桶）。新增：反向补录粘文本/上传TXT入口、media_content.is_winner、批量选winner、mine_structure结构桶、media_playbook表+浏览页+采纳(AI归并)。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。AI 走 `ask_ai`。

## Global Constraints

- **零下游注入**：打法库只"装着"，本轮**不碰写稿AI/决策引擎**（🅒🅓留后）。
- **一次一条**：挖精华每次只喂一条内容给AI。
- **库收敛**：抽结构喂已有打法名，AI 判 `similar_to`（是已有打法的又一例→归并补evidence，否则新建）。
- **候选绝不自动写库**：mine_structure 只返候选，人 adopt 才入 media_playbook。
- **结构桶只在 is_winner=1 时挖**（省钱+边界）；前两桶(materials/signatures)照旧。
- **诚实**：结构只从真爆款提炼、similar_to 收敛；成本 log_injection。
- **迁移**：is_winner 走 MIGRATIONS(idempotent ALTER)；media_playbook 走 SCHEMA(零迁移)。测试 DB `make_db()` 应用两者。FK约束开着，用独立persona id。改模板用 Edit/Write。JS不塞SVG进字符串。红`var(--down)`绿`var(--up)`。
- 不动 L1/L2/L3/决策引擎/写稿/挖精华前两桶逻辑。跑pytest假挂`taskkill //F //IM python.exe`。

---

### Task 1: DB（is_winner 列 + media_playbook 表）

**Files:** Modify `app/database.py`；Test `tests/test_media_playbook_schema.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_playbook_schema.py
"""老文案炼化+打法库 schema。"""
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


def test_media_content_has_is_winner():
    assert "is_winner" in _cols("media_content")


def test_media_playbook_columns():
    cols = _cols("media_playbook")
    assert {"id", "persona_id", "name", "structure", "when_to_use",
            "evidence", "source", "status", "created_at"} <= cols
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_playbook_schema.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/database.py`：`MIGRATIONS` 末尾加
```python
    "ALTER TABLE media_content ADD COLUMN is_winner INTEGER DEFAULT 0",
```
`SCHEMA` 里（media_anchor 之后、结束 `"""` 前）加：
```sql
CREATE TABLE IF NOT EXISTS media_playbook (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    structure TEXT DEFAULT '',
    when_to_use TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'validating',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: GREEN** — 同命令 → PASS

- [ ] **Step 5: Commit**
```bash
git add app/database.py tests/test_media_playbook_schema.py
git commit -m "feat(media): 打法库-media_content加is_winner列+media_playbook表"
```

---

### Task 2: `split_legacy_scripts` 纯函数（按序号切/回落空行）

**Files:** Create `app/services/media_legacy.py`；Test `tests/test_media_legacy.py`（新）

**Interfaces:** `split_legacy_scripts(text: str) -> list[str]`；`async create_legacy_contents(db, persona_id, segments) -> int`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_legacy.py
"""老文案切分 + 入库。"""
import asyncio
from tests.media_helpers import make_db, seed_content
from app.services import media_legacy as ml


def test_split_by_serial_numbers():
    txt = "1. 第一条内容\n讲了个坑\n\n2、第二条\n另一个故事\n3) 第三条"
    segs = ml.split_legacy_scripts(txt)
    assert len(segs) == 3
    assert segs[0].startswith("第一条内容")     # 序号前缀被剥掉
    assert segs[1].startswith("第二条")


def test_split_fallback_blank_line():
    segs = ml.split_legacy_scripts("第一段没序号\n\n第二段\n\n第三段")
    assert len(segs) == 3


def test_split_empty():
    assert ml.split_legacy_scripts("") == []
    assert ml.split_legacy_scripts("   \n  ") == []


def test_create_legacy_contents():
    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="seed", stage="idea")
            n = await ml.create_legacy_contents(db, "P1", ["文案甲\n正文", "文案乙"])
            assert n == 2
            cur = await db.execute(
                "SELECT title,stage,idea_source,script,is_winner FROM media_content "
                "WHERE persona_id='P1' AND idea_source='legacy_text' ORDER BY title")
            rows = [dict(r) for r in await cur.fetchall()]
            assert len(rows) == 2
            assert rows[0]["stage"] == "published" and rows[0]["is_winner"] == 0
            assert rows[0]["title"].startswith("文案甲") and rows[0]["script"].startswith("文案甲")
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_legacy.py -v` → FAIL（模块不存在）

- [ ] **Step 3: 实现**
```python
# app/services/media_legacy.py
"""老文案批量入库：按序号切分 → 建成 media_content(已发/反向-legacy_text)。"""
import re
import uuid

_NUM_LINE = re.compile(r'^\s*\d+\s*[.、)）]\s*')


def split_legacy_scripts(text: str) -> list:
    """带序号的 TXT/文本切成多条。序号行(1. / 2、 / 3) / 4）)为界；
    无序号回落空行分隔。剥掉每段开头的序号前缀。空段忽略。"""
    raw = (text or "").replace("\r\n", "\n")
    lines = raw.split("\n")
    idxs = [i for i, ln in enumerate(lines) if _NUM_LINE.match(ln)]
    if len(idxs) >= 2:
        segs = []
        for j, start in enumerate(idxs):
            end = idxs[j + 1] if j + 1 < len(idxs) else len(lines)
            block = "\n".join(lines[start:end]).strip()
            block = _NUM_LINE.sub("", block, count=1).strip()
            if block:
                segs.append(block)
        return segs
    return [b.strip() for b in re.split(r'\n\s*\n', raw) if b.strip()]


async def create_legacy_contents(db, persona_id: str, segments: list) -> int:
    n = 0
    for seg in segments:
        seg = (seg or "").strip()
        if not seg:
            continue
        title = (seg.split("\n")[0][:40]) or "老文案"
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
            "VALUES (?,?,?, 'published','legacy_text',?)",
            (str(uuid.uuid4()), persona_id, title, seg))
        n += 1
    await db.commit()
    return n
```

- [ ] **Step 4: GREEN** — 同命令 → PASS（4 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_legacy.py tests/test_media_legacy.py
git commit -m "feat(media): split_legacy_scripts按序号切分+create_legacy_contents入库"
```

---

### Task 3: 进料后端（预览/提交路由 + is_reverse 认 legacy_text + mark-winner）

**Files:** Modify `app/api/media.py`；Test `tests/test_media_legacy_routes.py`（新）

**Interfaces:** `POST /media/reverse/paste-text/preview`（text→段落预览，不写库）；`POST /media/reverse/paste-text/commit`（segments→建行）；`POST /media/legacy/mark-winner`（content_ids,winner→改is_winner）；`content_detail` 的 `is_reverse` 认 `legacy_text`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_legacy_routes.py
"""老文案进料/winner 路由。"""
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


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("legacy_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid="LGP"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES (?,?,?, '涨粉','active')", (pid, "嘉", "x"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_preview_then_commit_creates_contents():
    _seed_persona("LGP")
    r = _client().post("/media/reverse/paste-text/preview",
                       data={"text": "1. 甲\n正文\n2. 乙"})
    assert r.status_code == 200
    segs = r.json()["segments"]
    assert len(segs) == 2
    r2 = _client().post("/media/reverse/paste-text/commit",
                        data={"persona_id": "LGP", "segments": json.dumps(segs)})
    assert r2.status_code == 200 and r2.json()["count"] == 2

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT COUNT(*) n FROM media_content "
                                   "WHERE persona_id='LGP' AND idea_source='legacy_text'")
            assert (await cur.fetchone())["n"] == 2
        finally:
            await db.close()
    asyncio.run(check())


def test_mark_winner_batch():
    _seed_persona("LGP2")

    async def seed():
        db = await get_db()
        try:
            for cid in ("W1", "W2"):
                await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source) "
                                 "VALUES (?, 'LGP2', ?, 'published','legacy_text')", (cid, cid))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    r = _client().post("/media/legacy/mark-winner",
                       data={"content_ids": ["W1", "W2"], "winner": 1})
    assert r.status_code == 200

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT is_winner FROM media_content WHERE id='W1'")
            assert (await cur.fetchone())["is_winner"] == 1
        finally:
            await db.close()
    asyncio.run(check())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_legacy_routes.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py`：顶部加 import
```python
from app.services.media_legacy import split_legacy_scripts, create_legacy_contents
```
在 reverse-ingest 路由（约 :783）附近加：
```python
@router.post("/media/reverse/paste-text/preview")
async def reverse_paste_preview(text: str = Form("")):
    """切分预览，不写库。"""
    segs = split_legacy_scripts(text)
    return JSONResponse({"ok": True, "count": len(segs), "segments": segs})


@router.post("/media/reverse/paste-text/commit")
async def reverse_paste_commit(persona_id: str = Form(...), segments: str = Form(...)):
    """确认的段落 → 建成 media_content(legacy_text)。"""
    try:
        segs = json.loads(segments)
    except Exception:
        segs = []
    if not isinstance(segs, list) or not segs:
        return JSONResponse({"ok": False, "error": "无有效段落", "count": 0})
    db = await get_db()
    try:
        n = await create_legacy_contents(db, persona_id, [str(s) for s in segs])
    finally:
        await db.close()
    return JSONResponse({"ok": True, "count": n})


@router.post("/media/legacy/mark-winner")
async def legacy_mark_winner(content_ids: list[str] = Form([]),
                             winner: int = Form(1)):
    if not content_ids:
        return JSONResponse({"ok": True, "count": 0})
    db = await get_db()
    try:
        qs = ",".join("?" for _ in content_ids)
        await db.execute(
            f"UPDATE media_content SET is_winner=? WHERE id IN ({qs})",
            [1 if winner else 0, *content_ids])
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True, "count": len(content_ids)})
```
把 `content_detail` 里（约 :993）`"is_reverse": content.get("idea_source") == "video_reverse",` 改成：
```python
                 "is_reverse": content.get("idea_source") in ("video_reverse", "legacy_text"),
```

- [ ] **Step 4: GREEN** — 同命令 → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_legacy_routes.py
git commit -m "feat(media): 老文案粘文本预览/提交入库+mark-winner批量+is_reverse认legacy_text"
```

---

### Task 4: `mine_structure` 结构桶 + mine 路由扩展 + adopt-playbook

**Files:** Modify `app/services/media_ai.py`、`app/api/media.py`；Test `tests/test_media_playbook_mine.py`（新）

**Interfaces:** `async mine_structure(db, persona_id, transcript, existing_names=None, model="auto") -> dict`（返 `{ok, playbook:{name,structure,when_to_use,evidence,similar_to}}`，不写库）；mine 路由 winner→返 `playbook_candidate`；`POST /media/content/{cid}/mine/adopt-playbook`（归并/新建）。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_playbook_mine.py
"""结构桶 mine + adopt(归并/新建)。"""
import asyncio
import base64
import json
import uuid
import pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_ai


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pbmine_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed(pid, cid, is_winner):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES (?,?,?, '涨粉','active')", (pid, "嘉", "x"))
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script,is_winner) "
                             "VALUES (?,?, 't','published','legacy_text','转写正文…',?)",
                             (cid, pid, is_winner))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_mine_winner_returns_playbook_candidate(monkeypatch):
    _seed("MP1", "MC1", 1)

    async def fake_mine(db, persona_id, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": []}

    async def fake_struct(db, persona_id, transcript, existing_names=None, model="auto"):
        return {"ok": True, "playbook": {"name": "痛点自曝法", "structure": "抛痛点→自曝→给法",
                "when_to_use": "焦虑选题", "evidence": "片段", "similar_to": ""}}
    monkeypatch.setattr("app.api.media.mine_from_transcript", fake_mine)
    monkeypatch.setattr("app.api.media.mine_structure", fake_struct)
    r = _client().post("/media/content/MC1/mine")
    assert r.status_code == 200 and r.json()["playbook_candidate"]["name"] == "痛点自曝法"


def test_mine_non_winner_no_playbook(monkeypatch):
    _seed("MP2", "MC2", 0)

    async def fake_mine(db, persona_id, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": []}
    monkeypatch.setattr("app.api.media.mine_from_transcript", fake_mine)
    # mine_structure 不该被调；若被调用 name 会出现，断言其不在
    r = _client().post("/media/content/MC2/mine")
    d = r.json()
    assert d.get("playbook_candidate") in (None,) and "playbook_candidate" not in d or d.get("playbook_candidate") is None


def test_adopt_playbook_new_then_merge():
    _seed("MP3", "MC3", 1)
    # 新建
    _client().post("/media/content/MC3/mine/adopt-playbook", data={
        "name": "痛点自曝法", "structure": "抛痛点→自曝→给法",
        "when_to_use": "焦虑选题", "evidence": "出自A", "similar_to": ""})
    # 归并（similar_to 命中）
    _client().post("/media/content/MC3/mine/adopt-playbook", data={
        "name": "痛点自曝法-又一例", "structure": "x", "when_to_use": "y",
        "evidence": "出自B", "similar_to": "痛点自曝法"})

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT evidence FROM media_playbook WHERE persona_id='MP3' AND name='痛点自曝法'")
            rows = [dict(r) for r in await cur.fetchall()]
            assert len(rows) == 1                    # 没新增第二条
            assert "出自A" in rows[0]["evidence"] and "出自B" in rows[0]["evidence"]  # evidence累积
        finally:
            await db.close()
    asyncio.run(check())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_playbook_mine.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/services/media_ai.py`：在 `mine_from_transcript` 之后加（`ask_ai`/`extract_json`/`log_injection` 本文件已 import）：
```python
MINE_STRUCTURE_SYSTEM = """从这条真爆过的内容里，提炼一个可复制的「打法/结构」。
诚实：只提炼这条真爆款里清晰可复制的结构，别空泛、别硬凑；一条只提一个主打法。
给：name(打法名,如"痛点自曝法")、structure(骨架步骤+为什么成,如"前3秒抛痛点→自曝踩坑→给3步方法→反问收尾｜靠真实踩坑建信任")、when_to_use(什么选题适用)、evidence(这条里体现该打法的关键片段)。
若给了「已有打法名清单」，判断这条是不是其中某个打法的又一例：是则 similar_to 填那个打法名(人采纳时归并)，否则 similar_to 留空(提新打法)。
只输出严格 JSON：{"name":"","structure":"","when_to_use":"","evidence":"","similar_to":""}"""


async def mine_structure(db, persona_id: str, transcript: str,
                         existing_names=None, model: str = "auto") -> dict:
    """从一条爆款转写稿提炼一个打法候选。绝不写库——返回候选，人 adopt 才入。"""
    snippet = (transcript or "").strip()[:8000]
    if not snippet:
        return {"ok": False, "playbook": None, "cost": 0, "model": ""}
    names = "、".join(existing_names or []) or "（暂无）"
    result = await ask_ai(f"已有打法名清单：{names}\n\n内容转写：\n{snippet}",
                          model=model, task_type="media_topic",
                          system_prompt=MINE_STRUCTURE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "playbook": None, "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    if not obj or not obj.get("name"):
        return {"ok": False, "playbook": None, "error": "结构提炼失败",
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    await log_injection(db, "", "mine_structure", [], result.get("tokens", 0))
    return {"ok": True, "cost": result.get("cost", 0), "model": result.get("model", ""),
            "playbook": {"name": obj.get("name", ""), "structure": obj.get("structure", ""),
                         "when_to_use": obj.get("when_to_use", ""),
                         "evidence": obj.get("evidence", ""),
                         "similar_to": obj.get("similar_to", "")}}
```
`app/api/media.py`：import 加 `mine_structure`（与 `mine_from_transcript` 同处）。改 `content_mine`（约 :997）：
```python
@router.post("/media/content/{cid}/mine")
async def content_mine(cid: str):
    """挖精华：前两桶(经历/口头禅)；is_winner 再挖结构桶。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id,script,is_winner FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        try:
            result = await mine_from_transcript(db, row["persona_id"], row["script"] or "")
            if row["is_winner"]:
                cur = await db.execute(
                    "SELECT name FROM media_playbook WHERE persona_id=? "
                    "AND status IN ('validating','proven')", (row["persona_id"],))
                names = [r["name"] for r in await cur.fetchall()]
                st = await mine_structure(db, row["persona_id"], row["script"] or "", names)
                result["playbook_candidate"] = st.get("playbook") if st.get("ok") else None
        except Exception as e:
            log.exception("挖精华失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)
```
在 `mine/adopt-material` 路由之后加 adopt-playbook：
```python
@router.post("/media/content/{cid}/mine/adopt-playbook")
async def content_mine_adopt_playbook(cid: str, name: str = Form(...),
                                      structure: str = Form(""), when_to_use: str = Form(""),
                                      evidence: str = Form(""), similar_to: str = Form("")):
    """采纳结构候选 → 打法库。similar_to 命中已有打法则追加 evidence(归并)，否则新建。"""
    if not name.strip():
        return JSONResponse({"ok": False, "error": "空打法名"})
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        pid = row["persona_id"]
        merged = False
        if similar_to.strip():
            cur = await db.execute(
                "SELECT id,evidence FROM media_playbook WHERE persona_id=? AND name=?",
                (pid, similar_to.strip()))
            ex = await cur.fetchone()
            if ex:
                new_ev = ((ex["evidence"] or "") + "\n---\n" + evidence.strip()).strip()
                await db.execute("UPDATE media_playbook SET evidence=? WHERE id=?",
                                 (new_ev, ex["id"]))
                merged = True
        if not merged:
            await db.execute(
                "INSERT INTO media_playbook "
                "(id,persona_id,name,structure,when_to_use,evidence,source,status) "
                "VALUES (?,?,?,?,?,?, 'legacy_mine','validating')",
                (str(uuid.uuid4()), pid, name.strip(), structure.strip(),
                 when_to_use.strip(), evidence.strip()))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True, "merged": merged})
```

- [ ] **Step 4: GREEN** — 同命令 → PASS（3 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_ai.py app/api/media.py tests/test_media_playbook_mine.py
git commit -m "feat(media): mine_structure结构桶+mine路由winner扩展+adopt-playbook(归并/新建)"
```

---

### Task 5: 打法库读取 + 状态路由

**Files:** Create `app/services/media_playbook.py`；Modify `app/api/media.py`；Test `tests/test_media_playbook_routes.py`（新）

**Interfaces:** `async list_playbooks(db, persona_id)`（proven 在前）；`async get_playbook(db, id)`；`POST /media/playbook/{id}/status`（validating↔proven）。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_playbook_routes.py
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


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pbr_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_status_toggle():
    async def seed():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id='PBP'")
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('PBP','嘉','x','涨粉','active')")
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('PB1','PBP','痛点法','validating')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    r = _client().post("/media/playbook/PB1/status", data={"status": "proven"},
                       follow_redirects=False)
    assert r.status_code in (302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_playbook WHERE id='PB1'")
            assert (await cur.fetchone())["status"] == "proven"
        finally:
            await db.close()
    asyncio.run(check())


def test_status_rejects_bad_value():
    async def seed():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('PB2','PBP','x','validating')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    _client().post("/media/playbook/PB2/status", data={"status": "瞎写"},
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_playbook WHERE id='PB2'")
            assert (await cur.fetchone())["status"] == "validating"   # 未改
        finally:
            await db.close()
    asyncio.run(check())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_playbook_routes.py -v` → FAIL

- [ ] **Step 3: 实现**
```python
# app/services/media_playbook.py
"""打法库读取。"""


async def list_playbooks(db, persona_id: str) -> list:
    cur = await db.execute(
        "SELECT * FROM media_playbook WHERE persona_id=? "
        "ORDER BY CASE status WHEN 'proven' THEN 0 ELSE 1 END, created_at DESC",
        (persona_id,))
    return [dict(r) for r in await cur.fetchall()]


async def get_playbook(db, playbook_id: str):
    cur = await db.execute("SELECT * FROM media_playbook WHERE id=?", (playbook_id,))
    row = await cur.fetchone()
    return dict(row) if row else None
```
`app/api/media.py`：import `from app.services.media_playbook import list_playbooks, get_playbook`；加：
```python
@router.get("/media/playbook", response_class=HTMLResponse)
async def playbook_home(request: Request):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        pbs = await list_playbooks(db, pid) if pid else []
    finally:
        await db.close()
    return _tpl(request, "media_playbook.html", {"playbooks": pbs})


@router.post("/media/playbook/{pid}/status")
async def playbook_set_status(pid: str, status: str = Form(...)):
    if status in ("validating", "proven"):
        db = await get_db()
        try:
            await db.execute("UPDATE media_playbook SET status=? WHERE id=?", (status, pid))
            await db.commit()
        finally:
            await db.close()
    return RedirectResponse("/media/playbook", status_code=302)
```
（注：`media_playbook.html` 模板 Task 7 建；本任务测试不渲染页面，只测 status 路由。若跑测时 `/media/playbook` GET 未被测到则无碍——本任务两测只打 `/status`。）

- [ ] **Step 4: GREEN** — 同命令 → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_playbook.py app/api/media.py tests/test_media_playbook_routes.py
git commit -m "feat(media): 打法库list/get服务+浏览路由+status切换路由"
```

---

### Task 6: UI-A（反向补录导入入口 + legacy 批量选 winner 页）

**Files:** Modify `app/templates/media_board.html`（反向补录入口区）；Create `app/templates/media_legacy.html`；Modify `app/api/media.py`（`/media/legacy` 页路由）

- [ ] **Step 1: `/media/legacy` 页路由**

`app/api/media.py` 加：
```python
@router.get("/media/legacy", response_class=HTMLResponse)
async def legacy_home(request: Request):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        rows = []
        if pid:
            cur = await db.execute(
                "SELECT id,title,idea_source,is_winner FROM media_content "
                "WHERE persona_id=? AND idea_source IN ('video_reverse','legacy_text') "
                "ORDER BY created_at DESC", (pid,))
            rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_legacy.html", {"items": rows})
```

- [ ] **Step 2: 导入入口（media_board.html 反向补录区加"批量导入老文案"）**

grep `media_board.html` 找到反向入库入口块（`reverse-ingest`/`视频反向`），旁边加：
```html
<button class="btn" onclick="document.getElementById('legacy-import').style.display='block'">📄 批量导入老文案</button>
<div id="legacy-import" style="display:none; margin-top:10px">
  <textarea id="legacy-text" rows="6" style="width:100%" placeholder="粘贴带序号的老文案（1. … 2. …），或每条空行分隔"></textarea>
  <div style="margin-top:6px">
    <button class="btn" onclick="legacyPreview()">预览切分</button>
    <a href="/media/legacy" style="font-size:12px; margin-left:8px">→ 去标爆款</a>
  </div>
  <div id="legacy-preview" style="font-size:13px; margin-top:8px"></div>
</div>
<script>
let _segs = [];
async function legacyPreview(){
  const t = document.getElementById('legacy-text').value;
  const b = new URLSearchParams(); b.set('text', t);
  const r = await fetch('/media/reverse/paste-text/preview', {method:'POST', body:b});
  const d = await r.json(); _segs = d.segments || [];
  const box = document.getElementById('legacy-preview');
  box.innerHTML = '切成 ' + d.count + ' 段：<br>' +
    _segs.map((s,i)=>(i+1)+'. '+s.slice(0,30).replace(/</g,'&lt;')).join('<br>') +
    '<br><button class="btn" onclick="legacyCommit()">确认入库</button>';
}
async function legacyCommit(){
  const pid = "{{ persona.id if persona else '' }}";
  const b = new URLSearchParams(); b.set('persona_id', pid); b.set('segments', JSON.stringify(_segs));
  const r = await fetch('/media/reverse/paste-text/commit', {method:'POST', body:b});
  const d = await r.json();
  document.getElementById('legacy-preview').innerHTML =
    d.ok ? ('已入库 '+d.count+' 条 → <a href="/media/legacy">去标爆款</a>') : ('失败：'+(d.error||''));
}
</script>
```
（`persona.id` 变量名对齐 media_board.html 现有 context；若无则改成从页面取或用 _first_persona。**不塞 SVG 进 JS 字符串**。）

- [ ] **Step 3: legacy 批量选 winner 页 `media_legacy.html`**
```html
{% extends "base.html" %}
{% block title %}标记爆款{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  <h2>标记爆款（老内容）</h2>
  <p style="color:var(--ink-3); font-size:13px">勾选真爆过的 → 批量标为爆款；之后挖精华会对它们多挖一桶「结构/打法」。</p>
  <form method="post" action="/media/legacy/mark-winner">
    <input type="hidden" name="winner" value="1">
    <div style="margin:10px 0">
      {% for it in items %}
      <label style="display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); font-size:13.5px">
        <input type="checkbox" name="content_ids" value="{{ it.id }}">
        <span style="flex:1"><a href="/media/content/{{ it.id }}" style="color:var(--ink-1)">{{ it.title }}</a>
          <span style="font-size:11px; color:var(--ink-3)">（{{ '视频' if it.idea_source=='video_reverse' else '文案' }}）</span></span>
        {% if it.is_winner %}<span class="tag" style="color:var(--up)">爆款</span>{% endif %}
      </label>
      {% else %}<div class="empty">还没有反向补录的老内容</div>{% endfor %}
    </div>
    <button type="submit" class="btn primary">标记选中为爆款</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4: 冒烟（TestClient 渲染检查）** — 播 2 条 legacy_text 内容，GET `/media/legacy` 返 200 且含标题；导入区在 media_board 渲染。全套回归 `python -m pytest -q`。

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py app/templates/media_board.html app/templates/media_legacy.html
git commit -m "feat(media): UI-老文案批量导入入口(预览确认)+legacy批量标爆款页"
```

---

### Task 7: UI-B（内容详情结构桶展示+采纳 + 打法库浏览页 + 导航）

**Files:** Modify `app/templates/media_content.html`（反向视图挖精华区加结构桶）；Create `app/templates/media_playbook.html`；Modify `app/templates/base.html`（导航加「打法库」）

- [ ] **Step 1: 内容详情挖精华区加结构桶**

grep `media_content.html` 找到挖精华结果渲染的 JS（`/mine` 调用 + materials/signatures 渲染处）。在渲染 materials/signatures 之后，加结构候选渲染：若 `d.playbook_candidate` 存在，显示 name/structure/when_to_use + 「采纳进打法库」按钮（表单 post `/media/content/{cid}/mine/adopt-playbook`，带 hidden name/structure/when_to_use/evidence/similar_to）。**不塞 SVG 进 JS 字符串**；LLM 字段渲染前 `escapeHtml`（该文件已有 escapeHtml，沿用）。
```javascript
// 在 mine 成功回调里，materials/signatures 渲染之后：
if (d.playbook_candidate) {
  const p = d.playbook_candidate;
  html += '<div class="mine-sec"><b>🏗️ 结构/打法（爆款）</b>' +
    '<div style="border:1px solid var(--border);padding:8px;margin:6px 0;border-radius:8px">' +
    '<b>'+escapeHtml(p.name)+'</b><div style="font-size:12px;color:var(--ink-3)">'+escapeHtml(p.structure)+'</div>' +
    '<div style="font-size:12px">适用：'+escapeHtml(p.when_to_use)+(p.similar_to?'｜似已有《'+escapeHtml(p.similar_to)+'》':'')+'</div>' +
    '<button class="btn" onclick=\'adoptPlaybook('+JSON.stringify(p)+")'>采纳进打法库</button></div></div>";
}
```
加 `adoptPlaybook`（用 fetch post 表单字段，不整页刷新；成功提示）：
```javascript
async function adoptPlaybook(p){
  const b = new URLSearchParams();
  b.set('name',p.name); b.set('structure',p.structure||''); b.set('when_to_use',p.when_to_use||'');
  b.set('evidence',p.evidence||''); b.set('similar_to',p.similar_to||'');
  const cid = "{{ content.id }}";
  const r = await fetch(`/media/content/${cid}/mine/adopt-playbook`,{method:'POST',body:b});
  const d = await r.json();
  alert(d.ok ? (d.merged?'已归并到已有打法':'已采纳进打法库') : ('失败：'+(d.error||'')));
}
```
（`content.id` 变量对齐现有模板。escapeHtml 已存在则复用。）

- [ ] **Step 2: 打法库浏览页 `media_playbook.html`**
```html
{% extends "base.html" %}
{% block title %}打法库{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  <h2>🏗️ 打法库</h2>
  <p style="color:var(--ink-3); font-size:13px">从爆款炼出的可复制结构。跑通的标 proven。（本轮暂不接决策/写稿）</p>
  {% for p in playbooks %}
  <div class="module" style="margin-top:10px">
    <div class="mh"><span class="ttl">{{ p.name }}</span>
      <span class="tag" style="color:{{ 'var(--up)' if p.status=='proven' else 'var(--ink-3)' }}">
        {{ '已跑通' if p.status=='proven' else '验证中' }}</span></div>
    <div class="inner" style="font-size:13px">
      <div>{{ p.structure }}</div>
      {% if p.when_to_use %}<div style="color:var(--ink-3); margin-top:4px">适用：{{ p.when_to_use }}</div>{% endif %}
      {% if p.evidence %}<div style="color:var(--ink-3); font-size:12px; margin-top:4px; white-space:pre-wrap">出处：{{ p.evidence }}</div>{% endif %}
      <form method="post" action="/media/playbook/{{ p.id }}/status" style="margin-top:6px">
        <input type="hidden" name="status" value="{{ 'validating' if p.status=='proven' else 'proven' }}">
        <button type="submit" class="btn" style="font-size:12px">{{ '降回验证中' if p.status=='proven' else '标为已跑通' }}</button>
      </form>
    </div>
  </div>
  {% else %}<div class="empty">还没有打法。去爆款内容挖精华时采纳结构。</div>{% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 3: 导航加入口** — grep `base.html` 找到 `/media` 导航项，其附近或自媒体子菜单加 `<a href="/media/playbook">🏗️ 打法库</a>`（对齐现有导航写法）。

- [ ] **Step 4: 全套回归 + 浏览器冒烟（controller 亲跑）**
`python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟：粘文本预览→入库→/media/legacy 标爆款→爆款内容挖精华出结构桶→采纳→/media/playbook 渲染+status 切换，无 Jinja/500/console。

- [ ] **Step 5: Commit**
```bash
git add app/templates/media_content.html app/templates/media_playbook.html app/templates/base.html
git commit -m "feat(media): UI-内容详情结构桶展示+采纳+打法库浏览页+导航入口"
```

---

## Self-Review 记录

- **Spec 覆盖：** §4 表→T1；§5 切分/进料→T2+T3；§6 winner→T3；§7 结构桶+采纳→T4；§8 打法库页→T5+T7；进料/winner UI→T6；结构桶/打法库 UI→T7。§3 注意力纪律：零下游注入(全程不碰决策/写稿)、一次一条(mine 单条)、收敛(similar_to 归并 T4)、暂存薄(无独立暂存表,media_content 当台账)。
- **类型一致：** `split_legacy_scripts`/`create_legacy_contents`(T2)→T3 路由消费；`mine_structure` 返 `{ok,playbook}`(T4)→mine 路由 `playbook_candidate`→T7 UI 消费；adopt-playbook 字段 name/structure/when_to_use/evidence/similar_to(T4)↔T7 表单一致；`list_playbooks`(T5)→T7 页面。`is_winner`(T1)→T3 标记→T4 mine 门控。
- **无占位：** 每 step 完整代码/命令/期望。改现有用"原→改"精确定位（is_reverse、content_mine）。
