# 自媒体模块 UI 重做 — 设计文档

> 日期：2026-07-26
> 方法论：finesse-ui **workflow 路线**（pages you OPERATE），承接全站设计系统
> 前置：`2026-07-25-ui-redesign-design.md`（全站 token/组件/shell）、`2026-07-24-media-ops-system-design.md`（自媒体业务设计）
> 可视化基准：结构 https://claude.ai/code/artifact/358716be-b236-4904-a1eb-5375cad9ca65 ｜ 体系信息 https://claude.ai/code/artifact/c5694bae-c0e3-4f0d-8df4-b50b5749fe58

---

## 1. 背景

2026-07-25 全站 UI 重设计时，自媒体只有 4 个页面（board/content/persona/topics），走的是**仪表盘路线**。之后模块快速生长到 **14 个页面**：人设（含访谈/阶段/账号）、受众、生意锚点、原料库、打法库、老文案、话题库、内容看板、内容详情、阶段复盘、周期复盘、飞书复盘、人设总览。

**问题不是"某页难看"，是结构塌了：**
- 14 个页面各自手写导航（11 个页面自拼了跳转链接），规则不一致：有的链 `/media/persona`（总览）有的链 `/media/persona/{id}`（详情）
- `打法库` 是死胡同（零出口链接）；`老文案` 只能跳内容页
- 全局导航里"自媒体"只是一个入口，进去后没有第二层导航
- 模块本质是**一条流程**（选题→内容→发布→复盘，复盘回流养体系），却被摊成平级页面堆
- 4 个新页没跟上设计系统：`media_feishu_review`、`media_legacy`、`media_persona_interview`、`media_playbook`

**用户原话诉求：** 主次分层；每一步可返回、可进下一步；标题/解释/正文、已做完/未做完/可选/不可选都要能区别开；找不到转不过去；页面自身难看；手机上难用。

---

## 2. 已锁定的决策

| 项 | 决策 |
|---|---|
| 导航骨架 | **方案 A**：主线步骤条常驻顶部 + 体系库统一入口。否决方案 B（模块内二级左栏）——全局已有一列左栏，再套一列手机上要多折一层，且会把体系库提到与主线同级，与"体系是关联"不符 |
| 主线 | **选题 → 内容 → 发布 → 复盘**。话题/选题属于主线（用户明确） |
| 副线 | 人设、受众、生意锚点、原料库、打法库、老文案 —— 不占一级菜单，走**统一入口**（顶部常驻「体系库」按钮） |
| 体系信息呈现 | **方案 B**：AI 动作旁一行注入说明（本次调用事实）。**不做**独立的"这一步会用到的体系"区块（用户明确去掉），**不做**顶部体系状态行（与体系库入口重复、手机上占高） |
| 缺口提示 | 顶部「体系库」按钮在有空库时挂一个琥珀小点，平时干净 |

---

## 3. 信息架构

### 3.1 层级

```
全局左侧栏「自媒体」
   └─ /media  人设总览（选谁 / 新建）——不属于主线，是进入工作区的门
        └─ 进入某人设后 = 工作区，以下都在 cookie 级「当前人设」上下文里
             ├─ 人设上下文条（谁·几期·绑定账号 | 体系库 | 切换人设）  ← 常驻
             ├─ 主线步骤条：1选题 2内容 3发布 4复盘                    ← 常驻
             ├─ 步骤内容区
             └─ 底部步骤导航：← 回上一步 ／ 下一步 →                   ← 常驻
```

### 3.2 主线四步与现有页面/路由的映射

**不改后端**：四步是**表现层**，落在已有路由与已有内容状态机（7 阶段 `idea→scripted→recording→editing→ready→published→reviewed`）之上。

| 步 | 承载页面 | 数据口径（判定"这一步做没做完"） |
|---|---|---|
| 1 选题 | `media_topics` | 已采用话题数 > 0 |
| 2 内容 | `media_board`（多条总览）+ `media_content`（单条详情） | 存在 stage ∈ {scripted, recording, editing} 的内容 |
| 3 发布 | `media_board` 的筛选视图（stage ∈ {ready, published}）；单条仍进 `media_content` | 存在 stage = published 的内容 |
| 4 复盘 | `media_phase_review`、`media_review_cycle`、`media_feishu_review` | 存在 stage = reviewed 的内容 |

> **发布为什么单独成步而不是并进内容**：发布阶段的动作与内容创作完全不同（三平台差异化文案、标记已发、回填数据），且是"人做完交付"的节点。它复用 `media_board` 的数据，只是换筛选与动作集，不新增后端。

### 3.3 体系库（副线，统一入口）

顶部「体系库」按钮 → 展开面板，列出六个库及其存量：

| 库 | 页面 |
|---|---|
| 人设 | `media_persona`（+ `media_persona_interview` 访谈子页） |
| 受众 | `media_audience` |
| 生意锚点 | `media_anchor` |
| 原料库 | `media_materials` |
| 打法库 | `media_playbook` |
| 老文案 | `media_legacy` |

每个库页面：顶部保留人设上下文条 + 面包屑「体系库 / 打法库」，**底部有明确返回**（回体系库 / 回当前步骤），杜绝死胡同。

---

## 4. 状态词汇表（全模块统一）

用户明确要求区分的四态，视觉规格：

| 状态 | 视觉 | 规则 |
|---|---|---|
| **已完成** | 绿勾 `--up` + 数量说明（"采用 3 条"） | 必须带**数量**，不能只是一个勾 |
| **进行中** | `--accent` 青色高亮：边框 + 底色 `--accent-soft` + 文字变青 | 全屏只有一个"进行中" |
| **未开始** | 常规样式，标签用 `--ink-3` 灰 | 可点击、不置灰 |
| **不可用** | `opacity:.45` + 锁图标 + `cursor:not-allowed` | **必须写明原因**（例："要先发布才能复盘"），用 `--warn` 呈现原因文案。禁止只置灰让人猜 |

**可选 vs 必需**：可选项在标签后加"（可选）"字样，`--ink-3` 灰；必需项不加标记。不用颜色区分可选性（颜色已被状态占用）。

---

## 5. 文字层级（全模块统一）

| 角色 | 字号/字重/字体 | 颜色 |
|---|---|---|
| 步骤页大标题 | 24px / 600 / `--display` 宋体 | `--ink-1` |
| 解释文案（这一步是干嘛的） | 13px / 400 / sans，行高 1.6，宽度 ≤620px | `--ink-3` |
| 卡片标题 | 15px / 600 | `--ink-1` |
| 卡片副信息 | 12.5px / 400 | `--ink-3` |
| 正文 | 13.5px / 400，行高 1.65 | `--ink-2` |
| 分区标签 | 11px 大写 letter-spacing .12em / 600 | `--ink-3` |

三层字色（`--ink-1` / `--ink-2` / `--ink-3`）+ 三档字号共同承担区分，不靠单一维度。

---

## 6. 新增组件（落在 base.html，供 media 各页复用）

| 组件 | class | 规格 |
|---|---|---|
| 人设上下文条 | `.pbar` | 人设名 + 期数 pill + 绑定账号 + 右侧「体系库」按钮（可挂琥珀缺口点）+「切换人设」。手机上账号信息隐藏 |
| 主线步骤条 | `.rail` / `.step` + `.done/.now/.todo/.locked` | 每步：状态标签行（图标+文字）、步名（`N · 名`）、数量说明。横向可滚动，单步 min-width 140px |
| 步骤底部导航 | `.stepnav` | 左「← 回上一步」`.btn.ghost`，右「下一步：X →」`.btn.primary`。**下一步不可用时** 按钮 disabled 且下方一行 `--warn` 写明原因 |
| AI 注入说明 | `.inj` | 紧跟 AI 动作按钮：`✓ 已注入 人设 9 条 · 原料 3 条 · 打法 0` + 「看详情」。数量为 0 的库用 `--warn` |
| 体系库面板 | `.libpanel` | 顶部按钮展开，六个库卡片（名称 + 存量 + 空库琥珀提示），点击进对应页 |

均为纯 CSS + vanilla JS，无新框架。

---

## 7. 逐页改造范围

### 7.1 系统层（先做）
- **base.html**：新增 §6 五个组件类。
- **新建 `_media_shell.html`**：Jinja2 macro，输出人设上下文条 + 步骤条 + 底部步骤导航，各 media 页 `{% import %}` 调用，传入当前步、各步状态与原因。**这是消灭 11 处手写导航的关键**。

### 7.2 主线页
- `media_topics`（选题）、`media_board`（内容 + 发布两个筛选视图）、`media_content`（727 行，最大）、`media_phase_review` / `media_review_cycle` / `media_feishu_review`（复盘）。

### 7.3 体系库页
- `media_persona`（302 行）、`media_persona_interview`、`media_audience`、`media_anchor`、`media_materials`、`media_playbook`、`media_legacy`。
- 其中 `media_feishu_review`、`media_legacy`、`media_persona_interview`、`media_playbook` **尚未套设计系统**，需补齐。

### 7.4 入口页
- `media_overview`（人设总览）：不挂步骤条（还没选人设），只做卡片列表 + 新建。

---

## 8. 移动端（优先级高于桌面）

- 步骤条横向滚动，单步 min-width 140px，当前步进入时自动滚到可见。
- 人设上下文条：隐藏绑定账号明细，保留人设名 + 期数 + 体系库 + 切换。
- 底部步骤导航：两个按钮各占一半宽度，min-height 44px。
- 体系库面板：全屏浮层而非下拉。
- 所有触摸目标 ≥44px；375px 无横向溢出。

---

## 9. 约束

- **只改模板层**：`app/templates/media_*.html`、`base.html`、新增 `_media_shell.html`。**严禁改 `app/api/media.py`、`app/services/media_*.py`**（另一窗口在做功能）。
- 四步状态判定只用**模板 context 里已有的数据**；若某状态判定需要后端未提供的字段，**降级为不显示该数量**，不得编造，也不得改后端。
- 不改内容状态机的阶段名、顺序、前进规则（服务端强制）。
- 不引新前端框架/构建；Edit/Write 改模板（禁 PowerShell `-replace`）。
- 沿用全站 token；`--up`/`--down` 仅语义用途；无裸 hex、无 emoji 当图标。

---

## 10. 验收标准

- [ ] 11 处手写导航全部由 `_media_shell` 统一接管，无页面自拼跳转
- [ ] 任意 media 页都能看到：我是谁（人设）、我在第几步、上一步/下一步是什么
- [ ] 无死胡同：每个体系库页都有返回路径（打法库、老文案重点验）
- [ ] 四态视觉可分；"不可用"必写原因
- [ ] 标题/解释/正文三层可分
- [ ] AI 动作旁有注入说明，数据取自真实调用，0 的库标琥珀
- [ ] 体系库按钮有缺口时亮琥珀点
- [ ] 14 页全部套上设计系统（含此前 4 个漏网页）
- [ ] 375px 无横向溢出、触摸目标 ≥44px
- [ ] `app/api`、`app/services` 零改动；全部模板编译通过；media 路由全部 200

---

## 10.A 七阶段按主线三步分配 + 工具归位（本轮已完成）

**背景：** 原来内容看板一次显示全部 7 个阶段列，导致「已发」同时出现在内容步和发布步；四个导入/同步工具堆在看板页头，与所在步骤无关。

**已落地的分配：**

| 主线步 | 承载阶段列 |
|---|---|
| 2 内容 | `idea` 选题 · `scripted` 脚本 · `recording` 待录 · `editing` 待剪 |
| 3 发布 | `ready` 待发（**只此一列**） |
| 4 复盘 | `published` 已发 · `reviewed` 已复盘（在 `/media/review` 的「已发内容」区块） |

实现：`media_board.html` 用 `selectattr('stage','in',[...])` 前端筛选；`/media/review` 增加 published/reviewed 查询。**未改状态机，未改 `media.py`。**

**连带修正：** `/media/ui/steps` 的 `publish.done` 由「有已发」改为 `ready == 0 and published > 0`（发布步只管待发，没积压且发过东西才算做完）。

**工具归属（用户 2026-07-26 确认）：**

| 工具 | 最终归属 | 理由 |
|---|---|---|
| 从飞书同步 | **复盘**（已落地） | 拉平台数据回填，是复盘的输入 |
| 未对上的数据 | **复盘**（已落地） | 同步没匹配上的数据要人工处理，同属数据回填链路 |
| 视频反向入库 | **复盘**（已落地） | 跟着「已发」走，补录已发内容及其表现 |
| 批量导入老文案 | **体系库**（已落地） | 导的是历史稿子，喂老文案库/打法库，与复盘无关 |

前三个的入口现在在 `/media/review` 的「已发内容」区块头部；体系库面板的「导入」组只剩「批量导入老文案」。

**遗留：** 「视频反向入库」「批量导入老文案」的浮层仍定义在 `media_board.html`，入口通过 `?tool=reverse|legacy` 跳回看板自动打开。等哪天要彻底解耦，可把浮层就地搬到复盘页/体系库页，删掉 `?tool=` 机制。当前不影响使用。

---

## 10.B 改哪儿 —— 自媒体模块维护地图

### 纯 UI 改动（不碰后端，随时可改）

| 想改什么 | 去哪 |
|---|---|
| 步骤条 / 人设条 / 体系库面板 / 底部步骤导航 —— **改一处生效全站 14 页** | `app/templates/_media_shell.html` |
| 组件样式（`.step` `.pbar` `.rail` `.libpanel` `.stepnav` `.inj`） | `app/templates/base.html`，搜「自媒体工作流组件」 |
| 步骤完成状态、体系库存量的取数 | `app/api/media_ui.py`（只读，不含业务逻辑） |
| 单个页面的内容 | 对应的 `app/templates/media_*.html` |

### 「每平台独立的标题 / #标签 / 封面」——必须动后端，三处

**为什么现在做不了：** `media_content.title` 只有一个（且存的是**选题名**，不是发布标题）、`cover_idea` 只有一个、`#标签` 无独立字段。只有 `media_publish.publish_text` 是按平台分的。判断标准见 §10.A：**每平台不一样的东西，就该挂在 `media_publish` 上。**

1. **加字段** —— `app/database.py` 的 `MIGRATIONS` 列表（约 511 行）末尾追加（该列表幂等、启动自动执行）：
   ```
   "ALTER TABLE media_publish ADD COLUMN publish_title TEXT DEFAULT ''",
   "ALTER TABLE media_publish ADD COLUMN hashtags TEXT DEFAULT ''",
   "ALTER TABLE media_publish ADD COLUMN cover_idea TEXT DEFAULT ''",
   ```
2. **存 / 读 / 生成**
   - `app/api/media.py`：`media_publish` 的 INSERT/UPDATE（约 1481–1488 行）接入新字段
   - `app/services/media_ai.py::generate_platform_copy()`（约 962 行）：按平台分别产出标题与标签
3. **界面** —— `app/templates/media_content.html` 的「三平台发布」区，每个平台卡里加对应输入框

**顺带可清理：** 届时 `media_content.cover_idea` 的隐藏镜像机制（见下）可以删掉，改为每平台各自的封面字段。

### 已知的临时机制（改后端时可一并清理）

| 机制 | 位置 | 为什么这么做 |
|---|---|---|
| 封面思路「双表单互带值」 | `media_content.html` 的 `syncBeforeCoverSave()` + `#cover-mirror` | 后端 `/media/content/{cid}/script` 的签名是 `cover_idea: str = Form("")`，默认空值。把输入框移出脚本表单后，保存脚本会清空封面思路。不能改后端，故让两个表单互相携带对方当前值 |
| `?tool=reverse\|legacy` 回跳 | `_media_shell.html`、`media_review_home.html` → `media_board.html` | 两个导入浮层定义在看板页；入口移走后靠 URL 参数跳回并自动打开。浮层就地搬家后即可删除 |

### ⚠️ 协作提醒

本轮全程绕开 `app/api/media.py` 与 `app/services/media_*.py`（另一窗口在做功能开发）。**动这两处之前先确认那条线已停**，否则会冲突。

---

## 11. 不在本轮范围

- 后端逻辑、路由、数据模型（另一窗口）
- 自媒体新功能
- 内容状态机语义变更
- 全站其他模块
