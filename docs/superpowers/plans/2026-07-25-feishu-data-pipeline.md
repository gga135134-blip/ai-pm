# 飞书数据管道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让自己账号的数据从飞书多维表格自动同步进 AI-PM 的 `media_metrics`，把「手动抄数据」从飞轮里去掉。

**Architecture:** 飞书连接器（飞书侧配，非本代码）把三平台数据抓进多维表格 Base；AI-PM 用飞书 OpenAPI 读表 → 按 post_url（标题兜底）匹配到已有 `media_publish` → 写 metrics 快照（`collected_by='feishu'`）。读飞书的能力（`feishu_client.py`）建成系统层通用能力，自媒体第一个用。匹配不上的行持久化进 `media_feishu_unmatched`，UI 标色让用户手动补。

**Tech Stack:** Python 3.14 / FastAPI / Jinja2 / SQLite (aiosqlite) / httpx / pydantic-settings。复用一期 `media_metrics.normalize_metrics`。

## Global Constraints

- **不加新 Python 依赖**。httpx、aiosqlite、pydantic 已在。项目**无 pytest-asyncio**——纯函数写同步测试；异步+DB 逻辑用 TestClient live 测（伪造签名 session cookie，不输真实密码）。
- 测试命令：`cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`（当前基线 73 passed）。用 Bash（Git Bash/POSIX sh），工作目录不持久，每条命令前加 `cd /d/GAGA-5-25/ai-pm &&`。
- **ID 一律 `str(uuid.uuid4())`**；所有 `id` 列 `TEXT PRIMARY KEY`。
- **密钥只进 `.env`/`settings.json`（均已 gitignore），绝不进代码/Git。**
- **数据真实性红线**：飞书拿不到的指标**绝不填 0 冒充**，标 `missing_fields` 让 UI 显示「待手填」。
- **只读飞书不写飞书**（单向）。
- 前端：无新框架，vanilla JS，本地裁剪版 tailwind（用色前 grep 确认存在）；响应式用 `@media (max-width:767px)`，**不用 Tailwind `md:`**。
- `TemplateResponse` 必须三参数（用现有 `_tpl` helper）；Jinja2 无 `tojson`，用 `json.dumps()+|safe`；**模板 dict 键别用 `items`/`keys`/`values`/`get`**（会撞 dict 方法致渲染崩，一期踩过）。
- **不用 PowerShell `-replace` 改 UTF-8 文件**，一律用 Edit/Write 工具。
- 复用一期归一化：`from app.services.media_metrics import normalize_metrics, METRIC_FIELDS`。
- **无真实飞书凭证**：所有自动化测试 mock 飞书响应；真实飞书连通由用户配好后点「测试连接」验证。飞书 API 响应结构按飞书开放平台文档写，首次真实调用可能需微调解析。

---

### Task 1: 配置与建表

**Files:**
- Modify: `app/config.py`（加 feishu 凭证字段）
- Modify: `app/database.py`（加 media_metrics.missing_fields 列 + media_feishu_unmatched 表）
- Test: `tests/test_feishu_schema.py`

**Interfaces:**
- Produces: `settings.feishu_app_id`, `settings.feishu_app_secret`；表 `media_feishu_unmatched(id, post_url, title, raw_metrics, status, created_at, updated_at)`；列 `media_metrics.missing_fields TEXT DEFAULT '[]'`。

- [ ] **Step 1: 写失败测试**

`tests/test_feishu_schema.py`：
```python
import sqlite3
from app.database import SCHEMA, MIGRATIONS


def _cols(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def test_feishu_unmatched_table_and_missing_fields():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            db.execute(sql)
        except Exception:
            pass
    cur = db.cursor()
    assert {"id", "post_url", "title", "raw_metrics", "status",
            "created_at", "updated_at"} <= _cols(cur, "media_feishu_unmatched")
    assert "missing_fields" in _cols(cur, "media_metrics")
    db.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_schema.py -v`
Expected: FAIL（no such table media_feishu_unmatched）

- [ ] **Step 3: 加表与迁移**

在 `app/database.py` 的 `SCHEMA` 字符串结尾 `"""` 之前，追加：
```sql
CREATE TABLE IF NOT EXISTS media_feishu_unmatched (
    id TEXT PRIMARY KEY,
    post_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    raw_metrics TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

在 `MIGRATIONS` 列表末尾追加（给已存在的 media_metrics 加列）：
```python
    "ALTER TABLE media_metrics ADD COLUMN missing_fields TEXT DEFAULT '[]'",
```

在 `app/config.py` 的 `Settings` 类里，`openclaw_token` 附近加：
```python
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_schema.py -v`
Expected: PASS

- [ ] **Step 5: 全套回归**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`
Expected: 74 passed（73 + 1 新）

- [ ] **Step 6: 提交**

```bash
cd /d/GAGA-5-25/ai-pm && git add app/config.py app/database.py tests/test_feishu_schema.py && git commit -m "feat(feishu): 配置+建表(missing_fields, media_feishu_unmatched)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 飞书 OpenAPI 客户端（系统层通用）

**Files:**
- Create: `app/services/feishu_client.py`
- Test: `tests/test_feishu_client.py`

**Interfaces:**
- Consumes: `settings.feishu_app_id`, `settings.feishu_app_secret`
- Produces:
  - `async get_tenant_access_token() -> str`（带内存缓存，过期前复用）
  - `async list_bitable_records(app_token: str, table_id: str, page_size: int = 500) -> list[dict]`（返回记录列表，每条是飞书原始 record，含 `record_id` 和 `fields` 字典）
  - `_parse_records(payload: dict) -> tuple[list[dict], str | None]`（纯函数：从飞书响应体提取 (records, page_token)，供分页与测试）

- [ ] **Step 1: 写失败测试**

`tests/test_feishu_client.py`：
```python
from app.services.feishu_client import _parse_records


def test_parse_records_extracts_items_and_page_token():
    payload = {
        "code": 0,
        "data": {
            "items": [
                {"record_id": "r1", "fields": {"视频链接": "http://a", "播放量": "1.2万"}},
                {"record_id": "r2", "fields": {"视频链接": "http://b", "播放量": 3000}},
            ],
            "has_more": True,
            "page_token": "tok123",
        },
    }
    records, page_token = _parse_records(payload)
    assert len(records) == 2
    assert records[0]["fields"]["视频链接"] == "http://a"
    assert page_token == "tok123"


def test_parse_records_no_more_returns_none_token():
    payload = {"code": 0, "data": {"items": [], "has_more": False}}
    records, page_token = _parse_records(payload)
    assert records == []
    assert page_token is None


def test_parse_records_error_code_raises():
    payload = {"code": 1254005, "msg": "table not found", "data": {}}
    try:
        _parse_records(payload)
        assert False, "should raise"
    except RuntimeError as e:
        assert "1254005" in str(e)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_client.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 实现 feishu_client.py**

`app/services/feishu_client.py`：
```python
"""飞书开放平台 OpenAPI 客户端（系统层通用能力）。

只负责「拿 token、读多维表格记录」，不含任何媒体业务概念。
以后会议室等其它模块要读飞书，直接复用本模块。
飞书文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list
"""
import time
import httpx

from app.config import settings

_BASE = "https://open.feishu.cn/open-apis"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# tenant_access_token 内存缓存：{"token": str, "expire_at": float}
_token_cache: dict = {}


def _parse_records(payload: dict) -> tuple[list[dict], str | None]:
    """从飞书响应体提取 (records, next_page_token)。code!=0 抛错。纯函数。"""
    code = payload.get("code", -1)
    if code != 0:
        raise RuntimeError(
            f"飞书 API 报错 code={code}: {payload.get('msg', '')}")
    data = payload.get("data") or {}
    items = data.get("items") or []
    page_token = data.get("page_token") if data.get("has_more") else None
    return items, page_token


async def get_tenant_access_token() -> str:
    """换取 tenant_access_token，带内存缓存（有效期约 2 小时，提前 5 分钟过期）。"""
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("expire_at", 0) > now:
        return _token_cache["token"]
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("飞书 app_id/app_secret 未配置")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id,
                  "app_secret": settings.feishu_app_secret})
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书取 token 失败: {data.get('msg')}")
    token = data["tenant_access_token"]
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + data.get("expire", 7200) - 300
    return token


async def list_bitable_records(app_token: str, table_id: str,
                               page_size: int = 500) -> list[dict]:
    """读多维表格全部记录（自动翻页）。返回原始 record 列表。"""
    token = await get_tenant_access_token()
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}
    all_records: list[dict] = []
    page_token: str | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            records, page_token = _parse_records(resp.json())
            all_records.extend(records)
            if not page_token:
                break
    return all_records


def is_configured() -> bool:
    return bool(settings.feishu_app_id and settings.feishu_app_secret)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_client.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 全套回归**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`
Expected: 77 passed

- [ ] **Step 6: 提交**

```bash
cd /d/GAGA-5-25/ai-pm && git add app/services/feishu_client.py tests/test_feishu_client.py && git commit -m "feat(feishu): OpenAPI 客户端(token缓存+读多维表格)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 设置页配置 + 测试连接（早期可测里程碑）

**Files:**
- Modify: `app/api/settings.py`（加飞书配置读写 + 测试连接路由）
- Modify: `app/templates/settings.html`（加飞书数据源区块）
- Test: 路由冒烟 + 用户真实「测试连接」

**Interfaces:**
- Consumes: `feishu_client.list_bitable_records`
- Produces: `settings.json["feishu_media_map"] = {"app_token","table_id","fields":{...}}`；路由 `POST /settings/feishu`（存配置）、`POST /settings/feishu/test`（拉一条记录验证，返 JSON）

**说明**：本任务让用户**尽早**验证飞书连通、看到真实列名，好填字段映射。是关键去风险点。

- [ ] **Step 1: 读现有 settings 读写模式**

确认 settings 读写 helper。Run:
`cd /d/GAGA-5-25/ai-pm && grep -n "def load_settings\|def save_settings\|CONFIG_FILE" app/api/settings.py`
**已确认真名**：读用 `load_settings() -> dict`，写用 `save_settings(data: dict)`（合并式写回 data/settings.json）。全程用这两个，**不要写成 `_load`/`_save`**。

- [ ] **Step 2: 加飞书凭证到 .env 加载**

`app/config.py` 的 Settings 已在 Task 1 加了 `feishu_app_id/secret`（从 .env 读）。确认 pydantic-settings 从 `.env` 加载（看类的 `Config`/`model_config` 有 `env_file=".env"`）。app_token/table_id/字段映射不是密钥，存 `settings.json["feishu_media_map"]`。

- [ ] **Step 3: 加配置存储 + 测试连接路由**

在 `app/api/settings.py` 追加（用 `load_settings`/`save_settings`）：
```python
from app.services import feishu_client

@router.post("/settings/feishu")
async def save_feishu_config(request: Request,
                             app_token: str = Form(""),
                             table_id: str = Form(""),
                             f_post_url: str = Form(""),
                             f_title: str = Form(""),
                             f_views: str = Form(""),
                             f_likes: str = Form(""),
                             f_comments: str = Form(""),
                             f_shares: str = Form(""),
                             f_new_fans: str = Form(""),
                             f_snapshot_at: str = Form("")):
    cfg = load_settings()
    cfg["feishu_media_map"] = {
        "app_token": app_token.strip(),
        "table_id": table_id.strip(),
        "fields": {k: v.strip() for k, v in {
            "post_url": f_post_url, "title": f_title, "views": f_views,
            "likes": f_likes, "comments": f_comments, "shares": f_shares,
            "new_fans": f_new_fans, "snapshot_at": f_snapshot_at,
        }.items() if v.strip()},
    }
    save_settings(cfg)
    return RedirectResponse("/settings", status_code=302)


@router.post("/settings/feishu/test")
async def test_feishu(request: Request):
    cfg = load_settings().get("feishu_media_map") or {}
    app_token, table_id = cfg.get("app_token"), cfg.get("table_id")
    if not app_token or not table_id:
        return JSONResponse({"ok": False, "error": "请先填 app_token 和 table_id 并保存"})
    try:
        records = await feishu_client.list_bitable_records(app_token, table_id, page_size=1)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"连接失败: {e}"})
    columns = list(records[0]["fields"].keys()) if records else []
    return JSONResponse({"ok": True, "count": len(records), "columns": columns})
```
（确认文件顶部已 `from fastapi.responses import JSONResponse, RedirectResponse`、`from fastapi import Form`，以及 settings.py 里已有的 `load_settings`/`save_settings` 在同文件可直接调用；缺则补。**注意：settings 读写函数名是 `load_settings()`/`save_settings()`，不是 `_load`/`_save`。**）

- [ ] **Step 4: 加设置页 UI 区块**

在 `app/templates/settings.html` 里合适位置（其它配置块附近）加一个 `<form method="POST" action="/settings/feishu">`：包含 app_token、table_id 两个输入，和 7 个字段映射输入（`f_post_url`…`f_snapshot_at`，placeholder 写「飞书里这列叫什么，如：视频链接」），一个「保存飞书配置」按钮；另加一个「测试连接」按钮，JS 调 `/settings/feishu/test` 把返回的 `columns` 列出来给用户对照填映射。用现有模板变量（配置回填用 `settings_data.feishu_media_map` 之类，按本项目 settings 模板已有的回填方式）。响应式用 `@media (max-width:767px)`，不用 `md:`。

- [ ] **Step 5: 路由冒烟**

Run:
```
cd /d/GAGA-5-25/ai-pm && python -c "from app.main import app; p=[r.path for r in app.routes]; assert '/settings/feishu' in p and '/settings/feishu/test' in p, p; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: 全套回归 + 提交**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`（应仍 77 passed）
```bash
cd /d/GAGA-5-25/ai-pm && git add app/api/settings.py app/templates/settings.html && git commit -m "feat(feishu): 设置页配置+测试连接(早期可验证飞书连通)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> **⚠️ 用户验收点**：此处用户可先在飞书建应用+表格，填凭证，点「测试连接」，确认能连上、看到真实列名，再填字段映射。连通后再继续 Task 4+。

---

### Task 4: 同步纯核心 —— 行映射 + 标题归一

**Files:**
- Create: `app/services/media_feishu_sync.py`（先只放纯函数）
- Test: `tests/test_feishu_sync.py`

**Interfaces:**
- Consumes: `normalize_metrics`, `METRIC_FIELDS`（from media_metrics）
- Produces:
  - `norm_title(s: str) -> str`（归一化标题用于模糊匹配：去空格/标点/emoji，转小写）
  - `map_feishu_row(fields: dict, field_map: dict) -> dict | None`（返回 `{"post_url","title","metrics":{5字段},"missing_fields":[...]}`；无 post_url 且无 title 返回 None）

- [ ] **Step 1: 写失败测试**

`tests/test_feishu_sync.py`：
```python
from app.services.media_feishu_sync import norm_title, map_feishu_row

FIELD_MAP = {
    "fields": {
        "post_url": "视频链接", "title": "标题", "views": "播放量",
        "likes": "点赞", "comments": "评论", "shares": "转发",
        # 故意不映射 new_fans，模拟飞书拿不到涨粉
    }
}


def test_norm_title_strips_punct_space_emoji():
    assert norm_title(" 二胎妈妈的『时间黑洞』🕳️ ") == norm_title("二胎妈妈的时间黑洞")


def test_map_row_extracts_and_normalizes():
    row = {"视频链接": "http://x", "标题": "标题A", "播放量": "1.2万",
           "点赞": 350, "评论": 20, "转发": 5}
    out = map_feishu_row(row, FIELD_MAP)
    assert out["post_url"] == "http://x"
    assert out["title"] == "标题A"
    assert out["metrics"]["views"] == 12000
    assert out["metrics"]["likes"] == 350
    assert out["metrics"]["new_fans"] == 0  # 未映射


def test_map_row_missing_fields_flags_new_fans():
    row = {"视频链接": "http://x", "播放量": 100}
    out = map_feishu_row(row, FIELD_MAP)
    assert "new_fans" in out["missing_fields"]  # 飞书没给的标出来
    assert "views" not in out["missing_fields"]


def test_map_row_no_url_no_title_returns_none():
    assert map_feishu_row({"播放量": 100}, FIELD_MAP) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_sync.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 实现纯函数**

`app/services/media_feishu_sync.py`：
```python
"""飞书 → media_metrics 同步（媒体层）。

读飞书表 → 按 post_url(标题兜底) 匹配 media_publish → 写 metrics 快照。
匹配不上的持久化进 media_feishu_unmatched 让用户手动补。
"""
import json
import re
import uuid
from datetime import date

from app.services.feishu_client import list_bitable_records
from app.services.media_metrics import normalize_metrics, METRIC_FIELDS

_PUNCT = re.compile(r"[\s　\W_]+", re.UNICODE)


def norm_title(s: str) -> str:
    """归一化标题用于模糊匹配：去空白/标点/emoji，转小写。"""
    if not s:
        return ""
    return _PUNCT.sub("", str(s)).lower()


def _cell(fields: dict, col_name: str):
    """从飞书 fields 取一列的值。飞书文本列可能是 [{'text':..}] 结构，做个兼容。"""
    v = fields.get(col_name)
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v[0].get("text", "")
    return v


def map_feishu_row(fields: dict, field_map: dict) -> dict | None:
    """把一行飞书 fields 按映射提取。无 post_url 且无 title 返回 None。"""
    fm = (field_map or {}).get("fields") or {}
    post_url = str(_cell(fields, fm.get("post_url", "")) or "").strip()
    title = str(_cell(fields, fm.get("title", "")) or "").strip()
    if not post_url and not title:
        return None
    raw = {}
    missing = []
    for f in METRIC_FIELDS:
        col = fm.get(f)
        val = _cell(fields, col) if col else None
        if col and val not in (None, ""):
            raw[f] = val
        else:
            missing.append(f)  # 飞书没映射或没给值 → 标待手填
    return {"post_url": post_url, "title": title,
            "metrics": normalize_metrics(raw), "missing_fields": missing}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_sync.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 全套回归 + 提交**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`（81 passed）
```bash
cd /d/GAGA-5-25/ai-pm && git add app/services/media_feishu_sync.py tests/test_feishu_sync.py && git commit -m "feat(feishu): 同步纯核心(行映射+标题归一+missing_fields)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 同步编排 —— 匹配 + 写快照 + 未匹配落库

**Files:**
- Modify: `app/services/media_feishu_sync.py`（加异步编排 + 写库）
- Test: `tests/test_feishu_sync.py`（加 live 编排测试）

**Interfaces:**
- Consumes: `map_feishu_row`, `norm_title`, `list_bitable_records`, DB
- Produces:
  - `async sync_from_feishu(db, records=None) -> dict`（`records` 参数供测试注入假数据；为 None 时真调飞书）。返回 `{"ok","synced","updated","unmatched","suspected","error"}`
  - 内部：按 post_url 精确匹配；不中再按 norm_title 匹配（标 suspected）；匹配到写 metrics（当天 feishu 快照存在则更新，否则插入，带 missing_fields）；未匹配 upsert 进 media_feishu_unmatched

- [ ] **Step 1: 写失败 live 测试**

在 `tests/test_feishu_sync.py` 追加：
```python
import asyncio, base64, json as _json, itsdangerous
import app.api.auth as auth
import app.database as d


def _run(coro):
    return asyncio.run(coro)  # 每次新事件循环；DB 连接在 coro 内建，安全


async def _seed_publish(db, post_url, title):
    import uuid
    pid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_persona (id,name) VALUES (?,?)", (pid, "P"))
    cid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_content (id,persona_id,title) VALUES (?,?,?)",
                     (cid, pid, title))
    aid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_account (id,persona_id,platform) VALUES (?,?,?)",
                     (aid, pid, "douyin"))
    pubid = str(uuid.uuid4())
    await db.execute("INSERT INTO media_publish (id,content_id,account_id,post_url) "
                     "VALUES (?,?,?,?)", (pubid, cid, aid, post_url))
    await db.commit()
    return pubid


def test_sync_matches_by_url_and_writes_metrics():
    from app.services.media_feishu_sync import sync_from_feishu

    async def go():
        db = await d.get_db()
        pubid = await _seed_publish(db, "http://vid/1", "标题甲")
        records = [{"fields": {"视频链接": "http://vid/1", "标题": "标题甲",
                               "播放量": "1.5万", "点赞": 200}}]
        # 注入 field_map 到 settings
        import app.api.settings as st
        cfg = st.load_settings(); cfg["feishu_media_map"] = {
            "fields": {"post_url": "视频链接", "title": "标题",
                       "views": "播放量", "likes": "点赞"}}
        st.save_settings(cfg)
        rep = await sync_from_feishu(db, records=records)
        assert rep["ok"] and rep["synced"] == 1
        row = await (await db.execute(
            "SELECT views,collected_by,missing_fields FROM media_metrics "
            "WHERE publish_id=?", (pubid,))).fetchone()
        assert row["views"] == 15000
        assert row["collected_by"] == "feishu"
        assert "new_fans" in _json.loads(row["missing_fields"])
        # cleanup
        for t in ["media_metrics","media_publish","media_account",
                  "media_content","media_persona","media_feishu_unmatched"]:
            await db.execute(f"DELETE FROM {t}")
        await db.commit(); await db.close()
    _run(go())


def test_sync_unmatched_goes_to_table():
    from app.services.media_feishu_sync import sync_from_feishu

    async def go():
        db = await d.get_db()
        records = [{"fields": {"视频链接": "http://orphan", "标题": "野生视频",
                               "播放量": 999}}]
        import app.api.settings as st
        cfg = st.load_settings(); cfg["feishu_media_map"] = {
            "fields": {"post_url": "视频链接", "title": "标题", "views": "播放量"}}
        st.save_settings(cfg)
        rep = await sync_from_feishu(db, records=records)
        assert rep["unmatched"] == 1
        n = (await (await db.execute(
            "SELECT COUNT(*) c FROM media_feishu_unmatched WHERE status='pending'"
        )).fetchone())["c"]
        assert n == 1
        await db.execute("DELETE FROM media_feishu_unmatched"); await db.commit()
        await db.close()
    _run(go())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_sync.py -k sync -v`
Expected: FAIL（sync_from_feishu 未定义）

- [ ] **Step 3: 实现编排**

在 `app/services/media_feishu_sync.py` 追加：
```python
async def _load_field_map():
    import app.api.settings as st
    return st.load_settings().get("feishu_media_map") or {}


async def _write_feishu_snapshot(db, publish_id, metrics, missing_fields):
    """当天已有 feishu 快照则更新，否则插入。带 missing_fields。"""
    today = date.today().isoformat()
    cur = await db.execute(
        "SELECT id FROM media_metrics WHERE publish_id=? AND collected_by='feishu' "
        "AND date(snapshot_at)=?", (publish_id, today))
    existing = await cur.fetchone()
    mf = json.dumps(missing_fields, ensure_ascii=False)
    if existing:
        await db.execute(
            "UPDATE media_metrics SET views=?,likes=?,comments=?,shares=?,"
            "new_fans=?,missing_fields=?,snapshot_at=CURRENT_TIMESTAMP WHERE id=?",
            (metrics["views"], metrics["likes"], metrics["comments"],
             metrics["shares"], metrics["new_fans"], mf, existing["id"]))
    else:
        await db.execute(
            "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
            "shares,new_fans,collected_by,missing_fields) "
            "VALUES (?,?,?,?,?,?,?,'feishu',?)",
            (str(uuid.uuid4()), publish_id, metrics["views"], metrics["likes"],
             metrics["comments"], metrics["shares"], metrics["new_fans"], mf))


async def _upsert_unmatched(db, post_url, title, metrics):
    key = post_url or title
    cur = await db.execute(
        "SELECT id FROM media_feishu_unmatched WHERE (post_url=? AND post_url<>'') "
        "OR (post_url='' AND title=?)", (post_url, title))
    row = await cur.fetchone()
    raw = json.dumps(metrics, ensure_ascii=False)
    if row:
        await db.execute("UPDATE media_feishu_unmatched SET raw_metrics=?,"
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (raw, row["id"]))
    else:
        await db.execute(
            "INSERT INTO media_feishu_unmatched (id,post_url,title,raw_metrics) "
            "VALUES (?,?,?,?)", (str(uuid.uuid4()), post_url, title, raw))


async def sync_from_feishu(db, records=None) -> dict:
    """主同步。records 为 None 时真调飞书；测试可注入假数据。"""
    field_map = await _load_field_map()
    if not field_map.get("fields"):
        return {"ok": False, "error": "飞书字段映射未配置", "synced": 0,
                "updated": 0, "unmatched": 0, "suspected": 0}
    if records is None:
        try:
            records = await list_bitable_records(
                field_map.get("app_token"), field_map.get("table_id"))
        except Exception as e:
            return {"ok": False, "error": f"读飞书失败: {e}", "synced": 0,
                    "updated": 0, "unmatched": 0, "suspected": 0}

    # 建 url→publish、title→publish 索引
    cur = await db.execute("SELECT id,post_url,content_id FROM media_publish "
                           "WHERE post_url<>''")
    by_url = {}
    for r in await cur.fetchall():
        by_url[r["post_url"].strip()] = r["id"]
    cur = await db.execute(
        "SELECT p.id pid, c.title FROM media_publish p "
        "JOIN media_content c ON c.id=p.content_id")
    by_title = {}
    for r in await cur.fetchall():
        by_title.setdefault(norm_title(r["title"]), r["pid"])

    synced = suspected = unmatched = 0
    for rec in records:
        mapped = map_feishu_row(rec.get("fields") or {}, field_map)
        if not mapped:
            continue
        pubid = by_url.get(mapped["post_url"]) if mapped["post_url"] else None
        if not pubid and mapped["title"]:
            pubid = by_title.get(norm_title(mapped["title"]))
            if pubid:
                suspected += 1  # 靠标题匹配，标疑似
        if pubid:
            await _write_feishu_snapshot(db, pubid, mapped["metrics"],
                                         mapped["missing_fields"])
            synced += 1
        else:
            await _upsert_unmatched(db, mapped["post_url"], mapped["title"],
                                    mapped["metrics"])
            unmatched += 1
    await db.commit()
    return {"ok": True, "synced": synced, "updated": 0, "unmatched": unmatched,
            "suspected": suspected, "error": ""}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/test_feishu_sync.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 全套回归 + 提交**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`（83 passed）
```bash
cd /d/GAGA-5-25/ai-pm && git add app/services/media_feishu_sync.py tests/test_feishu_sync.py && git commit -m "feat(feishu): 同步编排(url+标题匹配/写快照去重/未匹配落库)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 同步按钮 + 报告 + 未匹配清单 UI

**Files:**
- Modify: `app/api/media.py`（加同步路由 + 未匹配清单页/操作）
- Modify: `app/templates/media_board.html`（加「🔄 从飞书同步」按钮）
- Create: `app/templates/media_feishu_review.html`（未匹配清单，标色 + 手动补）
- Test: 路由冒烟 + live 同步/关联

**Interfaces:**
- Consumes: `media_feishu_sync.sync_from_feishu`, DB
- Produces: 路由 `POST /media/feishu/sync`（调同步，返 JSON 报告）、`GET /media/feishu/review`（未匹配清单页）、`POST /media/feishu/unmatched/{uid}/link`（关联到指定 content → 建 publish+写 metrics，标 linked）、`POST /media/feishu/unmatched/{uid}/ignore`（标 ignored）

- [ ] **Step 1: 读现有 media.py 顶部与 _tpl**

Run: `cd /d/GAGA-5-25/ai-pm && grep -n "_tpl\|^from\|^import\|router = " app/api/media.py | head -25`
按现有 import 与 `_tpl(request, name, ctx)` 三参数模式写。

- [ ] **Step 2: 加同步与未匹配路由**

在 `app/api/media.py` 追加（import 处加 `from app.services.media_feishu_sync import sync_from_feishu`）：
```python
@router.post("/media/feishu/sync")
async def feishu_sync(request: Request):
    db = await get_db()
    try:
        rep = await sync_from_feishu(db)
    except Exception as e:
        log.exception("feishu sync failed")
        rep = {"ok": False, "error": str(e)}
    finally:
        await db.close()
    return JSONResponse(rep)


@router.get("/media/feishu/review")
async def feishu_review(request: Request):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM media_feishu_unmatched WHERE status='pending' "
            "ORDER BY updated_at DESC")
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT c.id,c.title FROM media_content c ORDER BY c.updated_at DESC "
            "LIMIT 200")
        contents = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_feishu_review.html",
                {"rows": rows, "contents": contents})


@router.post("/media/feishu/unmatched/{uid}/link")
async def feishu_link(request: Request, uid: str, content_id: str = Form(...),
                      account_id: str = Form(...)):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_feishu_unmatched WHERE id=?", (uid,))
        u = await cur.fetchone()
        if u:
            import uuid as _uuid, json as _json
            pubid = str(_uuid.uuid4())
            await db.execute(
                "INSERT INTO media_publish (id,content_id,account_id,post_url,status) "
                "VALUES (?,?,?,?,'published')",
                (pubid, content_id, account_id, u["post_url"]))
            metrics = _json.loads(u["raw_metrics"] or "{}")
            from app.services.media_metrics import normalize_metrics
            m = normalize_metrics(metrics)
            await db.execute(
                "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
                "shares,new_fans,collected_by) VALUES (?,?,?,?,?,?,?,'feishu')",
                (str(_uuid.uuid4()), pubid, m["views"], m["likes"], m["comments"],
                 m["shares"], m["new_fans"]))
            await db.execute("UPDATE media_feishu_unmatched SET status='linked',"
                             "updated_at=CURRENT_TIMESTAMP WHERE id=?", (uid,))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/feishu/review", status_code=302)


@router.post("/media/feishu/unmatched/{uid}/ignore")
async def feishu_ignore(request: Request, uid: str):
    db = await get_db()
    try:
        await db.execute("UPDATE media_feishu_unmatched SET status='ignored',"
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (uid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/feishu/review", status_code=302)
```
（确认 media.py 顶部有 `from fastapi.responses import JSONResponse, RedirectResponse`、`from fastapi import Form`、`from app.database import get_db`、`log`。缺则补。）

- [ ] **Step 3: 看板加同步按钮**

在 `app/templates/media_board.html` 顶部操作区加：
```html
<button onclick="feishuSync(this)"
  class="text-sm px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700">
  🔄 从飞书同步</button>
<a href="/media/feishu/review" class="text-sm px-3 py-1.5 rounded-lg border">未对上的数据</a>
<span id="feishu-msg" class="text-xs text-gray-500 ml-2"></span>
<script>
async function feishuSync(btn){
  btn.disabled=true; const msg=document.getElementById('feishu-msg');
  msg.textContent='同步中…';
  try{
    const r=await fetch('/media/feishu/sync',{method:'POST'});
    const j=await r.json();
    msg.textContent = j.ok
      ? `✅ 同步 ${j.synced} 条（疑似 ${j.suspected}），未对上 ${j.unmatched} 条`
      : `❌ ${j.error||'失败'}`;
  }catch(e){ msg.textContent='❌ '+e; }
  btn.disabled=false;
}
</script>
```

- [ ] **Step 4: 建未匹配清单模板**

`app/templates/media_feishu_review.html`（extends base.html）：列出 `rows`，每条**黄色卡片**显示 title/post_url/raw_metrics；给一个「关联到内容」的 form（select `content_id` 从 `contents`、select `account_id`——注：account 可先让用户在关联页选，或简化为按 content 的 persona 下账号，MVP 先放一个 account_id 文本/下拉）；一个「忽略」的 form POST 到 ignore。**注意：模板 context dict 的键别用 items/keys/values/get**。用 `{{ r.title }}`、`{% for c in contents %}` 等，`r`/`c` 的键（title/post_url/id/raw_metrics/status）均安全。响应式 `@media (max-width:767px)`。

- [ ] **Step 5: 路由冒烟**

Run:
```
cd /d/GAGA-5-25/ai-pm && python -c "from app.main import app; p=[r.path for r in app.routes]; assert '/media/feishu/sync' in p and '/media/feishu/review' in p and '/media/feishu/unmatched/{uid}/link' in p, p; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: 全套回归 + 提交**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`（应仍 83 passed）
```bash
cd /d/GAGA-5-25/ai-pm && git add app/api/media.py app/templates/media_board.html app/templates/media_feishu_review.html && git commit -m "feat(feishu): 同步按钮+报告+未匹配清单(标色手动补)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 数据展示区分来源 + 待手填（兑现真实性红线）

**Files:**
- Modify: `app/api/media.py`（content_detail 路由的 metrics 查询带上 missing_fields）
- Modify: `app/templates/media_content.html`（数据展示块：missing_fields 里的字段显示「待手填」，来源标签突出）
- Test: 控制器 live 验证

**Interfaces:**
- Consumes: `media_metrics.missing_fields`（Task 1 加的列）、`collected_by`
- 目标：飞书快照里飞书没给的字段（如涨粉），UI 显示灰色「待手填」而非 0；来源（feishu/manual/screenshot）有可见区分。

- [ ] **Step 1: 读现有数据展示**

Run: `cd /d/GAGA-5-25/ai-pm && grep -n "metrics.get\|m.views\|m.collected_by\|snapshot_at\|SELECT.*media_metrics" app/api/media.py app/templates/media_content.html`
现状：模板约 121-128 行 `{% set m = metrics.get(p.id) %}` 显示 `m.views/m.likes/m.collected_by`。找到 content_detail 路由里构造 `metrics`（按 publish_id 取最新一条）的 SQL，确认它 SELECT 了哪些列。

- [ ] **Step 2: 查询带上 missing_fields**

在 content_detail 路由那条「取每个 publish 最新 metrics」的 SQL 里，把 `missing_fields` 加进 SELECT 列（若用 `SELECT *` 则已包含，跳过）。确保传给模板的 `metrics` 字典每条含 `missing_fields`（字符串 JSON）。

- [ ] **Step 3: 模板显示待手填 + 来源**

改 `app/templates/media_content.html` 数据展示块：解析 `m.missing_fields`（Jinja2 无 tojson，用 `{% set miss = m.missing_fields %}` 后在模板里判断子串，或在路由侧预解析成 list 传入更稳——推荐路由侧 `json.loads` 后传 `m["missing_list"]`）。对 `views/likes/comments/shares/new_fans` 每个字段：若在 missing_list 里 → 显示灰色「待手填」；否则显示数值。来源用小标签：`feishu`=蓝、`manual`/`screenshot`=灰。**不用 Tailwind `md:`；dict 键不用 items/keys/values/get。**

- [ ] **Step 4: 控制器 live 验证**

TestClient 建 persona+content+account+publish，写一条 `collected_by='feishu'`、`missing_fields='["new_fans"]'`、views=30000 的 metrics，GET 内容详情页，断言：HTTP 200、页面含「30000」、含「待手填」、含来源标识「feishu」。清理测试数据。

- [ ] **Step 5: 提交**

```bash
cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q && git add app/api/media.py app/templates/media_content.html && git commit -m "feat(feishu): 数据展示区分来源+缺失字段标待手填(不填0冒充)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 端到端验收（控制器实测）

**Files:** 无（验证任务）

- [ ] **Step 1: 全套测试**

Run: `cd /d/GAGA-5-25/ai-pm && python -m pytest tests/ -q`
Expected: 83 passed

- [ ] **Step 2: 控制器 live 走查（mock 飞书数据）**

用 TestClient + 伪造 session cookie（仿一期），端到端验证（不需真实飞书）：
```python
# 伪造登录 → 建 persona+content+account+publish(post_url=http://vid/e2e)
# monkeypatch app.services.media_feishu_sync.list_bitable_records 返回假记录
#   [{"fields":{"视频链接":"http://vid/e2e","标题":"E2E","播放量":"3万","点赞":800}},
#    {"fields":{"视频链接":"http://orphan/e2e","标题":"野生","播放量":100}}]
# 配 settings feishu_media_map
# POST /media/feishu/sync → 断言 ok, synced=1, unmatched=1
# 查 media_metrics: 该 publish 有 collected_by='feishu' 的行, views=30000, missing_fields含new_fans
# GET /media/feishu/review → 断言页面含"野生", HTTP 200
# POST link 该未匹配到某 content+account → 断言建了 publish+metrics, unmatched 状态变 linked
# 清库
```
逐条确认通过后，清空所有 media_* 与 media_feishu_unmatched 测试数据。

- [ ] **Step 3: 更新进度账本 + 收尾**

在 `.superpowers/sdd/progress.md` 记完成。最终整支审查（opus）后交给 finishing-a-development-branch。

> **⚠️ 真实飞书验收（用户做）**：以上全是 mock 数据验证逻辑。用户需：① 飞书建应用+多维表格+连接器抓数据 ② 设置页填凭证+映射+测试连接 ③ 确保 AI-PM 发布内容填了 post_url ④ 点「从飞书同步」核对真实数据。

---

## 附：留给用户/后续的说明

- 本计划所有自动化测试用 **mock 飞书数据**；真实飞书连通由用户「测试连接」验证（Task 3 后即可先验）。
- `feishu_client.py` 是**系统层通用能力**，以后会议室等模块读飞书直接复用。
- 深度指标（涨粉等）飞书拿不到的走 `missing_fields`，UI 显示「待手填」，**不填 0 冒充**。
- 定时同步、AI 识别视频反向入库（功能 C）、AI 学改稿（功能 B）均为后续独立 spec，不在本计划。
