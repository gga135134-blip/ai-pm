# 视频批量粘链接（反向入库）— 设计

日期：2026-08-23
状态：设计已与用户敲定，待写实施计划

## 背景与目标

反向入库（功能C）现在**单条**已通：贴一条已发视频链接 → 抽音频 → ASR 转写 → AI 提选题 → 建 `media_content`（stage='published', idea_source='video_reverse'）。服务函数 `reverse_ingest`（`app/services/media_reverse.py`），路由 `POST /media/reverse-ingest`，UI 在反向工具 tab（`media_board` tool=reverse）。

本轮目标：加**一次贴多条链接、排队后台跑**，最多 **10** 条。每条慢且花钱（ASR+AI 几十秒到一两分钟），所以必须后台异步 + 轮询进度，不能同步等 HTTP。

## 已敲定的设计决策（与用户确认）

1. **上限 10 条**（用户选，防手滑贴太多烧 ASR 费用）。
2. **去重 = 跳过已入库**：贴的 url 若该人设已有同链接的 `video_reverse` 内容（按 `media_content.idea_reason` 原链接**精确字符串**匹配）→ 跳过不重跑。诚实边界：只精确匹配，同视频不同跟踪参数（`?xxx`）认不出算不同，第一版够用，不做视频指纹。
3. **后台跑器**：仿现有 `media_batch`（内存 `_jobs` + asyncio + 轮询）那套，逐条**串行**跑（跟单条一样，避免 ASR 限流/被抖音封）。
4. **进度只在反向 tab 显示**（不铺全局进度条到所有媒体页——YAGNI）。

## 架构与改动点

### 1. 后台跑器（新建 `app/services/media_reverse_batch.py`）
独立小模块，仿 `media_batch` 形状：
- 模块级 `_rev_jobs: dict[persona_id, job]`，`threading.Lock` 保护。每人设一个活跃任务。
- `start_reverse_batch(pid, urls: list[str]) -> bool`：已在跑返 `False` 拒绝；否则建 job（`{running:True, done:0, total:len(urls), results:[]}`）+ `asyncio.create_task(_run_reverse_batch(...))`，返 `True`。
- `get_reverse_status(pid) -> dict`：`{running, op:'reverse', done, total, results:[{url, ok, title, error}]}`；无任务返 `{running:False, total:0}`。
- `_run_reverse_batch(pid, urls, cfg, public_base, audio_dir, cookies_path)`：逐条串行，每条开自己的 db（`get_db()`）调现有 `reverse_ingest`，把 `{url, ok, title, error}` 追加进 results、`done+=1`；**单条异常/失败不中断**（try/except 记 error 继续下一条）。跑完 `running=False`。
- 诚实边界（与 media_batch 一致）：进程内内存任务，离页/切页不断，**服务器重启中断**（已入库的保留，没跑的丢）。

### 2. 去重 + 解析 helper（放 `media_reverse_batch.py`，纯函数好测）
- `parse_urls(text: str, cap: int = 10) -> list[str]`：按行拆，每行 `first_url()`（复用 `app.services.video_fetch.first_url`，单条流程也用它抠抖音分享文案）抠链接，丢空/抠不出的，**贴内去重**（保序），截断到 `cap`。
- 已入库去重放在端点里（需查库）：查该人设 `SELECT idea_reason FROM media_content WHERE persona_id=? AND idea_source='video_reverse'` 得已入 url 集合，`parse_urls` 结果里在集合内的挑出来当 `skipped`，其余当 `queued` 交给 runner。

### 3. 端点（`app/api/media.py`）
- `POST /media/reverse/batch`（Form `urls: str`，整块文本）：
  - 校验豆包 ASR 凭证（同单条路由，缺则返错）
  - `pid = _current_persona_id`，缺则返错
  - `parse_urls(urls, 10)` → 校验非空
  - 查已入库集合做去重 → `queued` / `skipped`
  - `queued` 空（全是重复）→ 返 `{ok:True, started:False, queued:0, skipped:N}`
  - `start_reverse_batch(pid, queued)`：已在跑返 `{ok:True, started:False, running:True}`；起成功返 `{ok:True, started:True, queued:len, skipped:N}`
- `GET /media/reverse/batch-status`：`get_reverse_status(pid)` 直接返 JSON。

### 4. 前端（`app/templates/media_board.html` 反向工具 tab）
现有单条表单下方加：
- textarea（placeholder「一行一条链接，最多 10 条，支持直接贴抖音分享文案」）+「批量入库」按钮
- JS `startReverseBatch()`：POST /media/reverse/batch → 起成功后 `pollReverseBatch()`
- `pollReverseBatch()`：每 3s GET batch-status，`running` 时显示「入库中 X/total」继续轮询；跑完显示逐条结果列表（✅《标题》 / ⏭️跳过已入库 / ❌失败原因）+「跳过 N 条已入库」提示
- `DOMContentLoaded` 也 poll（离页回来接着看进度）

## 数据流

```
贴文本 → parse_urls(抠链接/贴内去重/截10) → 端点查已入库集合做去重
   → queued + skipped
   → start_reverse_batch(pid, queued): _rev_jobs[pid]=job + asyncio.create_task
        → _run_reverse_batch 逐条 reverse_ingest(建content) → results 累加 → done++
   → 前端轮询 batch-status 显示进度+逐条结果
```

## 测试（TDD）

- **`parse_urls` 纯函数**（无 DB）：多行抠链接、丢空行/抠不出、贴内去重保序、超 10 截断、贴整段分享文案能抠出。
- **后台跑器**（tmp-DB_PATH 模块 fixture；monkeypatch `media_reverse_batch.reverse_ingest`）：3 条 url（模拟 1 成功/1 失败/1 成功）→ 起任务→轮询到 running=False → results 三条齐、done=3、失败那条 ok=False 不中断后续。已在跑再 start 返 False。
- **端点去重**（tmp-DB_PATH）：先塞一条 idea_reason=urlA 的 video_reverse 内容，POST urls=[urlA,urlB] → skipped 含 urlA、queued 只 urlB。全重复 → started=False, skipped=N。
- **端点校验**：空文本返错、缺 ASR 凭证返错、>10 只取前 10。

## 不在本轮（YAGNI / 已确认）
- 全局进度条（只反向 tab 显示进度）。
- 视频指纹去重（只精确 url 匹配）。
- 并发（逐条串行，更稳）。
- 断点续跑/持久化（内存任务，重启中断，与 media_batch 一致）。

## 迁移与部署
- **零迁移**（不加表/列，复用现有 media_content/reverse_ingest）。新模块 + 端点 + 模板，重启即生效。
- 依赖：豆包 ASR 凭证 + 抖音 cookie（`data/douyin_cookies.txt`）已配（单条已在用）。
