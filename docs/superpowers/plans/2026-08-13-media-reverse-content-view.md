# 反向补录内容视图 + 从转写稿挖精华 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `idea_source='video_reverse'` 的已发内容换一套精简视图（砍创作台噪音），加真实发布时间，并从转写稿挖两桶精华（内容素材→原料库、口头禅→人设 signature，AI 只给精华、人拍板入库）。

**Architecture:** 内容详情页认 `idea_source` 分支渲染；新 AI 能力 `mine_from_transcript` 挖两桶候选（不写库）；两条采纳路径复用现有原料库/人设 adopt；`fetch_audio` 顺带取 yt-dlp `upload_date` 写进新列 `media_content.published_at`。

**Tech Stack:** Python FastAPI + aiosqlite + httpx + Jinja2 + vanilla JS + yt-dlp（subprocess）。测试：pytest（无 pytest-asyncio，纯/桩为主，asyncio.run 跑异步，签名 cookie TestClient，AI 能力 smoke+浏览器验）。

## Global Constraints

- 挖料**人拍板**：AI 只提候选，采纳才入库；`mine_from_transcript` **绝不写库**。
- 挖料**诚实不编造**：只从真实转写稿挖，每条 `evidence` 引原文片段。
- **AI 只给精华**：AI 粗筛掉废话/边缘料，软上限（内容料每类 ≤3、口头禅 ≤5）。
- **成本可见**：`mine_from_transcript` 调用记 `log_injection`。
- **两桶分流**：内容素材→`media_material`（source=`反向挖料`）；口头禅→`media_persona_trait`（dimension=`signature`, source=`reverse_mine`, phase_tag=`''`）。
- **不打架**：signature 条目多来源同表同闸（人设框架），反向挖料是又一来源。
- AI 取值防御：DeepSeek 返回错类型，全字段走现有 `_txt`/`_clamp` 兜底。material.type 夹回 `MATERIAL_TYPES`。
- 模板改动用 Edit/Write（**禁 PowerShell -replace**，毁中文）；**不塞 `{{ ic.icon() }}` 进 JS 字符串**（崩坑），失败用预存 `orig=btn.innerHTML` 还原。
- DB 迁移用 idempotent `ALTER TABLE ADD COLUMN`，加进 `MIGRATIONS`。
- 运行测试：`cd D:/GAGA-5-25/ai-pm && python -m pytest`。假挂→`taskkill //F //IM python.exe` 重跑，用 `${PIPESTATUS[0]}` 看真退出码（别被 `| tail` 吞了）。

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `app/database.py` | schema+迁移 | +`media_content.published_at` |
| `app/services/video_fetch.py` | 抽音频 | `fetch_audio` 顺带取 upload_date，返回 `(path, upload_date)` |
| `app/services/media_reverse.py` | 编排 | 写 `published_at`（content+publish），修 CURRENT_TIMESTAMP bug |
| `app/services/media_ai.py` | AI 能力 | +`mine_from_transcript`+`MINE_SYSTEM` |
| `app/api/media.py` | 路由 | `content_detail` 传 is_reverse；+`/mine`、`/mine/adopt-material`、`/published-at`；adopt 白名单+`reverse_mine` |
| `app/templates/media_content.html` | 视图 | `{% if is_reverse %}` 精简视图+挖精华 AJAX |
| tests | | mine / video_fetch / reverse / 路由 / 视图 |

---

### Task 1: DB 加 `media_content.published_at`

**Files:**
- Modify: `app/database.py`（SCHEMA 的 `media_content` 段；MIGRATIONS 末尾）
- Test: `tests/test_media_schema.py`

**Interfaces:**
- Produces: `media_content.published_at DATETIME`（真实视频发布时间，可空）。

- [ ] **Step 1: 写失败测试**

`tests/test_media_schema.py` 末尾追加：

```python
def test_media_content_has_published_at():
    import asyncio
    from app.database import get_db, init_db

    async def check():
        await init_db()
        db = await get_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_content)")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    assert "published_at" in asyncio.run(check())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_schema.py::test_media_content_has_published_at -v`
Expected: FAIL（列不存在）

- [ ] **Step 3: 改 SCHEMA + MIGRATIONS**

`app/database.py` 的 `CREATE TABLE ... media_content` 里，`updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,` 那行之前加一行：

```sql
    published_at DATETIME,
```

`MIGRATIONS` 列表末尾加：

```python
    "ALTER TABLE media_content ADD COLUMN published_at DATETIME",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_schema.py::test_media_content_has_published_at -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/database.py tests/test_media_schema.py
git commit -m "feat(media): media_content 加 published_at 列(真实视频发布时间)"
```

---

### Task 2: `fetch_audio` 顺带取 upload_date

**Files:**
- Modify: `app/services/video_fetch.py`
- Test: `tests/test_video_fetch.py`

**Interfaces:**
- Consumes: 无。
- Produces: `fetch_audio(url, out_dir, cookies_path=None) -> tuple[Path, str|None]` —— 返回 `(音频路径, 发布日期)`，发布日期为 `'YYYY-MM-DD'` 或 `None`。**返回类型从 Path 改成 tuple**（现有调用方/测试需同步）。

**说明：** yt-dlp 加 `--no-simulate --print "UPLOADDATE:%(upload_date)s"`，下载的同时把发布日期打到 stdout；从 stdout 正则抠 8 位 `YYYYMMDD` 转成 `YYYY-MM-DD`。抠不到返回 `None`（非致命）。

- [ ] **Step 1: 写/改失败测试**

`tests/test_video_fetch.py` 里，`_FakeProc.communicate` 现在返回 `(b"out", stderr)`。upload_date 从 **stdout** 抠，所以让 `_FakeProc` 可配置 stdout。改 `_FakeProc`：

```python
class _FakeProc:
    def __init__(self, returncode, made_file=None, stderr=b"err", stdout=b"out"):
        self.returncode = returncode
        self._made = made_file
        self._stderr = stderr
        self._stdout = stdout

    async def communicate(self):
        if self._made:
            self._made.write_bytes(b"fake-audio")
        return (self._stdout, self._stderr)
```

现有 `test_fetch_audio_success` 断言 `p.exists()`——因返回值变 tuple 需解包。改它，并加 upload_date 测试：

```python
def test_fetch_audio_success(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out, stdout=b"UPLOADDATE:20241021\n")
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    p, upload_date = asyncio.run(fetch_audio("https://v.douyin.com/xxx/", tmp_path))
    assert p.exists() and p.suffix == ".mp3"
    assert upload_date == "2024-10-21"


def test_fetch_audio_no_upload_date_returns_none(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out, stdout=b"no date here")
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    p, upload_date = asyncio.run(fetch_audio("https://x/", tmp_path))
    assert p.exists() and upload_date is None
```

其余现有测试（nonzero/ffmpeg/ytdlp-missing/no-output/cookies）断言的是**抛异常或 args**，不解包返回值，不受影响——**但 `test_fetch_audio_adds_cookies_when_file_exists` / `test_fetch_audio_no_cookies_when_path_missing` 调 `fetch_audio(...)` 不接返回值，OK 不用改**。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_video_fetch.py -v`
Expected: FAIL（返回值还是 Path，解包/日期断言不过）

- [ ] **Step 3: 改实现**

`app/services/video_fetch.py`：`_URL_RE` 附近加日期正则：

```python
_DATE_RE = re.compile(r"UPLOADDATE:(\d{8})")
```

`fetch_audio` 的 args 加打印发布日期（在 `url` 之前，`args.append(url)` 之前）：

```python
    args = [sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "mp3",
            "--no-playlist", "--no-simulate", "--print", "UPLOADDATE:%(upload_date)s",
            "-o", str(out_dir / f"{name}.%(ext)s")]
    if cookies_path and Path(cookies_path).exists():
        args += ["--cookies", str(cookies_path)]
    args.append(url)
```

把 `_out, err = await ...` 保留 `_out`（stdout 要用了，改名 `out`）。返回前解析并返回 tuple。找到 `return target`，替换为：

```python
    upload_date = None
    m = _DATE_RE.search((out or b"").decode("utf-8", "ignore"))
    if m:
        d = m.group(1)  # YYYYMMDD
        upload_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return target, upload_date
```

并把上面 `_out, err = await asyncio.wait_for(...)` 改成 `out, err = await asyncio.wait_for(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_video_fetch.py -v`
Expected: PASS（含新 2 测 + 原有不回归）

- [ ] **Step 5: 提交**

```bash
git add app/services/video_fetch.py tests/test_video_fetch.py
git commit -m "feat(media): fetch_audio 顺带取 yt-dlp upload_date 返回(path,发布日期)"
```

---

### Task 3: `reverse_ingest` 写真实发布时间（修 bug）

**Files:**
- Modify: `app/services/media_reverse.py`
- Test: `tests/test_media_reverse.py`

**Interfaces:**
- Consumes: `fetch_audio` 现在返回 `(path, upload_date)`（Task 2）；`media_content.published_at`（Task 1）。
- Produces: `reverse_ingest` 把 upload_date 写进 `media_content.published_at`；`media_publish.published_at` 用 upload_date（拿不到则 `NULL`，**不再写 CURRENT_TIMESTAMP**）。

- [ ] **Step 1: 改测试（fake_fetch 返回 tuple + 断言 published_at）**

`tests/test_media_reverse.py` 的 `_patch` 里 `fake_fetch` 现在 `return p`（Path）。改成返回 tuple，并让 `_patch` 可注入 upload_date：

```python
def _patch(monkeypatch, audio_ok=True, asr_text="老板买AI工具用不起来", extract=None,
           upload_date="2024-10-21"):
    async def fake_fetch(url, out_dir, cookies_path=None):
        if not audio_ok:
            raise VideoFetchError("拿不到")
        p = Path(out_dir) / "a.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p, upload_date
    ...  # 其余 fake_asr / fake_extract 不变
```

`_content_rows` 的 SELECT 加 `published_at`：

```python
            cur = await db.execute(
                "SELECT stage,idea_source,title,script,topic_fingerprint,published_at "
                "FROM media_content WHERE persona_id=?", (pid,))
```

`test_success_creates_published_content_and_publish` 末尾加断言：

```python
    assert rows[0]["published_at"] == "2024-10-21"
```

加一个 upload_date 缺失的测试：

```python
def test_no_upload_date_leaves_published_at_null(tmp_path, monkeypatch):
    _seed_persona("REVP7")
    _patch(monkeypatch, upload_date=None)
    _run("REVP7", tmp_path)
    rows = _content_rows("REVP7")
    assert rows[0]["published_at"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_reverse.py -v`
Expected: FAIL（fetch 返回值/published_at 未处理）

- [ ] **Step 3: 改实现**

`app/services/media_reverse.py`：`fetch_audio` 调用解包 tuple：

```python
            audio_file, upload_date = await fetch_audio(video_url, audio_dir, cookies_path)
```

content 的 INSERT 加 `published_at` 列（找到 `INSERT INTO media_content ... VALUES (...)`）：

```python
        await db.execute(
            "INSERT INTO media_content "
            "(id,persona_id,title,puzzle,stage,idea_source,idea_reason,script,"
            " topic_fingerprint,published_at) "
            "VALUES (?,?,?,?,'published','video_reverse',?,?,?,?)",
            (content_id, persona_id, title, puzzle, video_url, transcript,
             fingerprint, upload_date))
```

publish 的 INSERT：把 `published_at=CURRENT_TIMESTAMP` 改成绑 upload_date（找到 `INSERT INTO media_publish ... VALUES (?,?,?,?,CURRENT_TIMESTAMP,'published')`）：

```python
            await db.execute(
                "INSERT INTO media_publish "
                "(id,content_id,account_id,post_url,published_at,status) "
                "VALUES (?,?,?,?,?,'published')",
                (str(uuid.uuid4()), content_id, acc["id"], video_url, upload_date))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_media_reverse.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/media_reverse.py tests/test_media_reverse.py
git commit -m "feat(media): reverse_ingest 写真实发布时间(published_at)修入库时刻 bug"
```

---

### Task 4: `mine_from_transcript` 挖两桶精华（AI 能力）

**Files:**
- Modify: `app/services/media_ai.py`（+`MINE_SYSTEM`+`mine_from_transcript`）

**Interfaces:**
- Consumes: 现有 `ask_ai`/`extract_json`/`_txt`/`log_injection`；`MATERIAL_TYPES` 键（story/pit/judgment/opinion/data/quote）。
- Produces: `async mine_from_transcript(db, persona_id, transcript, model="auto") -> dict`，返回 `{"ok", "materials":[{type,content,brief,evidence,reason}...], "signatures":[{content,brief,evidence,reason}...], "error", "cost", "model"}`。

- [ ] **Step 1: 写实现（AI 能力无单测，靠 Task 5 路由 stub + 真机；此步 import 冒烟）**

`app/services/media_ai.py` 末尾加。**material.type 夹回 MATERIAL_TYPES 键、全字段 `_txt` 兜底、记 log_injection**：

```python
_MINE_MATERIAL_TYPES = ("story", "pit", "judgment", "opinion", "data", "quote")

MINE_SYSTEM = """你是自媒体资产提炼师。给你一段已发视频的口播转写稿，你要挖出两桶「精华」——
只挖真正值钱的，废话直接不提（宁缺毋滥）。

桶A 内容素材（可复用的内容资产）——只留满足"未来别的内容能复用 + 体现真实经历/独特视角"的：
- story 故事：具体真实事件/案例（有人物有情节，不是泛泛"我经历过"）
- pit 踩过的坑：真实教训/弯路
- judgment 判断 / opinion 观点：有信息量的独到看法（不是正确的废话）
- data 数据素材：具体数字/实证
- quote 金句：有记忆点、能单独拎出来用的表达
丢：开场问候/自我介绍、点赞关注引导/平台套话、纯过渡句、泛泛而谈、重复啰嗦。

桶B 口头禅/记忆点（用户标志性的说话方式/腔调，进人设让 AI 写得越来越像用户）——
留：反复出现、标志性、一听就"这是他"的表达/句式/腔调。
丢：谁都在用的口水词（"然后""这个""嗯"）、偶发一次没成习惯的。

铁律：
1. 只从转写稿里挖，绝不编造用户没说过的；每条 evidence 引一句原文。
2. 只给精华：内容料每类最多3条、口头禅最多5条，够不上标准的不提。
3. 每条给一句"为什么值得留"(reason)。
4. 只输出 JSON，不要任何解释。

输出格式：
{"materials":[{"type":"story","content":"...","brief":"注入用一句话","evidence":"原文片段","reason":"为什么值得留"}],
 "signatures":[{"content":"口头禅/句式","brief":"一句话","evidence":"原文片段","reason":"为什么值得留"}]}"""


async def mine_from_transcript(db, persona_id: str, transcript: str,
                               model: str = "auto") -> dict:
    """从转写稿挖两桶精华候选（内容素材/口头禅）。绝不写库——返回候选，人拍板 adopt 才入。"""
    snippet = (transcript or "").strip()[:8000]
    if not snippet:
        return {"ok": False, "materials": [], "signatures": [],
                "error": "转写稿为空", "cost": 0, "model": ""}
    result = await ask_ai(f"转写稿：\n{snippet}", model=model, task_type="media_topic",
                          system_prompt=MINE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    await log_injection(db, "", "mine_from_transcript", [], result.get("tokens", 0))
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "materials": [], "signatures": [],
                "error": resp, "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    mats, sigs = [], []
    for it in (obj.get("materials") or []) if isinstance(obj, dict) else []:
        if not isinstance(it, dict):
            continue
        t = _txt(it.get("type"))
        mats.append({
            "type": t if t in _MINE_MATERIAL_TYPES else "story",
            "content": _txt(it.get("content")), "brief": _txt(it.get("brief")),
            "evidence": _txt(it.get("evidence")), "reason": _txt(it.get("reason")),
        })
    for it in (obj.get("signatures") or []) if isinstance(obj, dict) else []:
        if not isinstance(it, dict):
            continue
        sigs.append({
            "content": _txt(it.get("content")), "brief": _txt(it.get("brief")),
            "evidence": _txt(it.get("evidence")), "reason": _txt(it.get("reason")),
        })
    mats = [m for m in mats if m["content"]]
    sigs = [s for s in sigs if s["content"]]
    return {"ok": True, "materials": mats, "signatures": sigs, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
```

- [ ] **Step 2: 冒烟——import 不报错**

Run: `python -c "from app.services.media_ai import mine_from_transcript, MINE_SYSTEM; print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 3: 提交**

```bash
git add app/services/media_ai.py
git commit -m "feat(media): mine_from_transcript 从转写稿挖两桶精华(内容料/口头禅)"
```

---

### Task 5: 路由（is_reverse 标 + 挖精华 + 采纳 + 发布时间）

**Files:**
- Modify: `app/api/media.py`
- Test: `tests/test_media_routes.py`

**Interfaces:**
- Consumes: `mine_from_transcript`(T4)；`media_content.published_at`(T1)；`MATERIAL_TYPES`；现有 `persona_interview_adopt`。
- Produces: `content_detail` 传 `is_reverse`；`POST /media/content/{cid}/mine`（返候选）；`POST /media/content/{cid}/mine/adopt-material`（写 media_material）；`POST /media/content/{cid}/published-at`（存发布时间）；`persona_interview_adopt` 白名单加 `reverse_mine`。

- [ ] **Step 1: 写失败测试**

`tests/test_media_routes.py` 末尾加：

```python
def _seed_reverse_content(cid="RVC1", pid="RTP2"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_content WHERE id=?", (cid,))
            await db.execute(
                "INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                "VALUES (?,?,?,'published','video_reverse',?)",
                (cid, pid, "反向内容", "老板买了一堆AI工具还是用不起来，卡在哪"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_content_detail_reverse_shows_lean_view():
    _only_active_persona()
    _seed_reverse_content()
    html = _client().get("/media/content/RVC1").text
    assert "反向补录" in html            # 精简视图标记
    assert "AI 写脚本" not in html        # 创作台被砍


def test_mine_route_calls_ai(monkeypatch):
    _only_active_persona()
    _seed_reverse_content()
    import app.api.media as media_mod

    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [{"type": "story", "content": "一个真故事",
                "brief": "b", "evidence": "e", "reason": "r"}],
                "signatures": [{"content": "你要知道", "brief": "b", "evidence": "e",
                "reason": "r"}], "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr(media_mod, "mine_from_transcript", fake_mine)
    r = _client().post("/media/content/RVC1/mine")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["materials"][0]["type"] == "story"
    assert r.json()["signatures"][0]["content"] == "你要知道"


def test_mine_adopt_material_writes_row():
    _only_active_persona()
    _seed_reverse_content()
    _client().post("/media/content/RVC1/mine/adopt-material", data={
        "type": "story", "detail": "一个真故事", "brief": "b"}, follow_redirects=False)

    async def cnt():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT type,source FROM media_material WHERE persona_id='RTP2' "
                "AND detail='一个真故事'")
            return [dict(x) for x in await cur.fetchall()]
        finally:
            await db.close()
    rows = asyncio.run(cnt())
    assert rows and rows[0]["source"] == "反向挖料" and rows[0]["type"] == "story"


def test_published_at_saved():
    _only_active_persona()
    _seed_reverse_content()
    _client().post("/media/content/RVC1/published-at",
                   data={"published_at": "2024-10-21"}, follow_redirects=False)

    async def get():
        db = await get_db()
        try:
            cur = await db.execute("SELECT published_at FROM media_content WHERE id='RVC1'")
            return (await cur.fetchone())["published_at"]
        finally:
            await db.close()
    assert asyncio.run(get()) == "2024-10-21"


def test_interview_adopt_accepts_reverse_mine_source():
    _only_active_persona()
    _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "signature", "content": "你要知道", "source": "reverse_mine"},
        follow_redirects=False)

    async def get():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT source FROM media_persona_trait WHERE persona_id='RTP2' "
                "AND content='你要知道'")
            return [dict(x) for x in await cur.fetchall()]
        finally:
            await db.close()
    rows = asyncio.run(get())
    assert rows and rows[0]["source"] == "reverse_mine"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_media_routes.py -k "reverse or mine or published_at or reverse_mine" -v`
Expected: FAIL（路由/标记/白名单未加）

- [ ] **Step 3: content_detail 传 is_reverse + import**

`app/api/media.py`：确认顶部已 import `mine_from_transcript`（在现有 `from app.services.media_ai import (...)` 那行加入）。`content_detail` 的 `return _tpl(request, "media_content.html", {...})` 的 context 字典里加：

```python
                 "is_reverse": content.get("idea_source") == "video_reverse",
```

- [ ] **Step 4: adopt 白名单加 reverse_mine**

`persona_interview_adopt` 里 `src = source if source in ("interview", "learned_edit") else "interview"` 改为：

```python
    src = source if source in ("interview", "learned_edit", "reverse_mine") else "interview"
```

- [ ] **Step 5: 加三个路由**

在 `content_detail` 之后（或内容相关路由区）加：

```python
@router.post("/media/content/{cid}/mine")
async def content_mine(cid: str):
    """从转写稿挖两桶精华候选（不写库）。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id,script FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        try:
            result = await mine_from_transcript(db, row["persona_id"], row["script"] or "")
        except Exception as e:
            log.exception("挖精华失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/mine/adopt-material")
async def content_mine_adopt_material(cid: str, type: str = Form("story"),
                                      detail: str = Form(...), brief: str = Form("")):
    """采纳一条挖出的内容料 → 写原料库（source=反向挖料）。"""
    mtype = type if type in MATERIAL_TYPES else "story"
    detail = detail.strip()
    if not detail:
        return JSONResponse({"ok": False, "error": "空内容"})
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        await db.execute(
            "INSERT INTO media_material "
            "(id,persona_id,type,title,detail,brief,source) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), row["persona_id"], mtype, detail[:40], detail,
             (brief.strip() or detail)[:30], "反向挖料"))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/content/{cid}/published-at")
async def content_published_at(cid: str, published_at: str = Form("")):
    """存/改真实视频发布时间。"""
    db = await get_db()
    try:
        await db.execute("UPDATE media_content SET published_at=? WHERE id=?",
                         (published_at.strip() or None, cid))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=303)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_media_routes.py -q`
Expected: PASS（新测 + 无回归）。用 `${PIPESTATUS[0]}` 确认退出码。

- [ ] **Step 7: 提交**

```bash
git add app/api/media.py tests/test_media_routes.py
git commit -m "feat(media): 反向视图路由(is_reverse标+挖精华+采纳料+发布时间+reverse_mine白名单)"
```

---

### Task 6: `media_content.html` 反向精简视图 + 挖精华 AJAX

**Files:**
- Modify: `app/templates/media_content.html`

**Interfaces:**
- Consumes: `is_reverse`(T5)；`/mine`、`/mine/adopt-material`、`/published-at`、`/media/persona/{pid}/interview/adopt`(T5)、现有 `/script`。

- [ ] **Step 1: 读现文件确定包裹范围**

先读 `app/templates/media_content.html`，找到"创作台主体"的起止（阶段机之后、复盘之前的：创作辅助 + 口播脚本 + 三平台发布）。目标：`{% if is_reverse %} 精简视图 {% else %} 现有创作台 {% endif %}`，复盘块和阶段机保持在分支外（两种都显示）。

- [ ] **Step 2: 包裹 + 写精简视图**

把"创作辅助 + 口播脚本 + 三平台发布"三块用 `{% if not is_reverse %}...{% endif %}` 包起来（reverse 时不渲染）。在其位置（或页面头部合适处）加 `{% if is_reverse %}` 精简视图块：

```html
{% if is_reverse %}
<div class="module">
  <div class="mh"><span class="ttl" style="font-size:14px">🎬 反向补录 · 已发实况</span></div>
  <div style="padding:0 4px">
    {% if content.idea_reason %}
    <div style="margin:6px 0"><a href="{{ content.idea_reason }}" target="_blank" style="color:var(--accent); font-size:13px">🔗 原视频链接</a></div>
    {% endif %}
    <form method="post" action="/media/content/{{ content.id }}/published-at" style="display:flex; gap:8px; align-items:center; margin:8px 0">
      <label style="font-size:12.5px; color:var(--ink-3)">📅 发布时间</label>
      <input type="date" name="published_at" value="{{ content.published_at or '' }}" style="padding:4px 8px">
      <button class="btn" style="padding:4px 12px; font-size:12px">保存</button>
    </form>
  </div>
</div>

<div class="module">
  <div class="mh"><span class="ttl" style="font-size:14px">平台转写稿</span>
    <span style="color:var(--ink-3); font-size:12px">· 可改错字</span></div>
  <form method="post" action="/media/content/{{ content.id }}/script">
    <textarea name="script" rows="10" style="width:100%; font-size:13px"
              placeholder="平台转写">{{ content.script }}</textarea>
    <button class="btn" style="margin-top:6px; padding:6px 14px; font-size:12.5px">保存转写稿</button>
  </form>
</div>

<div class="module">
  <div class="mh"><span class="ttl" style="font-size:14px">🔬 从转写稿挖精华</span>
    <button onclick="mineEssence()" id="mine-btn" class="btn ai" style="padding:6px 12px; font-size:12px">{{ ic.icon('robot') }}挖精华</button></div>
  <div id="mine-status" style="font-size:12.5px; color:var(--ai); min-height:18px; padding:0 4px"></div>
  <div id="mine-materials"></div>
  <div id="mine-signatures"></div>
</div>

<div class="module">
  <div class="mh"><span class="ttl" style="font-size:14px">🔗 关联到我写过的内容</span></div>
  <div style="padding:4px; font-size:12.5px; color:var(--ink-3)">（规划中）把这条平台实况关联到你当初写过的内容，喂功能B学改稿，让 AI 越来越懂你临场怎么改。</div>
</div>
{% endif %}
```

- [ ] **Step 3: 加挖精华 AJAX**

在页面 `<script>` 里加（**不塞 SVG 进 JS 字符串**，用 `escapeHtml` 防 XSS，失败存 `orig` 还原）：

```javascript
function escapeHtml(s){ const d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML; }

async function mineEssence(){
  const btn=document.getElementById('mine-btn');
  const status=document.getElementById('mine-status');
  const orig=btn.innerHTML;
  btn.disabled=true; btn.textContent='挖料中…';
  status.textContent='AI 正在从转写稿挖精华…';
  try{
    const r=await fetch('/media/content/{{ content.id }}/mine',{method:'POST'});
    const d=await r.json();
    if(!d.ok){ status.textContent='失败：'+(d.error||'未知错误'); btn.disabled=false; btn.innerHTML=orig; return; }
    renderMine('mine-materials','内容素材（→原料库）', d.materials, 'material');
    renderMine('mine-signatures','口头禅/记忆点（→人设）', d.signatures, 'signature');
    status.textContent='挖出 内容料 '+d.materials.length+' 条、口头禅 '+d.signatures.length+' 条。逐条采纳。';
    btn.disabled=false; btn.innerHTML=orig;
  }catch(e){ status.textContent='请求失败：'+e; btn.disabled=false; btn.innerHTML=orig; }
}

function renderMine(elId, title, items, kind){
  const box=document.getElementById(elId);
  if(!items || !items.length){ box.innerHTML=''; return; }
  let h='<div style="font-size:12.5px; color:var(--ink-2); margin:10px 4px 4px">'+title+'</div>';
  items.forEach((it,i)=>{
    const t = kind==='material' ? ('['+escapeHtml(it.type)+'] ') : '';
    h+='<div class="card" style="padding:10px; margin:6px 0" id="'+kind+'-'+i+'">'
      +'<div style="font-size:13px">'+t+escapeHtml(it.content)+'</div>'
      +'<div style="font-size:11.5px; color:var(--ink-3); margin-top:4px">原文：'+escapeHtml(it.evidence)+'</div>'
      +'<div style="font-size:11.5px; color:var(--ai); margin-top:2px">为什么值得留：'+escapeHtml(it.reason)+'</div>'
      +'<div style="margin-top:6px"><button class="btn primary" style="padding:4px 12px; font-size:12px" '
      +'onclick=\'adoptMine("'+kind+'",'+i+')\'>采纳</button> '
      +'<button class="btn ghost" style="padding:4px 12px; font-size:12px" onclick=\'document.getElementById("'+kind+'-'+i+'").remove()\'>丢弃</button></div>'
      +'</div>';
  });
  box.innerHTML=h;
  box['_items']=items;
}

async function adoptMine(kind, i){
  const items=document.getElementById(kind==='material'?'mine-materials':'mine-signatures')['_items'];
  const it=items[i];
  let url, fd=new FormData();
  if(kind==='material'){
    url='/media/content/{{ content.id }}/mine/adopt-material';
    fd.append('type', it.type); fd.append('detail', it.content); fd.append('brief', it.brief||'');
  }else{
    url='/media/persona/{{ content.persona_id }}/interview/adopt';
    fd.append('dimension','signature'); fd.append('content', it.content);
    fd.append('brief', it.brief||''); fd.append('evidence', it.evidence||'');
    fd.append('source','reverse_mine');
  }
  try{
    const r=await fetch(url,{method:'POST', body:fd});
    const el=document.getElementById(kind+'-'+i);
    if(r.ok && el){ el.style.opacity='.5'; el.querySelector('button').outerHTML='<span style="color:var(--success); font-size:12px">已采纳✓</span>'; }
  }catch(e){}
}
```

- [ ] **Step 4: 校验模板可解析**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('app/templates')).get_template('media_content.html'); print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 5: 提交**

```bash
git add app/templates/media_content.html
git commit -m "feat(media): 内容详情页反向精简视图+挖精华AJAX(两桶采纳)"
```

---

### Task 7: 全套回归 + 浏览器 live 验证

**Files:** 无（验证任务）

- [ ] **Step 1: 全套测试绿**

Run: `python -m pytest -q`（看 `${PIPESTATUS[0]}`）
Expected: 全 PASS（229 基线 + 本计划新增 ~13）。假挂→`taskkill //F //IM python.exe` 重跑。

- [ ] **Step 2: 起本地 server + 造一条反向内容**

`preview_start` 起 dev server；用 python 播一条 `idea_source='video_reverse'` 的 media_content（带一段真实口播稿 script），或复用 Task 5 的 seed。签名 cookie 登录。

- [ ] **Step 3: 验反向视图**

打开该内容详情页：确认渲染「🎬反向补录」精简视图、有发布时间输入框、平台转写稿框、挖精华按钮、关联钩子占位；**不出现** AI写脚本/三平台发布/生成文案。`read_console_messages` 无 JS 报错、无 `<script>` 崩。

- [ ] **Step 4: 验挖精华（真调 DeepSeek）**

点「🔬挖精华」→ 看两组候选渲染（内容素材带类型标、口头禅组）、每条带原文+为什么值得留。采纳一条内容料 → DB 查 media_material 有 source='反向挖料'；采纳一条口头禅 → DB 查 media_persona_trait 有 dimension='signature' source='reverse_mine'。console 无错。

- [ ] **Step 5: 验发布时间 + 对照正向内容**

改发布时间保存 → DB 查 published_at 落库。另开一条**正向**内容详情页，确认它仍是完整创作台（is_reverse=False 分支没被误伤）。

- [ ] **Step 6: 截图交付**

`computer` 截反向视图（含挖精华结果）发用户。

---

## 部署提示（用户执行）

```bash
cd /www/wwwroot/ai-pm && git pull && systemctl restart ai-pm
```
（1 条 ADD COLUMN 迁移，重启自动跑。yt-dlp/ffmpeg/豆包凭证已就绪。）

---

## Self-Review 记录

- **Spec 覆盖**：§5 视图(T6)/§6 数据(T1,T3)/§7.1 mine(T4)/§7.2 upload_date(T2)/§7.3 published_at(T3)/§7.4 路由(T5)/§7.5 模板(T6)/§8 原则(贯穿)/§9 测试(各T+T7)/§10 落点全覆盖。
- **无占位符**：每 code step 完整代码。
- **类型一致**：`fetch_audio→(Path, upload_date)` 在 T2 定、T3 解包一致；`mine_from_transcript(db,persona_id,transcript,model)→{ok,materials,signatures,...}` T4 定、T5 消费一致；material source `反向挖料`、signature source `reverse_mine`(已加白名单)、dimension `signature`、`is_reverse`、`published_at` 全程同名。
- **注**：yt-dlp `--no-simulate --print` 组合真机若行为异常（下载+打印冲突）=改用 `--write-info-json` 解析，属 video_fetch 一处隔离，T7 真机验时留意。
