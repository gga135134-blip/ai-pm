# 自媒体多人设总览 + 人设工作区隔离 设计

## 背景与目标

自媒体模块的数据层从一开始就按 `persona_id` 隔离（每张表都带 `persona_id`），但 UI 层硬编码「取第一个 active 人设」（`_first_persona_id`，15 处调用），导致：

1. 界面永远只显示第一个人设（GAGA），无法查看/切换其他人设。
2. 新建人设入口藏着（仅在零人设时弹出）。

目标：让二人公司能给每个人（GAGA、同事…）各建一个人设，各自独立工作区，**一份代码所有人设共享每个功能**。核心约束不变：**AI 注意力别被无关信息分散**——默认全隔离，只有明确决定共享的才共享。

## 非目标（本轮不做）

- 不做用户登录/权限隔离（这是共享内部工具，人设 = 内容档案，不是账户）。
- 不做人设删除/归档（YAGNI，需要时再加）。
- 不做公司级原料的共享 UI（只留接口，见下）。
- 不碰任何 AI 逻辑、不改数据表的 persona_id 隔离本质。

## 底层框架统一（前提，天然满足）

一份代码、一套路由/模板/服务。人设只是「选哪份数据」的开关（cookie 里的 persona_id）。因此更新任何功能，所有人设自动生效——永远不存在"每个人设一套系统"。

## 导航结构

| 路由 | 现在 | 改后 |
|---|---|---|
| `GET /media` | 内容看板（取第一个人设） | **人设总览页**（列出所有人设卡 + 新建） |
| `GET /media/board` | 无 | **内容看板**（读当前人设 cookie） |
| `GET /media/persona/{pid}` | 人设档案 | 不变 |

- 点总览页某张人设卡 → 写 cookie `media_persona=<pid>` → 跳 `/media/board`。
- 每个子页顶部加面包屑「← 全部人设 ｜ 当前：<人设名>」，点左侧回 `/media` 换人。

## 当前人设：cookie + helper

新增 `_current_persona_id(request, db) -> str | None`：

1. 读 cookie `media_persona`。
2. 校验该 id 是**存在的**人设行（不做归档，故不额外卡 status）；不存在则回落第一个 active 人设（等价现有行为）。
3. 读不到任何人设返回 None（沿用现有"引导建人设"逻辑）。

把现有 **15 处 `_first_persona_id(db)`** 调用改为 `_current_persona_id(request, db)`（这些路由都已有 `request`）。`_first_persona_id` 保留（回落用）。总览页 `/media` 自己**不**用它（它要列全部人设）。

**设 cookie 的路由**：新增 `GET /media/persona/{pid}/enter`——人设卡是普通链接，点击即请求此路由：设 cookie 后 302 到 `/media/board`（若 pid 不是有效人设则 302 回 `/media`）。cookie 属性：`httponly`, `samesite=lax`, `max_age` 长期（如 1 年）。

## 人设总览页（`/media` → media_overview.html）

对每个人设（`SELECT * FROM media_persona ORDER BY created_at`，含 active 与其它状态）渲染一张卡，含概况三项：

1. **一句话定位 + 当前阶段**：`one_liner` + `current_phase` 徽章。
2. **内容数**：总 `COUNT(*)` · 已发 `stage='published'` · 爆款 `is_winner=1`（按 persona_id 聚合）。
3. **绑定账号/平台**：该人设的 `media_account`（平台 + account_name）。

卡片整体是进入链接（`<a href="/media/persona/{pid}/enter">`）。页尾「＋ 新建人设」→ 弹窗。

聚合查询集中在一个服务函数 `persona_overview(db) -> list[dict]`（`app/services/media_persona_overview.py` 或就放 media.py 里的 helper），每人设一行 dict：`{id,name,one_liner,current_phase,total,published,winners,accounts:[...]}`。

## 新建人设

复用已有 `POST /media/persona`（`persona_create`，收 name/one_liner）。总览页弹窗只填**名字 + 一句话定位**，建完 302 进新人设的 `/media/board`（空工作区）。其余（阶段/特征/账号）进人设档案慢慢补。

## 共享 vs 独享

| 数据/功能 | 归属 | 实现 |
|---|---|---|
| 人设特征/signature、账号、选题、内容、发布、数据、受众、锚点、复盘(L1/L2/L3) | **独享** | 现状不变（persona_id 隔离） |
| 设置/API凭证/飞书/豆包 | **全局** | 现状不变（系统级 config） |
| **打法库 media_playbook** | **共享（全公司一池）** | 见下 |
| **原料库 media_material** | **独享 + 留共享接口** | 见下 |

### 打法库共享

- `media_playbook.persona_id` 列**保留**，语义从"归属"变为"来源/谁贡献的"（挖自哪个人设的爆款，留出处）。
- 读取去掉 persona_id 过滤，全局一池：
  - `list_playbooks(db)`（去掉 persona_id 参数）→ 返回**所有**打法，proven 在前。
  - 浏览页 `GET /media/playbook` 不再按当前人设过滤，展示全公司打法。
  - `similar_to` 归并跨全池：mine 路由取已有打法名 `SELECT name FROM media_playbook WHERE status IN ('validating','proven')`（去掉 persona_id）；adopt-playbook 归并查 `WHERE name=?`（去掉 persona_id）。
  - status 切换（validating↔proven）本就按 id，天然全局。
- 采纳新打法时 `persona_id` 记为来源内容的 persona_id（provenance，NOT NULL 不破）。

### 原料库留共享接口（不装门）

- `media_material` 加列 `scope TEXT DEFAULT 'persona'`（idempotent ALTER 迁移）。取值 `'persona'`（默认，个人独享）| `'shared'`（公司级，全人设可见）。
- 4 处读取加共享条款（注意 SQL 优先级用括号）：
  - `app/api/media.py:333`、`:966`
  - `app/services/media_ai.py:371`、`:807`
  - 改为 `WHERE (persona_id=? OR scope='shared') AND status='active' ...`。
- 现在没有任何料是 `'shared'`，故行为与现状**完全一致**（纯隔离）。**本轮不加设置 scope 的 UI**。将来想开：加个「设为公司级」按钮把某条料 `UPDATE scope='shared'`，查询一行不用改，立刻两个号可见。

## 测试

- `_current_persona_id`：cookie 命中有效人设 → 返回它；cookie 指向不存在的 id → 回落第一个；无 cookie → 回落第一个；零人设 → None。
- `persona_overview`：多人设各自聚合正确（内容总/已发/爆款分人设不串；账号只列本人设）。
- `enter` 路由：设 cookie + 302 到 /media/board。
- 打法库共享：两个人设各采纳一条打法，`list_playbooks` 都返回全部；similar_to 归并跨人设命中（A 人设建、B 人设 similar_to 命中同名 → 归并到同一条不新增）。
- 原料库接口：默认 scope='persona' 时行为不变（只见本人设料）；手工置一条 scope='shared' 后，另一人设的读取能看到它。
- 回归：只有 GAGA 单人设时，总览页/看板/子页行为与改造前一致。

## 迁移

- `media_material ADD COLUMN scope TEXT DEFAULT 'persona'` → MIGRATIONS 追加（idempotent ALTER，重启自动跑）。
- 无新表。打法库共享是纯查询改动，零迁移。
