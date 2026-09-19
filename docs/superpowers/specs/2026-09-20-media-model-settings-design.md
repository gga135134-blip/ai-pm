# 自媒体模型设置分家 · 设计 + 接续点（自包含，恢复时只读这一个文件）

- 日期：2026-09-20　作者：GAGA + Claude Opus 4.8
- 状态：**设计已过审，未开始实现**。恢复时直接从「实现步骤」第 1 步开工，按 TDD。

## 背景（一句话，不用翻别的）

刚把全局 `/settings` 重排成 5-tab、模型显示真名、加了「当前实际生效」列，已上线（生产 `4557fad`，服务器已 pull+restart，`_icons.html` 已追平 git 不再冲突）。
用户接着提：**自媒体是独立项目，它的模型该跟 ai-pm 全局分家**，别混在全局设置里。

## 已过审的设计

**入口**：自媒体人设条（`_media_shell.html` 顶栏，第 20-30 行那排：人设名 / 体系库 / 切换人设）上，**挨着「切换人设」加一个 ⚙️** → 新页 `GET /media/settings`。**不放体系库下拉**（用户明确不要）。

**新页 `/media/settings` 内容**（走 media shell，一个保存按钮）：
1. **写稿模型**（route `media_script`）+ 当前实际生效
2. **自媒体默认模型**（新 route `media_default`）+ 当前生效 —— 管选题/文案/复盘等其它十几个 `media_*` 任务；选「跟随全局」= 用 ai-pm 默认。**用户已同意做这个总开关**
3. **审稿策略**（`media_review_strategy`，从全局挪回来）
4. 说明行：`API Key 在全局设置里配（所有模型共享）`

**全局 `/settings` 收缩**：`settings.html` 「模型与路由」tab 里**撤掉写稿行 + 自媒体审稿策略**，只留 ai-pm 通用（code/writing/analysis/review/vision）+ 默认/备用/Key；加一行指路「自媒体的模型在 自媒体 → ⚙️ 里设」。

**边界**：全局管 ai-pm 通用 + 共享 API Key；自媒体页管自媒体自己的模型。以后学习/财务要独立模型照此模式各自加。

## 自媒体的 `media_*` 任务清单（已从代码查明，不用再 grep）

都在 `app/services/media_ai.py` / `media_agent_tools.py`，调 `ask_ai(..., task_type=)`：
`media_topic`（选题/挖矿/reverse）、`media_interview`、`media_evidence`、`media_persona_interview`、`media_persona_extract`、`media_learn_edit`、`media_draft_audience`、`media_draft_anchor`、**`media_script`（写稿）**、`media_angle`、`media_copy`、**`media_review`/`media_critique`（审稿）**、`media_phase_review`、`media_review_cycle`。
助手对话/总AI执行走 `agent_tools._get_tool_client()`（写死 deepseek→qwen→openai，不受路由控制，跟本次无关）。

## 实现步骤（TDD，按序）

1. **`get_model_for_task` 加 media_default 回落**（`app/services/ai_router.py:61`）：
   先失败测试——task_type 以 `media_` 开头、routes 里没单独指定该任务、但 `routes['media_default']` 有设（!=auto）时，返回 media_default；否则回落全局 default。向后兼容：media_default 缺省 `auto`＝跟随全局，行为不变。
   同时加 `effective_media_default_model()` 显示解析器（复用现有 `effective_text_model` 套路，`app/services/ai_router.py` 已有 `effective_text_model`/`effective_vision_model`、`agent_tools.effective_tool_model`）。
2. **新端点 `POST /media/settings/models`**（`app/api/media.py`，参照现有 `POST /media/settings/review-strategy`）：存 `routes.media_script`、`routes.media_default`（走 routes 合并，别重建抹别的键——见宪法「保存要合并」）、`media_review_strategy`。先写路由测试（TestClient + 签名 cookie，参照 `tests/test_settings_notify.py`、`tests/test_media_ai_script_route.py`）。
3. **新页面模板 + GET 路由 `/media/settings`**：用 `_media_shell` 宏（current 传一个不高亮主线的值），三块 + 一个保存。加渲染冒烟测试（参照 `tests/test_settings_page_render.py`）。
4. **`_media_shell.html` 顶栏加 ⚙️**（第 30 行「切换人设」旁）→ `/media/settings`。
5. **全局 `settings.html` 收缩**：删写稿行 + 审稿策略块，加指路小字；`app/api/settings.py` 的 `settings_save` 去掉 `route_media_script`（保留在 routes 里靠合并不丢；更新 `tests/test_settings_routes_merge.py` 若需要）。
6. **验证**：`python -m pytest` 全绿（当前基线 **479 passed**）；本地起服眼验两页（起法见下），真机眼验。

## 本地起服 + 眼验（这次验证跑通的办法，照抄）

- 本机 8000 被残留进程占，**换端口**：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8012 --no-access-log`（后台跑）。
- 浏览器 pane **能连本机 localhost**（旧备忘说不能，已证伪）。
- 本地 /media 需登录态：生成签名 cookie 注入 pane——
  `python -c "import base64,json;from itsdangerous import TimestampSigner;from app.api.auth import get_or_create_session_secret;print(TimestampSigner(get_or_create_session_secret()).sign(base64.b64encode(json.dumps({'user':'t'}).encode())).decode())"`
  然后 pane 里 `document.cookie="session=<值>; path=/"` 再导航。
- 验完 `taskkill //F //PID <uvicorn pid>`。

## 部署（做完后）

服务器网页终端（直连 GitHub 挂，走镜像；本次改了 `.py` **必须重启**）：
`cd /www/wwwroot/ai-pm && git pull https://ghfast.top/https://github.com/gga135134-blip/ai-pm.git main && systemctl restart ai-pm && sleep 2 && systemctl is-active ai-pm`
（服务器现在 `4557fad`、`_icons.html` 已干净，不会再冲突。入口见 [[server-access-aipm2-online]] 记忆：`https://aipm2.online`，Caddy→127.0.0.1:8000。）
