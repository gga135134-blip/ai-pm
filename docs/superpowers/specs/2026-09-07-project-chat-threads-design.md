# 项目对话分线 + 从对话沉淀（设计）

日期：2026-09-07
状态：待董事会（Gaga/An）过审

## 一句话

不做单独的"秘书"页。改造现有"总 AI 对话"，让**跟 AI 聊**这件事变得可追溯、聊出来的东西能自动沉淀：

- **A｜对话分线**：聊天不再是一条大流，按项目分线；每条线每轮现读该项目的全量内容；线可随时清空。
- **B｜从对话沉淀**：聊到决策/待办/要点，一键（或划选）拴到对应项目，两头双向留痕。

## 背景 / 痛点（董事会原话归纳）

用户的真实流程：想立项 → 找 AI 聊 → 聊出几个决策 → 让它立项 → 项目跑起来后中途有问题再回去聊。当前"总 AI 对话"有两个不趁手：

1. **所有对话挤在一条流里**（`messages` 全是 `channel='master_ai'`、`project_id` 恒为 NULL），立项聊的和三个月后答疑的混在一起，翻不回"当初 P005 为啥这么定"。
2. **聊出来的决策会被埋掉**——AI 未必主动 `decision` 存档，就算存了，"怎么聊到的"过程还是散在滚动条里，没拴到项目。

## 核心心智模型（贯穿全设计）

> **聊天记录 = 草稿纸；项目的真实记忆 = 项目数据（描述 / 任务 / 决策 / 核心档 / 知识库）。**

AI 每轮从数据库现读项目数据，不依赖聊天记录当记忆。因此：
- "AI 随时能看项目内容" = 真（每轮现读注入）。
- "对话线太长可清空" = 安全（清掉的只是草稿纸；重要的早在聊的过程里被能力 B 沉淀进项目数据了）。

## 决策记录（本次拍板）

| 议题 | 结论 |
|---|---|
| 独立秘书页 vs 改造总 AI 对话 | **改造总 AI 对话**，不新建秘书页 |
| 对话分线方式 | **按项目分线**（做法二）：每项目一条线 + 一条"综合线"放立项前/跨项目闲聊 |
| AI 能否随时看项目内容 | 能。每轮现读注入项目**全量**：描述 + 任务(带状态) + 决策 + 核心档 + 知识库导航 |
| 对话线清空 | 支持，按线清空（清草稿纸，不动项目数据） |
| 立项前对话去哪 | 在**综合线**聊；`create_project` 后**不搬运聊天记录**，新项目起一条空线，立项聊到的关键决策靠能力 B 沉淀进新项目 |
| 沉淀触发方式 | **两种都留**：① AI 主动拟卡（默认开）② 用户手动划选存 |
| 沉淀去向 | 决策→`decisions`(made_by=用户，归"人工决策")；待办→`tasks`(挂当前项目)；要点/资料→`notes`(挂当前项目) |
| 双向留痕 | 每条沉淀记录 ↔ 来源消息互相回指（决策卡有【看当初讨论】；对话消息有 📌「已记入…」） |
| 语音输入 | 列入 v1，但作为**独立最后一片**，且**只在部署到公网服务器后生效**（豆包录音文件识别需公网可达音频 URL） |

## 架构 / 数据流

```
[项目对话线 UI] --选中某项目线--> 每轮:
    build_project_chat_context(project_id)   # 现读项目全量
      + _get_recent_history(project_id)      # 只读本线历史
    --> master_chat(message, sender, model, project_id)
    --> AI 返回 { action, params, reply, capture_suggestion? }
          reply 落 messages(project_id=该线)
          capture_suggestion 渲染成"待沉淀卡片"(不落库,等用户点)
[用户点"记下" / 划选"存为…"]
    --> POST /chat/capture
          写 decisions/tasks/notes + 建 chat_captures 回指
```

综合线（`project_id IS NULL`）：不注入单项目上下文，改用 `get_context_for_master()` 的全局摘要；立项对话在此发生。

## 数据模型

**复用**：`messages.project_id`（已存在，当前恒 NULL）作为分线键。
- 某项目线 = `messages WHERE channel='master_ai' AND project_id=<pid>`
- 综合线 = `messages WHERE channel='master_ai' AND project_id IS NULL`
- 清空一条线 = `DELETE ... WHERE channel='master_ai' AND project_id (= <pid> | IS NULL)`

**新增一张回指表**（同时服务两个方向的留痕）：
```sql
CREATE TABLE chat_captures (
  id           TEXT PRIMARY KEY,
  message_id   TEXT NOT NULL,      -- 来源消息（对话里那句）
  project_id   TEXT,               -- 冗余，便于筛
  kind         TEXT NOT NULL,      -- decision | task | note
  target_table TEXT NOT NULL,      -- 'decisions' | 'tasks' | 'notes'
  target_id    TEXT NOT NULL,      -- 落库记录 id
  created_at   TEXT NOT NULL
);
```
- 消息侧 📌 标记：`SELECT ... FROM chat_captures WHERE message_id=?`
- 决策侧【看当初讨论】：`SELECT message_id FROM chat_captures WHERE target_table='decisions' AND target_id=?`

沉淀落库仍复用现成写法（对齐 `master_ai.py` 里的 `_action_decision / _action_create_note` 及 `tasks` 插入）：
- decisions：`made_by=<sender>`（人工决策），`project_id`=当前线
- notes：`source_type='chat_capture'`，`project_id`=当前线
- tasks：`project_id`=当前线，`assignee` 默认人（董事会），可标 `ai`

## 组件拆解（每片可独立实现/测试）

### 片 1｜对话分线（后端 + 最小 UI）
- `master_chat(..., project_id)`：入参加线；写消息带 `project_id`；`_get_recent_history` 按 `project_id` 过滤。
- API：`GET /chat?project=<code|id>` 打开某线；`POST /chat/ask` 带 `project_id`；`POST /chat/clear` 按线清空。
- 依赖 / 用途：读 `projects` 列活跃项目做左栏；`messages.project_id` 分线。

### 片 2｜每轮现读项目全量上下文
- 新函数 `build_project_chat_context(project_id) -> str`：在现有 `project_context.build_project_context` 基础上补 **任务清单(带状态)** 与 **决策列表**（原函数只给描述 + 核心档 + 知识库 + 检索参考）。综合线走 `get_context_for_master()`。
- 用途：让"AI 随时能看项目内容"落地为真·完整；清空对话后仍门儿清。

### 片 3｜从对话沉淀 + 双向留痕
- `MASTER_SYSTEM` 扩展：允许在响应里附 `capture_suggestion: {kind,title,content,reason}`（默认在决策/待办成型时主动拟；无则不附）。
- 前端：AI 气泡下渲染"待沉淀卡片"（记下 / 改改 / 忽略）；每条消息可划选/点按"存为 决策/笔记/任务"。
- API `POST /chat/capture`：写目标表 + 建 `chat_captures`。
- 决策日志页 `decisions.html`：有回指的决策显示【看当初讨论】→ 跳 `/chat?project=…#msg-<id>`；对话消息渲染 📌「已记入…」→ 跳目标记录。

### 片 4｜语音输入（独立、可延后；仅公网服务器生效）
- 前端麦克风录音（MediaRecorder）→ 上传音频 → 存 `ASR_PUBLIC_DIR` → 生成免登录 token URL（复用 `media_asr_audio` 那套 `GET /…/asr-audio/{token}` 模式）→ `asr_client.transcribe_url(public_url, cfg)`（异步提交+轮询）→ 回填输入框 → 走片 1 的正常发送。
- 硬约束：豆包"录音文件识别"要**公网可达**的音频 URL，本地 dev 转不了，需 `public_base` 配置（已在 settings）。若要先上线，片 4 可最后做。

## 错误处理
- `POST /chat/ask` 沿用现有稳健 JSON 提取（`_extract_action_json`）与 try/except 包动作，失败在 `reply` 里如实报错、不假装成功（宪法诚信红线）。
- `capture_suggestion` 解析失败 → 忽略该卡，不影响 `reply` 正常显示。
- `POST /chat/capture` 落库失败 → 返回错误，前端卡片保持未沉淀态，可重试。
- 清空按线做，带二次确认（沿用现 `/chat/clear` 的 confirm）。

## 测试
- 分线：两条线消息互不串；`_get_recent_history` 只回本线；清空只清本线。
- 上下文：`build_project_chat_context` 含描述/任务状态/决策；总量 ≤ 上限（沿用 `TOTAL_CONTEXT_MAX_CHARS`）。
- 沉淀：capture 写对目标表 + `chat_captures` 回指；两个方向都能由 id 反查到对方。
- 立项：从综合线 `create_project` 后新项目起空线；综合线历史不被搬走。
- 语音：`transcribe_url` 已有单测（`tests/test_asr_client.py`），片 4 只测"上传→token URL→回填"编排，ASR 适配器 monkeypatch。

## 本版范围外（不是不做，只是这版先不带；董事会要就随时加）
- 日报/周报汇总（这版聚焦"归档+行动"；要"复盘总结"再加）。
- 提醒推送、AI 主动追问。
- 立项时自动搬运聊天记录到新项目线（这版用能力 B 沉淀替代；如果董事会更想要"搬运"，可另加）。

（会话级分线（做法一）、单流筛选（做法三）是被否决的**方案选型**，不属于此列——脊梁已定按项目分线。）

## 复用到的现有件
- `app/services/master_ai.py`：`master_chat`、`_action_decision/_action_create_note`、`get_context_for_master`、`_extract_action_json`、`_resolve_project`、`MASTER_SYSTEM`。
- `app/services/project_context.py`：`build_project_context`（片 2 在其上扩展）。
- `app/api/chat.py`：`/chat`、`/chat/ask`、`/chat/clear`。
- `app/templates/chat.html`、`decisions.html`；`app/api/projects.py`（项目/决策加载）。
- `app/services/asr_client.py`：`transcribe_url`；`app/api/media.py` 的 `media_asr_audio` 免登录音频服务模式。
