# L2 周期复盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给自媒体飞轮补上 L2 周期复盘——汇总上轮之后新发的内容数据，AI 找规律、验上轮假设、提本轮新假设 + 候选资产，人拍板采纳。

**Architecture:** 新表 `media_review_cycle` 存每轮复盘（含假设结转字段）。新服务 `media_review_cycle.py`：纯计算周期范围/门槛/metrics 汇总 + AI 找规律（候选绝不写库）。路由触发 + 报告详情页 + 人设页复盘区。候选采纳复用现成人拍板闸（trait/audience adopt）。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + 本地 Tailwind + vanilla JS。AI 走 `ask_ai`（DeepSeek 优先）。

## Global Constraints

- **零 migration**：新表走 `app/database.py` 的 `SCHEMA`（`CREATE TABLE IF NOT EXISTS`），不进 `MIGRATIONS`。
- **候选绝不自动写库**：所有 AI 提炼的资产候选存进 cycle 行，人拍板 adopt 才入。
- **四条标准动作**：①人拍板 ②诚实（evidence 引原文/数字，样本少标 low）③只给精华（软上限：规律 ≤5、新假设 ≤3、候选每类 ≤3）④成本可见（`log_injection`）。
- **纳入去重铁律**：一条内容只被一轮 L2 纳入——纳入判定 = 该 persona 已发(published)且有 metrics 的内容，且其 id **不在任何往轮 cycle 的 `content_ids` 并集**里。
- **数据门槛** `L2_MIN_CONTENTS = 5`：纳入 < 5 条返回 warn 不写库；`force=True` 越过。
- **改模板一律用 Edit/Write**（禁 PowerShell -replace 毁中文）。
- **JS 不把 SVG 图标塞进字符串**（`innerHTML='{{ ic.icon(...) }}'` 会 SyntaxError；失败路径存 `const orig=btn.innerHTML` 还原）。
- 不动 L1 `review_content` / 功能B / 决策引擎逻辑；trait adopt 白名单只**增** `l2_review`。
- 测试基建：`tests/media_helpers.py` 的 `make_db()`（内存 DB + SCHEMA + MIGRATIONS）、`fake_ai(response)`（stub ask_ai）、`seed_content()`。异步用 `asyncio.run`（无 pytest-asyncio）。路由测试用 `_client()`（签名 cookie 登录）+ module 级 tmp DB 隔离。

---

### Task 1: 数据模型 `media_review_cycle`

**Files:**
- Modify: `app/database.py`（`SCHEMA` 尾部，`media_anchor` 表之后、SCHEMA 结束的 `"""`（约 :446）之前）
- Test: `tests/test_media_review_cycle_schema.py`（新）

**Interfaces:**
- Produces: `media_review_cycle` 表，列见下。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_review_cycle_schema.py
"""L2 周期复盘表 schema 测试。"""
import asyncio
from tests.media_helpers import make_db


def _cols(table):
    async def go():
        db = await make_db()
        try:
            cur = await db.execute(f"PRAGMA table_info({table})")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    return asyncio.run(go())


def test_review_cycle_table_has_expected_columns():
    cols = _cols("media_review_cycle")
    expected = {
        "id", "persona_id", "level", "seq", "period_start", "period_end",
        "content_ids", "metrics_summary", "patterns", "hypotheses",
        "hypotheses_tested", "proposed_traits", "proposed_audience",
        "advisory", "cost", "model", "generated_by", "created_at",
    }
    assert expected <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_review_cycle_schema.py -v`
Expected: FAIL（`no such table: media_review_cycle`）

- [ ] **Step 3: 加表定义到 SCHEMA**

在 `app/database.py` 的 `media_anchor` 表定义之后、`SCHEMA` 字符串结束的 `"""` 之前，插入：

```sql
CREATE TABLE IF NOT EXISTS media_review_cycle (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    level TEXT DEFAULT 'L2',
    seq INTEGER DEFAULT 1,
    period_start DATETIME,
    period_end DATETIME,
    content_ids TEXT DEFAULT '[]',
    metrics_summary TEXT DEFAULT '{}',
    patterns TEXT DEFAULT '[]',
    hypotheses TEXT DEFAULT '[]',
    hypotheses_tested TEXT DEFAULT '[]',
    proposed_traits TEXT DEFAULT '[]',
    proposed_audience TEXT DEFAULT '[]',
    advisory TEXT DEFAULT '{}',
    cost REAL DEFAULT 0,
    model TEXT DEFAULT '',
    generated_by TEXT DEFAULT 'ai',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_review_cycle_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_media_review_cycle_schema.py
git commit -m "feat(media): media_review_cycle 表（L2 周期复盘）"
```

---

### Task 2: 纯计算核心（汇总 / 周期范围 / 门槛）

**Files:**
- Create: `app/services/media_review_cycle.py`
- Test: `tests/test_media_review_cycle_calc.py`（新）

**Interfaces:**
- Produces:
  - `_agg(content: dict) -> dict`：一条内容跨平台取各指标 max，返回 `{views,likes,comments,shares,new_fans}`。
  - `_median(nums: list[int]) -> int`。
  - `summarize_metrics(contents: list[dict]) -> dict`：见 metrics_summary 结构（`content_count/avg/median/hit_count/flop_count/hit_content_ids/flop_content_ids`）。hit = 该内容聚合 views ≥ 1.5×批次中位；flop = ≤ 0.5×批次中位。
  - `async gather_cycle_contents(db, persona_id) -> tuple[list[dict], dict | None]`：返回 (纳入内容列表, 上一轮 cycle 行 dict 或 None)。每条内容含 `id/title/puzzle/script/platforms(list)` + `_agg` 展开的聚合指标。**已被往轮纳入的 id 排除。**
  - `L2_MIN_CONTENTS = 5`。
- Consumes: Task 1 的表。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_review_cycle_calc.py
"""L2 纯计算：聚合/中位/门槛/去重。"""
import asyncio
import json
import uuid
from tests.media_helpers import make_db, seed_content
from app.services import media_review_cycle as rc


def test_agg_takes_max_across_platforms():
    c = {"platforms": [
        {"views": 100, "likes": 5, "comments": 1, "shares": 0, "new_fans": 2},
        {"views": 300, "likes": 2, "comments": 4, "shares": 1, "new_fans": 1},
    ]}
    a = rc._agg(c)
    assert a["views"] == 300 and a["likes"] == 5 and a["comments"] == 4


def test_median_odd_even():
    assert rc._median([9000, 1000, 5000]) == 5000
    assert rc._median([2, 4, 6, 8]) == 5
    assert rc._median([]) == 0


def test_summarize_marks_hit_and_flop():
    def mk(cid, v):
        return {"id": cid, "platforms": [{"views": v, "likes": 0,
                "comments": 0, "shares": 0, "new_fans": 0}]}
    contents = [mk("a", 1000), mk("b", 1000), mk("c", 5000), mk("d", 100)]
    s = rc.summarize_metrics(contents)
    assert s["content_count"] == 4
    assert s["median"]["views"] == 1000
    assert "c" in s["hit_content_ids"]      # 5000 >= 1.5*1000
    assert "d" in s["flop_content_ids"]     # 100 <= 0.5*1000
    assert s["hit_count"] == 1 and s["flop_count"] == 1


def _insert_published_with_metrics(db, persona_id, content_id, views):
    async def go():
        await seed_content(db, persona_id=persona_id, content_id=content_id,
                           stage="published")
        aid = "ACC-" + content_id
        await db.execute(
            "INSERT INTO media_account (id,persona_id,platform,handle) "
            "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
        pid = "PUB-" + content_id
        await db.execute(
            "INSERT INTO media_publish (id,content_id,account_id,status) "
            "VALUES (?,?,?, 'published')", (pid, content_id, aid))
        await db.execute(
            "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
            "shares,new_fans) VALUES (?,?,?,0,0,0,0)",
            (str(uuid.uuid4()), pid, views))
        await db.commit()
    return go()


def test_gather_excludes_already_reviewed():
    async def go():
        db = await make_db()
        try:
            # seed_content 会插 persona；第一条用 seed_content 建 persona
            await _insert_published_with_metrics(db, "P1", "c1", 1000)
            await _insert_published_with_metrics_no_persona(db, "P1", "c2", 2000)
            # 往轮已纳入 c1
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "period_end) VALUES (?,?,?,?,datetime('now'))",
                (str(uuid.uuid4()), "P1", 1, json.dumps(["c1"])))
            await db.commit()
            contents, prev = await rc.gather_cycle_contents(db, "P1")
            ids = {c["id"] for c in contents}
            assert ids == {"c2"}          # c1 已复盘被排除
            assert prev is not None and prev["seq"] == 1
        finally:
            await db.close()
    asyncio.run(go())


async def _insert_published_with_metrics_no_persona(db, persona_id, content_id, views):
    """persona 已存在时只插内容+发布+数据。"""
    import uuid as _u
    await db.execute(
        "INSERT INTO media_content (id,persona_id,title,puzzle,stage) "
        "VALUES (?,?,?,?, 'published')",
        (content_id, persona_id, "标题" + content_id, "谜题"))
    aid = "ACC-" + content_id
    await db.execute(
        "INSERT INTO media_account (id,persona_id,platform,handle) "
        "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
    pid = "PUB-" + content_id
    await db.execute(
        "INSERT INTO media_publish (id,content_id,account_id,status) "
        "VALUES (?,?,?, 'published')", (pid, content_id, aid))
    await db.execute(
        "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
        "shares,new_fans) VALUES (?,?,?,0,0,0,0)", (str(_u.uuid4()), pid, views))
    await db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_review_cycle_calc.py -v`
Expected: FAIL（`No module named 'app.services.media_review_cycle'`）

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/media_review_cycle.py
"""L2 周期复盘：飞轮的动力源。

近 N 条内容对比找规律、验上轮假设、提本轮假设 + 候选资产。
候选绝不自动写库 —— AI 提炼，人拍板 adopt 才入（沿用 L1 复盘/人设访谈同款哲学）。
"""
import json
import uuid

from app.services.ai_router import ask_ai
from app.services.media_context import extract_json, log_injection

L2_MIN_CONTENTS = 5

_METRIC_KEYS = ("views", "likes", "comments", "shares", "new_fans")


def _agg(content: dict) -> dict:
    """一条内容跨平台取各指标最大值（哪个平台爆了算哪个）。"""
    out = {}
    for k in _METRIC_KEYS:
        out[k] = max((int(p.get(k) or 0) for p in content.get("platforms") or []),
                     default=0)
    return out


def _median(nums: list) -> int:
    xs = sorted(int(n or 0) for n in nums)
    if not xs:
        return 0
    n = len(xs)
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) // 2


def summarize_metrics(contents: list) -> dict:
    """纯计算汇总：条数、各指标均值/中位、爆款/flop 计数（vs 批次中位 views）。"""
    aggs = [(c["id"], _agg(c)) for c in contents]
    n = len(aggs) or 1
    avg = {k: sum(a[k] for _, a in aggs) // n for k in _METRIC_KEYS}
    median = {k: _median([a[k] for _, a in aggs]) for k in _METRIC_KEYS}
    mv = median["views"]
    hit_ids = [cid for cid, a in aggs if mv and a["views"] >= 1.5 * mv]
    flop_ids = [cid for cid, a in aggs if mv and a["views"] <= 0.5 * mv]
    return {
        "content_count": len(aggs),
        "avg": avg, "median": median,
        "hit_count": len(hit_ids), "flop_count": len(flop_ids),
        "hit_content_ids": hit_ids, "flop_content_ids": flop_ids,
    }


async def _prev_cycle(db, persona_id: str):
    cur = await db.execute(
        "SELECT * FROM media_review_cycle WHERE persona_id=? AND level='L2' "
        "ORDER BY seq DESC LIMIT 1", (persona_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def _reviewed_ids(db, persona_id: str) -> set:
    cur = await db.execute(
        "SELECT content_ids FROM media_review_cycle WHERE persona_id=? "
        "AND level='L2'", (persona_id,))
    seen = set()
    for r in await cur.fetchall():
        try:
            seen.update(json.loads(r["content_ids"] or "[]"))
        except Exception:
            pass
    return seen


async def gather_cycle_contents(db, persona_id: str):
    """纳入内容 = 该 persona 已发+有数据 且 不在任何往轮 content_ids 里。

    返回 (contents, prev_cycle)。每条 content 带 platforms(list) + 聚合指标。
    """
    prev = await _prev_cycle(db, persona_id)
    reviewed = await _reviewed_ids(db, persona_id)

    cur = await db.execute(
        "SELECT DISTINCT c.id, c.title, c.puzzle, c.script "
        "FROM media_content c "
        "WHERE c.persona_id=? AND EXISTS ("
        "  SELECT 1 FROM media_publish p JOIN media_metrics m ON m.publish_id=p.id "
        "  WHERE p.content_id=c.id AND p.status='published')", (persona_id,))
    rows = [dict(r) for r in await cur.fetchall()]

    contents = []
    for c in rows:
        if c["id"] in reviewed:
            continue
        pcur = await db.execute(
            "SELECT a.platform, m.views, m.likes, m.comments, m.shares, m.new_fans "
            "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
            "LEFT JOIN media_metrics m ON m.id=("
            "  SELECT id FROM media_metrics WHERE publish_id=p.id "
            "  ORDER BY snapshot_at DESC LIMIT 1) "
            "WHERE p.content_id=? AND p.status='published'", (c["id"],))
        c["platforms"] = [dict(r) for r in await pcur.fetchall()]
        c.update(_agg(c))
        contents.append(c)
    return contents, prev
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_review_cycle_calc.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_review_cycle.py tests/test_media_review_cycle_calc.py
git commit -m "feat(media): L2 纯计算核心（聚合/中位/门槛/纳入去重）"
```

---

### Task 3: `run_l2_cycle` 编排 + `L2_SYSTEM` + 读取助手

**Files:**
- Modify: `app/services/media_review_cycle.py`
- Test: `tests/test_media_review_cycle_run.py`（新）

**Interfaces:**
- Consumes: Task 2 的 `gather_cycle_contents` / `summarize_metrics` / `L2_MIN_CONTENTS`；`ask_ai` / `extract_json` / `log_injection`。
- Produces:
  - `async run_l2_cycle(db, persona_id, model="auto", force=False) -> dict`：门槛不足返回 `{"ok": False, "warn": ..., "count": X}`；成功写一行 `media_review_cycle` 并返回 `{"ok": True, "cycle_id","seq","count","cost","model"}`。
  - `async list_cycles(db, persona_id) -> list[dict]`（seq 降序，JSON 字段保持原文本，列表页只用元信息）。
  - `async get_cycle(db, cycle_id) -> dict | None`（JSON 字段解析成对象）。
  - `L2_SYSTEM: str`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_review_cycle_run.py
"""L2 编排：门槛拦截、写库、假设补 id、读取助手。"""
import asyncio
import json
import uuid
import pytest
from tests.media_helpers import make_db, seed_content
from app.services import media_review_cycle as rc


async def _seed_published(db, persona_id, cid, views, first=False):
    if first:
        await seed_content(db, persona_id=persona_id, content_id=cid, stage="published")
    else:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage) "
            "VALUES (?,?,?,?, 'published')", (cid, persona_id, "标题" + cid, "谜题"))
    aid = "ACC-" + cid
    await db.execute("INSERT INTO media_account (id,persona_id,platform,handle) "
                     "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
    pid = "PUB-" + cid
    await db.execute("INSERT INTO media_publish (id,content_id,account_id,status) "
                     "VALUES (?,?,?, 'published')", (pid, cid, aid))
    await db.execute("INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
                     "shares,new_fans) VALUES (?,?,?,0,0,0,0)",
                     (str(uuid.uuid4()), pid, views))
    await db.commit()


def test_below_threshold_returns_warn_no_write(monkeypatch):
    async def go():
        db = await make_db()
        try:
            await _seed_published(db, "P1", "c1", 1000, first=True)
            await _seed_published(db, "P1", "c2", 2000)
            res = await rc.run_l2_cycle(db, "P1")   # 只有 2 条 < 5
            assert res["ok"] is False and res["count"] == 2 and "warn" in res
            cur = await db.execute("SELECT COUNT(*) n FROM media_review_cycle")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(go())


def test_run_writes_row_and_assigns_hypothesis_ids(monkeypatch):
    fake = {
        "patterns": [{"pattern": "带故事的更火", "evidence": "c3", "confidence": "medium"}],
        "hypotheses": [{"statement": "前3秒抛问题提完播", "how_to_test": "下轮3条采用", "basis": "x"}],
        "hypotheses_tested": [],
        "proposed_traits": [{"dimension": "signature", "content": "爱用反问",
                             "brief": "反问", "evidence": "c3", "confidence": 3}],
        "proposed_audience": [],
        "advisory": {"weight_suggestion": "涨粉期抬 fit"},
    }

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek", "tokens": 20, "cost": 0.01}
    monkeypatch.setattr(rc, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            for i in range(5):
                await _seed_published(db, "P1", f"c{i}", 1000 + i * 500, first=(i == 0))
            res = await rc.run_l2_cycle(db, "P1")
            assert res["ok"] is True and res["count"] == 5 and res["seq"] == 1
            cyc = await rc.get_cycle(db, res["cycle_id"])
            assert cyc["metrics_summary"]["content_count"] == 5
            assert cyc["hypotheses"][0]["id"].startswith("h-")   # 补了稳定 id
            assert cyc["patterns"][0]["pattern"].startswith("带故事")
            # 成本记 log_injection
            cur = await db.execute("SELECT COUNT(*) n FROM media_injection_log "
                                   "WHERE ai_type='media_review_cycle'")
            assert (await cur.fetchone())["n"] == 1
        finally:
            await db.close()
    asyncio.run(go())


def test_second_cycle_carries_prev_hypotheses(monkeypatch):
    seen = {}

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": json.dumps({"patterns": [], "hypotheses": [],
                "hypotheses_tested": [], "proposed_traits": [],
                "proposed_audience": [], "advisory": {}}),
                "model": "deepseek", "tokens": 5, "cost": 0}
    monkeypatch.setattr(rc, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="seed", stage="idea")
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "hypotheses,period_end) VALUES (?,?,?,?,?,datetime('now'))",
                (str(uuid.uuid4()), "P1", 1, json.dumps(["old"]),
                 json.dumps([{"id": "h-prev0001", "statement": "老假设A"}])))
            await db.commit()
            for i in range(5):
                await _seed_published(db, "P1", f"n{i}", 1000, first=False)
            res = await rc.run_l2_cycle(db, "P1")
            assert res["seq"] == 2                    # 第二轮
            assert "老假设A" in seen["prompt"]         # 上轮假设进了 prompt
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_review_cycle_run.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'run_l2_cycle'`）

- [ ] **Step 3: Write minimal implementation**

在 `app/services/media_review_cycle.py` 末尾追加：

```python
L2_SYSTEM = """你是资深自媒体操盘手，看一批已发内容的对比，找可复制的规律。

铁律（违反即失败）：
1. 你只提炼规律和候选，绝不假装能改数据库；一切靠人拍板。
2. 诚实——每条规律必须有这批数据支撑，evidence 引具体内容标题或数字；
   样本太少不足以下结论时标 confidence:"low"，不硬凑规律。
3. 只给精华：规律 ≤5 条，新假设 ≤3 条，候选人设条目/受众修正各 ≤3 条。
4. 别把偶然当规律——一条爆了是运气，多条同向才是规律。

假设-验证：
- 若给了"上轮假设"，逐条判定 verdict：confirmed / refuted / inconclusive，
  并给这批数据里的证据；数据不足以判就 inconclusive（诚实，别硬判）。
- 提出本轮新假设：statement（结论）+ how_to_test（下轮怎么验）+ basis（本轮依据）。

落点分流：
- 人设特征候选 → proposed_traits（dimension 用 tone/signature/positioning 等）。
- 受众修正 → proposed_audience。
- 打法/教训/红线/权重调整建议 → advisory（这些暂无自动落点，先当文字建议）。

只输出严格 JSON，结构：
{"patterns":[{"pattern":"","evidence":"","confidence":"high|medium|low"}],
 "hypotheses":[{"statement":"","how_to_test":"","basis":""}],
 "hypotheses_tested":[{"ref_id":"","verdict":"confirmed|refuted|inconclusive","evidence":""}],
 "proposed_traits":[{"dimension":"","content":"","brief":"","evidence":"","confidence":3}],
 "proposed_audience":[{"segment":"","field":"","new_value":"","evidence":""}],
 "advisory":{"playbooks":[],"lessons":[],"redlines":[],"weight_suggestion":""}}"""


def _build_l2_prompt(contents, summary, prev):
    parts = [f"【本轮纳入 {summary['content_count']} 条已发内容】"]
    for c in contents:
        parts.append(
            f"- 《{c['title']}》谜题：{c.get('puzzle') or '—'}；"
            f"播放 {c['views']}，赞 {c['likes']}，评 {c['comments']}，"
            f"转 {c['shares']}，粉 +{c['new_fans']}")
    parts.append(
        f"【汇总】均值播放 {summary['avg']['views']}，中位 {summary['median']['views']}；"
        f"爆款 {summary['hit_count']} 条，flop {summary['flop_count']} 条。")
    if prev:
        try:
            prev_hyp = json.loads(prev.get("hypotheses") or "[]")
        except Exception:
            prev_hyp = []
        if prev_hyp:
            parts.append("【上轮假设（请逐条用 ref_id 判定）】")
            for h in prev_hyp:
                parts.append(f"- ref_id={h.get('id')}：{h.get('statement')}")
    parts.append("请复盘这批内容，找规律、验上轮假设、提本轮假设与候选资产。")
    return "\n".join(parts)


async def run_l2_cycle(db, persona_id: str, model: str = "auto",
                       force: bool = False) -> dict:
    contents, prev = await gather_cycle_contents(db, persona_id)
    count = len(contents)
    if count < L2_MIN_CONTENTS and not force:
        return {"ok": False, "count": count,
                "warn": f"才 {count} 条，规律不可靠，建议攒到 ~{L2_MIN_CONTENTS} 条再跑"}

    summary = summarize_metrics(contents)
    prompt = _build_l2_prompt(contents, summary, prev)
    result = await ask_ai(prompt, model=model, task_type="media_review_cycle",
                          system_prompt=L2_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "count": count, "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "count": count, "error": "复盘结果无法解析",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 新假设补稳定 id（下轮据此结转判定）
    hyps = [h for h in (obj.get("hypotheses") or []) if isinstance(h, dict)]
    for h in hyps:
        if not h.get("id"):
            h["id"] = "h-" + uuid.uuid4().hex[:8]

    seq = (prev["seq"] + 1) if prev else 1
    cid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_review_cycle "
        "(id,persona_id,level,seq,period_start,period_end,content_ids,"
        " metrics_summary,patterns,hypotheses,hypotheses_tested,"
        " proposed_traits,proposed_audience,advisory,cost,model) "
        "VALUES (?,?,'L2',?,?,datetime('now'),?,?,?,?,?,?,?,?,?,?)",
        (cid, persona_id, seq,
         prev["period_end"] if prev else None,
         json.dumps([c["id"] for c in contents], ensure_ascii=False),
         json.dumps(summary, ensure_ascii=False),
         json.dumps(obj.get("patterns") or [], ensure_ascii=False),
         json.dumps(hyps, ensure_ascii=False),
         json.dumps(obj.get("hypotheses_tested") or [], ensure_ascii=False),
         json.dumps(obj.get("proposed_traits") or [], ensure_ascii=False),
         json.dumps(obj.get("proposed_audience") or [], ensure_ascii=False),
         json.dumps(obj.get("advisory") or {}, ensure_ascii=False),
         result.get("cost", 0), result.get("model", "")))
    await db.commit()
    await log_injection(db, "", "media_review_cycle",
                        [c["id"] for c in contents], result.get("tokens", 0))
    return {"ok": True, "cycle_id": cid, "seq": seq, "count": count,
            "cost": result.get("cost", 0), "model": result.get("model", "")}


_JSON_FIELDS = ("content_ids", "metrics_summary", "patterns", "hypotheses",
                "hypotheses_tested", "proposed_traits", "proposed_audience",
                "advisory")


async def list_cycles(db, persona_id: str) -> list:
    cur = await db.execute(
        "SELECT id, seq, period_start, period_end, content_ids, metrics_summary, "
        "cost, model, created_at FROM media_review_cycle "
        "WHERE persona_id=? AND level='L2' ORDER BY seq DESC", (persona_id,))
    out = []
    for r in await cur.fetchall():
        d = dict(r)
        try:
            d["count"] = len(json.loads(d.get("content_ids") or "[]"))
        except Exception:
            d["count"] = 0
        out.append(d)
    return out


async def get_cycle(db, cycle_id: str):
    cur = await db.execute(
        "SELECT * FROM media_review_cycle WHERE id=?", (cycle_id,))
    row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    for f in _JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or ("[]" if f != "metrics_summary"
                              and f != "advisory" else "{}"))
        except Exception:
            d[f] = [] if f not in ("metrics_summary", "advisory") else {}
    return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_review_cycle_run.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_review_cycle.py tests/test_media_review_cycle_run.py
git commit -m "feat(media): run_l2_cycle 编排 + L2_SYSTEM + 读取助手（假设结转闭环）"
```

---

### Task 4: 路由（触发 / 详情 / adopt 白名单）

**Files:**
- Modify: `app/api/media.py`（import 区加 `media_review_cycle`；trait adopt 白名单 `:217`；新增两路由）
- Test: `tests/test_media_review_cycle_routes.py`（新）

**Interfaces:**
- Consumes: `run_l2_cycle` / `get_cycle` / `_first_persona_id`。
- Produces:
  - `POST /media/persona/{pid}/l2-review`（Form `force: int = 0`）→ JSON（stub 后端时透传 dict）。
  - `GET /media/review-cycle/{cid}` → HTML（`media_review_cycle.html`）。
  - trait adopt 白名单加 `l2_review`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_review_cycle_routes.py
"""L2 路由：触发（stub 后端）、详情页、adopt 白名单。"""
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


def _client():
    signer = TimestampSigner(get_or_create_session_secret())
    data = base64.b64encode(json.dumps({"user": "test"}).encode())
    c = TestClient(app)
    c.cookies.set("session", signer.sign(data).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("l2_routes_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid="LP"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "一句话", "涨粉"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_trigger_route_passes_through(monkeypatch):
    _seed_persona("LP")

    async def fake(db, persona_id, model="auto", force=False):
        return {"ok": False, "warn": "才 2 条", "count": 2}
    monkeypatch.setattr("app.api.media.run_l2_cycle", fake)
    r = _client().post("/media/persona/LP/l2-review", data={"force": 0})
    assert r.status_code == 200 and r.json()["warn"].startswith("才")


def test_detail_page_renders():
    cid = str(uuid.uuid4())

    async def go():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "metrics_summary,patterns,hypotheses,period_end) "
                "VALUES (?,?,?,?,?,?,?,datetime('now'))",
                (cid, "LP", 1, json.dumps(["x"]),
                 json.dumps({"content_count": 1, "avg": {"views": 10},
                             "median": {"views": 10}, "hit_count": 0, "flop_count": 0,
                             "hit_content_ids": [], "flop_content_ids": []}),
                 json.dumps([{"pattern": "P", "evidence": "e", "confidence": "low"}]),
                 json.dumps([{"id": "h-1", "statement": "假设", "how_to_test": "t"}])))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())
    r = _client().get(f"/media/review-cycle/{cid}")
    assert r.status_code == 200 and "假设" in r.text


def test_adopt_trait_whitelist_accepts_l2_review():
    # 白名单加了 l2_review 后，interview/adopt 带 source=l2_review 应原样入库
    from app.services import media_ai  # noqa
    _seed_persona("LP2")
    r = _client().post("/media/persona/LP2/interview/adopt", data={
        "dimension": "signature", "content": "爱反问", "brief": "反问",
        "evidence": "", "confidence": 3, "source": "l2_review"})
    assert r.status_code in (200, 302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT source FROM media_persona_trait WHERE persona_id='LP2'")
            rows = [x["source"] for x in await cur.fetchall()]
            assert "l2_review" in rows
        finally:
            await db.close()
    asyncio.run(check())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_review_cycle_routes.py -v`
Expected: FAIL（触发路由 404 / 白名单不含 l2_review）

- [ ] **Step 3: Write minimal implementation**

在 `app/api/media.py` 顶部 import 区（`from app.services.media_decision import ...` 附近）加：

```python
from app.services.media_review_cycle import run_l2_cycle, list_cycles, get_cycle
```

把 `:217` 白名单那行改成（**只增 `l2_review`**）：

```python
    src = source if source in ("interview", "learned_edit", "reverse_mine",
                               "l2_review") else "interview"
```

在文件"复盘"分区（`# ─────────────── 复盘 ───────────────` 附近）加两个路由：

```python
@router.post("/media/persona/{pid}/l2-review")
async def persona_l2_review(pid: str, force: int = Form(0)):
    db = await get_db()
    try:
        try:
            result = await run_l2_cycle(db, pid, force=bool(force))
        except Exception as e:
            log.exception("L2 周期复盘失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.get("/media/review-cycle/{cid}", response_class=HTMLResponse)
async def review_cycle_detail(cid: str, request: Request):
    db = await get_db()
    try:
        cyc = await get_cycle(db, cid)
    finally:
        await db.close()
    if not cyc:
        return RedirectResponse("/media/persona", status_code=302)
    return _tpl(request, "media_review_cycle.html", {"cyc": cyc})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_review_cycle_routes.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/api/media.py tests/test_media_review_cycle_routes.py
git commit -m "feat(media): L2 触发/详情路由 + trait adopt 白名单加 l2_review"
```

---

### Task 5: UI（人设页复盘区按钮 + 历史列表；报告详情页）

**Files:**
- Create: `app/templates/media_review_cycle.html`
- Modify: `app/api/media.py`（人设页路由 `GET /media/persona` 的 context 加 `l2_cycles`）
- Modify: `app/templates/media_persona.html`（复盘区加 L2 按钮 + 历史列表）

**Interfaces:**
- Consumes: `list_cycles`（人设页）、`get_cycle`（详情页 `cyc`）、`POST /media/persona/{pid}/l2-review`。

- [ ] **Step 1: 人设页路由 context 加 l2_cycles**

先定位人设页路由：`grep -n 'def .*persona\|/media/persona"' app/api/media.py`。在渲染 `media_persona.html` 的路由里，取到 persona 后加载周期列表并塞进 context：

```python
    l2_cycles = await list_cycles(db, persona["id"]) if persona else []
    # ... TemplateResponse 的 context 里加： "l2_cycles": l2_cycles
```

- [ ] **Step 2: 详情页模板**

创建 `app/templates/media_review_cycle.html`（继承站点 base，参考 `media_persona.html` 头部）。核心块：

```html
{% extends "base.html" %}
{% block content %}
<div style="max-width:820px;margin:0 auto">
  <p style="font-size:13px"><a href="/media/persona">← 人设</a></p>
  <h2>第 {{ cyc.seq }} 轮周期复盘（L2）</h2>
  <p style="color:var(--ink-3);font-size:13px">
    周期 {{ cyc.period_start or '起始' }} → {{ cyc.period_end }}
    · 纳入 {{ cyc.metrics_summary.content_count }} 条
    · 成本 ${{ '%.4f'|format(cyc.cost or 0) }} · {{ cyc.model }}
  </p>

  <h3>📊 汇总</h3>
  <div>均值播放 {{ cyc.metrics_summary.avg.views }}，中位 {{ cyc.metrics_summary.median.views }}；
       爆款 {{ cyc.metrics_summary.hit_count }} · flop {{ cyc.metrics_summary.flop_count }}</div>

  <h3>🔍 发现的规律</h3>
  {% for p in cyc.patterns %}
    <div class="card"><b>{{ p.pattern }}</b>
      <span class="badge">{{ p.confidence }}</span>
      <div style="color:var(--ink-3);font-size:13px">{{ p.evidence }}</div></div>
  {% else %}<div class="empty">这轮没提炼出规律</div>{% endfor %}

  <h3>🧪 假设台账</h3>
  {% if cyc.hypotheses_tested %}
    <h4>上轮假设的验证结果</h4>
    {% for h in cyc.hypotheses_tested %}
      <div class="card">
        <span class="badge">{{ h.verdict }}</span> {{ h.ref_id }}
        <div style="font-size:13px">{{ h.evidence }}</div></div>
    {% endfor %}
  {% endif %}
  <h4>本轮新假设</h4>
  {% for h in cyc.hypotheses %}
    <div class="card"><b>{{ h.statement }}</b>
      <div style="font-size:13px">怎么验：{{ h.how_to_test }}</div>
      <div style="font-size:12px;color:var(--ink-3)">依据：{{ h.basis }}</div></div>
  {% else %}<div class="empty">无</div>{% endfor %}

  <h3>🎯 候选资产（人拍板）</h3>
  {% for t in cyc.proposed_traits %}
    <form method="post" action="/media/persona/{{ cyc.persona_id }}/interview/adopt"
          style="border:1px solid var(--line);padding:8px;margin:6px 0">
      <input type="hidden" name="dimension" value="{{ t.dimension }}">
      <input type="hidden" name="content" value="{{ t.content }}">
      <input type="hidden" name="brief" value="{{ t.brief }}">
      <input type="hidden" name="evidence" value="{{ t.evidence }}">
      <input type="hidden" name="confidence" value="{{ t.confidence or 3 }}">
      <input type="hidden" name="source" value="l2_review">
      <b>[{{ t.dimension }}]</b> {{ t.content }}
      <button type="submit">采纳进人设</button>
    </form>
  {% else %}<div class="empty">这轮没提候选人设条目</div>{% endfor %}
  {% for a in cyc.proposed_audience %}
    <div class="card">受众修正建议：{{ a.segment }} · {{ a.field }} → {{ a.new_value }}
      <div style="font-size:12px;color:var(--ink-3)">去 <a href="/media/audience">受众页</a> 手动应用</div></div>
  {% endfor %}

  <h3>💡 建议（暂无自动落点）</h3>
  <div style="font-size:13px;color:var(--ink-3)">⚙️ 待打法库/权重 UI 建成后可落地</div>
  {% if cyc.advisory.weight_suggestion %}<div>权重：{{ cyc.advisory.weight_suggestion }}</div>{% endif %}
  {% for x in cyc.advisory.playbooks or [] %}<div>打法：{{ x }}</div>{% endfor %}
  {% for x in cyc.advisory.lessons or [] %}<div>教训：{{ x }}</div>{% endfor %}
  {% for x in cyc.advisory.redlines or [] %}<div>红线：{{ x }}</div>{% endfor %}
</div>
{% endblock %}
```

（`.card` / `.badge` / `.empty` 若 base 无对应类，用现有 `media_persona.html` 里已用的类名或行内样式对齐。实施时 grep 一下现有模板用的类。）

- [ ] **Step 3: 人设页复盘区加按钮 + 历史列表**

在 `media_persona.html` 现有"让 AI 复盘"（功能B）区块附近，用 Edit 加：

```html
<div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--line)">
  <h4>🔄 周期复盘（L2）</h4>
  <p style="font-size:12px;color:var(--ink-3)">汇总上次之后新发的内容，找规律、验假设</p>
  <button id="l2-btn" onclick="runL2()">跑一轮周期复盘</button>
  <div id="l2-msg" style="font-size:13px;margin-top:6px"></div>
  <div style="margin-top:10px">
    {% for c in l2_cycles %}
      <a href="/media/review-cycle/{{ c.id }}" style="display:block;font-size:13px;padding:4px 0">
        第 {{ c.seq }} 轮 · 纳入 {{ c.count }} 条 · {{ c.created_at }}</a>
    {% else %}
      <div style="font-size:12px;color:var(--ink-3)">还没跑过周期复盘</div>
    {% endfor %}
  </div>
</div>
<script>
async function runL2(force){
  const btn = document.getElementById('l2-btn');
  const msg = document.getElementById('l2-msg');
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = '正在复盘…'; msg.textContent = '';
  try {
    const pid = "{{ persona.id }}";
    const body = new URLSearchParams(); body.set('force', force ? 1 : 0);
    const r = await fetch(`/media/persona/${pid}/l2-review`, {method:'POST', body});
    const d = await r.json();
    if (d.ok) { location.href = `/media/review-cycle/${d.cycle_id}`; return; }
    if (d.warn) {
      msg.innerHTML = d.warn + ' <a href="#" onclick="runL2(true);return false;">仍要跑</a>';
    } else { msg.textContent = '失败：' + (d.error || ''); }
  } catch(e) { msg.textContent = '出错：' + e; }
  finally { btn.disabled = false; btn.textContent = orig; }
}
</script>
```

（注意：`persona.id` 变量名对齐人设页现有 context；若人设页用的是别的变量名，grep `media_persona.html` 确认后替换。**不要把 SVG 图标塞进 JS 字符串**。）

- [ ] **Step 4: 浏览器冒烟 + 全套回归**

启动本地：`preview_start {name: "ai-pm"}`（`.claude/launch.json` 无则先建，`python run.py` 端口 8000），用测试同款签名 cookie 登录后：
1. 打开 `/media/persona`，确认"周期复盘（L2）"区块出现、无 console 报错。
2. 点"跑一轮周期复盘"：数据不足弹"才 X 条 … 仍要跑"；点"仍要跑"应跳详情页（本地 DeepSeek key 已配可真调；无 key 则看到干净错误提示不崩）。
3. 详情页确认汇总/规律/假设台账/候选采纳区渲染，无 `<script>` 崩。

全套回归：

Run: `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}`
Expected: 全绿（237 基线 + 新增 L2 测试）。若假挂 = 残留 python 进程，`taskkill //F //IM python.exe` 后重跑。

- [ ] **Step 5: Commit**

```bash
git add app/templates/media_review_cycle.html app/templates/media_persona.html app/api/media.py
git commit -m "feat(media): L2 复盘 UI（人设页按钮+历史列表+报告详情页）"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 数据模型→Task 1；§4 服务（纯计算/编排/读取/L2_SYSTEM）→Task 2+3；§5 路由+adopt 白名单→Task 4；§6 UI→Task 5；§7 优雅降级→advisory 存库(Task 3)+详情页展示(Task 5)；§8 质量→各 Task 的 TDD + Task 5 全套回归；§1.1 假设-验证闭环→Task 3 的 id 补齐 + prompt 结转 + `test_second_cycle_carries_prev_hypotheses`。
- **纳入去重（Global Constraint）：** Task 2 `_reviewed_ids` + `gather_cycle_contents` 排除往轮 id；`test_gather_excludes_already_reviewed` 锁定。精化了 spec §10 的时间判定开放问题（改按已复盘去重，更稳）。
- **类型一致：** `run_l2_cycle`/`gather_cycle_contents`/`summarize_metrics`/`get_cycle`/`list_cycles` 签名在 Task 2/3 定义，Task 4/5 消费一致；`source="l2_review"` 白名单 Task 4 加、Task 5 表单用，一致。
- **无占位：** 所有 step 含完整代码/命令/期望输出。
