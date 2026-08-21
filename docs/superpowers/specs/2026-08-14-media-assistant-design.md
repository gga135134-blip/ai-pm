# 自媒体 AI 助手（每人设一个）设计 spec

**日期：** 2026-08-14
**分支：** feat/media-assistant
**触发词：** 接 AI-PM

## 1. 目标

给自媒体模块接入**对话式 AI 助手**：用一句话操作，减少"每加个功能就多一个按钮"的膨胀。助手绑定**当前人设**，能查、能建草稿、能改（留痕可撤），核心动作要人确认。对话是**补充**不是替代——高频/批量仍走 UI。

## 2. 已拍板的设计支柱（brainstorm 逐条确认）

1. **每人设一个**（不做全局，怕混）。助手绑当前人设（cookie）。
2. **共享库按现有规则可见**：打法库全部（本就共享）、原料库 `scope='shared'` 的公司级料。助手工具查库的方式与 `write_script`/`list_playbooks` 一致，自动继承隔离+共享边界。
3. **人拍板分层**：
   - **查（读）**：无需确认、不留痕。
   - **改（可逆）**：AI 直接做，但**留痕、可撤**（建选题/写下一条续集/写脚本草稿/改草稿文字）。
   - **核心/入库/删除**：AI 可做但**要人确认**，**也留痕、可撤**（采纳素材·口头禅·打法进库、标爆款、删内容）。
4. **全程留痕 + 可回滚**：助手每次写操作记动作日志，有"助手改动记录"页可逐条撤销；被动过的记录挂"助手"小标识。
5. **注意力纪律**：工具只回 scoped 数据（打法用 match 只回一条；列内容回标题清单不回全文），绝不整库倒进 agent 上下文。
6. **入口在看板下方**（可观性强），**助手本体是独立一页** `/media/assistant`（够空间对话+留痕+切人设）。不塞进选题页的窄栏。
7. **复用 `run_agent_loop`**（DeepSeek 函数调用循环，总AI/项目AI 都用它）。

## 3. 分期实施（本 spec 覆盖设计全貌，落地分两期）

因为这版偏大（工具集 + 确认机制 + 动作日志/撤销 + 对话页），拆两期，**先出 Phase 1 看效果**，再上 Phase 2 的确认层。

### Phase 1（本轮建）= 查 + 改（草稿）+ 留痕可撤 + 对话页
- 助手引擎 + 对话页 + 入口。
- 工具集：**查**（列内容/选题池、看某条、看打法库、看原料库/受众/锚点）+ **改草稿**（建选题、写下一条续集、写脚本草稿、匹配打法）。
- 动作日志 + 「助手改动记录」页 + 撤销（Phase 1 的改层动作都是 `applied` 即时执行、可撤）。
- 被动过的记录挂"助手"标识。
- **不含**核心/入库/删除工具（那些 Phase 1 仍走现有 UI/复核页）。

### Phase 2（下一轮，另起 spec/plan）= 核心动作进对话 + 确认机制
- 加核心工具（采纳素材/口头禅/打法、标爆款、删内容）。
- 确认机制：核心动作**不即时执行**，在动作日志里落 `pending`，助手回复给"待确认卡"，人确认（点/回"确认"）才执行转 `applied`。

## 4. 引擎（复用 + 小幅扩展 run_agent_loop）

`run_agent_loop(prompt, system, project_id, max_steps, on_step)` 现在硬编码 `TOOL_SCHEMAS` + `_dispatch_tool(name,args,project_id)`。**向后兼容地扩展**：加可选参 `tool_schemas=None`（默认 TOOL_SCHEMAS）、`dispatch=None`（默认 _dispatch_tool）、`ctx=None`（默认取 project_id）。循环里 `tools=tool_schemas or TOOL_SCHEMAS`、`await (dispatch or _dispatch_tool)(name, args, ctx if ctx is not None else project_id)`。现有调用零改动。

媒体助手传：媒体 `MEDIA_TOOL_SCHEMAS` + `dispatch_media_tool` + `ctx=persona_id`。

## 5. 媒体工具集（Phase 1）

放 `app/services/media_agent_tools.py`（schemas + dispatch）。每个工具签名 `async tool_xxx(args: dict, persona_id: str) -> str`（返给 agent 的文本，scoped）。

**查（无需确认、不留痕）：**
- `list_contents(stage?)` → 该人设内容标题清单（可按阶段筛），回标题+id+阶段，**不回全文**。
- `read_content(id)` → 某条的标题/谜题/转写或脚本（单条全文，人点名了才给）。
- `list_topics()` → 选题池待选清单（标题+id）。
- `list_playbooks()` → 打法库（**共享全部**，名字+适用+状态，紧凑一行一条）。
- `list_materials()` / `list_audiences()` / `list_anchors()` → 该人设的（原料库含 `scope='shared'`）。

**改（可逆·即时·留痕·可撤）：**
- `create_topic(title, puzzle?, reason?)` → 建一条选题进**选题池 `media_topic`**（status='pool'，复用现有选题池落库；来源标记如 source='assistant'）。记日志（target_table='media_topic'）。
- `write_next(from_content_id)` → 读那条转写稿+结尾预告+人设，AI 拟下一条选题，建成**一条 `media_content`（idea 阶段）**（续集是要展开做的具体内容，不是池里待选）+ 记 `parent_content_id` 血缘。记日志（target_table='media_content'）。
  - 区别：`create_topic`=往选题池加个待选想法；`write_next`=直接开一条具体的续集内容。
- `draft_script(content_id, hint?)` → 复用 `write_script` 写 `ai_draft` 草稿（不碰定稿 `script`）。记日志。
- `match_playbook(content_id)` → 复用打法匹配，回**一条**最贴的打法（读，不留痕）。

**纪律**：`list_*` 回清单/摘要不回全文；`list_playbooks` 是给助手判断用的紧凑清单（这是隔离的助手上下文，不是写稿 prompt）；写稿/续集仍只注一条打法（沿用🅓的匹配）。

## 6. 数据

**6.1 `media_content` 加 `parent_content_id TEXT DEFAULT ''`**（MIGRATIONS，续集血缘）。

**6.2 动作日志表 `media_assistant_action`**（SCHEMA）：
```sql
CREATE TABLE IF NOT EXISTS media_assistant_action (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    conversation_ref TEXT DEFAULT '',   -- 关联对话消息/轮次
    action_type TEXT NOT NULL,          -- create_topic|write_next|draft_script|(Phase2: adopt_*|mark_winner|delete_content)
    target_table TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    before_json TEXT DEFAULT '',        -- 改前值（撤销用）；create 类为空
    after_json TEXT DEFAULT '',
    status TEXT DEFAULT 'applied',      -- applied|reverted（Phase2 加 pending）
    reversible INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

**6.3 对话历史表 `media_assistant_message`**（SCHEMA，`messages` 表带 projects FK 不复用）：
```sql
CREATE TABLE IF NOT EXISTS media_assistant_message (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    role TEXT DEFAULT 'user',           -- user|assistant
    content TEXT DEFAULT '',
    cost REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

## 7. 留痕 + 撤销

- **服务** `app/services/media_assistant.py`：`log_action(db, persona_id, action_type, target_table, target_id, before, after, ...)`；`list_actions(db, persona_id)`；`revert_action(db, action_id)`。
- **撤销语义**（靠 before_json）：`create_topic`/`write_next` → 删掉建的记录；`draft_script` → 把 `ai_draft` 还原成 before（草稿被覆盖前的值）。撤销后 status='reverted'。
- **改动记录页** `GET /media/assistant/actions`：按时间列 applied 动作，每条带「撤销」按钮（`POST /media/assistant/action/{id}/revert`）。
- **"助手"标识**：被动过的记录（有对应 applied 动作）在正常界面挂个小标签。Phase 1 最小实现——在改动记录页看全貌即可；内容/选题行的内联标识可用"该 id 在动作日志里"派生（查询时 LEFT JOIN 或单独查），本轮**先只在内容详情页头部**显示"🤖 助手创建/改过"（够用），全站内联标识留作打磨。

## 8. 对话页 + 入口

- **入口**：内容看板（`media_board.html`）步骤条下方加一张「🤖 助手」卡（显眼），链到 `/media/assistant`。
- **助手页** `GET /media/assistant`（认当前人设 cookie，顶部人设名 + 切换）：消息流（读 `media_assistant_message`）+ 输入框 + 「🤖 改动记录」入口。
- **对话端点** `POST /media/assistant/ask`（Form message）：存用户消息 → 组 prompt（系统提示=公司宪法+人设定位+助手职责+纪律）→ `run_agent_loop`(媒体工具, ctx=persona_id) → 存助手回复 + cost → 返回 JSON（回复文本 + 本轮工具步骤摘要，像项目AI那样展示"调了啥工具"）。
- **清空** `POST /media/assistant/clear`。

## 9. 代码落点（Phase 1）

- `app/services/agent_tools.py`：`run_agent_loop` 加 3 个可选参（向后兼容）。
- `app/services/media_agent_tools.py`（新）：`MEDIA_TOOL_SCHEMAS` + 各 `tool_*` + `dispatch_media_tool(name,args,persona_id)`。查类复用现有 SQL；改类复用现有落库/`write_script`/匹配；改类调 `log_action`。
- `app/services/media_assistant.py`（新）：`log_action`/`list_actions`/`revert_action` + `MEDIA_ASSISTANT_SYSTEM` 系统提示。
- `app/api/media.py`：`/media/assistant`（页）、`/media/assistant/ask`、`/clear`、`/actions`、`/action/{id}/revert`。
- `app/database.py`：+`parent_content_id` 列（MIGRATIONS）+ 2 张新表（SCHEMA）。
- `app/templates/media_assistant.html`（新）、`media_assistant_actions.html`（新）、`media_board.html`（加入口卡）、`media_content.html`（头部"助手创建/改过"标识）。
- 测试：工具（scoped 查询/共享库可见/建选题+续集+草稿落库且记日志/match 回一条）、留痕撤销（create→删、draft→还原、status 流转）、对话端点冒烟（存消息+调 agent，agent 用 fake 工具循环）、入口/页面渲染。

## 10. 边界（本轮不做 = Phase 2 或更后）

- 核心/入库/删除工具 + 确认机制（Phase 2）。
- 改人设阶段/trait（人设进化）不进助手——太重，走复盘页。
- 全站内联"助手"标识打磨（本轮只内容详情页头部）。
- 常驻侧边栏助手（v2 体验升级）。
- 多轮工具 streaming 展示（长任务）——本轮同步返回够用。

## 11. 验收清单（Phase 1）

- [ ] run_agent_loop 向后兼容扩展（现有调用不受影响）。
- [ ] 媒体工具：查类回 scoped 清单不回全文；list_playbooks 回共享全部；list_materials 含 scope='shared'。
- [ ] create_topic/write_next(带 parent_content_id)/draft_script 落库且各记一条 applied 动作日志；match_playbook 回一条。
- [ ] 撤销：create→删记录、draft→还原 ai_draft、status→reverted。
- [ ] 对话端点：存 user+assistant 消息、走 run_agent_loop（媒体工具、ctx=persona_id）、记 cost、回工具步骤摘要。
- [ ] 入口在看板步骤条下方；助手页认当前人设、能对话、能进改动记录页撤销。
- [ ] 内容详情页头部显示"🤖 助手创建/改过"（对有动作日志的内容）。
- [ ] 全套测试绿 + 浏览器冒烟（发一句→AI 调工具建选题→改动记录出现→撤销→选题消失），无 Jinja/500。
- [ ] 真机 DeepSeek 端到端：对话让它"针对某条写下一条"，真的建出续集并记血缘+日志。
