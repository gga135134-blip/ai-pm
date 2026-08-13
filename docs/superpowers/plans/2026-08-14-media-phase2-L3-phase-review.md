# L3 阶段复盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补上复盘三层最顶层 L3 阶段复盘——回看攒够的 L2 轮次 + 真实数据趋势，AI 摆退出信号 + 给「进阶段/原地」建议 + trait 归档/晋升候选，人拍板真动人设。

**Architecture:** 新表 `media_phase_review`（L2 的 `media_review_cycle` 保持 L2 专用）。新服务 `media_phase_review.py`：纯计算趋势/退出信号/纳入范围 + AI 给阶段建议与 trait 策展（候选绝不自动应用）。读路由（触发/详情/删除）+ **应用路由（切阶段改 persona.current_phase、trait 归档/晋升改 trait），全部人点击才发生**。UI 跟 L2 区块并列。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + 本地 Tailwind + vanilla JS。AI 走 `ask_ai`（DeepSeek 优先）。

## Global Constraints

- **零 migration**：新表走 `app/database.py` 的 `SCHEMA`（`CREATE TABLE IF NOT EXISTS`），不进 `MIGRATIONS`。
- **候选绝不自动应用**：AI 产出的阶段建议 / trait 动作只存进 `media_phase_review` 行；改 `persona.current_phase` / `trait.status` / `trait.confidence` **只在人点应用按钮时发生**。
- **四条标准动作**：①人拍板 ②诚实（阶段建议以退出信号实际数据为主依据、trait 动作引具体 L2 规律做 evidence，够不着别硬推）③只给精华（trait 动作每类 ≤3）④成本可见（`log_injection`）。
- **阶段只前进或原地**：`PHASE_ORDER = ["冷启动", "涨粉", "转化"]`。`phase_to` 只能是 `phase_from` 的**下一个**；AI 给别的（含倒退/跳级）一律回落 `phase_reco='stay'`。应用切阶段时再校验一次（目标必须是当前阶段的下一个）。人设进化以真实数据达标为主依据，多数信号未达参考线就倾向 stay。
- **L3 只策展现有 trait 不造新**（造新是 L2 的活）：`trait_actions` 的 `action` 只认 `archive`/`promote`，`trait_id` 必须在当前 active trait 集合里（过滤 AI 瞎编 id）。
- **数据门槛** `L3_MIN_L2_CYCLES = 3`：纳入 L2 轮 < 3 返回 warn 不写库；`force=True` 越过。
- **退出信号阈值是代码常量**（`COLD_VIEWS_BASELINE=3000` / `COLD_HIT_MIN=1` / `GROWTH_CONFIRMED_MIN=2`），本轮不做设置 UI。
- **改模板一律用 Edit/Write**（禁 PowerShell -replace 毁中文）。
- **JS 不把 SVG 图标塞进字符串**（`innerHTML='{{ ic.icon(...) }}'` 会 SyntaxError；按钮存 `orig` 还原）。红色系变量是 `var(--down)`，**不是** `--danger`（base.html 没定义 danger）。
- 不动 L1/L2/功能B/决策引擎逻辑；只**读** L2 cycles + persona/trait，只在人点击时**写** persona/trait。
- 测试基建：`tests/media_helpers.py` 的 `make_db()`（内存 DB + SCHEMA + MIGRATIONS）、`fake_ai(response)`、`seed_content()`。异步用 `asyncio.run`（无 pytest-asyncio）。路由测试用 `_client()`（签名 cookie 登录）+ module 级 tmp DB 隔离（`_db_mod.DB_PATH` swap + `init_db()`）。⚠️ 测试 DB **外键约束是开的**：删/建引用 persona 的行注意 FK；测试用**独立 persona id** 避免与他测共享行冲突。⚠️ `media_account` 列名是 `account_name` 不是 `handle`。跑 pytest 若假挂=残留进程，`taskkill //F //IM python.exe`。

---

### Task 1: 数据模型 `media_phase_review`

**Files:**
- Modify: `app/database.py`（`SCHEMA` 尾部，`media_review_cycle` 表之后、SCHEMA 结束的 `"""` 之前）
- Test: `tests/test_media_phase_review_schema.py`（新）

**Interfaces:**
- Produces: `media_phase_review` 表，列见下。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_phase_review_schema.py
"""L3 阶段复盘表 schema 测试。"""
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


def test_phase_review_table_has_expected_columns():
    cols = _cols("media_phase_review")
    expected = {
        "id", "persona_id", "seq", "phase_from", "l2_cycle_ids",
        "metrics_trend", "phase_signals", "phase_reco", "phase_to",
        "phase_reason", "trait_actions", "cost", "model",
        "generated_by", "created_at",
    }
    assert expected <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_schema.py -v`
Expected: FAIL（`no such table: media_phase_review`）

- [ ] **Step 3: 加表定义到 SCHEMA**

在 `app/database.py` 的 `media_review_cycle` 表定义之后、`SCHEMA` 字符串结束的 `"""` 之前，插入：

```sql
CREATE TABLE IF NOT EXISTS media_phase_review (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    seq INTEGER DEFAULT 1,
    phase_from TEXT DEFAULT '',
    l2_cycle_ids TEXT DEFAULT '[]',
    metrics_trend TEXT DEFAULT '{}',
    phase_signals TEXT DEFAULT '[]',
    phase_reco TEXT DEFAULT 'stay',
    phase_to TEXT DEFAULT '',
    phase_reason TEXT DEFAULT '',
    trait_actions TEXT DEFAULT '[]',
    cost REAL DEFAULT 0,
    model TEXT DEFAULT '',
    generated_by TEXT DEFAULT 'ai',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_media_phase_review_schema.py
git commit -m "feat(media): media_phase_review 表（L3 阶段复盘）"
```

---

### Task 2: 纯计算核心（阶段序列 / 趋势 / 退出信号 / 纳入范围 / 门槛）

**Files:**
- Create: `app/services/media_phase_review.py`
- Test: `tests/test_media_phase_review_calc.py`（新）

**Interfaces:**
- Produces:
  - 常量 `PHASE_ORDER = ["冷启动", "涨粉", "转化"]`、`L3_MIN_L2_CYCLES = 3`、`COLD_VIEWS_BASELINE = 3000`、`COLD_HIT_MIN = 1`、`GROWTH_CONFIRMED_MIN = 2`。
  - `_next_phase(phase_from: str) -> str | None`：PHASE_ORDER 下一个，终点/未知返回 None。
  - `summarize_trend(l2: list[dict]) -> dict`：`{"series":[{seq,avg_views,avg_new_fans,hit_count}]}`。
  - `phase_exit_signals(phase_from: str, l2: list[dict]) -> list[dict]`：见 §4，每项 `{signal,value,ref,met}`。
  - `async gather_l2_since(db, persona_id) -> tuple[list[dict], dict | None]`：返回（上轮 L3 之后的 L2 轮 dict 列表，含 parsed metrics_summary/hypotheses_tested/patterns；上一轮 L3 行或 None）。
  - `async _prev_l3(db, persona_id) -> dict | None`。
  - 每个 l2 dict 的形状：`{id, seq, metrics_summary(dict), hypotheses_tested(list), patterns(list), ...}`。
- Consumes: Task 1 表；L2 的 `media_review_cycle`（level='L2'）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_phase_review_calc.py
"""L3 纯计算：阶段序列/趋势/退出信号/纳入范围。"""
import asyncio
import json
import uuid
from tests.media_helpers import make_db, seed_content
from app.services import media_phase_review as pr


def test_next_phase():
    assert pr._next_phase("冷启动") == "涨粉"
    assert pr._next_phase("涨粉") == "转化"
    assert pr._next_phase("转化") is None
    assert pr._next_phase("瞎写") is None


def test_summarize_trend():
    l2 = [
        {"seq": 1, "metrics_summary": {"avg": {"views": 8000, "new_fans": 30},
                                       "hit_count": 0}},
        {"seq": 2, "metrics_summary": {"avg": {"views": 12000, "new_fans": 55},
                                       "hit_count": 1}},
    ]
    t = pr.summarize_trend(l2)
    assert t["series"][0]["avg_views"] == 8000
    assert t["series"][1]["avg_new_fans"] == 55
    assert t["series"][1]["hit_count"] == 1


def test_exit_signals_cold_phase():
    l2 = [
        {"metrics_summary": {"avg": {"views": 2000}, "hit_count": 0}},
        {"metrics_summary": {"avg": {"views": 5000}, "hit_count": 1}},
    ]
    sig = pr.phase_exit_signals("冷启动", l2)
    by = {s["signal"]: s for s in sig}
    assert by["累计爆款数"]["value"] == 1 and by["累计爆款数"]["met"] is True
    # 最近一轮均值播放 5000 >= 3000
    assert by["最近一轮均值播放"]["value"] == 5000 and by["最近一轮均值播放"]["met"] is True


def test_exit_signals_growth_phase():
    l2 = [
        {"metrics_summary": {"avg": {"new_fans": 20}},
         "hypotheses_tested": [{"verdict": "confirmed"}, {"verdict": "refuted"}]},
        {"metrics_summary": {"avg": {"new_fans": 40}},
         "hypotheses_tested": [{"verdict": "confirmed"}]},
    ]
    sig = pr.phase_exit_signals("涨粉", l2)
    by = {s["signal"]: s for s in sig}
    assert by["新增粉丝持续为正"]["met"] is True          # 20,40 全正
    assert by["累计已验证假设"]["value"] == 2 and by["累计已验证假设"]["met"] is True


def test_exit_signals_conversion_phase_empty():
    assert pr.phase_exit_signals("转化", [{"metrics_summary": {}}]) == []


def test_gather_l2_since_only_after_last_l3():
    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="seed", stage="idea")
            # 两轮 L2
            for i, cid in enumerate(["L2A", "L2B"]):
                await db.execute(
                    "INSERT INTO media_review_cycle (id,persona_id,level,seq,"
                    "metrics_summary,hypotheses_tested,patterns,created_at) "
                    "VALUES (?,?,'L2',?,?,?,?,?)",
                    (cid, "P1", i + 1, json.dumps({"avg": {"views": 100}}),
                     json.dumps([]), json.dumps([]),
                     f"2026-08-1{i}T00:00:00"))
            # 一轮旧 L3（时间在 L2A 之后、L2B 之前）
            await db.execute(
                "INSERT INTO media_phase_review (id,persona_id,seq,created_at) "
                "VALUES (?,?,?,?)", (str(uuid.uuid4()), "P1", 1,
                                     "2026-08-10T12:00:00"))
            await db.commit()
            l2, prev = await pr.gather_l2_since(db, "P1")
            ids = {c["id"] for c in l2}
            assert ids == {"L2B"}          # 只纳入上轮 L3 之后的 L2
            assert prev is not None and prev["seq"] == 1
            assert isinstance(l2[0]["metrics_summary"], dict)   # 已解析
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_calc.py -v`
Expected: FAIL（`No module named 'app.services.media_phase_review'`）

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/media_phase_review.py
"""L3 阶段复盘：人设进化层。

回看攒够的 L2 轮次 + 真实数据趋势，判断人设是否该进下一阶段、哪些 trait 该
归档/晋升。候选绝不自动应用 —— AI 提议，人点应用按钮才改人设。

阶段建议以退出信号的实际数据（账号真实已发数据算出）为主依据；数据没到参考线
就倾向 stay。只前进或原地，绝不倒退。
"""
import json
import uuid

from app.services.ai_router import ask_ai
from app.services.media_context import extract_json, log_injection

PHASE_ORDER = ["冷启动", "涨粉", "转化"]
L3_MIN_L2_CYCLES = 3
COLD_VIEWS_BASELINE = 3000
COLD_HIT_MIN = 1
GROWTH_CONFIRMED_MIN = 2


def _next_phase(phase_from: str):
    try:
        i = PHASE_ORDER.index(phase_from)
    except ValueError:
        return None
    return PHASE_ORDER[i + 1] if i + 1 < len(PHASE_ORDER) else None


def summarize_trend(l2: list) -> dict:
    series = []
    for c in l2:
        ms = c.get("metrics_summary") or {}
        avg = ms.get("avg") or {}
        series.append({
            "seq": c.get("seq"),
            "avg_views": avg.get("views", 0),
            "avg_new_fans": avg.get("new_fans", 0),
            "hit_count": ms.get("hit_count", 0),
        })
    return {"series": series}


def phase_exit_signals(phase_from: str, l2: list) -> list:
    if phase_from == "冷启动":
        cum_hit = sum((c.get("metrics_summary") or {}).get("hit_count", 0)
                      for c in l2)
        latest = (((l2[-1].get("metrics_summary") or {}).get("avg") or {})
                  .get("views", 0)) if l2 else 0
        return [
            {"signal": "累计爆款数", "value": cum_hit, "ref": COLD_HIT_MIN,
             "met": cum_hit >= COLD_HIT_MIN},
            {"signal": "最近一轮均值播放", "value": latest,
             "ref": COLD_VIEWS_BASELINE, "met": latest >= COLD_VIEWS_BASELINE},
        ]
    if phase_from == "涨粉":
        fans_pos = bool(l2) and all(
            ((c.get("metrics_summary") or {}).get("avg") or {}).get("new_fans", 0) > 0
            for c in l2)
        cum_conf = sum(
            sum(1 for h in (c.get("hypotheses_tested") or [])
                if h.get("verdict") == "confirmed")
            for c in l2)
        return [
            {"signal": "新增粉丝持续为正", "value": ("是" if fans_pos else "否"),
             "ref": "全为正", "met": fans_pos},
            {"signal": "累计已验证假设", "value": cum_conf,
             "ref": GROWTH_CONFIRMED_MIN, "met": cum_conf >= GROWTH_CONFIRMED_MIN},
        ]
    return []


async def _prev_l3(db, persona_id: str):
    cur = await db.execute(
        "SELECT * FROM media_phase_review WHERE persona_id=? "
        "ORDER BY seq DESC LIMIT 1", (persona_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def gather_l2_since(db, persona_id: str):
    """纳入上一轮 L3 之后新建的 L2 轮（无上轮 L3 → 全部 L2 轮）。"""
    prev = await _prev_l3(db, persona_id)
    if prev:
        cur = await db.execute(
            "SELECT * FROM media_review_cycle WHERE persona_id=? AND level='L2' "
            "AND created_at > ? ORDER BY seq", (persona_id, prev["created_at"]))
    else:
        cur = await db.execute(
            "SELECT * FROM media_review_cycle WHERE persona_id=? AND level='L2' "
            "ORDER BY seq", (persona_id,))
    l2 = []
    for r in await cur.fetchall():
        d = dict(r)
        for f in ("metrics_summary", "hypotheses_tested", "patterns"):
            default = "{}" if f == "metrics_summary" else "[]"
            try:
                d[f] = json.loads(d.get(f) or default)
            except Exception:
                d[f] = {} if f == "metrics_summary" else []
        l2.append(d)
    return l2, prev
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_calc.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_phase_review.py tests/test_media_phase_review_calc.py
git commit -m "feat(media): L3 纯计算核心（阶段序列/趋势/退出信号/纳入范围）"
```

---

### Task 3: `run_l3_review` 编排 + `L3_SYSTEM` + 校验 + 读取助手

**Files:**
- Modify: `app/services/media_phase_review.py`
- Test: `tests/test_media_phase_review_run.py`（新）

**Interfaces:**
- Consumes: Task 2 的纯计算函数；`ask_ai`/`extract_json`/`log_injection`。
- Produces:
  - `async run_l3_review(db, persona_id, model="auto", force=False) -> dict`：门槛不足返回 `{"ok": False, "warn":..., "count": N}`；成功写一行返回 `{"ok": True, "review_id","seq","count","cost","model"}`。
  - `async list_phase_reviews(db, persona_id) -> list[dict]`（seq 降序，含 `l2_count`）。
  - `async get_phase_review(db, review_id) -> dict | None`（JSON 字段解析）。
  - `L3_SYSTEM: str`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_phase_review_run.py
"""L3 编排：门槛拦截、写库、AI 输出校验（phase_to 只能下一个、trait_id 过滤）。"""
import asyncio
import json
import uuid
from tests.media_helpers import make_db, seed_content
from app.services import media_phase_review as pr


async def _seed_l2(db, persona_id, cid, seq, views=5000, new_fans=20,
                   hit=1, confirmed=1, created_at=None):
    await db.execute(
        "INSERT INTO media_review_cycle (id,persona_id,level,seq,metrics_summary,"
        "hypotheses_tested,patterns,created_at) VALUES (?,?,'L2',?,?,?,?,COALESCE(?,datetime('now')))",
        (cid, persona_id, seq,
         json.dumps({"avg": {"views": views, "new_fans": new_fans},
                     "hit_count": hit}),
         json.dumps([{"verdict": "confirmed"}] * confirmed),
         json.dumps([{"pattern": "带故事更火"}]), created_at))
    await db.commit()


def test_below_threshold_warns_no_write():
    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="s", stage="idea")
            await _seed_l2(db, "P1", "a", 1)
            await _seed_l2(db, "P1", "b", 2)          # 只 2 轮 < 3
            res = await pr.run_l3_review(db, "P1")
            assert res["ok"] is False and res["count"] == 2 and "warn" in res
            cur = await db.execute("SELECT COUNT(*) n FROM media_phase_review")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(go())


def _seed_persona_phase(db, pid, phase):
    return db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "一句话", phase))


def test_run_writes_row_validates_phase_and_traits(monkeypatch):
    fake = {
        "phase_reco": "advance", "phase_to": "涨粉",
        "phase_reason": "累计爆款达标、播放站上基线",
        "trait_actions": [
            {"trait_id": "T-REAL", "action": "promote",
             "evidence": "近轮规律印证", "reason": "反复印证"},
            {"trait_id": "T-FAKE", "action": "promote", "evidence": "x",
             "reason": "y"},                                  # 瞎编 id，应被过滤
            {"trait_id": "T-REAL", "action": "bogus"},        # 非法 action，过滤
        ],
    }

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek",
                "tokens": 20, "cost": 0.01}
    monkeypatch.setattr(pr, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await _seed_persona_phase(db, "P1", "冷启动")
            await db.execute(
                "INSERT INTO media_persona_trait (id,persona_id,dimension,content,"
                "status) VALUES ('T-REAL','P1','signature','爱反问','active')")
            for i in range(3):
                await _seed_l2(db, "P1", f"c{i}", i + 1)
            res = await pr.run_l3_review(db, "P1")
            assert res["ok"] is True and res["count"] == 3
            row = await pr.get_phase_review(db, res["review_id"])
            assert row["phase_from"] == "冷启动"
            assert row["phase_reco"] == "advance" and row["phase_to"] == "涨粉"
            acts = row["trait_actions"]
            assert len(acts) == 1 and acts[0]["trait_id"] == "T-REAL"   # 只剩合法的
            # 未自动改人设
            cur = await db.execute("SELECT current_phase FROM media_persona WHERE id='P1'")
            assert (await cur.fetchone())["current_phase"] == "冷启动"
            cur = await db.execute("SELECT status FROM media_persona_trait WHERE id='T-REAL'")
            assert (await cur.fetchone())["status"] == "active"
            # 成本记 log_injection
            cur = await db.execute("SELECT COUNT(*) n FROM media_injection_log "
                                   "WHERE ai_type='media_phase_review'")
            assert (await cur.fetchone())["n"] == 1
        finally:
            await db.close()
    asyncio.run(go())


def test_illegal_phase_to_falls_back_to_stay(monkeypatch):
    fake = {"phase_reco": "advance", "phase_to": "转化",   # 冷启动的下一个是涨粉，非转化
            "phase_reason": "跳级", "trait_actions": []}

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek", "tokens": 5, "cost": 0}
    monkeypatch.setattr(pr, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await _seed_persona_phase(db, "P1", "冷启动")
            for i in range(3):
                await _seed_l2(db, "P1", f"c{i}", i + 1)
            res = await pr.run_l3_review(db, "P1")
            row = await pr.get_phase_review(db, res["review_id"])
            assert row["phase_reco"] == "stay" and row["phase_to"] == ""  # 跳级被回落
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_run.py -v`
Expected: FAIL（`AttributeError: ... has no attribute 'run_l3_review'`）

- [ ] **Step 3: Write minimal implementation**

在 `app/services/media_phase_review.py` 末尾追加：

```python
L3_SYSTEM = """你站在阶段的高度，看一个人设阶段积累的规律与真实数据趋势，
判断人设是否该进化。

铁律（违反即失败）：
1. 你只提建议和动作候选，绝不假装能改人设；一切靠人点应用按钮。
2. 诚实——阶段建议必须以「退出信号」的实际数据为主要依据，trait 动作必须引
   具体 L2 规律做 evidence；数据够不着就别硬推。
3. 只给精华：trait 归档/晋升每类 ≤3 条，别把整个注册表翻底朝天。
4. 别把偶然当趋势——一轮好不代表阶段到了。

阶段建议：
- 结合退出信号的实际数据 + L2 规律，给 phase_reco：advance（进下一阶段）或
  stay（原地）。多数信号未达参考线就 stay——人设进化要真实数据达到程度才发生。
- 只前进或原地：phase_to 只能是当前阶段的下一个；绝不建议倒退或跳级。

trait 策展（只对给定的现有注册表，不造新——造新是 L2 的活）：
- archive：陈旧（近几轮 L2 规律不再印证）或被新规律矛盾。
- promote：被近几轮 L2 规律反复印证，值得提置信。
- 每条给 trait_id（必须来自给定清单）+ action(archive/promote) + evidence + reason。

只输出严格 JSON：
{"phase_reco":"advance|stay","phase_to":"","phase_reason":"",
 "trait_actions":[{"trait_id":"","action":"archive|promote","evidence":"","reason":""}]}"""


def _build_l3_prompt(phase_from, next_phase, signals, l2, traits):
    parts = [f"【当前阶段】{phase_from}（下一阶段：{next_phase or '已是终点，只可 stay'}）"]
    parts.append("【阶段退出信号·实际数据】")
    for s in signals:
        parts.append(f"- {s['signal']}：实际 {s['value']}，参考线 {s['ref']}，"
                     f"{'达标' if s['met'] else '未达'}")
    if not signals:
        parts.append("（终点阶段，无退出信号，只做 trait 策展）")
    parts.append(f"【纳入 {len(l2)} 轮 L2 的规律与验证】")
    for c in l2:
        pats = "；".join(p.get("pattern", "") for p in (c.get("patterns") or []))
        conf = sum(1 for h in (c.get("hypotheses_tested") or [])
                   if h.get("verdict") == "confirmed")
        parts.append(f"- 第{c.get('seq')}轮：规律[{pats}]，已验证假设 {conf} 条")
    parts.append("【当前人设条目（只在这些里做 archive/promote，别造新）】")
    for t in traits:
        parts.append(f"- trait_id={t['id']}｜[{t['dimension']}] {t['content']}"
                     f"（置信 {t['confidence']}）")
    parts.append("请判断阶段是否该进化，并对现有条目给策展动作。")
    return "\n".join(parts)


async def run_l3_review(db, persona_id: str, model: str = "auto",
                        force: bool = False) -> dict:
    l2, prev = await gather_l2_since(db, persona_id)
    count = len(l2)
    if count < L3_MIN_L2_CYCLES and not force:
        return {"ok": False, "count": count,
                "warn": f"才 {count} 轮 L2，还看不出阶段级趋势，"
                        f"建议攒到 ~{L3_MIN_L2_CYCLES} 轮再跑"}

    cur = await db.execute(
        "SELECT current_phase FROM media_persona WHERE id=?", (persona_id,))
    prow = await cur.fetchone()
    phase_from = prow["current_phase"] if prow else ""
    next_phase = _next_phase(phase_from)

    trend = summarize_trend(l2)
    signals = phase_exit_signals(phase_from, l2)

    cur = await db.execute(
        "SELECT id, dimension, content, confidence FROM media_persona_trait "
        "WHERE persona_id=? AND status='active' ORDER BY confidence DESC",
        (persona_id,))
    traits = [dict(r) for r in await cur.fetchall()]
    active_ids = {t["id"] for t in traits}

    prompt = _build_l3_prompt(phase_from, next_phase, signals, l2, traits)
    result = await ask_ai(prompt, model=model, task_type="media_phase_review",
                          system_prompt=L3_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "count": count, "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "count": count, "error": "阶段复盘结果无法解析",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 校验阶段建议：phase_to 只能是当前阶段的下一个，否则回落 stay
    reco = obj.get("phase_reco")
    phase_to = obj.get("phase_to") or ""
    if reco == "advance" and phase_to and phase_to == next_phase:
        phase_reco, phase_to_final = "advance", phase_to
    else:
        phase_reco, phase_to_final = "stay", ""

    # 校验 trait 动作：trait_id 必须在 active 集合、action 白名单
    trait_actions = []
    for a in (obj.get("trait_actions") or []):
        if not isinstance(a, dict):
            continue
        tid = a.get("trait_id")
        if tid in active_ids and a.get("action") in ("archive", "promote"):
            # 补 dimension/content 便于报告页展示
            t = next((x for x in traits if x["id"] == tid), {})
            trait_actions.append({
                "trait_id": tid, "dimension": t.get("dimension", ""),
                "content": t.get("content", ""), "action": a.get("action"),
                "evidence": a.get("evidence", ""), "reason": a.get("reason", "")})

    seq = (prev["seq"] + 1) if prev else 1
    rid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_phase_review "
        "(id,persona_id,seq,phase_from,l2_cycle_ids,metrics_trend,phase_signals,"
        " phase_reco,phase_to,phase_reason,trait_actions,cost,model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, persona_id, seq, phase_from,
         json.dumps([c["id"] for c in l2], ensure_ascii=False),
         json.dumps(trend, ensure_ascii=False),
         json.dumps(signals, ensure_ascii=False),
         phase_reco, phase_to_final, obj.get("phase_reason", ""),
         json.dumps(trait_actions, ensure_ascii=False),
         result.get("cost", 0), result.get("model", "")))
    await db.commit()
    await log_injection(db, "", "media_phase_review",
                        [c["id"] for c in l2], result.get("tokens", 0))
    return {"ok": True, "review_id": rid, "seq": seq, "count": count,
            "cost": result.get("cost", 0), "model": result.get("model", "")}


_JSON_FIELDS = ("l2_cycle_ids", "metrics_trend", "phase_signals", "trait_actions")


async def list_phase_reviews(db, persona_id: str) -> list:
    cur = await db.execute(
        "SELECT id, seq, phase_from, phase_reco, phase_to, l2_cycle_ids, "
        "cost, model, created_at FROM media_phase_review "
        "WHERE persona_id=? ORDER BY seq DESC", (persona_id,))
    out = []
    for r in await cur.fetchall():
        d = dict(r)
        try:
            d["l2_count"] = len(json.loads(d.get("l2_cycle_ids") or "[]"))
        except Exception:
            d["l2_count"] = 0
        out.append(d)
    return out


async def get_phase_review(db, review_id: str):
    cur = await db.execute(
        "SELECT * FROM media_phase_review WHERE id=?", (review_id,))
    row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    for f in _JSON_FIELDS:
        default = "{}" if f == "metrics_trend" else "[]"
        try:
            d[f] = json.loads(d.get(f) or default)
        except Exception:
            d[f] = {} if f == "metrics_trend" else []
    return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_run.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_phase_review.py tests/test_media_phase_review_run.py
git commit -m "feat(media): run_l3_review 编排 + L3_SYSTEM + AI输出校验（阶段只进一格/trait只策展现有）"
```

---

### Task 4: 读路由（触发 / 详情 / 删除）

**Files:**
- Modify: `app/api/media.py`（import + 3 路由）
- Test: `tests/test_media_phase_review_routes.py`（新）

**Interfaces:**
- Consumes: `run_l3_review` / `get_phase_review`。
- Produces:
  - `POST /media/persona/{pid}/l3-review`（Form `force: int = 0`）→ JSON。
  - `GET /media/phase-review/{rid}` → HTML（`media_phase_review.html`）。
  - `POST /media/phase-review/{rid}/delete` → 删行，跳 `/media/persona`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_phase_review_routes.py
"""L3 读路由：触发（stub）、详情、删除。"""
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
    tmp = tmp_path_factory.mktemp("l3_routes_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid, phase="冷启动"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "一句话", phase))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_trigger_passes_through(monkeypatch):
    _seed_persona("LP3")

    async def fake(db, persona_id, model="auto", force=False):
        return {"ok": False, "warn": "才 1 轮 L2", "count": 1}
    monkeypatch.setattr("app.api.media.run_l3_review", fake)
    r = _client().post("/media/persona/LP3/l3-review", data={"force": 0})
    assert r.status_code == 200 and r.json()["warn"].startswith("才")


def test_detail_renders():
    _seed_persona("LP3D")
    rid = str(uuid.uuid4())

    async def go():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_phase_review (id,persona_id,seq,phase_from,"
                "phase_reco,phase_to,phase_reason,phase_signals,metrics_trend,"
                "trait_actions,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (rid, "LP3D", 1, "冷启动", "advance", "涨粉", "数据达标",
                 json.dumps([{"signal": "累计爆款数", "value": 2, "ref": 1,
                              "met": True}]),
                 json.dumps({"series": []}), json.dumps([])))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())
    r = _client().get(f"/media/phase-review/{rid}")
    assert r.status_code == 200 and "数据达标" in r.text


def test_delete_removes_and_redirects():
    _seed_persona("LP3X")
    rid = str(uuid.uuid4())

    async def seed():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_phase_review (id,persona_id,seq,phase_from,"
                "created_at) VALUES (?,?,?,?,datetime('now'))",
                (rid, "LP3X", 1, "冷启动"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    r = _client().post(f"/media/phase-review/{rid}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/media/persona"

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) n FROM media_phase_review WHERE id=?", (rid,))
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(check())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_routes.py -v`
Expected: FAIL（触发/详情/删除 404）

- [ ] **Step 3: Write minimal implementation**

在 `app/api/media.py` import 区（`from app.services.media_review_cycle import ...` 附近）加：

```python
from app.services.media_phase_review import (
    run_l3_review, list_phase_reviews, get_phase_review)
```

在文件"复盘"分区附近加三个路由：

```python
@router.post("/media/persona/{pid}/l3-review")
async def persona_l3_review(pid: str, force: int = Form(0)):
    db = await get_db()
    try:
        try:
            result = await run_l3_review(db, pid, force=bool(force))
        except Exception as e:
            log.exception("L3 阶段复盘失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.get("/media/phase-review/{rid}", response_class=HTMLResponse)
async def phase_review_detail(rid: str, request: Request):
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
    finally:
        await db.close()
    if not rev:
        return RedirectResponse("/media/persona", status_code=302)
    return _tpl(request, "media_phase_review.html", {"rev": rev})


@router.post("/media/phase-review/{rid}/delete")
async def phase_review_delete(rid: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM media_phase_review WHERE id=?", (rid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/persona", status_code=302)
```

**注意：** 详情路由渲染 `media_phase_review.html`，该模板 Task 6 才建。本任务的详情测试需要它存在——建一个**最小占位** `app/templates/media_phase_review.html`（extends base.html，渲染 `rev.phase_reason` 即可）让测试过，Task 6 替换成完整报告页。占位里写注释标明。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_routes.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/api/media.py app/templates/media_phase_review.html tests/test_media_phase_review_routes.py
git commit -m "feat(media): L3 触发/详情/删除路由（详情页最小占位待Task6）"
```

---

### Task 5: 应用路由（切阶段 / trait 归档晋升 —— 真动人设，人点击才发生）

**Files:**
- Modify: `app/api/media.py`（2 应用路由）
- Test: `tests/test_media_phase_review_apply.py`（新）

**Interfaces:**
- Consumes: `get_phase_review`、`media_phase_review._next_phase`。
- Produces:
  - `POST /media/phase-review/{rid}/apply-phase` → 校验 `rev.phase_to` 是 persona 当前阶段的下一个才 `UPDATE media_persona SET current_phase`；跳回报告页。
  - `POST /media/phase-review/{rid}/apply-trait`（Form `trait_id`, `action`）→ 校验 trait 属该 persona；archive→`status='archived'`，promote→`confidence=MIN(5,confidence+1)`；跳回报告页。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_phase_review_apply.py
"""L3 应用路由：切阶段/trait 归档晋升 —— 真改人设，且带校验。"""
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
    tmp = tmp_path_factory.mktemp("l3_apply_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


async def _seed(db, pid, phase, rid, phase_to):
    await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "x", phase))
    await db.execute(
        "INSERT INTO media_phase_review (id,persona_id,seq,phase_from,phase_reco,"
        "phase_to,created_at) VALUES (?,?,?,?, 'advance',?,datetime('now'))",
        (rid, pid, 1, phase, phase_to))
    await db.commit()


def test_apply_phase_advances_when_legal():
    pid, rid = "AP1", str(uuid.uuid4())
    asyncio.run(_run_seed(pid, "冷启动", rid, "涨粉"))
    r = _client().post(f"/media/phase-review/{rid}/apply-phase",
                       follow_redirects=False)
    assert r.status_code in (302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
            assert (await cur.fetchone())["current_phase"] == "涨粉"
        finally:
            await db.close()
    asyncio.run(check())


def test_apply_phase_rejects_illegal_jump():
    pid, rid = "AP2", str(uuid.uuid4())
    asyncio.run(_run_seed(pid, "冷启动", rid, "转化"))   # 跳级，非法
    _client().post(f"/media/phase-review/{rid}/apply-phase",
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
            assert (await cur.fetchone())["current_phase"] == "冷启动"   # 没变
        finally:
            await db.close()
    asyncio.run(check())


def test_apply_trait_archive_and_promote():
    pid, rid = "AP3", str(uuid.uuid4())
    tid_a, tid_p = "TA", "TP"
    asyncio.run(_run_seed(pid, "涨粉", rid, "转化"))

    async def seed_traits():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona_trait (id,persona_id,dimension,content,"
                "status,confidence) VALUES (?,?, 'signature','旧','active',3)", (tid_a, pid))
            await db.execute(
                "INSERT INTO media_persona_trait (id,persona_id,dimension,content,"
                "status,confidence) VALUES (?,?, 'signature','好','active',3)", (tid_p, pid))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_traits())

    _client().post(f"/media/phase-review/{rid}/apply-trait",
                   data={"trait_id": tid_a, "action": "archive"},
                   follow_redirects=False)
    _client().post(f"/media/phase-review/{rid}/apply-trait",
                   data={"trait_id": tid_p, "action": "promote"},
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_persona_trait WHERE id=?", (tid_a,))
            assert (await cur.fetchone())["status"] == "archived"
            cur = await db.execute("SELECT confidence FROM media_persona_trait WHERE id=?", (tid_p,))
            assert (await cur.fetchone())["confidence"] == 4    # 3+1
        finally:
            await db.close()
    asyncio.run(check())


async def _run_seed(pid, phase, rid, phase_to):
    db = await get_db()
    try:
        await _seed(db, pid, phase, rid, phase_to)
    finally:
        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_apply.py -v`
Expected: FAIL（apply 路由 404）

- [ ] **Step 3: Write minimal implementation**

在 `app/api/media.py` import 区把 Task 4 的 import 补上 `_next_phase`（或单独 import）：

```python
from app.services.media_phase_review import (
    run_l3_review, list_phase_reviews, get_phase_review, _next_phase)
```

加两个应用路由：

```python
@router.post("/media/phase-review/{rid}/apply-phase")
async def phase_review_apply_phase(rid: str):
    """人拍板：把建议的阶段切换真正写进 persona。仅当目标是当前阶段的下一个。"""
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
        if rev:
            cur = await db.execute(
                "SELECT current_phase FROM media_persona WHERE id=?",
                (rev["persona_id"],))
            prow = await cur.fetchone()
            cur_phase = prow["current_phase"] if prow else ""
            target = rev.get("phase_to") or ""
            if target and target == _next_phase(cur_phase):
                await db.execute(
                    "UPDATE media_persona SET current_phase=? WHERE id=?",
                    (target, rev["persona_id"]))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/phase-review/{rid}", status_code=302)


@router.post("/media/phase-review/{rid}/apply-trait")
async def phase_review_apply_trait(rid: str, trait_id: str = Form(...),
                                   action: str = Form(...)):
    """人拍板：归档/晋升一条人设条目。"""
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
        if rev and action in ("archive", "promote"):
            # 校验 trait 属于该 review 的 persona
            cur = await db.execute(
                "SELECT confidence FROM media_persona_trait "
                "WHERE id=? AND persona_id=?", (trait_id, rev["persona_id"]))
            trow = await cur.fetchone()
            if trow:
                if action == "archive":
                    await db.execute(
                        "UPDATE media_persona_trait SET status='archived' WHERE id=?",
                        (trait_id,))
                else:
                    newc = min(5, (trow["confidence"] or 3) + 1)
                    await db.execute(
                        "UPDATE media_persona_trait SET confidence=? WHERE id=?",
                        (newc, trait_id))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/phase-review/{rid}", status_code=302)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_apply.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/api/media.py tests/test_media_phase_review_apply.py
git commit -m "feat(media): L3 应用路由（切阶段带合法性校验/trait归档晋升，人点击才改人设）"
```

---

### Task 6: UI（人设页 L3 区块 + 报告详情页）

**Files:**
- Create/Replace: `app/templates/media_phase_review.html`（替换 Task 4 占位）
- Modify: `app/api/media.py`（`persona_detail` context 加 `l3_reviews`）
- Modify: `app/templates/media_persona.html`（L2 区块下并列加 L3 区块 + runL3 JS）

**Interfaces:**
- Consumes: `list_phase_reviews`（人设页）、`get_phase_review`（详情页 `rev`）、`POST /media/persona/{pid}/l3-review`、apply/delete 路由。

- [ ] **Step 1: 人设页 context 加 l3_reviews**

在 `app/api/media.py` 的 `persona_detail`（约 :134，`l2_cycles = await list_cycles(...)` 那行旁）加：

```python
        l3_reviews = await list_phase_reviews(db, pid) if persona else []
```
并把 `list_phase_reviews` 加进 Task 4/5 的 import。再把 context（约 :151）里加 `"l3_reviews": l3_reviews,`。

- [ ] **Step 2: 报告详情页模板**

用完整版覆盖 `app/templates/media_phase_review.html`：

```html
{% extends "base.html" %}
{% import "_icons.html" as ic %}
{% block title %}L3 阶段复盘 #{{ rev.seq }}{% endblock %}
{% block topbar %}
<span class="crumb"><a href="/media" style="color:inherit;text-decoration:none">自媒体</a> {{ ic.icon('chevron') }} <b>阶段复盘 #{{ rev.seq }}</b></span>
{% endblock %}
{% block content %}
<div style="max-width:820px; margin:0 auto">
  <p style="font-size:13px"><a href="/media/persona" style="color:var(--ink-3)">← 人设</a></p>
  <h2 style="margin-bottom:4px">第 {{ rev.seq }} 轮阶段复盘（L3）</h2>
  <p style="color:var(--ink-3); font-size:13px; margin-bottom:16px">
    当前阶段 {{ rev.phase_from }} · 纳入 {{ rev.l2_cycle_ids|length }} 轮 L2
    · 成本 ${{ '%.4f'|format(rev.cost or 0) }} · {{ rev.model }}
  </p>

  <div class="module">
    <div class="mh"><span class="ttl">🧭 阶段建议</span></div>
    <div class="inner">
      {% if rev.phase_reco == 'advance' and rev.phase_to %}
      <p style="font-size:14px; margin-bottom:8px"><b>建议：进入「{{ rev.phase_to }}」阶段</b></p>
      <p style="font-size:13px; color:var(--ink-3); margin-bottom:10px">{{ rev.phase_reason }}</p>
      <form method="post" action="/media/phase-review/{{ rev.id }}/apply-phase"
            onsubmit="return confirm('确认把人设切换到「{{ rev.phase_to }}」阶段？这会改变 AI 给选题打分的权重。')">
        <button type="submit" class="btn primary">✅ 应用切换到 {{ rev.phase_to }} 阶段</button>
      </form>
      {% else %}
      <p style="font-size:14px"><b>建议：原地（暂不进阶段）</b></p>
      <p style="font-size:13px; color:var(--ink-3)">{{ rev.phase_reason }}</p>
      {% endif %}
    </div>
  </div>

  <div class="module" style="margin-top:12px">
    <div class="mh"><span class="ttl">📶 阶段退出信号</span></div>
    <div class="inner">
      {% for s in rev.phase_signals %}
      <div style="display:flex; justify-content:space-between; font-size:13px; padding:4px 0; border-bottom:1px solid var(--border)">
        <span>{{ s.signal }}</span>
        <span>实际 {{ s.value }} / 参考 {{ s.ref }}
          <b style="color:{{ 'var(--up)' if s.met else 'var(--ink-3)' }}">{{ '达标' if s.met else '未达' }}</b></span>
      </div>
      {% else %}<div class="empty" style="text-align:left; padding:0">终点阶段，无退出信号</div>{% endfor %}
    </div>
  </div>

  <div class="module" style="margin-top:12px">
    <div class="mh"><span class="ttl">📈 各 L2 轮趋势</span></div>
    <div class="inner">
      {% for p in rev.metrics_trend.series %}
      <div style="font-size:13px; padding:3px 0">第{{ p.seq }}轮 L2：均值播放 {{ p.avg_views }}，新增粉 {{ p.avg_new_fans }}，爆款 {{ p.hit_count }}</div>
      {% else %}<div class="empty" style="text-align:left; padding:0">无趋势数据</div>{% endfor %}
    </div>
  </div>

  <div class="module" style="margin-top:12px">
    <div class="mh"><span class="ttl">🎯 人设条目策展（人拍板）</span></div>
    <div class="inner">
      {% for a in rev.trait_actions %}
      <div style="border:1px solid var(--border); border-radius:8px; padding:10px; margin-bottom:8px">
        <div style="font-size:13.5px"><b>[{{ a.action == 'archive' and '归档' or '晋升' }}]</b> [{{ a.dimension }}] {{ a.content }}</div>
        <div style="font-size:12px; color:var(--ink-3); margin:4px 0">依据：{{ a.evidence }}｜{{ a.reason }}</div>
        <form method="post" action="/media/phase-review/{{ rev.id }}/apply-trait" style="display:inline">
          <input type="hidden" name="trait_id" value="{{ a.trait_id }}">
          <input type="hidden" name="action" value="{{ a.action }}">
          <button type="submit" class="btn" style="font-size:12.5px;
            {% if a.action == 'archive' %}color:var(--down); border-color:var(--down){% endif %}">
            {{ a.action == 'archive' and '归档这条' or '晋升这条' }}</button>
        </form>
      </div>
      {% else %}<div class="empty" style="text-align:left; padding:0">这轮没提策展动作</div>{% endfor %}
    </div>
  </div>

  <form method="post" action="/media/phase-review/{{ rev.id }}/delete"
        onsubmit="return confirm('删除第 {{ rev.seq }} 轮阶段复盘？')" style="margin-top:16px">
    <button type="submit" class="btn" style="font-size:12.5px; color:var(--down); border-color:var(--down)">🗑 删除这轮</button>
  </form>
</div>
{% endblock %}
```

（渲染前 grep `base.html` 确认 `.module/.mh/.inner/.btn/.btn.primary/.empty`、`var(--up)/var(--down)/var(--border)` 都在——Task 5/L2 已用同款，应齐。）

- [ ] **Step 3: 人设页 L3 区块 + runL3 JS**

在 `app/templates/media_persona.html` 的 L2 module（`</div>` 结束于约 :193）之后，用 Edit 插入并列的 L3 module：

```html
    <div class="module" style="margin-top:12px">
      <div class="mh"><span class="ttl" style="font-size:14px">🧭 阶段复盘（L3）</span></div>
      <div class="inner">
        <p style="font-size:12px; color:var(--ink-3); margin-bottom:10px">回看攒够的周期复盘，判断人设该不该进下一阶段</p>
        <button id="l3-btn" class="btn primary" style="width:100%; justify-content:center" onclick="runL3()">跑一轮阶段复盘</button>
        <div id="l3-msg" style="font-size:13px; margin-top:8px; color:var(--ink-3)"></div>
        <div style="margin-top:10px">
          {% for r in l3_reviews %}
          <a href="/media/phase-review/{{ r.id }}" style="display:block; font-size:13px; padding:6px 0; border-bottom:1px solid var(--border); color:var(--ink-1); text-decoration:none">
            第 {{ r.seq }} 轮 · {{ r.phase_from }}{% if r.phase_reco == 'advance' %} → {{ r.phase_to }}{% endif %} · 纳入 {{ r.l2_count }} 轮L2 · {{ r.created_at }}</a>
          {% else %}
          <div class="empty" style="text-align:left; padding:0">还没跑过阶段复盘</div>
          {% endfor %}
        </div>
      </div>
    </div>
```

在 `runL2` 函数之后（约 :266，`</script>` 之前）加 `runL3`（镜像 runL2，跳 phase-review）：

```javascript
async function runL3(force){
  const btn = document.getElementById('l3-btn');
  const msg = document.getElementById('l3-msg');
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = '正在复盘…'; msg.textContent = '';
  try {
    const pid = "{{ persona.id }}";
    const body = new URLSearchParams(); body.set('force', force ? 1 : 0);
    const r = await fetch(`/media/persona/${pid}/l3-review`, {method:'POST', body});
    const d = await r.json();
    if (d.ok) { location.href = `/media/phase-review/${d.review_id}`; return; }
    if (d.warn) {
      msg.innerHTML = d.warn + ' <a href="#" onclick="runL3(true);return false;">仍要跑</a>';
    } else { msg.textContent = '失败：' + (d.error || ''); }
  } catch(e) { msg.textContent = '出错：' + e; }
  finally { btn.disabled = false; btn.textContent = orig; }
}
```

（**不要把 SVG 塞进 JS 字符串**；`persona.id` 变量名对齐人设页现有用法。）

- [ ] **Step 4: 浏览器冒烟 + 全套回归**

全套回归：
Run: `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}`
Expected: 全绿（251 基线 + L3 新增测试）。假挂=残留进程 `taskkill //F //IM python.exe` 后重跑。

浏览器冒烟（controller 亲跑，本任务实现者可只做 TestClient 渲染检查）：人设页 `/media/persona/{pid}` 出现「🧭 阶段复盘（L3）」区块、无 console 错；给某 persona 播一条 media_phase_review（含 advance + 信号 + trait_actions）打开 `/media/phase-review/{rid}` 看阶段建议卡/信号表/趋势/策展列表/应用按钮都渲染、无 Jinja/500。

- [ ] **Step 5: Commit**

```bash
git add app/templates/media_phase_review.html app/templates/media_persona.html app/api/media.py
git commit -m "feat(media): L3 UI（人设页阶段复盘区块+历史列表+报告详情页含应用按钮）"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 表→Task 1；§4 退出信号+§5 服务（纯计算/编排/L3_SYSTEM/校验/读取）→Task 2+3；§6 读路由→Task 4、应用路由→Task 5；§7 UI→Task 6；§8 质量→各 Task TDD + Task 6 全套回归 + controller 浏览器冒烟。
- **"数据达标才进阶段"（用户强调）：** 退出信号纯计算自真实 L2 数据（Task 2）；L3_SYSTEM 要 AI 数据没达标就 stay（Task 3）；phase_to 只能下一个，run 里校验回落 stay（Task 3 `test_illegal_phase_to_falls_back_to_stay`），apply-phase 再校验一次（Task 5 `test_apply_phase_rejects_illegal_jump`）。双重闸。
- **候选绝不自动应用：** run_l3_review 只写 media_phase_review 行，不改 persona/trait（Task 3 断言 current_phase/trait 未变）；改动只在 apply 路由（Task 5）人点击时发生。
- **类型一致：** `run_l3_review`/`gather_l2_since`/`summarize_trend`/`phase_exit_signals`/`_next_phase`/`get_phase_review`/`list_phase_reviews` 签名 Task 2/3 定义，Task 4/5/6 一致消费。`review_id`（返回）↔ `rev`（模板）↔ `rid`（路由参数）命名一致。
- **无占位：** 每 step 含完整代码/命令/期望。
