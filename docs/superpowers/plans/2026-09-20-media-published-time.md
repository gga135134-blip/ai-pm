# 已发内容线补完整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让复盘助手按真实发布时间答准、复盘页能就地补录发布时间、已发内容有统一的短标题+摘要+清理正文，并确认视频反向入库已写发布时间。

**Architecture:** 读取侧统一用 `COALESCE(published_at, created_at)` 兜底（不写死 created_at）；格式统一复用现成 `organize_content`/`run_organize_one`，只给它加第三个产出「短标题」；发布时间录入复用已有端点 `POST /media/content/{cid}/published-at`。

**Tech Stack:** Python + aiosqlite + FastAPI + Jinja2；测试 pytest（无 pytest-asyncio，异步用 `asyncio.run`，基建见 `tests/media_helpers.py`）。

## Global Constraints

- **绝不把 `created_at` 写进 `published_at`**：空发布时间一律读取时兜底，写库时保持 `published_at` 为 NULL。
- 估算标注文案统一为：`（按录入时间估）`。
- 短标题约定：≤20 字，点出主题。
- 改写 `title`/`script` 必须走 `log_action`（可撤），沿用现有 `organize_format` action_type。
- 日期字符串格式 `YYYY-MM-DD`，端点原样存。
- 每个任务 TDD：先写失败测试→跑挂→最小实现→跑绿→提交。测试用 `asyncio.run`。

---

### Task 1: ⑥C — `organize_content` 增产短标题

**Files:**
- Modify: `app/services/media_ai.py:1730-1754`（`ORGANIZE_SYSTEM` + `organize_content`）
- Test: `tests/test_media_organize_ai.py`

**Interfaces:**
- Produces: `organize_content(script, model="auto") -> {"ok", "title", "summary", "formatted", "cost", "model"}`（新增 `title` 键；缺失或非法容错为 `""`）。

- [ ] **Step 1: Write the failing test**

在 `tests/test_media_organize_ai.py` 追加：

```python
def test_organize_returns_title(monkeypatch):
    async def fake_ai(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": '{"title":"给AI装档案柜治健忘","summary":"外挂记忆库","formatted":"清理后的正文"}',
                "model": "x", "tokens": 5, "cost": 0.0}
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai)

    async def go():
        r = await media_ai.organize_content("很长的原始正文……")
        assert r["ok"] and r["title"] == "给AI装档案柜治健忘"
        assert r["summary"] == "外挂记忆库" and r["formatted"] == "清理后的正文"
    asyncio.run(go())


def test_organize_missing_title_defaults_empty(monkeypatch):
    async def fake_ai(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": '{"summary":"只有摘要","formatted":"正文"}',
                "model": "x", "tokens": 5, "cost": 0.0}
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai)

    async def go():
        r = await media_ai.organize_content("正文……")
        assert r["ok"] and r["title"] == "" and r["summary"] == "只有摘要"
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_organize_ai.py::test_organize_returns_title -v`
Expected: FAIL（`KeyError: 'title'` 或 `r["title"]` 不存在）

- [ ] **Step 3: Write minimal implementation**

改 `app/services/media_ai.py` 的 `ORGANIZE_SYSTEM`（约 1730）：

```python
ORGANIZE_SYSTEM = """给你一条口播/文案正文，做三件事：
1) title：给这条起一个短标题（≤20字，点出主题，别用正文首句原样照抄，别加书名号）。
2) summary：一句话说这条讲了啥（≤30字，抓核心，别客套）。
3) formatted：把正文清理成统一排版——合并被切碎的行、去掉残留序号、统一分段。**只重排版面，别改内容、别扩写、别删信息、别润色措辞。**
只输出严格 JSON：{"title":"","summary":"","formatted":""}"""
```

改 `organize_content`（约 1747-1754）取出 title：

```python
    obj = extract_json(resp, expect="object") or {}
    title = (obj.get("title") or "").strip()
    summary = (obj.get("summary") or "").strip()
    formatted = (obj.get("formatted") or "").strip()
    if not summary and not formatted:
        return {"ok": False, "title": "", "summary": "", "formatted": "", "error": "整理失败",
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    return {"ok": True, "title": title, "summary": summary, "formatted": formatted,
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

同时把上面两处早返回（空 body、`[错误]`/`[费用保护]` 分支）也补上 `"title": ""` 键，保持返回结构一致：
- 空 body 分支（约 1740）：`return {"ok": False, "title": "", "summary": "", "formatted": "", "cost": 0, "model": ""}`
- 报错分支（约 1745）：加 `"title": "",`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_media_organize_ai.py -v`
Expected: PASS（含原有 2 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_ai.py tests/test_media_organize_ai.py
git commit -m "feat(media): organize_content 增产短标题 title"
```

---

### Task 2: ⑥C — `run_organize_one` 落库短标题（留痕可撤）

**Files:**
- Modify: `app/services/media_batch.py:10-28`（`run_organize_one`）
- Test: `tests/test_media_batch_core.py`

**Interfaces:**
- Consumes: `organize_content(...) -> {..., "title", "summary", "formatted"}`（Task 1）
- Produces: `run_organize_one(db, cid) -> {"ok", "summary", ...}`；副作用：title 非空时 `UPDATE media_content SET title`，且 `log_action` 的 before/after 记 `title`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_media_batch_core.py` 追加：

```python
def test_run_organize_one_rewrites_title(monkeypatch):
    async def fake_org(script, model="auto"):
        return {"ok": True, "title": "新短标题", "summary": "一句摘要",
                "formatted": "整理后", "cost": 0, "model": "x"}
    monkeypatch.setattr(mb, "organize_content", fake_org)

    async def go():
        db = await _seed()
        await mb.run_organize_one(db, "C1")
        cur = await db.execute("SELECT title,summary,script FROM media_content WHERE id='C1'")
        row = dict(await cur.fetchone())
        assert row["title"] == "新短标题" and row["summary"] == "一句摘要" and row["script"] == "整理后"
        # log_action 记了 title 前后值（可撤）
        cur = await db.execute("SELECT before_json,after_json FROM media_assistant_action "
                               "WHERE action_type='organize_format'")
        act = dict(await cur.fetchone())
        assert "老文案" in act["before_json"] and "新短标题" in act["after_json"]
        await db.close()
    asyncio.run(go())


def test_run_organize_one_empty_title_keeps_original(monkeypatch):
    async def fake_org(script, model="auto"):
        return {"ok": True, "title": "", "summary": "s", "formatted": "f", "cost": 0, "model": "x"}
    monkeypatch.setattr(mb, "organize_content", fake_org)

    async def go():
        db = await _seed()
        await mb.run_organize_one(db, "C1")
        cur = await db.execute("SELECT title FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["title"] == "老文案"   # 空 title 不清空原标题
        await db.close()
    asyncio.run(go())
```

> 注：`_seed()` 里内容 title 是 `'老文案'`（见文件顶部），断言据此。`before_json`/`after_json` 是 `media_assistant_action` 存 before/after 的列——Step 3 前先确认列名，若不同按实列名断言（见 Step 1.5）。

- [ ] **Step 1.5: 确认 action 表列名**

Run: `python -m pytest tests/test_media_batch_core.py::test_run_organize_one -v`（现有测试，先跑通确认基建）
再查列名：`grep -n "before" app/services/media_assistant.py` 找到 `log_action` 写的实际列（可能是 `before_json`/`after_json` 或 `before`/`after`）。据实调整 Step 1 的断言列名。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_batch_core.py::test_run_organize_one_rewrites_title -v`
Expected: FAIL（title 仍是 `老文案`，未改写）

- [ ] **Step 3: Write minimal implementation**

改 `app/services/media_batch.py` 的 `run_organize_one`（10-28）。当前只取 script、只写 summary+script；改成也取 title、before 带 title、title 非空才写：

```python
async def run_organize_one(db, cid) -> dict:
    """整理一条：短标题 + 摘要 + 格式改写(留痕可撤)。传入 db，由调用方管连接。"""
    cur = await db.execute("SELECT persona_id,title,script FROM media_content WHERE id=?", (cid,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在"}
    pid, old_title, script = row["persona_id"], row["title"] or "", row["script"] or ""
    if not script.strip():
        return {"ok": False, "error": "无正文"}
    res = await organize_content(script)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "整理失败")}
    formatted = res.get("formatted") or script
    new_title = (res.get("title") or "").strip()
    final_title = new_title or old_title       # 空 title 不清空原标题
    await log_action(db, pid, "organize_format", "media_content", cid,
                     before={"title": old_title, "script": script},
                     after={"title": final_title, "script": formatted})
    await db.execute("UPDATE media_content SET title=?, summary=?, script=? WHERE id=?",
                     (final_title, res.get("summary", ""), formatted, cid))
    await db.commit()
    return {"ok": True, "summary": res.get("summary", "")}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_media_batch_core.py -v`
Expected: PASS（含原有 `test_run_organize_one`、`test_run_mine_one_signature`）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_batch.py tests/test_media_batch_core.py
git commit -m "feat(media): 批量整理同时改写短标题（留痕可撤）"
```

---

### Task 3: ③ — 复盘助手读工具按发布时间排序+返回

**Files:**
- Modify: `app/services/media_agent_tools.py:11-45`（`_tool_list_contents`、`_tool_read_content`）
- Modify: `app/services/media_assistant.py:5`（`MEDIA_ASSISTANT_SYSTEM` 补一句）
- Test: `tests/test_media_agent_published_sort.py`（新建）

**Interfaces:**
- Produces: `dispatch_media_tool("list_contents", {}, pid)` / `("read_content", {"id"}, pid)` 输出含发布时间；空 `published_at` 标 `（按录入时间估）`，按 `COALESCE(published_at, created_at) DESC` 排序。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_media_agent_published_sort.py`（沿用 `tests/test_media_agent_read_tools.py` 的文件 DB fixture 模式）：

```python
"""复盘助手读工具：按 COALESCE(published_at,created_at) 排序 + 估算标注。"""
import asyncio
import pytest
import app.database as _db_mod
from app.database import get_db, init_db
from app.services import media_agent_tools as mat

_SEEDED = False


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pub_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


async def _seed_once():
    global _SEEDED
    if _SEEDED:
        return
    db = await get_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('A','嘉','x','涨粉','active')")
    # CPnull: 无真实发布时间，created_at 最新 → 兜底日 2026-08-14
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,created_at) "
                     "VALUES ('CPnull','A','没真日期','published','2026-08-14 09:00:00')")
    # CPmid: 真实发布 2026-07-15
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,published_at,created_at) "
                     "VALUES ('CPmid','A','七月那条','published','2026-07-15','2026-05-01 09:00:00')")
    # CPold: 真实发布 2026-06-01
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,published_at,created_at) "
                     "VALUES ('CPold','A','六月那条','published','2026-06-01','2026-05-01 09:00:00')")
    await db.commit()
    await db.close()
    _SEEDED = True


def test_list_contents_sorted_by_published_fallback():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_contents", {}, "A")
        # COALESCE 排序：08-14(兜底) > 07-15 > 06-01
        assert out.index("没真日期") < out.index("七月那条") < out.index("六月那条")
    asyncio.run(go())


def test_list_contents_marks_estimate():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("list_contents", {}, "A")
        lines = {l.split("]")[1].split("（")[0].strip(): l
                 for l in out.splitlines() if "]" in l}
        assert "（按录入时间估）" in lines["没真日期"]        # 无真日期→标估算
        assert "（按录入时间估）" not in lines["七月那条"]     # 有真日期→不标
        assert "2026-07-15" in lines["七月那条"]
    asyncio.run(go())


def test_read_content_returns_published_time():
    async def go():
        await _seed_once()
        out = await mat.dispatch_media_tool("read_content", {"id": "CPmid"}, "A")
        assert "发布时间" in out and "2026-07-15" in out
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_agent_published_sort.py -v`
Expected: FAIL（当前按 updated_at 排、输出无发布时间/估算标注）

- [ ] **Step 3: Write minimal implementation**

改 `app/services/media_agent_tools.py`。

`_tool_list_contents`（11-28）——SELECT 带上时间、改排序、输出带发布日：

```python
async def _tool_list_contents(args, pid):
    stage = (args or {}).get("stage")
    db = await get_db()
    try:
        if stage:
            cur = await db.execute(
                "SELECT id,title,stage,published_at,created_at FROM media_content "
                "WHERE persona_id=? AND stage=? "
                "ORDER BY COALESCE(published_at, created_at) DESC LIMIT 50", (pid, stage))
        else:
            cur = await db.execute(
                "SELECT id,title,stage,published_at,created_at FROM media_content "
                "WHERE persona_id=? "
                "ORDER BY COALESCE(published_at, created_at) DESC LIMIT 50", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    if not rows:
        return "（该人设暂无内容）"
    return "\n".join(_fmt_content_line(r) for r in rows)


def _fmt_content_line(r):
    """一行内容摘要，带发布时间；无真实 published_at 时用 created_at 并标估算。"""
    pub = (r.get("published_at") or "").strip()
    if pub:
        when = f" · 发布 {pub[:10]}"
    else:
        ca = (r.get("created_at") or "").strip()
        when = f" · 发布 {ca[:10]}（按录入时间估）" if ca else ""
    return f"[{r['id']}] {r['title']}（{r['stage']}）{when}"
```

`_tool_read_content`（31-45）——SELECT 带时间、返回加发布时间行：

```python
async def _tool_read_content(args, pid):
    cid = (args or {}).get("id", "")
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT title,puzzle,script,ai_draft,stage,published_at,created_at "
            "FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        return "（找不到这条内容，或不属于当前人设）"
    r = dict(row)
    body = r["script"] or r["ai_draft"] or "（暂无正文/脚本）"
    pub = (r.get("published_at") or "").strip()
    if pub:
        when = f"{pub[:10]}"
    else:
        ca = (r.get("created_at") or "").strip()
        when = f"{ca[:10]}（按录入时间估）" if ca else "未知"
    return (f"标题：{r['title']}\n谜题：{r['puzzle']}\n阶段：{r['stage']}\n"
            f"发布时间：{when}\n正文：\n{body}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_agent_published_sort.py tests/test_media_agent_read_tools.py -v`
Expected: PASS（新文件全绿，且原读工具测试不回归）

- [ ] **Step 5: 补系统提示词（无测试，copy 改动）**

`app/services/media_assistant.py` 的 `MEDIA_ASSISTANT_SYSTEM`（约第 5 行起）末尾加一句：

```
回答"最后一条/这周/这个月发了什么"等问题，按 list_contents 返回的发布时间为准（已按发布时间倒序）；标了"（按录入时间估）"的是占位、不是真实发布时间，据此回答时要说明。
```

- [ ] **Step 6: Commit**

```bash
git add app/services/media_agent_tools.py app/services/media_assistant.py tests/test_media_agent_published_sort.py
git commit -m "feat(media): 复盘助手读工具按真实发布时间排序+返回（空的标估算）"
```

---

### Task 4: ⑤ — 单测证明视频反向入库写 published_at

**Files:**
- Test: `tests/test_media_reverse.py`（追加；若已有等价断言则只补缺口）

**Interfaces:**
- Consumes: `reverse_ingest(db, persona_id, video_url, cfg, public_base, audio_dir, model, cookies_path)`。

- [ ] **Step 1: 先看现有测试是否已覆盖**

Run: `grep -n "published_at\|reverse_ingest\|fetch_audio" tests/test_media_reverse.py`
若已有"published_at 落库"断言 → 本任务只跑一遍确认，跳到 Step 4。否则继续。

- [ ] **Step 2: Write the failing test**

在 `tests/test_media_reverse.py` 追加（mock 掉 fetch/asr/extract，纯验落库；参照该文件已有 monkeypatch 风格）：

```python
def test_reverse_ingest_writes_published_at(monkeypatch, tmp_path):
    import app.services.media_reverse as mr

    async def fake_fetch(url, out_dir, cookies_path=None):
        f = tmp_path / "x.mp3"; f.write_bytes(b"0")
        return f, "2026-06-12"                      # yt-dlp 抓到的发布日
    async def fake_asr(public_url, cfg):
        return "这是转写正文"
    async def fake_extract(transcript, model="auto"):
        return {"title": "标题", "puzzle": "", "topic_fingerprint": ""}
    monkeypatch.setattr(mr, "fetch_audio", fake_fetch)
    monkeypatch.setattr(mr, "transcribe_url", fake_asr)
    monkeypatch.setattr(mr, "extract_from_transcript", fake_extract)

    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('P','嘉','x','涨粉','active')")
        await db.commit()
        r = await mr.reverse_ingest(db, "P", "https://v/1", {}, "http://h", tmp_path)
        assert r["ok"]
        cur = await db.execute("SELECT published_at FROM media_content WHERE id=?", (r["content_id"],))
        assert (await cur.fetchone())["published_at"] == "2026-06-12"
        await db.close()
    asyncio.run(go())
```

> 顶部若无 `from tests.media_helpers import make_db` 则补上。

- [ ] **Step 3: Run test to verify it passes（预期直接绿——功能已存在）**

Run: `python -m pytest tests/test_media_reverse.py::test_reverse_ingest_writes_published_at -v`
Expected: PASS（[media_reverse.py:58-61](../../../app/services/media_reverse.py) 已写）。若 FAIL 说明真有洞，按错误修 `reverse_ingest`。

- [ ] **Step 4: Commit**

```bash
git add tests/test_media_reverse.py
git commit -m "test(media): 锁定视频反向入库写入 published_at"
```

---

### Task 5: ② — 复盘页已发列表就地补录发布时间

**Files:**
- Modify: `app/templates/media_review_home.html:44-65`（已发列表 `rv-row`）

**Interfaces:**
- Consumes: 现成端点 `POST /media/content/{cid}/published-at`（`published_at` 表单字段）。

- [ ] **Step 1: 改模板——把日期框放进行内，且不被整行链接吞掉**

当前每行是整块 `<a class="rv-row" href="...">`。日期框若放进 `<a>` 里点击会跳页。改成：行外层 div，标题区仍是链接，日期框作兄弟节点，`onclick` 阻止冒泡。替换 44-57 的 `{% for %}` 行体：

```html
    {% for c in published %}
    <div class="rv-row" style="display:flex; align-items:center; gap:10px">
      <a href="/media/content/{{ c.id }}" style="flex:1; min-width:0; display:block; color:inherit; text-decoration:none">
        <div class="rv-t">{{ c.title }}</div>
        {% if c.summary %}<div style="font-size:11.5px; color:var(--ink-3); margin-top:2px">{{ c.summary }}</div>{% endif %}
        <div class="rv-m">
          {% if c.stage == 'reviewed' %}已复盘{% else %}待复盘{% endif %}
          {% if c.is_winner %} · <span style="color:var(--warn)">爆款</span>{% endif %}
          {% if not c.published_at %} · <span style="color:var(--ink-3)">日期待补（按录入时间估）</span>{% endif %}
        </div>
      </a>
      <input type="date" value="{{ (c.published_at or '')[:10] }}"
             onclick="event.stopPropagation()"
             onchange="savePubAt('{{ c.id }}', this)"
             style="flex-shrink:0; padding:3px 6px; font-size:12px">
      <span id="pubmsg-{{ c.id }}" style="font-size:11px; color:var(--success); flex-shrink:0; width:14px"></span>
    </div>
    {% endfor %}
```

- [ ] **Step 2: 加保存 JS**

在该模板的 `<script>` 块（页面底部；若无则在 `{% endblock %}` 前加 `<script>...</script>`）加：

```javascript
async function savePubAt(cid, el){
  const msg = document.getElementById('pubmsg-' + cid);
  const b = new URLSearchParams(); b.set('published_at', el.value);
  try{
    const r = await fetch('/media/content/' + cid + '/published-at', {method:'POST', body:b});
    const d = await r.json().catch(()=>({ok:r.ok}));
    if(msg){ msg.textContent = d.ok ? '✓' : '✕'; msg.style.color = d.ok ? 'var(--success)' : 'var(--down)';
             setTimeout(()=>{ if(msg) msg.textContent=''; }, 2000); }
  }catch(e){ if(msg){ msg.textContent='✕'; msg.style.color='var(--down)'; } }
}
```

- [ ] **Step 3: 本地起服 + 真机验证（用眼睛，不靠单测）**

- `.claude/launch.json` 配置本项目 dev server（端口用 8012，避开常被占的 8000）。
- `preview_start {name: "..."}` 起服 → `navigate` 到 `/media/review`（先选好有已发内容的人设）。
- 验：① 每行右侧出现日期框；② 空的行显示"日期待补（按录入时间估）"；③ 填一条日期→出现 ✓；④ 刷新后日期仍在（`read_network_requests` 确认 POST 200 + `read_page` 确认值）；⑤ 点日期框不会误跳到详情页；⑥ 点标题区仍正常进详情页。
- `computer screenshot` 截图留证给用户看。

- [ ] **Step 4: Commit**

```bash
git add app/templates/media_review_home.html
git commit -m "feat(media): 复盘页已发列表就地补录/修改发布时间"
```

---

### Task 6: 收尾——跑存量清洗 + 全量回归

**Files:** 无代码改动（操作 + 验证）

- [ ] **Step 1: 全量测试回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿（新增测试 + 无回归）。

- [ ] **Step 2: 存量格式清洗（真机操作，非代码）**

- 本地/生产的老文案页多选存量【文案 NN】/长首句内容 → 「批量整理」（走后台跑器）→ 它们获得短标题+摘要+清理正文。
- 回复盘页确认列表变成"短标题 + 摘要 + 日期"的统一样子（截图）。
- 逐条补那 90 条的真实发布时间（用 Task 5 的行内日期框；或详情页）。

- [ ] **Step 3: 助手抽验**

在助手对话问"最后一条已发是什么""这个月发了哪几条"，确认按真实发布时间回答、占位的有标注。

- [ ] **Step 4: 更新路线图**

`docs/进展与路线图.md` 把本轮标已完成；在待办区补记 **B·未来内容排期（轻量清单版，非日历，单开一轮）**。commit。

---

## Self-Review

**Spec coverage：**
- ③ 助手答准 → Task 3 ✅
- ② 就地补录 → Task 5 ✅
- ⑤ 上传抓准确时间（视频路径）→ Task 4（单测锁定）✅；老文案无日期源→手动补（Task 5/6）✅
- ⑥ 格式统一 A(摘要)/B(正文)/C(标题) → Task 1+2（C 新增，A/B 复用）+ Task 6 存量清洗 ✅；A 列表显示统一 → Task 5 模板 ✅
- 不写死 created_at（读取兜底）→ Task 3 COALESCE + Global Constraints ✅
- 留痕可撤 → Task 2 log_action ✅
- B 不做、记待办 → Task 6 Step 4 ✅

**Placeholder scan：** 无 TBD/TODO；每个代码步给了完整代码。Task 2 Step 1.5 明确了列名需据实核对的动作（非占位，是显式校验步骤）。

**Type consistency：** `organize_content` 返回新增 `title` 键（Task 1）→ `run_organize_one` 消费 `res.get("title")`（Task 2）一致；`_fmt_content_line` 新 helper 仅 Task 3 内部用；端点字段名 `published_at` 与模板/JS 一致。
