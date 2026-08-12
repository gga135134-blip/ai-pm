# 功能C 视频反向入库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 贴一条已发视频链接 → yt-dlp 抽音频 → 豆包录音文件识别转写 → AI 提「标题+谜题」→ 补建成 media_content（已发阶段）+ publish 记录，纳入飞轮。

**Architecture:** 三个隔离的叶子服务（`video_fetch` 抽音频 / `asr_client` 豆包转写 / `media_ai.extract_from_transcript` AI提选题）+ 一个编排器 `media_reverse` 串起来 + 路由/临时公开音频服务/设置凭证。脆的外部依赖各关一个文件、可换。

**Tech Stack:** Python FastAPI + aiosqlite + httpx（已依赖）+ Jinja2 + vanilla JS + yt-dlp（系统工具，subprocess 调用）。

## Global Constraints

- 豆包 ASR 凭证从 `data/settings.json`（经 `_load_config()`）读，**绝不硬编码**。
- 临时/公开音频：随机 uuid token 文件名，免登录服务，**防目录穿越**（token 只接受 uuid，不含路径分隔符），无论成败在 `finally` 删除。
- **不塞 SVG 图标 `{{ ic.icon() }}` 进 JS 字符串**（已知让整个 `<script>` SyntaxError 崩）；按钮忙碌态用 `textContent`，失败用预存 `orig=btn.innerHTML` 还原。
- 模板改动用 Edit/Write 工具，**禁 PowerShell `-replace`**（毁中文）。
- 适配器隔离：`video_fetch`/`asr_client` 可换，换它们时编排器与路由不动。
- 复用现有表，**零 schema 变更**；`idea_source='video_reverse'` 是新取值（字段已存）。
- fetch/asr 硬失败**不建任何行**；仅 AI 提取失败才建 fallback 内容行（title=链接、puzzle 空、script=转写稿，稿不丢）。
- AI 取值防御：DeepSeek 返回错类型，用现有 `_txt` 兜底。AI 调用记 `log_injection`。
- 运行测试：`cd D:/GAGA-5-25/ai-pm && python -m pytest`。假挂→`taskkill //F //IM python.exe` 重跑。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `app/services/video_fetch.py` | 新建：`fetch_audio(url,out_dir)` yt-dlp 抽音频 + `VideoFetchError` |
| `app/services/asr_client.py` | 新建：`transcribe_url(audio_url,cfg)` 豆包录音文件识别适配器 + `ASRError` |
| `app/services/media_ai.py` | 加 `extract_from_transcript(transcript,model)` + `EXTRACT_SYSTEM` |
| `app/services/media_reverse.py` | 新建：`reverse_ingest(db,persona_id,video_url,model)` 编排器 |
| `app/api/settings.py` | `load_settings` 加豆包凭证默认 + 新 `POST /settings/asr` 保存路由 |
| `app/api/media.py` | 加 `POST /media/reverse-ingest` + `GET /media/asr-audio/{token}` |
| `app/main.py` | 白名单放行 `/media/asr-audio/` |
| `app/templates/media_board.html` | 看板头加「🎬 视频反向入库」入口 + overlay + AJAX |
| `app/templates/settings.html` | 加豆包 ASR 凭证表单块 |
| `tests/` | 各服务/编排器/路由测 |

---

### Task 1: `video_fetch.py` — yt-dlp 抽音频

**Files:**
- Create: `app/services/video_fetch.py`
- Test: `tests/test_video_fetch.py`

**Interfaces:**
- Produces: `class VideoFetchError(Exception)`; `async fetch_audio(url: str, out_dir: Path) -> Path` — 抽出音频文件路径。失败抛 `VideoFetchError`。

- [ ] **Step 1: Write the failing test**

`tests/test_video_fetch.py`：

```python
"""video_fetch 单测：不真跑网络，桩掉 subprocess。"""
import asyncio
from pathlib import Path

import pytest

from app.services import video_fetch
from app.services.video_fetch import fetch_audio, VideoFetchError


class _FakeProc:
    def __init__(self, returncode, made_file=None):
        self.returncode = returncode
        self._made = made_file

    async def communicate(self):
        if self._made:
            self._made.write_bytes(b"fake-audio")
        return (b"out", b"err")


def test_fetch_audio_success(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        # yt-dlp 输出模板决定文件名；模拟它产出一个 mp3
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out)

    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    # 让 fetch_audio 用固定文件名，便于断言（见实现：out_dir/<uuid>.mp3）
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    p = asyncio.run(fetch_audio("https://v.douyin.com/xxx/", tmp_path))
    assert p.exists() and p.suffix == ".mp3"


def test_fetch_audio_nonzero_raises(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(1)
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError):
        asyncio.run(fetch_audio("https://bad/", tmp_path))


def test_fetch_audio_no_output_file_raises(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(0)  # 退出 0 但没产文件
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError):
        asyncio.run(fetch_audio("https://x/", tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_fetch.py -v`
Expected: FAIL（模块不存在 / ImportError）

- [ ] **Step 3: Write the implementation**

`app/services/video_fetch.py`：

```python
"""链接→音频：用 yt-dlp 抽已发视频的音频。脆弱环节隔离在此，可换解析源。"""
import asyncio
import logging
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_TIMEOUT = 120  # 秒，防挂起


class VideoFetchError(Exception):
    """拿不到视频音频（平台防爬、链接失效、yt-dlp 出错）。"""


async def fetch_audio(url: str, out_dir: Path) -> Path:
    """用 yt-dlp 把 url 的音频抽成 mp3 到 out_dir/<uuid>.mp3，返回路径。

    失败（yt-dlp 非零退出 / 超时 / 没产出文件）抛 VideoFetchError。
    一期不带 cookie，尽力而为。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = str(uuid.uuid4())
    target = out_dir / f"{name}.mp3"
    # -x 抽音频，--audio-format mp3，-o 固定输出名（%(ext)s 会被替换成 mp3）
    args = ["yt-dlp", "-x", "--audio-format", "mp3",
            "--no-playlist", "-o", str(out_dir / f"{name}.%(ext)s"), url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise VideoFetchError("拿不到视频音频（下载超时）")
    except FileNotFoundError:
        raise VideoFetchError("服务器未安装 yt-dlp（pip install yt-dlp）")
    if proc.returncode != 0:
        log.warning("yt-dlp 失败 rc=%s err=%s", proc.returncode, (err or b"")[:500])
        raise VideoFetchError("拿不到视频音频（平台可能防爬或链接失效）")
    if not target.exists():
        raise VideoFetchError("拿不到视频音频（未生成音频文件）")
    return target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_fetch.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/video_fetch.py tests/test_video_fetch.py
git commit -m "feat(media): video_fetch 用 yt-dlp 抽视频音频"
```

---

### Task 2: `asr_client.py` — 豆包录音文件识别适配器

**Files:**
- Create: `app/services/asr_client.py`
- Test: `tests/test_asr_client.py`

**Interfaces:**
- Produces: `class ASRError(Exception)`; `async transcribe_url(audio_url: str, cfg: dict) -> str` — 提交音频 URL 给豆包录音文件识别、轮询取全文。cfg 需含 `app_id`/`access_key`/`resource_id`（可选 `base_url`）。失败/超时抛 `ASRError`。

**说明：** 按火山「录音文件识别大模型」v3（bigmodel，投 URL 异步）实现。base_url/resource_id 走 cfg，便于对齐用户实际控制台或换代。单测桩掉 httpx，真机 e2e 在服务器验。

- [ ] **Step 1: Write the failing test**

`tests/test_asr_client.py`：

```python
"""asr_client 单测：桩掉 httpx，不真调豆包。"""
import asyncio

import pytest

from app.services import asr_client
from app.services.asr_client import transcribe_url, ASRError

_CFG = {"app_id": "APPID", "access_key": "AK", "resource_id": "volc.bigasr.auc"}


class _Resp:
    def __init__(self, status_code=200, headers=None, json_body=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_body or {}

    def json(self):
        return self._json


class _FakeClient:
    """按预设脚本依次返回响应。submit 一次 + query 若干次。"""
    def __init__(self, script):
        self._script = list(script)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        return self._script.pop(0)


def _patch(monkeypatch, script):
    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda *a, **k: _FakeClient(script))
    monkeypatch.setattr(asr_client.asyncio, "sleep", lambda *a, **k: asyncio.sleep(0))


def test_transcribe_success_after_polling(monkeypatch):
    script = [
        _Resp(headers={"X-Api-Status-Code": "20000000"}),                       # submit 受理
        _Resp(headers={"X-Api-Status-Code": "20000001"}),                       # query 处理中
        _Resp(headers={"X-Api-Status-Code": "20000000"},                        # query 完成
              json_body={"result": {"text": "老板买了一堆AI工具还是用不起来"}}),
    ]
    _patch(monkeypatch, script)
    text = asyncio.run(transcribe_url("http://pub/aud.mp3", _CFG))
    assert "AI工具" in text


def test_transcribe_submit_rejected_raises(monkeypatch):
    _patch(monkeypatch, [_Resp(status_code=403, headers={"X-Api-Status-Code": "40000"})])
    with pytest.raises(ASRError):
        asyncio.run(transcribe_url("http://pub/aud.mp3", _CFG))


def test_transcribe_timeout_raises(monkeypatch):
    # submit ok，之后一直处理中 → 触发轮询上限
    script = [_Resp(headers={"X-Api-Status-Code": "20000000"})] + \
             [_Resp(headers={"X-Api-Status-Code": "20000001"})] * 200
    _patch(monkeypatch, script)
    monkeypatch.setattr(asr_client, "_MAX_POLLS", 3)
    with pytest.raises(ASRError):
        asyncio.run(transcribe_url("http://pub/aud.mp3", _CFG))


def test_transcribe_missing_creds_raises(monkeypatch):
    with pytest.raises(ASRError):
        asyncio.run(transcribe_url("http://pub/aud.mp3", {"app_id": "", "access_key": ""}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_asr_client.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: Write the implementation**

`app/services/asr_client.py`：

```python
"""豆包/火山「录音文件识别大模型」(bigmodel, 投 URL 异步) 适配器。

接口稳定：transcribe_url(audio_url, cfg) -> 全文。换 ASR 只动此文件。
凭证/base_url 全从 cfg（settings.json），不硬编码。
"""
import asyncio
import logging
import uuid

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
_MAX_POLLS = 60          # 轮询上限次
_POLL_INTERVAL = 3       # 秒
_STATUS_DONE = "20000000"
_STATUS_PROCESSING = {"20000001", "20000002"}


class ASRError(Exception):
    """转写失败或超时。"""


def _headers(cfg: dict, request_id: str) -> dict:
    return {
        "X-Api-App-Key": cfg.get("app_id", ""),
        "X-Api-Access-Key": cfg.get("access_key", ""),
        "X-Api-Resource-Id": cfg.get("resource_id") or "volc.bigasr.auc",
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
        "Content-Type": "application/json",
    }


async def transcribe_url(audio_url: str, cfg: dict) -> str:
    """提交音频 URL → 轮询 → 返回转写全文。失败/超时抛 ASRError。"""
    if not cfg.get("app_id") or not cfg.get("access_key"):
        raise ASRError("未配置豆包 ASR 凭证")
    base = (cfg.get("base_url") or _DEFAULT_BASE).rstrip("/")
    request_id = str(uuid.uuid4())
    submit_body = {
        "user": {"uid": "ai-pm"},
        "audio": {"url": audio_url, "format": "mp3"},
        "request": {"model_name": "bigmodel"},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base}/submit", headers=_headers(cfg, request_id),
                              json=submit_body)
        if r.headers.get("X-Api-Status-Code") != _STATUS_DONE:
            raise ASRError(f"提交转写任务失败（{r.headers.get('X-Api-Status-Code')}）")

        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            q = await client.post(f"{base}/query", headers=_headers(cfg, request_id),
                                  json={})
            status = q.headers.get("X-Api-Status-Code", "")
            if status == _STATUS_DONE:
                return _extract_text(q.json())
            if status in _STATUS_PROCESSING:
                continue
            raise ASRError(f"转写失败（{status}）")
    raise ASRError("转写超时")


def _extract_text(body: dict) -> str:
    """从 query 响应体拿全文。兼容 result.text / result.utterances 拼接。"""
    result = body.get("result") or {}
    if isinstance(result, dict):
        if result.get("text"):
            return str(result["text"]).strip()
        utts = result.get("utterances") or []
        if utts:
            return "".join(u.get("text", "") for u in utts if isinstance(u, dict)).strip()
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_asr_client.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/asr_client.py tests/test_asr_client.py
git commit -m "feat(media): asr_client 豆包录音文件识别适配器(投URL异步轮询)"
```

---

### Task 3: `extract_from_transcript` — AI 从稿提选题

**Files:**
- Modify: `app/services/media_ai.py`（加 `EXTRACT_SYSTEM` + `extract_from_transcript`）

**Interfaces:**
- Consumes: 现有 `ask_ai` / `extract_json` / `_txt` / `log_injection`。
- Produces: `async extract_from_transcript(transcript: str, model: str = "auto") -> dict` — 返回 `{"title","puzzle","topic_fingerprint"}`（提取失败返回 `{}`）。

- [ ] **Step 1: Write the implementation（AI 能力无单测，靠 Task 6 路由 stub + 真机验；此步做 import 冒烟）**

在 `app/services/media_ai.py` 末尾加。函数**不持 db、不调 log_injection**（成本由编排器统一记）；返回值带 `_cost`/`_tokens` 供编排器记账，`title` 存在与否作为"提取成功"判据：

```python
EXTRACT_SYSTEM = """你是自媒体选题分析师。给你一段口播视频的转写文字，你要提炼出：
1. title：这条视频的选题标题（≤20 字，点出主题）
2. puzzle：核心谜题（受众想解开的那个疑问，带钩子；提炼不出就给空串）
3. topic_fingerprint：3-6 个用顿号分隔的主题标签（供查重，如"AI落地、中小企业、避坑"）

只输出 JSON 对象，不要任何解释：
{"title":"...","puzzle":"...","topic_fingerprint":"..."}"""


async def extract_from_transcript(transcript: str, model: str = "auto") -> dict:
    """从视频转写稿提「标题+谜题+话题指纹」。

    提取失败返回不含 title 的 dict（调用方据 title 缺失走 fallback）。
    不持 db、不记 log_injection；成本由编排器 reverse_ingest 统一记（带 _cost/_tokens 出去）。
    """
    snippet = (transcript or "").strip()[:6000]  # 控成本，前 6000 字够判主题
    if not snippet:
        return {}
    result = await ask_ai(f"转写文字：\n{snippet}", model=model,
                          task_type="media_topic", system_prompt=EXTRACT_SYSTEM,
                          json_mode=True)
    meta = {"_cost": result.get("cost", 0), "_tokens": result.get("tokens", 0)}
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return meta
    obj = extract_json(resp, expect="object")
    if not isinstance(obj, dict) or not obj:
        return meta
    return {
        "title": _txt(obj.get("title")),
        "puzzle": _txt(obj.get("puzzle")),
        "topic_fingerprint": _txt(obj.get("topic_fingerprint")),
        **meta,
    }
```

- [ ] **Step 2: 冒烟——import 不报错**

Run: `python -c "from app.services.media_ai import extract_from_transcript, EXTRACT_SYSTEM; print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 3: Commit**

```bash
git add app/services/media_ai.py
git commit -m "feat(media): extract_from_transcript 从转写稿提选题+指纹"
```

---

### Task 4: `media_reverse.py` — 编排器

**Files:**
- Create: `app/services/media_reverse.py`
- Test: `tests/test_media_reverse.py`

**Interfaces:**
- Consumes: `video_fetch.fetch_audio`(T1)、`asr_client.transcribe_url`(T2)、`media_ai.extract_from_transcript`(T3)、`media_context.log_injection`。
- Produces: `async reverse_ingest(db, persona_id: str, video_url: str, cfg: dict, public_base: str, audio_dir: Path, model: str = "auto") -> dict` — 返回 `{"ok","content_id","title","error"}`。

**说明：** `cfg`=豆包凭证 dict；`public_base`=对外可达前缀（如 `http://159.75.200.213:8000`）；`audio_dir`=公开音频目录（Path）。这些由路由从 settings/config 传入，编排器不自己读全局，便于测试注入。

- [ ] **Step 1: Write the failing test**

`tests/test_media_reverse.py`：

```python
"""编排器单测：桩掉 fetch/asr/extract，隔离到临时 DB。"""
import asyncio
from pathlib import Path

import pytest

from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_reverse
from app.services.media_reverse import reverse_ingest
from app.services.video_fetch import VideoFetchError
from app.services.asr_client import ASRError


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reverse_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid="REVP", with_account=True):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES (?,?,?,?)", (pid, "测试", "一句话", "涨粉"))
            if with_account:
                await db.execute(
                    "INSERT INTO media_account (id,persona_id,platform,account_name) "
                    "VALUES (?,?,?,?)", ("ACC_"+pid, pid, "douyin", "测试号"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def _patch(monkeypatch, audio_ok=True, asr_text="老板买AI工具用不起来", extract=None):
    async def fake_fetch(url, out_dir):
        if not audio_ok:
            raise VideoFetchError("拿不到")
        p = Path(out_dir) / "a.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p
    async def fake_asr(audio_url, cfg):
        if asr_text is None:
            raise ASRError("转写失败")
        return asr_text
    async def fake_extract(transcript, model="auto"):
        return extract if extract is not None else {
            "title": "老板买AI工具用不起来", "puzzle": "卡在哪", "topic_fingerprint": "AI落地",
            "_cost": 0, "_tokens": 10}
    monkeypatch.setattr(media_reverse, "fetch_audio", fake_fetch)
    monkeypatch.setattr(media_reverse, "transcribe_url", fake_asr)
    monkeypatch.setattr(media_reverse, "extract_from_transcript", fake_extract)


def _run(pid, tmp_path):
    async def go():
        db = await get_db()
        try:
            return await reverse_ingest(
                db, pid, "https://v.douyin.com/x/", cfg={"app_id": "A", "access_key": "K"},
                public_base="http://pub", audio_dir=tmp_path / "pub")
        finally:
            await db.close()
    return asyncio.run(go())


def _content_rows(pid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT stage,idea_source,title,script,topic_fingerprint FROM media_content "
                "WHERE persona_id=?", (pid,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    return asyncio.run(go())


def test_success_creates_published_content_and_publish(tmp_path, monkeypatch):
    _seed_persona("REVP1")
    _patch(monkeypatch)
    res = _run("REVP1", tmp_path)
    assert res["ok"] and res["content_id"]
    rows = _content_rows("REVP1")
    assert len(rows) == 1
    assert rows[0]["stage"] == "published"
    assert rows[0]["idea_source"] == "video_reverse"
    assert rows[0]["script"] == "老板买AI工具用不起来"
    assert rows[0]["topic_fingerprint"] == "AI落地"


def test_audio_dir_cleaned_after_success(tmp_path, monkeypatch):
    _seed_persona("REVP2")
    _patch(monkeypatch)
    _run("REVP2", tmp_path)
    # 公开音频目录里不应残留文件
    pub = tmp_path / "pub"
    assert not any(pub.glob("*.mp3")) if pub.exists() else True


def test_fetch_fail_creates_no_row(tmp_path, monkeypatch):
    _seed_persona("REVP3")
    _patch(monkeypatch, audio_ok=False)
    res = _run("REVP3", tmp_path)
    assert not res["ok"] and res["error"]
    assert _content_rows("REVP3") == []


def test_asr_fail_creates_no_row(tmp_path, monkeypatch):
    _seed_persona("REVP4")
    _patch(monkeypatch, asr_text=None)
    res = _run("REVP4", tmp_path)
    assert not res["ok"]
    assert _content_rows("REVP4") == []


def test_extract_fail_creates_fallback_row(tmp_path, monkeypatch):
    _seed_persona("REVP5")
    _patch(monkeypatch, extract={"_cost": 0, "_tokens": 5})  # 无 title = 提取失败
    res = _run("REVP5", tmp_path)
    assert res["ok"] and res["content_id"]
    rows = _content_rows("REVP5")
    assert len(rows) == 1
    assert rows[0]["title"] == "https://v.douyin.com/x/"   # 链接兜底
    assert rows[0]["script"] == "老板买AI工具用不起来"       # 稿不丢


def test_no_account_creates_content_without_publish(tmp_path, monkeypatch):
    _seed_persona("REVP6", with_account=False)
    _patch(monkeypatch)
    res = _run("REVP6", tmp_path)
    assert res["ok"] and res["content_id"]

    async def pub_count():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) c FROM media_publish p JOIN media_content c "
                "ON p.content_id=c.id WHERE c.persona_id='REVP6'")
            return (await cur.fetchone())["c"]
        finally:
            await db.close()
    assert asyncio.run(pub_count()) == 0   # 没号 → 不建 publish
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_reverse.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: Write the implementation**

`app/services/media_reverse.py`：

```python
"""功能C 编排器：链接→音频→ASR→提选题→建 content。串起脆弱外部依赖。"""
import logging
import uuid
from pathlib import Path

from app.services.video_fetch import fetch_audio, VideoFetchError
from app.services.asr_client import transcribe_url, ASRError
from app.services.media_ai import extract_from_transcript
from app.services.media_context import log_injection

log = logging.getLogger(__name__)


async def reverse_ingest(db, persona_id: str, video_url: str, cfg: dict,
                         public_base: str, audio_dir: Path,
                         model: str = "auto") -> dict:
    """串 ①抽音频 ②托管 ③ASR ④提选题 ⑤建content+publish ⑥清理。

    cfg=豆包凭证；public_base=对外可达前缀；audio_dir=公开音频目录。
    fetch/asr 硬失败不建行；extract 失败建 fallback 行（稿不丢）。
    返回 {ok, content_id, title, error}。
    """
    audio_dir = Path(audio_dir)
    audio_file = None
    try:
        # ① 抽音频到公开目录（文件名即随机 token）
        try:
            audio_file = await fetch_audio(video_url, audio_dir)
        except VideoFetchError as e:
            return {"ok": False, "content_id": "", "title": "", "error": str(e)}

        token = audio_file.name  # <uuid>.mp3
        public_url = f"{public_base.rstrip('/')}/media/asr-audio/{token}"

        # ③ ASR
        try:
            transcript = await transcribe_url(public_url, cfg)
        except ASRError as e:
            return {"ok": False, "content_id": "", "title": "", "error": str(e)}
        if not transcript.strip():
            return {"ok": False, "content_id": "", "title": "", "error": "转写结果为空"}

        # ④ 提选题（失败兜底）
        ext = await extract_from_transcript(transcript, model=model)
        if ext.get("_tokens"):
            await log_injection(db, "", "extract_from_transcript", [], ext["_tokens"])
        title = ext.get("title") or video_url          # 兜底用链接
        puzzle = ext.get("puzzle", "")
        fingerprint = ext.get("topic_fingerprint", "")

        # ⑤ 建 content（已发）
        content_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_content "
            "(id,persona_id,title,puzzle,stage,idea_source,idea_reason,script,"
            " topic_fingerprint) VALUES (?,?,?,?,'published','video_reverse',?,?,?)",
            (content_id, persona_id, title, puzzle, video_url, transcript, fingerprint))

        # 挂 publish 到该人设第一个 account；没号则只建 content
        cur = await db.execute(
            "SELECT id FROM media_account WHERE persona_id=? ORDER BY created_at LIMIT 1",
            (persona_id,))
        acc = await cur.fetchone()
        if acc:
            await db.execute(
                "INSERT INTO media_publish "
                "(id,content_id,account_id,post_url,published_at,status) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP,'published')",
                (str(uuid.uuid4()), content_id, acc["id"], video_url))
        await db.commit()
        return {"ok": True, "content_id": content_id, "title": title, "error": ""}
    finally:
        # ⑥ 清理临时音频（成败都删）
        try:
            if audio_file and Path(audio_file).exists():
                Path(audio_file).unlink()
        except OSError:
            log.warning("清理临时音频失败：%s", audio_file)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_reverse.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/media_reverse.py tests/test_media_reverse.py
git commit -m "feat(media): reverse_ingest 编排器串起视频反向入库全链路"
```

---

### Task 5: 豆包 ASR 凭证进设置

**Files:**
- Modify: `app/api/settings.py`（`load_settings` 默认 + 新 `POST /settings/asr`）
- Modify: `app/templates/settings.html`（凭证表单块）
- Test: `tests/test_settings_asr.py`

**Interfaces:**
- Produces: settings.json 里 `douyin_asr`={"app_id","access_key","resource_id"}；`POST /settings/asr` 保存。

- [ ] **Step 1: Write the failing test**

`tests/test_settings_asr.py`（两个测试都 monkeypatch `CONFIG_FILE` 到 tmp，**绝不动用户真实 settings.json**）：

```python
"""豆包 ASR 凭证保存路由测试。CONFIG_FILE 隔离到 tmp，不碰真配置。"""
import base64
import json

from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_or_create_session_secret
import app.api.settings as st


def _client():
    signer = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", signer.sign(
        base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


def test_load_settings_has_asr_default(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CONFIG_FILE", tmp_path / "s.json")
    cfg = st.load_settings()
    assert "douyin_asr" in cfg and cfg["douyin_asr"]["app_id"] == ""


def test_save_asr_creds_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CONFIG_FILE", tmp_path / "s.json")
    _client().post("/settings/asr", data={
        "douyin_asr_app_id": "APP123", "douyin_asr_access_key": "AK123",
        "douyin_asr_resource_id": "volc.bigasr.auc"}, follow_redirects=False)
    cfg = st.load_settings()
    assert cfg["douyin_asr"]["app_id"] == "APP123"
    assert cfg["douyin_asr"]["access_key"] == "AK123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_asr.py -v`
Expected: FAIL（路由不存在 / 无 douyin_asr 默认）

- [ ] **Step 3: Write the implementation**

在 `app/api/settings.py` 的 `load_settings` defaults 里加一行（`"feishu_media_map"` 后）：

```python
        "douyin_asr": {"app_id": "", "access_key": "", "resource_id": "volc.bigasr.auc"},
```

在 `settings.py` 末尾加保存路由：

```python
@router.post("/settings/asr")
async def settings_save_asr(
    douyin_asr_app_id: str = Form(""),
    douyin_asr_access_key: str = Form(""),
    douyin_asr_resource_id: str = Form("volc.bigasr.auc"),
):
    save_settings({"douyin_asr": {
        "app_id": douyin_asr_app_id.strip(),
        "access_key": douyin_asr_access_key.strip(),
        "resource_id": douyin_asr_resource_id.strip() or "volc.bigasr.auc",
    }})
    return RedirectResponse("/settings?msg=豆包ASR凭证已保存", status_code=303)
```

确认文件顶部已 `from fastapi import ... Form`（已在，settings_save 用了 Form）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings_asr.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 加设置页表单块**

在 `app/templates/settings.html` 里，找到飞书那块表单之后（或任一 `</form>` 之后合适位置），加：

```html
<form method="post" action="/settings/asr" style="margin-top:16px">
  <h3 style="font-size:15px; margin-bottom:8px">豆包录音文件识别（视频反向入库用）</h3>
  <label>App ID
    <input name="douyin_asr_app_id" value="{{ config.douyin_asr.app_id }}" style="width:100%">
  </label>
  <label>Access Key
    <input name="douyin_asr_access_key" value="{{ config.douyin_asr.access_key }}" style="width:100%">
  </label>
  <label>Resource ID
    <input name="douyin_asr_resource_id" value="{{ config.douyin_asr.resource_id }}" style="width:100%">
  </label>
  <button class="btn primary" style="margin-top:8px">保存豆包 ASR</button>
</form>
```

（类名/样式对齐 settings.html 现有风格即可；关键是 name 与路由 Form 参数一致。）

- [ ] **Step 6: 校验模板可解析**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('app/templates')).get_template('settings.html'); print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 7: Commit**

```bash
git add app/api/settings.py app/templates/settings.html tests/test_settings_asr.py
git commit -m "feat(media): 设置页加豆包 ASR 凭证 + /settings/asr 保存"
```

---

### Task 6: 路由 — reverse-ingest + 公开音频服务 + 白名单

**Files:**
- Modify: `app/api/media.py`（两个路由）
- Modify: `app/main.py`（白名单）
- Test: `tests/test_media_routes.py`

**Interfaces:**
- Consumes: `reverse_ingest`(T4)、`load_settings`/`_load_config` 取 `douyin_asr`。
- Produces: `POST /media/reverse-ingest`（form `video_url`）→ JSON；`GET /media/asr-audio/{token}` 公开返回音频。

**说明：** 公开音频目录定为 `data/asr_public/`；`public_base` 取请求的 scheme+host（`str(request.base_url).rstrip('/')`）。

- [ ] **Step 1: Write the failing test**

在 `tests/test_media_routes.py` 末尾加：

```python
def test_reverse_ingest_no_creds_returns_ok_false(monkeypatch):
    _only_active_persona()
    # 桩掉 _load_config 让 douyin_asr 为空
    import app.api.media as media_mod
    monkeypatch.setattr(media_mod, "_load_config", lambda: {})
    r = _client().post("/media/reverse-ingest", data={"video_url": "https://v.douyin.com/x/"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_reverse_ingest_calls_orchestrator(monkeypatch):
    _only_active_persona()
    import app.api.media as media_mod
    monkeypatch.setattr(media_mod, "_load_config",
                        lambda: {"douyin_asr": {"app_id": "A", "access_key": "K"}})

    async def fake_ingest(db, pid, url, cfg, public_base, audio_dir, model="auto"):
        return {"ok": True, "content_id": "CX", "title": "标题", "error": ""}
    monkeypatch.setattr(media_mod, "reverse_ingest", fake_ingest)

    r = _client().post("/media/reverse-ingest", data={"video_url": "https://v.douyin.com/x/"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["content_id"] == "CX"


def test_asr_audio_public_and_traversal_guarded(tmp_path, monkeypatch):
    import app.api.media as media_mod
    monkeypatch.setattr(media_mod, "ASR_PUBLIC_DIR", tmp_path)
    (tmp_path / "abc.mp3").write_bytes(b"AUDIO")
    # 免登录 client（不塞 cookie）
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/media/asr-audio/abc.mp3").content == b"AUDIO"     # 公开可达
    assert c.get("/media/asr-audio/nope.mp3").status_code == 404
    assert c.get("/media/asr-audio/..%2f..%2fsecret").status_code == 404  # 穿越挡掉
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_routes.py -k "reverse_ingest or asr_audio" -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: media.py 加 import + 常量 + 路由**

`app/api/media.py` 顶部加 import（与现有 import 同区）：

```python
from pathlib import Path as _Path
from fastapi.responses import FileResponse
from app.services.media_reverse import reverse_ingest
```

在模块级加常量（靠近其它常量）：

```python
ASR_PUBLIC_DIR = BASE_DIR / "data" / "asr_public"
```

加两个路由（放在选题相关路由附近）：

```python
@router.post("/media/reverse-ingest")
async def media_reverse_ingest(request: Request, video_url: str = Form(...)):
    """功能C：贴已发视频链接→转写→AI提选题→建 content。"""
    url = video_url.strip()
    if not url:
        return JSONResponse({"ok": False, "error": "请填视频链接"})
    cfg = (_load_config().get("douyin_asr") or {})
    if not cfg.get("app_id") or not cfg.get("access_key"):
        return JSONResponse({"ok": False, "error": "未配置豆包 ASR 凭证，去设置页填"})
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        public_base = str(request.base_url).rstrip("/")
        try:
            result = await reverse_ingest(db, pid, url, cfg, public_base, ASR_PUBLIC_DIR)
        except Exception as e:
            log.exception("视频反向入库失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.get("/media/asr-audio/{token}")
async def media_asr_audio(token: str):
    """公开免登录返回临时音频（供豆包 ASR 抓）。防目录穿越。"""
    if "/" in token or "\\" in token or ".." in token:
        return JSONResponse({"error": "bad token"}, status_code=404)
    path = _Path(ASR_PUBLIC_DIR) / token
    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(path), media_type="audio/mpeg")
```

> 确认 `BASE_DIR`、`Request`、`Form`、`JSONResponse`、`_load_config`、`log` 均已在 media.py 导入（现有代码已用）。

- [ ] **Step 4: main.py 白名单放行**

`app/main.py` 的 `_AuthMiddleware.__call__` 里，把 `/media/asr-audio/` 加进免登录判断：

```python
        if (path.startswith("/static") or path.startswith("/s/")
                or path.startswith("/media/asr-audio/") or path in _PUBLIC):
            await self.app(scope, receive, send)
            return
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_media_routes.py -k "reverse_ingest or asr_audio" -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add app/api/media.py app/main.py tests/test_media_routes.py
git commit -m "feat(media): /media/reverse-ingest 路由 + 公开音频服务 + 白名单"
```

---

### Task 7: 看板入口「🎬 视频反向入库」

**Files:**
- Modify: `app/templates/media_board.html`（头部入口 + overlay + AJAX）

**Interfaces:**
- Consumes: `POST /media/reverse-ingest`(T6)。

- [ ] **Step 1: 加头部按钮**

在 `app/templates/media_board.html` 头部按钮区（`未对上的数据` 那行 `<a>` 之后，约 46 行）加：

```html
    <button onclick="document.getElementById('rev-overlay').classList.add('open')" class="btn" style="padding:6px 12px; font-size:12.5px">🎬 视频反向入库</button>
```

- [ ] **Step 2: 加 overlay + AJAX**

在 `media_board.html` 的 `new-overlay` 那个浮层附近（同级）加一个反向入库浮层，并在 `<script>` 里加 `reverseIngest()`。**不塞 SVG 进 JS**：

```html
<div id="rev-overlay" class="overlay">
  <div class="sheet">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
      <b>视频反向入库</b>
      <button onclick="document.getElementById('rev-overlay').classList.remove('open')" class="iconbtn">{{ ic.icon('x') }}</button>
    </div>
    <p style="font-size:12.5px; color:var(--ink-3); margin-bottom:8px">贴一条已发视频链接，AI 转写并提选题，补建成已发内容。</p>
    <input id="rev-url" placeholder="抖音/小红书 视频链接" style="width:100%; margin-bottom:8px">
    <div id="rev-status" style="font-size:12.5px; color:var(--ai); min-height:18px; margin-bottom:8px"></div>
    <button onclick="reverseIngest()" id="rev-btn" class="btn primary" style="width:100%; justify-content:center">开始（可能要 1 分钟）</button>
  </div>
</div>
```

`<script>` 里加：

```javascript
async function reverseIngest(){
  const btn = document.getElementById('rev-btn');
  const status = document.getElementById('rev-status');
  const url = document.getElementById('rev-url').value.trim();
  if(!url){ status.textContent = '请先贴链接'; return; }
  const orig = btn.innerHTML;
  btn.disabled = true; btn.textContent = '处理中…（转写较慢，请勿关闭）';
  status.textContent = '正在抽音频 → 转写 → 提选题…';
  try {
    const fd = new FormData(); fd.append('video_url', url);
    const r = await fetch('/media/reverse-ingest', {method:'POST', body: fd});
    const d = await r.json();
    if(d.ok){
      status.textContent = '已入库：' + (d.title||'') + '，跳转中…';
      window.location = '/media/content/' + d.content_id;
    } else {
      status.textContent = '失败：' + (d.error||'未知错误');
      btn.disabled = false; btn.innerHTML = orig;
    }
  } catch(e){
    status.textContent = '请求失败：' + e;
    btn.disabled = false; btn.innerHTML = orig;
  }
}
```

> 若 `media_board.html` 没有 `.overlay/.sheet` 样式，沿用 `new-overlay` 用的同套类名（读该文件确认后对齐；关键是 open class 切换 + 表单结构）。

- [ ] **Step 3: 校验模板可解析**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('app/templates')).get_template('media_board.html'); print('ok')"`
Expected: 打印 `ok`

- [ ] **Step 4: Commit**

```bash
git add app/templates/media_board.html
git commit -m "feat(media): 看板加「视频反向入库」入口+AJAX"
```

---

### Task 8: 回归 + 本地流程验证（真机 e2e 延后到服务器）

**Files:** 无（验证任务）

- [ ] **Step 1: 全套测试绿**

Run: `python -m pytest -q`
Expected: 全 PASS（202 基线 + 本计划新增 ~18）。假挂→`taskkill //F //IM python.exe` 重跑。

- [ ] **Step 2: 起本地 server，验入口不崩**

`preview_start`（ai-pm dev server），登录（测试签名 cookie），开 `/media`，点「🎬 视频反向入库」看浮层弹出、`read_console_messages` 无 JS 报错、无 `<script>` 崩。

- [ ] **Step 3: 验无凭证/无音频的错误路径**

本地没配豆包 → 点开始应回"未配置豆包 ASR 凭证"。或临时在 settings.json 配假凭证 + 贴一个 yt-dlp 拿不到的链接 → 应回清楚错误、页面不建垃圾内容。用 DB 抽查 `SELECT count(*) FROM media_content WHERE idea_source='video_reverse'` 确认没脏行。

- [ ] **Step 4: 交付说明**

给用户列出**服务器上线前置**：①venv 里 `pip install yt-dlp` ②设置页填豆包 ASR 凭证 ③服务器 `git pull && systemctl restart ai-pm`（零 migration）。真机 e2e（真链接→真豆包→真内容）由用户在服务器配好凭证后实测。

---

## 部署提示（用户执行）

```bash
cd /www/wwwroot/ai-pm && source venv/bin/activate && pip install yt-dlp && git pull && systemctl restart ai-pm
```

设置页填豆包录音文件识别的 App ID / Access Key / Resource ID，然后看板点「🎬 视频反向入库」贴链接实测。**零 schema 迁移**（复用现有表）。

---

## Self-Review 记录

- **Spec 覆盖**：§2 管线(T1-4)/§3.1 video_fetch(T1)/§3.2 asr_client(T2)/§3.3 extract(T3)/§3.4 media_reverse(T4)/§3.5 路由(T6)/§3.6 白名单(T6)/§3.7 凭证(T5)/§3.8 入口(T7)/§4 落库(T4)/§5 失败处理(T4)/§7 测试(各T+T8)/§8 落点全覆盖。
- **无占位符**：每 code step 有完整代码。
- **类型一致**：`fetch_audio(url,out_dir)→Path`、`transcribe_url(audio_url,cfg)→str`、`extract_from_transcript(transcript,model)→dict`、`reverse_ingest(db,persona_id,video_url,cfg,public_base,audio_dir,model)→{ok,content_id,title,error}` 全程签名一致；编排器 monkeypatch 的名字（`fetch_audio`/`transcribe_url`/`extract_from_transcript`）与其在 media_reverse 里的 import 名一致；`idea_source='video_reverse'`、`stage='published'`、`ASR_PUBLIC_DIR`、`douyin_asr` 全程同名。
- **注**：asr_client 按火山 bigmodel v3 实现，真机若接口代次不同=适配器隔离，改一个文件；单测桩 httpx 不依赖真 API。
