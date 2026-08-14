# 老文案批量挖矿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（inline）。Steps use checkbox (`- [ ]`).

**Goal:** 老文案两条批量流（记忆点从所有 / 精华从爆款），逐条喂 AI，候选进暂存表去重复选采纳，已挖有标识。

**Architecture:** 复用 mine_from_transcript/mine_structure；新增候选暂存表 + queue 服务 + mine-to-queue 端点 + 复核页；前端逐条编排。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。

## Global Constraints

- **纪律**：AI 一次只喂一条老文案（前端逐条 fetch，绝不合并 prompt）。
- **流A 记忆点**=从所有老文案，复用 mine_from_transcript 取 signatures 桶；**流B 精华**=仅爆款(is_winner)，取 materials 桶 + mine_structure 打法桶。
- **候选暂存**跨刷新不丢；采纳/丢弃走去重组（同 dedup_key 整组消）。
- **采纳 source 复用**：signature→media_persona_trait(dimension='signature',source='reverse_mine')；material→media_material(source='反向挖料')；playbook→similar_to 归并否则新建(source='legacy_mine')。
- **迁移**：media_content 加列走 MIGRATIONS(idempotent ALTER)；media_mine_candidate 走 SCHEMA(零迁移)。测试 make_db() 应用两者，FK 开着，独立 persona id。
- 改模板用 Edit/Write，JS 不塞 SVG。跑 pytest：`cd /d/GAGA-5-25/ai-pm && python -m pytest ... ; echo EXIT=${PIPESTATUS[0]}`（cwd 每次重置，每条 bash 先 cd）。假挂 `taskkill //F //IM python.exe`。

---

### Task 1: DB（2 标识列 + media_mine_candidate 表）

**Files:** Modify `app/database.py`；Test `tests/test_media_batch_mine_schema.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_batch_mine_schema.py
"""批量挖矿 schema。"""
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


def test_content_has_mined_markers():
    cols = _cols("media_content")
    assert "mined_signature_at" in cols and "mined_essence_at" in cols


def test_candidate_table_columns():
    cols = _cols("media_mine_candidate")
    assert {"id", "persona_id", "kind", "payload", "source_content_id",
            "dedup_key", "status", "created_at"} <= cols
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_batch_mine_schema.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/database.py`：MIGRATIONS 末尾加
```python
    "ALTER TABLE media_content ADD COLUMN mined_signature_at DATETIME",
    "ALTER TABLE media_content ADD COLUMN mined_essence_at DATETIME",
```
SCHEMA 里（media_playbook 之后、结束 `"""` 前）加：
```sql
CREATE TABLE IF NOT EXISTS media_mine_candidate (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    source_content_id TEXT DEFAULT '',
    dedup_key TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: GREEN** → PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add app/database.py tests/test_media_batch_mine_schema.py && git commit -m "feat(media): 批量挖矿-media_content加2标识列+media_mine_candidate暂存表"
```

---

### Task 2: `media_mine_queue` 服务（enqueue/分组/采纳/丢弃）

**Files:** Create `app/services/media_mine_queue.py`；Test `tests/test_media_mine_queue.py`（新）

**Interfaces:**
- `_dedup_key(kind, payload) -> str`
- `async enqueue_candidates(db, persona_id, source_content_id, kind, items) -> int`（items=payload dict 列表；同 (persona,kind,dedup_key,source_content_id) 已 pending 则跳过；返新增数）
- `async list_pending_grouped(db, persona_id) -> dict`（{'signature':[...], 'material':[...], 'playbook':[...]}，每组含 rep_id/payload/count/sources[标题]）
- `async adopt_candidates(db, ids) -> int`（ids=每组 rep_id；落对应库 + 整组标 adopted）
- `async discard_candidates(db, ids) -> int`（整组标 discarded）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_mine_queue.py
"""批量挖矿候选队列：去重写入/分组/采纳落库/丢弃。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_mine_queue as q


def _mkdb():
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('BM','嘉','x','涨粉','active')")
        for cid, t in (("C1", "文案甲"), ("C2", "文案乙")):
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source) "
                             "VALUES (?,?,?, 'published','legacy_text')", (cid, "BM", t))
        await db.commit()
        return db
    return asyncio.run(go())


def test_enqueue_dedup_within_content():
    async def go():
        db = _mkdb()
        # 同一条里同句返回两次 → 只存一条
        n = await q.enqueue_candidates(db, "BM", "C1", "signature",
            [{"content": "你要知道", "brief": "", "evidence": "e", "reason": "r"},
             {"content": "你要知道", "brief": "", "evidence": "e2", "reason": "r2"}])
        assert n == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_mine_candidate")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())


def test_group_counts_across_contents():
    async def go():
        db = _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "signature",
            [{"content": "你要知道", "evidence": "来自甲"}])
        await q.enqueue_candidates(db, "BM", "C2", "signature",
            [{"content": "你要知道", "evidence": "来自乙"}])
        grouped = await q.list_pending_grouped(db, "BM")
        sigs = grouped["signature"]
        assert len(sigs) == 1 and sigs[0]["count"] == 2      # 跨两条合并计数
        assert len(sigs[0]["sources"]) == 2
        await db.close()
    asyncio.run(go())


def test_adopt_signature_writes_trait_and_marks_group():
    async def go():
        db = _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "signature",
            [{"content": "你要知道", "evidence": "来自甲"}])
        await q.enqueue_candidates(db, "BM", "C2", "signature",
            [{"content": "你要知道", "evidence": "来自乙"}])
        grouped = await q.list_pending_grouped(db, "BM")
        rep = grouped["signature"][0]["rep_id"]
        n = await q.adopt_candidates(db, [rep])
        assert n == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait "
                               "WHERE persona_id='BM' AND dimension='signature'")
        assert (await cur.fetchone())["c"] == 1              # 写了一条人设
        cur = await db.execute("SELECT COUNT(*) c FROM media_mine_candidate WHERE status='pending'")
        assert (await cur.fetchone())["c"] == 0              # 整组消掉
        await db.close()
    asyncio.run(go())


def test_adopt_material_and_playbook():
    async def go():
        db = _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "material",
            [{"type": "story", "content": "踩过的坑", "brief": "坑", "evidence": "e", "reason": "r"}])
        await q.enqueue_candidates(db, "BM", "C1", "playbook",
            [{"name": "痛点自曝法", "structure": "抛→自曝→给法", "when_to_use": "焦虑", "evidence": "e", "similar_to": ""}])
        g = await q.list_pending_grouped(db, "BM")
        ids = [g["material"][0]["rep_id"], g["playbook"][0]["rep_id"]]
        await q.adopt_candidates(db, ids)
        cur = await db.execute("SELECT COUNT(*) c FROM media_material WHERE persona_id='BM'")
        assert (await cur.fetchone())["c"] == 1
        cur = await db.execute("SELECT COUNT(*) c FROM media_playbook WHERE name='痛点自曝法'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())


def test_discard_marks_group():
    async def go():
        db = _mkdb()
        await q.enqueue_candidates(db, "BM", "C1", "signature", [{"content": "口水词"}])
        g = await q.list_pending_grouped(db, "BM")
        await q.discard_candidates(db, [g["signature"][0]["rep_id"]])
        g2 = await q.list_pending_grouped(db, "BM")
        assert g2["signature"] == []
        await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_mine_queue.py -v` → FAIL（模块无）

- [ ] **Step 3: 实现**
```python
# app/services/media_mine_queue.py
"""老文案批量挖矿候选队列：去重写入 / 去重分组 / 采纳落对应库 / 丢弃。纯 DB，不调 AI。"""
import json
import uuid


def _dedup_key(kind: str, payload: dict) -> str:
    if kind == "playbook":
        base = (payload.get("name") or "").strip()
    else:
        base = (payload.get("content") or "").strip()
    return f"{kind}:{base[:60]}"


async def enqueue_candidates(db, persona_id: str, source_content_id: str,
                             kind: str, items: list) -> int:
    n = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        dk = _dedup_key(kind, it)
        # 同一条内容里同句被 AI 返回多次 → 幂等（含 source_content_id，跨内容保留以便计数）
        cur = await db.execute(
            "SELECT 1 FROM media_mine_candidate WHERE persona_id=? AND kind=? "
            "AND dedup_key=? AND source_content_id=? AND status='pending'",
            (persona_id, kind, dk, source_content_id))
        if await cur.fetchone():
            continue
        await db.execute(
            "INSERT INTO media_mine_candidate "
            "(id,persona_id,kind,payload,source_content_id,dedup_key) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), persona_id, kind, json.dumps(it, ensure_ascii=False),
             source_content_id, dk))
        n += 1
    await db.commit()
    return n


async def list_pending_grouped(db, persona_id: str) -> dict:
    cur = await db.execute(
        "SELECT mc.*, c.title AS src_title FROM media_mine_candidate mc "
        "LEFT JOIN media_content c ON c.id=mc.source_content_id "
        "WHERE mc.persona_id=? AND mc.status='pending' ORDER BY mc.created_at",
        (persona_id,))
    rows = [dict(r) for r in await cur.fetchall()]
    groups = {"signature": {}, "material": {}, "playbook": {}}
    for r in rows:
        bucket = groups.get(r["kind"])
        if bucket is None:
            continue
        g = bucket.get(r["dedup_key"])
        if not g:
            g = {"rep_id": r["id"], "payload": json.loads(r["payload"] or "{}"),
                 "count": 0, "sources": []}
            bucket[r["dedup_key"]] = g
        g["count"] += 1
        if r.get("src_title"):
            g["sources"].append(r["src_title"])
    return {k: list(v.values()) for k, v in groups.items()}


async def _adopt_one(db, cand: dict):
    kind, p = cand["kind"], json.loads(cand["payload"] or "{}")
    pid = cand["persona_id"]
    if kind == "signature":
        content = (p.get("content") or "").strip()
        if content:
            await db.execute(
                "INSERT INTO media_persona_trait "
                "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
                "VALUES (?,?, 'signature',?,?, 'reverse_mine',?,3,'')",
                (str(uuid.uuid4()), pid, content, (p.get("brief") or content)[:30],
                 (p.get("evidence") or "").strip()))
    elif kind == "material":
        detail = (p.get("content") or "").strip()
        if detail:
            mtype = p.get("type") or "story"
            await db.execute(
                "INSERT INTO media_material (id,persona_id,type,title,detail,brief,source) "
                "VALUES (?,?,?,?,?,?, '反向挖料')",
                (str(uuid.uuid4()), pid, mtype, detail[:40], detail,
                 (p.get("brief") or detail)[:30]))
    elif kind == "playbook":
        name = (p.get("name") or "").strip()
        if name:
            sim = (p.get("similar_to") or "").strip()
            merged = False
            if sim:
                cur = await db.execute(
                    "SELECT id,evidence FROM media_playbook WHERE persona_id=? AND name=?",
                    (pid, sim))
                ex = await cur.fetchone()
                if ex:
                    new_ev = ((ex["evidence"] or "") + "\n---\n" + (p.get("evidence") or "")).strip()
                    await db.execute("UPDATE media_playbook SET evidence=? WHERE id=?",
                                     (new_ev, ex["id"]))
                    merged = True
            if not merged:
                await db.execute(
                    "INSERT INTO media_playbook "
                    "(id,persona_id,name,structure,when_to_use,evidence,source,status) "
                    "VALUES (?,?,?,?,?,?, 'legacy_mine','validating')",
                    (str(uuid.uuid4()), pid, name, (p.get("structure") or "").strip(),
                     (p.get("when_to_use") or "").strip(), (p.get("evidence") or "").strip()))


async def _resolve_group(db, rep_id: str):
    """由代表 id 找回它那一组（同 persona/kind/dedup_key 的所有 pending）。"""
    cur = await db.execute("SELECT * FROM media_mine_candidate WHERE id=?", (rep_id,))
    rep = await cur.fetchone()
    if not rep:
        return None, []
    rep = dict(rep)
    cur = await db.execute(
        "SELECT id FROM media_mine_candidate WHERE persona_id=? AND kind=? "
        "AND dedup_key=? AND status='pending'",
        (rep["persona_id"], rep["kind"], rep["dedup_key"]))
    ids = [r["id"] for r in await cur.fetchall()]
    return rep, ids


async def adopt_candidates(db, ids: list) -> int:
    n = 0
    for rep_id in ids or []:
        rep, group_ids = await _resolve_group(db, rep_id)
        if not rep or not group_ids:
            continue
        await _adopt_one(db, rep)
        qs = ",".join("?" for _ in group_ids)
        await db.execute(
            f"UPDATE media_mine_candidate SET status='adopted' WHERE id IN ({qs})", group_ids)
        n += 1
    await db.commit()
    return n


async def discard_candidates(db, ids: list) -> int:
    n = 0
    for rep_id in ids or []:
        rep, group_ids = await _resolve_group(db, rep_id)
        if not rep or not group_ids:
            continue
        qs = ",".join("?" for _ in group_ids)
        await db.execute(
            f"UPDATE media_mine_candidate SET status='discarded' WHERE id IN ({qs})", group_ids)
        n += 1
    await db.commit()
    return n
```

- [ ] **Step 4: GREEN** → PASS（5 passed）

- [ ] **Step 5: Commit**
```bash
git add app/services/media_mine_queue.py tests/test_media_mine_queue.py && git commit -m "feat(media): 批量挖矿候选队列服务(去重写入/分组计数/采纳落三库/丢弃)"
```

---

### Task 3: mine-to-queue 端点 + legacy_home 标识 context

**Files:** Modify `app/api/media.py`；Test `tests/test_media_mine_to_queue.py`（新）

**Interfaces:** `POST /media/content/{cid}/mine-to-queue`（Form kind∈{signature,essence}, force=0）→ 调 mine 函数 + enqueue + 打标；返 `{ok, added, skipped}`。

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_mine_to_queue.py
"""mine-to-queue 端点：signature 写候选打标 / essence 非爆款skip / 已挖skip / force。"""
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
    tmp = tmp_path_factory.mktemp("mq_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed(cid, is_winner):
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='MQ'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MQ','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script,is_winner) "
                         "VALUES (?, 'MQ','t','published','legacy_text','转写正文你要知道',?)", (cid, is_winner))
        await db.commit(); await db.close()
    asyncio.run(go())


def test_signature_enqueues_and_marks(monkeypatch):
    _seed("Q1", 0)
    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": [{"content": "你要知道"}]}
    monkeypatch.setattr(media_api, "mine_from_transcript", fake_mine)
    r = _client().post("/media/content/Q1/mine-to-queue", data={"kind": "signature"})
    assert r.status_code == 200 and r.json()["added"] == 1
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT mined_signature_at FROM media_content WHERE id='Q1'")
        assert (await cur.fetchone())["mined_signature_at"] is not None
        cur = await db.execute("SELECT COUNT(*) c FROM media_mine_candidate WHERE kind='signature'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(chk())


def test_essence_skips_non_winner():
    _seed("Q2", 0)
    r = _client().post("/media/content/Q2/mine-to-queue", data={"kind": "essence"})
    assert r.status_code == 200 and r.json()["skipped"] == "not_winner"


def test_already_mined_skips_without_force(monkeypatch):
    _seed("Q3", 0)
    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": [{"content": "x"}]}
    monkeypatch.setattr(media_api, "mine_from_transcript", fake_mine)
    _client().post("/media/content/Q3/mine-to-queue", data={"kind": "signature"})
    r = _client().post("/media/content/Q3/mine-to-queue", data={"kind": "signature"})
    assert r.json()["skipped"] == "already"
    r2 = _client().post("/media/content/Q3/mine-to-queue", data={"kind": "signature", "force": "1"})
    assert r2.json().get("skipped") != "already"
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_mine_to_queue.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py`：顶部已 import `mine_from_transcript, mine_structure`（Task 打法库🅓那轮加的）。加 `from app.services.media_mine_queue import enqueue_candidates, list_pending_grouped, adopt_candidates, discard_candidates`。在 `content_mine` 附近加：
```python
@router.post("/media/content/{cid}/mine-to-queue")
async def content_mine_to_queue(cid: str, kind: str = Form(...), force: int = Form(0)):
    """批量挖矿：逐条调（前端编排）。kind=signature 从任意内容挖口头禅；essence 仅爆款挖素材+打法。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id,script,is_winner,mined_signature_at,mined_essence_at "
            "FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        pid, script = row["persona_id"], row["script"] or ""
        try:
            if kind == "signature":
                if row["mined_signature_at"] and not force:
                    return JSONResponse({"ok": True, "added": 0, "skipped": "already"})
                res = await mine_from_transcript(db, pid, script)
                added = await enqueue_candidates(db, pid, cid, "signature",
                                                 res.get("signatures") or [])
                await db.execute("UPDATE media_content SET mined_signature_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
                await db.commit()
                return JSONResponse({"ok": True, "added": added, "skipped": ""})
            elif kind == "essence":
                if not row["is_winner"]:
                    return JSONResponse({"ok": True, "added": 0, "skipped": "not_winner"})
                if row["mined_essence_at"] and not force:
                    return JSONResponse({"ok": True, "added": 0, "skipped": "already"})
                res = await mine_from_transcript(db, pid, script)
                added = await enqueue_candidates(db, pid, cid, "material",
                                                 res.get("materials") or [])
                st = await mine_structure(db, pid, script)
                if st.get("ok") and st.get("playbook"):
                    added += await enqueue_candidates(db, pid, cid, "playbook", [st["playbook"]])
                await db.execute("UPDATE media_content SET mined_essence_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
                await db.commit()
                return JSONResponse({"ok": True, "added": added, "skipped": ""})
            return JSONResponse({"ok": False, "error": "kind 非法"})
        except Exception as e:
            log.exception("批量挖矿失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
```
`legacy_home` 的 SELECT 加两个标识列，模板才能显示：
```python
                "SELECT id,title,idea_source,is_winner,mined_signature_at,mined_essence_at "
                "FROM media_content "
```

- [ ] **Step 4: GREEN** → PASS（3 passed）

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py tests/test_media_mine_to_queue.py && git commit -m "feat(media): mine-to-queue逐条挖矿端点(signature全部/essence仅爆款·已挖skip·force)+legacy标识context"
```

---

### Task 4: 复核页 + 采纳/丢弃路由 + 模板

**Files:** Modify `app/api/media.py`；Create `app/templates/media_mine_review.html`；Test `tests/test_media_mine_review.py`（新）

- [ ] **Step 1: 失败测试**
```python
# tests/test_media_mine_review.py
"""复核页 + 批量采纳/丢弃路由。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_mine_queue as q


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mr_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='MR'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MR','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source) "
                         "VALUES ('MRC','MR','标题','published','legacy_text')")
        await q.enqueue_candidates(db, "MR", "MRC", "signature", [{"content": "你要知道", "evidence": "e"}])
        await db.commit()
        cur = await db.execute("SELECT id FROM media_mine_candidate WHERE persona_id='MR' LIMIT 1")
        rid = (await cur.fetchone())["id"]
        await db.close()
        return rid
    return asyncio.run(go())


def test_review_page_renders():
    _seed()
    r = _client().get("/media/mine-review")
    assert r.status_code == 200 and "你要知道" in r.text


def test_adopt_route():
    rid = _seed()
    r = _client().post("/media/mine-review/adopt", data={"candidate_ids": [rid]},
                       follow_redirects=False)
    assert r.status_code in (302, 303)
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait WHERE persona_id='MR'")
        assert (await cur.fetchone())["c"] >= 1
        await db.close()
    asyncio.run(chk())
```

- [ ] **Step 2: RED** — `python -m pytest tests/test_media_mine_review.py -v` → FAIL

- [ ] **Step 3: 实现** — `app/api/media.py` 加路由：
```python
@router.get("/media/mine-review", response_class=HTMLResponse)
async def mine_review(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        grouped = await list_pending_grouped(db, pid) if pid else {"signature": [], "material": [], "playbook": []}
    finally:
        await db.close()
    return _tpl(request, "media_mine_review.html", {"grouped": grouped})


@router.post("/media/mine-review/adopt")
async def mine_review_adopt(candidate_ids: list[str] = Form([])):
    if candidate_ids:
        db = await get_db()
        try:
            await adopt_candidates(db, candidate_ids)
        finally:
            await db.close()
    return RedirectResponse("/media/mine-review", status_code=303)


@router.post("/media/mine-review/discard")
async def mine_review_discard(candidate_ids: list[str] = Form([])):
    if candidate_ids:
        db = await get_db()
        try:
            await discard_candidates(db, candidate_ids)
        finally:
            await db.close()
    return RedirectResponse("/media/mine-review", status_code=303)
```
新建 `app/templates/media_mine_review.html`：
```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}挖矿复核{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} <b>挖矿复核</b></span>
{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  <h1 class="pname" style="margin:0 0 5px">挖矿复核</h1>
  <p style="font-size:13px;color:var(--ink-3);margin:0 0 16px">批量挖出的候选，去重后勾选采纳。同句合并显示出现次数。</p>
  <form method="post" id="rev-form">
    {% set sections = [('signature','记忆点 → 人设'), ('material','素材 → 原料库'), ('playbook','打法 → 打法库')] %}
    {% for key, label in sections %}
    <div class="module" style="margin-top:12px">
      <div class="mh"><span class="ttl">{{ label }}（{{ grouped[key]|length }}）</span></div>
      <div class="inner">
        {% for g in grouped[key] %}
        <label style="display:flex; gap:8px; padding:7px 0; border-bottom:1px solid var(--border); font-size:13px">
          <input type="checkbox" name="candidate_ids" value="{{ g.rep_id }}">
          <span style="flex:1">
            <b>{{ g.payload.name if key=='playbook' else g.payload.content }}</b>
            {% if g.count > 1 %}<span class="tag" style="color:var(--ai)">出现 {{ g.count }} 次</span>{% endif %}
            {% if key=='playbook' and g.payload.structure %}<div style="color:var(--ink-3); font-size:12px">{{ g.payload.structure }}</div>{% endif %}
            {% if g.payload.evidence %}<div style="color:var(--ink-3); font-size:11.5px; margin-top:2px">原文：{{ g.payload.evidence }}</div>{% endif %}
            {% if g.sources %}<div style="color:var(--ink-3); font-size:11px">来自：{{ g.sources[:3]|join('、') }}{% if g.sources|length>3 %} 等{% endif %}</div>{% endif %}
          </span>
        </label>
        {% else %}<div class="empty" style="padding:16px; color:var(--ink-3); font-size:12.5px">无</div>{% endfor %}
      </div>
    </div>
    {% endfor %}
    <div style="margin-top:14px; display:flex; gap:8px">
      <button type="submit" formaction="/media/mine-review/adopt" class="btn primary">采纳选中</button>
      <button type="submit" formaction="/media/mine-review/discard" class="btn">丢弃选中</button>
      <label style="margin-left:auto; font-size:12px; color:var(--ink-3)"><input type="checkbox" onclick="document.querySelectorAll('#rev-form input[name=candidate_ids]').forEach(c=>c.checked=this.checked)"> 全选</label>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4: GREEN** → PASS（2 passed）；全套回归 `python -m pytest -q`

- [ ] **Step 5: Commit**
```bash
git add app/api/media.py app/templates/media_mine_review.html tests/test_media_mine_review.py && git commit -m "feat(media): 挖矿复核页(去重分组)+批量采纳/丢弃路由"
```

---

### Task 5: 老文案页 UI（两批量按钮 + 逐条编排 + 标识）

**Files:** Modify `app/templates/media_legacy.html`

- [ ] **Step 1: 加标识 + 两批量按钮 + 编排 JS** — 在 `media_legacy.html` 现有勾选表单里，把每行 winner 标签那行加两个已挖标识；表单下方加两个批量按钮 + 进度 + 编排脚本。

每行 `{% if it.is_winner %}...爆款{% endif %}` 那行后加：
```html
        {% if it.mined_signature_at %}<span class="tag" style="color:var(--ink-3)">记忆点✓</span>{% endif %}
        {% if it.mined_essence_at %}<span class="tag" style="color:var(--ink-3)">精华✓</span>{% endif %}
```

在 `<button type="submit" class="btn primary">标记选中为爆款</button>` 之后加：
```html
    <div style="margin-top:12px; padding-top:12px; border-top:1px solid var(--border); display:flex; gap:8px; flex-wrap:wrap; align-items:center">
      <button type="button" onclick="batchMine('signature')" class="btn ai" style="font-size:12.5px">批量挖记忆点（选中）</button>
      <button type="button" onclick="batchMine('essence')" class="btn ai" style="font-size:12.5px">批量挖精华（选中爆款）</button>
      <label style="font-size:12px; color:var(--ink-3)"><input type="checkbox" id="mine-force"> 已挖也重挖</label>
      <a href="/media/mine-review" style="font-size:12.5px; margin-left:auto">去复核采纳 →</a>
    </div>
    <div id="mine-progress" style="font-size:12.5px; color:var(--ai); margin-top:8px"></div>
```
在 `{% endblock %}` 前加脚本（`content_ids` 复选框已在表单里，name="content_ids"）：
```html
<script>
async function batchMine(kind){
  const ids=[...document.querySelectorAll('input[name=content_ids]:checked')].map(c=>c.value);
  const prog=document.getElementById('mine-progress');
  if(!ids.length){ prog.textContent='先勾选老文案'; return; }
  const force=document.getElementById('mine-force').checked?'1':'0';
  let added=0, skipped=0, done=0;
  for(const id of ids){
    prog.textContent='挖矿中… '+(++done)+'/'+ids.length;
    try{
      const fd=new FormData(); fd.append('kind',kind); fd.append('force',force);
      const r=await fetch('/media/content/'+id+'/mine-to-queue',{method:'POST',body:fd});
      const txt=await r.text(); let d; try{ d=JSON.parse(txt); }catch(_){ continue; }
      if(d.skipped) skipped++; else added+=(d.added||0);
    }catch(e){}
  }
  prog.innerHTML='完成：新增候选 '+added+' 条，跳过 '+skipped+' 条 → <a href="/media/mine-review">去复核采纳</a>';
}
</script>
```

- [ ] **Step 2: 全套回归 + 浏览器冒烟（controller 亲跑）** — `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}` 全绿。冒烟（TestClient+真机）：老文案页两按钮渲染、标识显示；勾选→批量挖记忆点→进度→复核页去重分组→采纳落人设；批量挖精华对非爆款 skip。无 Jinja/500/console。

- [ ] **Step 3: Commit**
```bash
git add app/templates/media_legacy.html && git commit -m "feat(media): 老文案页两批量挖按钮(逐条编排+进度)+已挖标识"
```

---

## Self-Review 记录

- **Spec 覆盖：** §5 表→T1；§6 AI 拆分+§7 端点→T3；§9 队列服务(enqueue/分组/采纳/丢弃)→T2；§9 复核页+路由→T4；§8 老文案页 UI→T5；§5 标识 context→T3(legacy_home)+T5(显示)。
- **类型一致：** `enqueue_candidates(db,pid,cid,kind,items)`(T2)→T3 调；`list_pending_grouped`→{kind:[{rep_id,payload,count,sources}]}(T2)→T4 页面+模板 g.rep_id/g.payload/g.count/g.sources；`adopt/discard_candidates(db,ids)`(T2)→T4 路由；mine 函数返回 signatures/materials(mine_from_transcript)、playbook(mine_structure)→T3 取桶 enqueue。
- **纪律：** T3 端点每次只处理一条；T5 前端 for 循环逐条 fetch，绝不合并。
- **无占位：** 每 step 完整代码。dedup 幂等键含 source_content_id（跨内容保留计数），修正 spec §7 措辞。
