# 媒体批量后台跑（整理/挖矿·离页不断）设计 spec

**日期：** 2026-08-14
**分支：** feat/media-batch-background
**触发词：** 接 AI-PM

## 1. 目标

批量整理/批量挖矿现在是**浏览器循环**，一离开页面就断。改成**服务器后台跑**：起了任务你可以切去别的页面，它继续跑；顶部有**显眼的全局进度条**，任何媒体页都能看到它在跑到几分之几。

## 2. 已拍板

- **范围**：整理 + 挖矿（挖记忆点/挖精华）**都后台**，做一个通用后台跑器。
- **全局指示灯**：要，且**显眼**——放媒体外壳 `_media_shell.html`（所有媒体页都有），任何媒体页可见。
- **执行**：inline。
- **诚实边界**：进程内内存任务，离页/切页不影响（同一服务器进程在跑）；只有**服务器重启**会中断（跟 auto_runner 一样），已处理的都已入库（做一条存一条），重跑剩下的即可。

## 3. 架构

复用 auto_runner/worker_status 的模式（模块级内存 dict + `asyncio.create_task` + Lock）。

### 3.1 通用后台跑器 `app/services/media_batch.py`
- 内存 `_jobs: dict[persona_id, dict]` + `Lock`。每人设**一个活跃任务**。job 结构 `{op, done, total, running, started_at, ok_count}`。
- `start_batch(persona_id, op, content_ids) -> bool`：已有 running 任务→返 False（拒绝）；否则置 job + `asyncio.create_task(_run_batch(...))`→返 True。
- `_run_batch(persona_id, op, content_ids)`：逐条循环，每条开自己的 db、调对应 per-content 操作、更新进度、关 db；末尾 `running=False`。**每条一次 AI 调用（守纪律）**。
- `get_status(persona_id) -> dict | None`。
- **per-content 核心（从现有路由抽出，路由与跑器共用，DRY）**：
  - `run_organize_one(db, cid) -> dict`：organize_content + log_action('organize_format') + UPDATE summary/script（= 现 content_organize 的核心）。
  - `run_mine_one(db, cid, kind, force) -> dict`：mine_from_transcript/mine_structure + enqueue + 打标（= 现 content_mine_to_queue 的核心）。
- op 取值：`'organize'` / `'mine_signature'` / `'mine_essence'`。`_run_batch` 按 op 分派到 run_organize_one / run_mine_one(kind='signature'|'essence')。

### 3.2 端点（`app/api/media.py`）
- `POST /media/legacy/batch`（Form `op`, `content_ids: list[str]`, `force=0`）：认当前人设→`start_batch`。返 `{ok, started: bool, error?}`（已在跑返 started=False + 提示）。
- `GET /media/legacy/batch-status`：认当前人设→`get_status`→返 `{running, op, done, total}`（无任务返 running=False）。
- **重构**：现有 `content_organize` / `content_mine_to_queue` 路由改为调 `run_organize_one` / `run_mine_one` 再包 JSONResponse（保留单条端点不变，供内容详情页等；逻辑抽到 media_batch 后两处共用）。

### 3.3 前端
- **老文案页 `media_legacy.html`**：三个批量按钮（整理/挖记忆点/挖精华）改成 **POST /media/legacy/batch**（带 op + 勾选的 content_ids），不再 JS 逐条循环。起成功后开始轮询。
- **轮询**：老文案页 + 全局指示灯每 2 秒 `GET /media/legacy/batch-status`，running 时显示"整理中 12/40"（op 中文名 + done/total），done→显示"完成，刷新看结果"。**页面一加载就查一次**（离开再回来能接着看进度）。
- **全局指示灯（`_media_shell.html`，显眼）**：外壳顶部一条**醒目横条**（跑时才显示，idle 隐藏）——如亮色背景 + 🔄 + "AI 整理中 12/40"，任何媒体页都能看到。复用外壳已有的 `<script>`（现在轮询 /media/ui/steps）加一段轮询 batch-status。
- 任务在跑时老文案页批量按钮禁用（防重复起；start 返 started=False 也提示"已有任务在跑"）。

## 4. 数据

**零 schema 变更**：整理/挖矿的落库表都已存在（summary/script/media_mine_candidate/媒体动作日志）；后台跑器是内存状态。

## 5. 代码落点

- `app/services/media_batch.py`（新）：_jobs/_lock + start_batch/get_status/_run_batch + run_organize_one/run_mine_one。
- `app/api/media.py`：+ `POST /media/legacy/batch` + `GET /media/legacy/batch-status`；`content_organize`/`content_mine_to_queue` 改调 media_batch 的 run_*_one。
- `app/templates/media_legacy.html`：批量按钮改 POST 起任务 + 轮询显示 + 按钮禁用。
- `app/templates/_media_shell.html`：全局显眼进度横条 + 轮询 batch-status。
- 测试：run_organize_one/run_mine_one（落库正确·复用现有断言）；start_batch（起任务/已跑拒绝）；get_status；batch 端点（起+查，用 monkeypatch 让 organize/mine 秒回避免真 AI）；单条 organize/mine 端点重构后仍绿（回归）。

## 6. 边界（本轮不做）

- 任务不持久化（内存·重启丢，接受）；不做暂停/取消（跑完即停，量可控）。
- 不做多人设并发多任务（每人设一个活跃任务够用）。
- 全局指示灯只显示"在跑/进度"，不做点开看明细（要看去老文案页）。

## 7. 验收清单

- [ ] media_batch：start_batch 起后台任务、已跑拒绝；_run_batch 逐条跑更新进度；get_status 返进度。
- [ ] run_organize_one/run_mine_one 抽出后，单条端点(/organize、/mine-to-queue)重构调它们且行为不变（回归绿）。
- [ ] POST /media/legacy/batch 起任务；GET batch-status 返进度。
- [ ] 老文案页批量按钮改 POST 起后台任务 + 轮询显示 + 跑时禁用。
- [ ] 全局指示灯在 _media_shell 显眼显示"AI 整理中 X/Y"，跑时任何媒体页可见，idle 隐藏。
- [ ] 离页再回老文案页/切到别的媒体页，进度条仍显示在跑（同进程）。
- [ ] 全套测试绿 + 浏览器冒烟（起任务→切页看全局条→回来看完成，无 Jinja/500）。
- [ ] 真机 DeepSeek：起批量整理→切去别的页→看全局条在涨→跑完刷新看摘要。
