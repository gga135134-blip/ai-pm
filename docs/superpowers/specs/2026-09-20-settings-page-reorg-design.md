# 设置页重排 · 设计（spec）

- 日期：2026-09-20
- 作者：GAGA + Claude Opus 4.8
- 触发：用户在使用中发现 `/settings` 一长条、7 个模块堆叠，**模型设置被拆散**
  （全局路由在"主设置"里、自媒体审稿策略孤零零在最底下、助手模型无入口），
  且**页面显示的模型名跟实际在跑的对不上**（写"Claude Sonnet"实际 `claude-opus-5`）。

## 目标

1. **一目了然**：设置页做成一眼看清的参考页——平时不用看，要看时去设置一眼看到
   "现在到底用的什么模型"。
2. **显示 = 真实**：所有模型名一律显示代码里的真实值；用户选哪个、实际就跑哪个
   （对已生效的任务如实呈现，对未生效的如实标注，绝不做"看着能用实则无效"的控件）。
3. **模型设置归队**：散落各处的模型相关设置全部收进一个 tab。

## 非目标（本次不做）

- **不碰 `_get_tool_client()`**：会调工具的 AI（助手对话 / 总AI执行·拆解）仍走写死的
  `deepseek→qwen→openai`，Claude 进不来。让工具类 AI「选=跑」（尤其接 Claude tool-use）
  是"不是改一行"的独立后端活，另立任务。**理由**：用户明确"平时基本不问助手、
  不需要切它的模型"——为不会用的开关造可切换性属 YAGNI。
- 不改任何 POST 端点的行为；不动数据结构 / `settings.json` 字段。

## 布局：顶部 5 个 Tab（复用现有 `.tab` 组件）

### ① 模型与路由（核心，全页的眼睛）
- **API Keys**：Anthropic / OpenAI / DeepSeek / 通义千问（表单 `POST /settings`）
- **默认模型 + 备用顺序**（同上表单）
- **路由表**：一行一任务，三列 `任务 | 你选谁 | 当前实际生效（真实模型名）`
  - 写稿 / 文案(writing) / 代码(code) / 分析(analysis) / 审核(review) / 识图(vision)
    —— 这些走 `ai_router`，选=跑本就成立，"当前实际生效"列显示解析后的真实模型
  - **自媒体·审稿策略**（从页面最底下 `POST /media/settings/review-strategy` 搬上来归队）
  - **助手对话**：**只读**一行，老实标「DeepSeek `deepseek-chat` · 写死，设置不控制它」
    （项目宪法第14条：界面不许说谎——不做成看着能选实则无效的下拉）
- **费用参考表**（真实价格）

### ② 账号
用户列表 / 加用户 / 改密码（三个独立表单，端点不变：
`/settings/users/{add,change-password,delete}`）

### ③ 通知
Server酱 / PushPlus / 飞书 Webhook（`POST /settings` 表单内）+ 发送测试通知
（`POST /settings/test-notify`）

### ④ 数据源与集成
飞书多维表格 + 字段映射（`POST /settings/feishu` + `/settings/feishu/test`）
+ 豆包 ASR 凭证（`POST /settings/asr`）

### ⑤ AI 宪法
公司运作手册（`POST /settings/manual`）

## 显示真名：真实值以代码为准

| 现在页面写 | 实际在跑（真实值来源） |
|---|---|
| Claude Sonnet | **claude-opus-5**（`ai_router.CLAUDE_MODEL`） |
| DeepSeek V3 | **deepseek-v4-flash**（`_call_deepseek`）；工具类是 **deepseek-chat**（`_get_tool_client`） |
| GPT-4o | gpt-4o ✅（`_call_openai`） |
| 通义千问 Plus | qwen-plus ✅（`_call_qwen`）；识图 qwen-vl-plus |

"当前实际生效"列的取值：复用 `ai_router.get_model_for_task(task_type)` 的解析结果，
再映射到真实模型名（避免另写一套解析逻辑、避免和后端不一致）。

## 关键设计细节

- **Tab 切换 + 多表单保存的回位**：每个表单 POST 后重定向回 `/settings`，会丢掉当前 tab。
  **定用 `sessionStorage`**（切 tab 时写、页面加载时恢复，参照 `_media_shell` 里 `ms_last_main`）——
  不动任何端点的重定向，跟"后端不变"一致。**必须保证保存后停在原 tab**，不跳回第一个。
  （默认 tab = ①模型与路由；`sessionStorage` 读不到或异常时回落到它，try/catch 包住。）
- **后端不变**：本次是纯前端重排 + 改标签。唯一模板层动作是把
  `/media/settings/review-strategy` 那个表单从底部搬进 ①。

## 验证（项目宪法第 11/15 条：UI 必须用眼睛验，每条渲染路径都真跑）

1. `python -m pytest` 全绿（后端未改，现有 464 测试应不受影响）——若有涉及 settings 渲染的
   测试，确认仍通过。
2. 浏览器真机（`https://aipm2.online/settings`，本地起服则本地）逐 tab 眼验：
   - 5 个 tab 都能切、切换后内容对；
   - 每个表单**分别**保存一次，确认保存后**回到原 tab**、值持久；
   - "模型与路由"表里"当前实际生效"列显示的真实模型名，跟设置一致；
   - 助手那行显示"写死 DeepSeek"、不可选。
3. 手机窄屏（tab 可横滑 / 不撑破）。

## 风险 / 边界

- 设置页只有一个渲染路径（`GET /settings` → `settings.html`），无共享片段被多路复用，
  比历史上"抽片段两条路只验一条"的坑小；但仍要每 tab、每表单真跑一遍。
- `settings.json` 已有的字段/存量不变，无迁移。
