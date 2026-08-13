# L3 锚点策展（L3-v2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把「锚点策展」并进现有 L3 阶段复盘——AI 对每个 active 锚点给定性观察 + 建议动作（跑通/放弃/保持），人点应用改 `media_anchor.status`，让 L3 成为完整人设进化（阶段+trait+锚点）。

**Architecture:** 在现有 `media_phase_review` 表加一列 `anchor_actions`（idempotent ALTER）。扩展现有 `run_l3_review`（加载 active 锚点 + 弱信号 + AI 段 + 校验 + 存），镜像已落地的 apply-trait 加 `apply-anchor` 路由，报告页加第四段。诚实：没转化数据就不装数据驱动，AI 给定性观察，人拍板。

**Tech Stack:** Python + FastAPI + aiosqlite + Jinja2 + vanilla JS。AI 走现有 L3 的 `ask_ai(task_type="media_phase_review")`。

## Global Constraints

- **不打架**：复用现成 `media_anchor` 表 + `/media/anchor` 页；锚点应用镜像 apply-trait 同一套人拍板模式；决策引擎消费 status 的逻辑（proven1.0/validating0.7/dropped0.3 + dropped_drift）**一行不改**。
- **不麻烦人**：AI 只对值得动的锚点提 `to_proven`/`to_dropped`（有按钮）；`keep` 只观察无按钮；软上限每类 ≤3。
- **有边界**：只加载 `validating`+`proven` 锚点，**不碰 `dropped`**（不提议复活）；apply-anchor 目标只能 `proven`/`dropped`，校验锚点归属（SQL 谓词 `WHERE id=? AND persona_id=?`）。
- **候选绝不自动应用**：`run_l3_review` 只写 `media_phase_review.anchor_actions`；改 `media_anchor.status` 只在人点 apply-anchor 时发生。
- **诚实**：`L3_SYSTEM` 锚点段明说 AI 看不到真实成交，只给定性观察 + attention 弱信号，成没成要人按真实成交确认。
- **不动**决策引擎 / L1 / L2 / 已有 phase·trait 逻辑；只**读** media_anchor/media_topic，只在人点 apply-anchor 时**写** media_anchor.status。
- 改模板用 Edit/Write（禁 PowerShell -replace）。JS 不塞 SVG 进字符串。红色 `var(--down)`（不是 --danger）。
- 迁移：`MIGRATIONS` 追加 idempotent ALTER；测试 DB 用 `make_db()`（应用 SCHEMA+MIGRATIONS，try/except 忽略已加列）。测试 DB **外键约束开着**，用独立 persona id。跑 pytest 假挂=残留进程 `taskkill //F //IM python.exe`。

---

### Task 1: 迁移加列 + `count_topics_serving` 纯函数

**Files:**
- Modify: `app/database.py`（`MIGRATIONS` 追加一条）
- Modify: `app/services/media_phase_review.py`（加 `count_topics_serving`）
- Test: `tests/test_media_phase_review_calc.py`（追加 2 测）

**Interfaces:**
- Produces: `media_phase_review.anchor_actions` 列；`count_topics_serving(anchor_id: str, topics: list[dict]) -> int`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_media_phase_review_calc.py` 末尾追加：

```python
def test_count_topics_serving():
    from app.services import media_phase_review as pr
    topics = [
        {"anchor_ids": '["a1","a2"]'},      # JSON 字符串形式（DB 原样）
        {"anchor_ids": ["a1"]},             # list 形式
        {"anchor_ids": '[]'},
        {"anchor_ids": None},
    ]
    assert pr.count_topics_serving("a1", topics) == 2
    assert pr.count_topics_serving("a2", topics) == 1
    assert pr.count_topics_serving("zzz", topics) == 0


def test_phase_review_has_anchor_actions_column():
    import asyncio
    from tests.media_helpers import make_db
    async def go():
        db = await make_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_phase_review)")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    assert "anchor_actions" in asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_calc.py -k "count_topics_serving or anchor_actions_column" -v`
Expected: FAIL（`count_topics_serving` 不存在 / 列不存在）

- [ ] **Step 3: 加迁移 + 纯函数**

在 `app/database.py` 的 `MIGRATIONS` 列表末尾（最后一条 ALTER 之后、`]` 之前）追加：

```python
    "ALTER TABLE media_phase_review ADD COLUMN anchor_actions TEXT DEFAULT '[]'",
```

在 `app/services/media_phase_review.py` 顶部（`_next_phase` 定义之后）加纯函数：

```python
def count_topics_serving(anchor_id: str, topics: list) -> int:
    """近期有几条选题在往这个锚点靠（media_topic.anchor_ids 含 anchor_id）。

    anchor_ids 可能是 DB 原样的 JSON 字符串，也可能已解析成 list。
    """
    n = 0
    for t in topics:
        raw = t.get("anchor_ids")
        try:
            ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            ids = []
        if anchor_id in ids:
            n += 1
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_calc.py -k "count_topics_serving or anchor_actions_column" -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add app/database.py app/services/media_phase_review.py tests/test_media_phase_review_calc.py
git commit -m "feat(media): L3锚点策展-加anchor_actions列(迁移)+count_topics_serving纯函数"
```

---

### Task 2: `run_l3_review` 扩展锚点段 + `L3_SYSTEM` + `get_phase_review`

**Files:**
- Modify: `app/services/media_phase_review.py`
- Test: `tests/test_media_phase_review_run.py`（追加锚点校验测）

**Interfaces:**
- Consumes: Task 1 的列 + `count_topics_serving`。
- Produces: `run_l3_review` 现在还产 `anchor_actions`（存进新列）；`_build_l3_prompt` 新增 `anchors` 参数；`get_phase_review` 解析 `anchor_actions`。
- anchor_actions 每项：`{anchor_id,name,type,from_status,action:to_proven|to_dropped|keep,observation,reason}`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_media_phase_review_run.py` 末尾追加（复用文件里已有的 `_seed_l2`/`_seed_persona_phase` helper）：

```python
def test_anchor_actions_validated_and_not_auto_applied(monkeypatch):
    fake = {
        "phase_reco": "stay", "phase_to": "", "phase_reason": "原地",
        "trait_actions": [],
        "anchor_actions": [
            {"anchor_id": "AN-REAL", "action": "to_proven",
             "observation": "6条选题在靠", "reason": "attention强"},
            {"anchor_id": "AN-DROP", "action": "to_proven",       # dropped锚点，未加载→过滤
             "observation": "x", "reason": "y"},
            {"anchor_id": "AN-FAKE", "action": "to_proven"},       # 瞎编id→过滤
            {"anchor_id": "AN-REAL", "action": "bogus"},           # 非法action→过滤
        ],
    }

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek", "tokens": 10, "cost": 0}
    monkeypatch.setattr(pr, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await _seed_persona_phase(db, "P1", "涨粉")
            await db.execute(
                "INSERT INTO media_anchor (id,persona_id,name,type,status) "
                "VALUES ('AN-REAL','P1','陪跑营','service','validating')")
            await db.execute(
                "INSERT INTO media_anchor (id,persona_id,name,type,status) "
                "VALUES ('AN-DROP','P1','旧带货','带货','dropped')")
            for i in range(3):
                await _seed_l2(db, "P1", f"c{i}", i + 1)
            res = await pr.run_l3_review(db, "P1")
            row = await pr.get_phase_review(db, res["review_id"])
            acts = row["anchor_actions"]
            assert len(acts) == 1 and acts[0]["anchor_id"] == "AN-REAL"   # 只剩合法
            assert acts[0]["name"] == "陪跑营" and acts[0]["from_status"] == "validating"
            # 未自动改 media_anchor.status
            cur = await db.execute("SELECT status FROM media_anchor WHERE id='AN-REAL'")
            assert (await cur.fetchone())["status"] == "validating"
        finally:
            await db.close()
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_run.py -k anchor -v`
Expected: FAIL（`anchor_actions` 不在结果 / KeyError）

- [ ] **Step 3: 改实现**

**(a)** `_build_l3_prompt` 加 `anchors` 参数并拼锚点段。把整个函数签名行和末尾改成：

原（`app/services/media_phase_review.py`，`_build_l3_prompt` 定义）：
```python
def _build_l3_prompt(phase_from, next_phase, signals, l2, traits):
```
改为：
```python
def _build_l3_prompt(phase_from, next_phase, signals, l2, traits, anchors):
```

在 `_build_l3_prompt` 末尾的 `parts.append("请判断阶段是否该进化，并对现有条目给策展动作。")` **之前**插入：
```python
    parts.append("【当前生意锚点（只在这些里给 to_proven/to_dropped/keep，"
                 "不碰已放弃的，不造新）】")
    for a in anchors:
        parts.append(f"- anchor_id={a['id']}｜{a['name']}（{a['type']}，"
                     f"当前{a['status']}）：近期 {a['serving_count']} 条选题在靠")
    if not anchors:
        parts.append("（暂无验证中/已跑通的锚点）")
```

**(b)** `L3_SYSTEM` 加锚点段。在 `L3_SYSTEM` 字符串里，`trait 策展（...）` 段之后、`只输出严格 JSON：` 之前插入：
```
锚点策展（只对给定的验证中/已跑通锚点，不碰已放弃，不造新——造锚点是锚点页的活）：
- 你看不到真实成交/转化，只能看 attention 弱信号（多少选题在靠）+ 锚点定义。
  别假装判定生意成败——给定性观察 + 建议，明说最终成没成要人按真实成交确认。
- to_proven：attention 信号强、方向清晰，建议人确认成交后标为已跑通。
- to_dropped：长期没选题在靠、方向明显偏了，建议放弃。
- keep：没到火候，只给观察不推动作。每类 ≤3。
- 每条给 anchor_id（必来自清单）+ action(to_proven/to_dropped/keep)+observation+reason。
```
并把 `L3_SYSTEM` 结尾的 JSON 模板从：
```
 "trait_actions":[{"trait_id":"","action":"archive|promote","evidence":"","reason":""}]}"""
```
改为：
```
 "trait_actions":[{"trait_id":"","action":"archive|promote","evidence":"","reason":""}],
 "anchor_actions":[{"anchor_id":"","action":"to_proven|to_dropped|keep","observation":"","reason":""}]}"""
```

**(c)** `run_l3_review` 里，在加载 traits + `active_ids` 之后、`prompt = _build_l3_prompt(...)` 之前，插入加载锚点 + topics + 弱信号：
```python
    cur = await db.execute(
        "SELECT id, name, type, status FROM media_anchor "
        "WHERE persona_id=? AND status IN ('validating','proven')", (persona_id,))
    anchors = [dict(r) for r in await cur.fetchall()]
    active_anchor_ids = {a["id"] for a in anchors}
    cur = await db.execute(
        "SELECT anchor_ids FROM media_topic WHERE persona_id=?", (persona_id,))
    topics = [dict(r) for r in await cur.fetchall()]
    for a in anchors:
        a["serving_count"] = count_topics_serving(a["id"], topics)
```

把 `prompt = _build_l3_prompt(phase_from, next_phase, signals, l2, traits)` 改为：
```python
    prompt = _build_l3_prompt(phase_from, next_phase, signals, l2, traits, anchors)
```

**(d)** 在 trait_actions 校验循环之后、`seq = ...` 之前，加 anchor_actions 校验：
```python
    # 校验锚点动作：anchor_id 必须在 active(validating/proven) 集合、action 白名单
    anchor_actions = []
    for a in (obj.get("anchor_actions") or []):
        if not isinstance(a, dict):
            continue
        aid = a.get("anchor_id")
        if aid in active_anchor_ids and a.get("action") in ("to_proven", "to_dropped", "keep"):
            anc = next((x for x in anchors if x["id"] == aid), {})
            anchor_actions.append({
                "anchor_id": aid, "name": anc.get("name", ""),
                "type": anc.get("type", ""), "from_status": anc.get("status", ""),
                "action": a.get("action"), "observation": a.get("observation", ""),
                "reason": a.get("reason", "")})
```

**(e)** 把 INSERT 语句加上 `anchor_actions` 列和值。原：
```python
        "INSERT INTO media_phase_review "
        "(id,persona_id,seq,phase_from,l2_cycle_ids,metrics_trend,phase_signals,"
        " phase_reco,phase_to,phase_reason,trait_actions,cost,model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
```
改为：
```python
        "INSERT INTO media_phase_review "
        "(id,persona_id,seq,phase_from,l2_cycle_ids,metrics_trend,phase_signals,"
        " phase_reco,phase_to,phase_reason,trait_actions,anchor_actions,cost,model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
```
并在 values 里 `json.dumps(trait_actions, ensure_ascii=False),` 之后插入：
```python
         json.dumps(anchor_actions, ensure_ascii=False),
```

**(f)** `_JSON_FIELDS` 加 `anchor_actions`。原：
```python
_JSON_FIELDS = ("l2_cycle_ids", "metrics_trend", "phase_signals", "trait_actions")
```
改为：
```python
_JSON_FIELDS = ("l2_cycle_ids", "metrics_trend", "phase_signals", "trait_actions",
                "anchor_actions")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_run.py -v`
Expected: PASS（原有 3 测 + 新 anchor 测全绿；确认没破坏原有 run 测）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_phase_review.py tests/test_media_phase_review_run.py
git commit -m "feat(media): run_l3_review产锚点观察(加载validating/proven锚点+弱信号+AI+校验,候选不自动应用)"
```

---

### Task 3: `apply-anchor` 应用路由（镜像 apply-trait）

**Files:**
- Modify: `app/api/media.py`
- Test: `tests/test_media_phase_review_apply.py`（追加 apply-anchor 测）

**Interfaces:**
- Produces: `POST /media/phase-review/{rid}/apply-anchor`（Form `anchor_id`, `target_status`）。
- Consumes: `get_phase_review`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_media_phase_review_apply.py` 末尾追加（复用文件已有的 `_run_seed`/`_client`）：

```python
def test_apply_anchor_to_proven_and_reject_illegal():
    pid, rid = "ANP1", str(uuid.uuid4())
    aid = "ANC1"
    asyncio.run(_run_seed(pid, "涨粉", rid, "转化"))

    async def seed_anchor():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_anchor (id,persona_id,name,type,status) "
                "VALUES (?,?, '陪跑营','service','validating')", (aid, pid))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_anchor())

    # 合法 to proven
    _client().post(f"/media/phase-review/{rid}/apply-anchor",
                   data={"anchor_id": aid, "target_status": "proven"},
                   follow_redirects=False)

    async def check1():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_anchor WHERE id=?", (aid,))
            assert (await cur.fetchone())["status"] == "proven"
        finally:
            await db.close()
    asyncio.run(check1())

    # 非法目标 validating → 不改（保持 proven）
    _client().post(f"/media/phase-review/{rid}/apply-anchor",
                   data={"anchor_id": aid, "target_status": "validating"},
                   follow_redirects=False)

    async def check2():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_anchor WHERE id=?", (aid,))
            assert (await cur.fetchone())["status"] == "proven"   # 没被改成 validating
        finally:
            await db.close()
    asyncio.run(check2())


def test_apply_anchor_rejects_cross_persona():
    pid, rid = "ANP2", str(uuid.uuid4())
    asyncio.run(_run_seed(pid, "涨粉", rid, "转化"))
    other = "ANC-OTHER"

    async def seed_other():
        db = await get_db()
        try:
            # 属于别的 persona 的锚点
            await db.execute("DELETE FROM media_persona WHERE id='OTHERP'")
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES ('OTHERP','x','y','涨粉','active')")
            await db.execute(
                "INSERT INTO media_anchor (id,persona_id,name,type,status) "
                "VALUES (?, 'OTHERP','别人的','service','validating')", (other,))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_other())

    _client().post(f"/media/phase-review/{rid}/apply-anchor",
                   data={"anchor_id": other, "target_status": "proven"},
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_anchor WHERE id=?", (other,))
            assert (await cur.fetchone())["status"] == "validating"   # 越权未改
        finally:
            await db.close()
    asyncio.run(check())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_phase_review_apply.py -k anchor -v`
Expected: FAIL（apply-anchor 路由 404）

- [ ] **Step 3: 加路由**

在 `app/api/media.py` 的 `phase_review_apply_trait` 路由之后加：

```python
@router.post("/media/phase-review/{rid}/apply-anchor")
async def phase_review_apply_anchor(rid: str, anchor_id: str = Form(...),
                                    target_status: str = Form(...)):
    """人拍板：把锚点标为已跑通/已放弃。目标只能 proven/dropped，校验锚点归属。"""
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
        if rev and target_status in ("proven", "dropped"):
            cur = await db.execute(
                "SELECT id FROM media_anchor WHERE id=? AND persona_id=?",
                (anchor_id, rev["persona_id"]))
            if await cur.fetchone():
                await db.execute(
                    "UPDATE media_anchor SET status=? WHERE id=?",
                    (target_status, anchor_id))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/phase-review/{rid}", status_code=302)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_phase_review_apply.py -v`
Expected: PASS（原有 3 测 + 新 2 anchor 测全绿）

- [ ] **Step 5: Commit**

```bash
git add app/api/media.py tests/test_media_phase_review_apply.py
git commit -m "feat(media): apply-anchor路由(镜像apply-trait,目标proven/dropped白名单+归属校验)"
```

---

### Task 4: 报告页第四段「锚点策展」+ 冒烟 + 全套回归

**Files:**
- Modify: `app/templates/media_phase_review.html`

**Interfaces:**
- Consumes: `rev.anchor_actions`、`POST /media/phase-review/{rid}/apply-anchor`。

- [ ] **Step 1: 加第四段模板**

在 `app/templates/media_phase_review.html` 里，trait 策展 module 的结束 `</div>`（约 :72，即 `</div>\n  </div>` 之后）和 delete 表单（约 :74 `<form method="post" action=".../delete"`）**之间**，插入：

```html
  <div class="module" style="margin-top:12px">
    <div class="mh"><span class="ttl">🎯 锚点策展（人拍板 · 你按真实成交确认）</span></div>
    <div class="inner">
      {% for a in rev.anchor_actions %}
      <div style="border:1px solid var(--border); border-radius:8px; padding:10px; margin-bottom:8px">
        <div style="font-size:13.5px"><b>{{ a.name }}</b>（{{ a.type }}，当前 {{ a.from_status }}）</div>
        <div style="font-size:12px; color:var(--ink-3); margin:4px 0">观察：{{ a.observation }}｜{{ a.reason }}</div>
        {% if a.action == 'to_proven' %}
        <form method="post" action="/media/phase-review/{{ rev.id }}/apply-anchor" style="display:inline"
              onsubmit="return confirm('把「{{ a.name }}」标为已跑通？请先确认真实成交。')">
          <input type="hidden" name="anchor_id" value="{{ a.anchor_id }}">
          <input type="hidden" name="target_status" value="proven">
          <button type="submit" class="btn" style="font-size:12.5px; color:var(--up); border-color:var(--up)">标为已跑通</button>
        </form>
        {% elif a.action == 'to_dropped' %}
        <form method="post" action="/media/phase-review/{{ rev.id }}/apply-anchor" style="display:inline"
              onsubmit="return confirm('把「{{ a.name }}」标为已放弃？放弃是重决策。')">
          <input type="hidden" name="anchor_id" value="{{ a.anchor_id }}">
          <input type="hidden" name="target_status" value="dropped">
          <button type="submit" class="btn" style="font-size:12.5px; color:var(--down); border-color:var(--down)">标为已放弃</button>
        </form>
        {% else %}
        <span style="font-size:12px; color:var(--ink-3)">（保持观察，暂不动）</span>
        {% endif %}
      </div>
      {% else %}<div class="empty" style="text-align:left; padding:0">这轮没提锚点动作</div>{% endfor %}
    </div>
  </div>
```

（`var(--up)` 绿 / `var(--down)` 红均在 base.html；LLM-origin 的 name/observation/reason 走 `{{ }}` autoescape，无 `|safe`。）

- [ ] **Step 2: 全套回归**

Run: `python -m pytest -q; echo EXIT=${PIPESTATUS[0]}`
Expected: 全绿（267 基线 + 本轮新增测）。假挂 `taskkill //F //IM python.exe` 后重跑。

- [ ] **Step 3: 浏览器冒烟（controller 亲跑，实现者可只做 TestClient 渲染检查）**

给某 persona 播一条 media_phase_review（anchor_actions 含 to_proven + to_dropped + keep 各一），打开 `/media/phase-review/{rid}` 看「🎯 锚点策展」段渲染：三种动作分别显示「标为已跑通」（绿）/「标为已放弃」（红）/「保持观察」，无 Jinja/500。

- [ ] **Step 4: Commit**

```bash
git add app/templates/media_phase_review.html
git commit -m "feat(media): L3报告页第四段锚点策展(跑通绿/放弃红/keep观察,二次确认)"
```

---

## Self-Review 记录

- **Spec 覆盖：** §3 加列→Task 1；§4 服务(count_topics_serving/run扩展/L3_SYSTEM/校验/get解析)→Task 1+2；§5 apply-anchor→Task 3；§6 UI→Task 4；§7 质量→各 Task TDD + Task 4 回归 + controller 冒烟。
- **三原则落实：** 不打架(复用media_anchor+镜像apply-trait+决策引擎不改，Task 2/3)；不麻烦人(keep无按钮+软上限提示词，Task 2/4)；有边界(只加载validating/proven-Task 2 SQL、apply目标白名单proven/dropped-Task 3、归属SQL谓词-Task 3、候选不自动应用-Task 2 断言media_anchor.status未变)。
- **类型一致：** `count_topics_serving`(Task1)→run消费(Task2)；`anchor_actions` 结构(Task2产)↔报告页字段 name/type/from_status/action/observation/reason(Task4消费)↔apply-anchor 的 anchor_id/target_status(Task3)一致；`_build_l3_prompt` 加 anchors 参数(Task2)调用处同步改。
- **无占位：** 每 step 完整代码/命令/期望。改现有函数用"原→改"精确定位。
