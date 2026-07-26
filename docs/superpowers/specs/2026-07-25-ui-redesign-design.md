# AI-PM 全局 UI 重设计 — 设计文档

> 日期：2026-07-25
> 方法论：finesse-ui skill（Product 路径）+ Dieter Rams 上一轮成果
> 可视化预览（本设计的像素基准）：https://claude.ai/code/artifact/0338e91e-d062-4c8f-9c76-b841c13e8779
> 字体候选对比：https://claude.ai/code/artifact/d8f62caf-350f-4ad4-b997-66630a6ddfe1

---

## 1. 背景与目标

### 1.1 为什么再来一轮

2026-06-20 已做过一轮 Dieter Rams 重设计（评 15/30 判 REDESIGN），做的是**减法 / 信息架构**：7 层工具墙 → 3 层、颜色统一 blue/violet/amber、Tab 精简、加移动端响应式。那轮解决了**杂乱**，但**从未触碰视觉 craft 层**——表面质感、排版节奏、间距刻度、阴影/边框质量、组件打磨、数据可视化样式。所以结果是"更干净但仍然廉价"。

用户诊断（本轮确认的病根，二选二命中）：
- **表面廉价 / 平**：配色发灰发脏、卡片和按钮是默认 SaaS 样子、阴影/边框生硬、没质感，像半成品/免费模板。
- **没有识别度 / 没气质**：整体没个性，跟随便一个后台没区别，不像"我们公司自己的工具"。

这两点正是 finesse Product 路径的主战场，**与上一轮不重叠**——这是缺失的另一半，不是重复。

### 1.2 目标

给 ai-pm 一套**高质感、有识别度、成体系**的视觉语言，落成可全站复用的**设计 token + 组件层**，一次性覆盖全部页面。核心识别是"**AI 管 AI、人看着 worker 干活的作战室 / 指挥中心**"——把 ai-pm 独特的产品概念变成视觉语言。

---

## 2. 已锁定的方向决策

| 维度 | 决策 |
|---|---|
| **灵魂（soul）** | 作战室 / 控制台（Grafana 系）为**主气质**，用于仪表盘/项目/作战室等"看数据"的页面；阅读型页面（知识库/笔记）借 Linear 的**克制**（安静、留白、悬停出操作）。二者是**同一套系统**的两种"气质"，不是两套配色。 |
| **配色** | finesse **Steel Cyan**（监控/自动化类产品推荐）。青色 accent + 紫色作 AI 功能标记 + 语义色，青调中性 ramp。**深色为默认** + 浅色模式切换。 |
| **技术路线** | **设计 token + 组件 CSS 层**（落 base.html）。不碰 `tailwind.min.css`、不加构建、不引新前端框架、纯 CSS + vanilla JS。绕开裁剪版 tailwind 缺色问题。 |
| **范围** | **全站全部页面**（约 40 个模板）。另一个功能窗口已停工，base.html 冲突不再是顾虑。 |
| **节奏** | **先立系统、再铺页面**（系统不定死每页都返工）：base.html → 项目详情/作战室（旗舰）→ 知识库（最不满意，重点治）→ 其余页面。 |

**否决的技术路线**（记录以防回退）：
- 继续手补 tailwind 缺失工具类（上轮做法）——不 scale、越补越乱、做不了深浅色切换、表达不出成体系视觉。
- 换完整版 tailwind / 上 CDN + 构建——违反"不引新框架/不加构建"约束，生产 CDN 不可靠。

---

## 3. 设计 Token（落在 base.html 的 `<style>` 里，全站单一来源）

### 3.1 颜色 token 合约

一套 CSS 变量，`:root` 为深色默认，`[data-theme="light"]` 覆盖浅色，并跟随 `@media (prefers-color-scheme)`。**CSS 里不出现裸 hex，一律引用变量。**

```css
:root{
  /* 中性 ramp（青调，占 80% 像素——不廉价的关键，不是纯灰） */
  --page:#0a0e0f; --bg:#12181a; --panel-2:#192124; --border:rgba(255,255,255,.08);
  --ink-1:#e8f0f1; --ink-2:#a6b3b5; --ink-3:#7e8c8f;   /* ink-3 需 ≥4.5:1 on --bg */
  /* 品牌 */
  --accent:#2FD3C5; --accent-soft:rgba(47,211,197,.14); --on-accent:#06110f;  /* 青亮，配深字 */
  /* AI 功能标记（保留"AI=紫"语言，但只当功能色，不装饰） */
  --ai:#9B85FF; --ai-soft:rgba(155,133,255,.15);
  /* 语义色（纯功能，不参与品牌） */
  --up:#3FCB86; --down:#FF6257; --warn:#F0A868;
  /* 效果 */
  --glow:rgba(47,211,197,.28);   /* 每屏最多用一次：主 CTA / 关键卡 */
  --shadow:0 1px 3px rgba(0,0,0,.38); --shadow-lg:0 24px 60px -40px rgba(0,0,0,.8);
  /* 侧栏 */
  --rail:#0c1213; --rail-active:var(--accent-soft);
  /* 半径/字体 */
  --r:14px; --r-sm:9px;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  --display:"Noto Serif SC","Source Han Serif SC","Songti SC",SimSun,serif;
}
[data-theme="light"]{
  --page:#eef2f1; --bg:#ffffff; --panel-2:#f2f6f5; --border:rgba(16,44,42,.10);
  --ink-1:#0f2322; --ink-2:#3a4b4a; --ink-3:#63736f;
  --accent:#127C77; --accent-soft:rgba(18,124,119,.10); --on-accent:#ffffff;
  --ai:#5B4BD1; --ai-soft:rgba(91,75,209,.10);
  --up:#2C7A62; --down:#C0453C; --warn:#B0730F;
  --glow:rgba(18,124,119,.24);
  --shadow:0 1px 3px rgba(19,61,58,.06); --shadow-lg:0 24px 60px -40px rgba(19,61,58,.30);
  --rail:#0f2322;
}
```

**碰撞检查（finesse §7，落地前 grep 复核）**：青 accent 与绿 `--up` 需可区分（作战室里 worker 运行=青/live，完成=绿）；`--ink-3` 在 `--bg` 上 ≥4.5:1；青/紫 fill 上文字对比达标（青用深字 `--on-accent`）；`--border` 是 alpha 不是硬灰；`--glow` 每屏一次；`:root` 外无裸 hex。

### 3.2 间距 / 半径 / 阴影

- 间距 4px 基：4 / 8 / 12 / 16 / 24 / 32 / 48。
- 半径：卡片 `--r:14px`、小控件 `--r-sm:9px`、pill `999px`。
- 阴影：染墨微阴影 `--shadow`（每张卡），大阴影 `--shadow-lg` 只给浮层/模态。**禁止硬 1px 灰边框当分隔**——用发丝 alpha 边框或微阴影。

### 3.3 字体与字阶

- **字阶（固定档，不用 clamp）**，各级明确拉开：

  | 角色 | 字号 | 字重 | 颜色 |
  |---|---|---|---|
  | 数据大数字（KPI 值） | 31px mono | 700 | `--ink-1` |
  | 页面标题 / 项目名 | 26–27px **`--display`** | 600 | `--ink-1` |
  | 模块标题（看板/作战室） | 18px | 650 | `--ink-1` |
  | 列表项标题（笔记标题等） | 16px | 600 | `--ink-1` |
  | 正文 | 14px | 400 | `--ink-2` |
  | 说明 / 副标题 | 12px | 400 | `--ink-3` |
  | 分区标签（eyebrow） | 11.5px 大写 letter-spacing .12em | 600 | `--ink-3` |

- **字体分工**：`--display`（宋体）**只用于页面级大标题**；`--sans`（系统黑体）正文/控件；`--mono` 数字/代码/worker 步骤/费用。
- **`--display` 落地方案**：优先 **系统宋体栈**（`Songti SC` / `SimSun` 全平台自带，零下载，即出宋体气质）；可选增强：把 **思源宋体（Noto Serif SC）常用 3500 字子集**化为 woff2（约 1MB）自托管到 `/static`，`font-display:swap`。**不要用 Google Fonts CDN**——腾讯云 + 国内用户，Google Fonts 常被墙/慢。
- **保留现有全站字号缩放**（base.html 的 `--ui-font-px` / rem 机制）不动，与本字阶叠加。

### 3.4 图标策略：干掉 emoji，换内联 SVG

现在满屏 `📁⭐🔍📷🗂️🤖🛰️` 是"廉价"一大来源。

- 建一套 **内联 SVG 图标**（约 30 个，`stroke:currentColor` 自动跟色/跟深浅、`width/height:1em` 跟字号），纯静态、离线、零依赖、无框架。
- 实现方式：**SVG symbol sprite**（`app/static/icons.svg` 存 `<symbol>`，模板 `<svg><use href="/static/icons.svg#folder"></use></svg>`），或 Jinja2 macro 直出内联 SVG。**推荐 Jinja2 macro**（`{% macro icon(name) %}`），零额外请求、可传 class。
- 需要的图标（起始集）：folder / file / doc / search / plus / check / clock / play / pause / robot(ai) / satellite(war) / chart-bar / kanban / tag / edit / move / share / trash / copy / settings / gear / chevron / arrow-up / arrow-down / dollar / bell / menu / x / star / eye / dots。
- **保留 🛰️ 作为作战室 motif**（可选，一两处品牌意味），其余全部替换。

---

## 4. 布局 Shell

### 4.1 左侧栏（canonical dashboard shell）

导航从**顶栏改为左侧栏**（finesse 仪表盘标准布局）：

- 宽 230px（中屏 196px）；`--rail` 底色、右侧发丝边框。
- 结构：顶部 brand → 分组菜单（工作区：AI Chat / 总览 / 项目 / 知识库 / 自媒体 / 学习 / 财务）→ 底部固定组（设置 + **深浅色切换按钮**）。
- 菜单项：图标 + 文字（永不纯图标）；选中态 = `--accent-soft` 底 + accent 文字 + 左侧 3px accent 竖条。
- 主区：顶栏（52px：面包屑 + 搜索框）+ 滚动内容区（padding 22/24px）。

### 4.2 深浅色切换

- 全局开关，存 localStorage（**复用现有 `--ui-font-px` 同款机制**：页面顶部 inline script 尽早读取，避免闪烁），并尊重 `prefers-color-scheme` 作为初值。
- 入口放侧栏底部。深色默认。

### 4.3 移动端

- 沿用自定义 `@media`（裁剪版 tailwind 无 `md:` 布局工具类，已 grep 确认，只有 `md:hover/focus`）。
- **两级断点**：`≤900px` 内容列堆叠（KPI 2 列、看板单列、知识库侧栏移上方、隐藏顶栏搜索）；`≤560px` 左侧栏折成顶部横向可滚动条。
- 触摸目标 ≥44px；看板横向滚动；worker 行降列；手机优先级高于桌面。

---

## 5. 组件库（语义类，全站复用）

每个组件一个语义 class，模板从"一堆 tailwind 工具类"改成用这些类。锁定"一个意图一个变体"。

| 组件 | class | 要点 |
|---|---|---|
| 按钮 | `.btn` / `.btn.primary` / `.btn.ai` / `.btn.ghost` | primary=accent 填充 + 一次性 glow；ai=紫软底；一律图标+文字 |
| 卡片 | `.card` | `--bg` 底、发丝边框、`--shadow`、`--r` |
| KPI 卡 | `.kpi` | 顶行=图标 chip（36px，`--accent-soft`）+ delta chip；标签 12px；大数字 31px mono tabular-nums；可选 sparkline。完成卡绿 chip、费用卡琥珀 chip |
| 模块面板 | `.module` + `.mh`（标题栏）+ `.inner` | 让看板/作战室各成**独立带标题面板**，边界分明。标题栏 18px + 图标 + 副标题 + 右侧操作 |
| 作战室 | `.module.war` + `.worker` | 面板用青色渐变头 + 青描边跟看板区分；worker 行 = 状态灯（live 脉冲，尊重 reduced-motion）+ mono 名 + mono 步骤 + 大号 mono 进度% |
| 状态徽章 | `.pill` + `.pill.run/.done/.blocked/...` | 复用现有 status-* 语义，改用 token |
| Tab | `.tab` | 活跃态 accent 下划线/底 |
| 看板 | `.cols` / `.card`（任务卡）/ `.col-empty` | 只显示有任务的列 + 空列折叠（竖排灰标签）；任务卡顶部 2px lane 编码状态（run=青/rev=琥珀） |
| 笔记卡 | `.note` | 标题 16 / 摘要 13.5 `--ink-2` 两行截断 / 元信息 11.5 `--ink-3`；**操作按钮悬停才出**（`.note-acts` opacity 0→1），收起现在那 6 个灰字按钮，改成图标按钮 |
| 知识库侧栏 | `.kb-side` / `.kb-grp .gh` / `.frow` / `.frow.child` | 见 §6.KB：三级层级在字号/字重/颜色/缩进/图标五维度拉开 |
| 标签 | `.tag` / `.tag.ai` | accent 软底；AI 生成用紫软底 |
| 空状态 | `.empty` | 图标 + 一句引导 + CTA（finesse：邀请不是道歉） |
| 骨架屏 | `.skeleton` | 表格/列表加载用骨架，不用居中 spinner（防 CLS） |
| 表单 | `.field` / `.label` / `.input` | label 在上、必填标记、focus 2px accent ring、错误在字段下说清原因+怎么改 |
| 图标 | `icon()` macro | §3.4 |

**两种气质分配到组件**：仪表盘页用 KPI/状态灯/mono 大数/作战室；阅读页用安静卡片/宽松排版/悬停操作。同一 token，不同组件组合。

---

## 6. 逐页范围（全站，按实现顺序）

> 原则：**只改模板与前端（base.html + 各 .html 的结构/样式/vanilla JS）**。**不碰 `app/api`、`app/services` 的逻辑**（用户明确要求；另一窗口负责功能）。如遇模板依赖某后端字段缺失，记录但不改后端逻辑。

### Phase 0 — 系统层（先做，其余全依赖它）
- **base.html**：注入 §3 全部 token、§4 左侧栏 shell + 顶栏 + 深浅色切换、§3.4 图标 macro/sprite、§5 组件基础类、全局 focus/disabled/skeleton 样式。移除死 `md:` 类。
- **产出**：一套 `docs` 里的组件速查（可选），供后续每页对照。

### Phase 1 — 旗舰页
- **project_detail.html**（项目详情/看板/任务树/作战室）：本设计的旗舰，KPI 行 + 模块化看板 + 独立作战室面板 + FAB。保留上轮的 3 层信息架构与全部功能入口，只换视觉。回归检查：AI 费用实时、授权模式、worker 实时状态、项目 AI 对话不丢。

### Phase 2 — 知识库（重点治，用户最不满意）
- **notes.html**：宽侧栏（三级层级见下）+ 笔记卡（悬停出图标操作，收起 6 灰按钮）+ 顶部操作精简 + 筛选 chip 统一 accent（去掉蓝/紫/绿/黄随机色）+ emoji 换 SVG。
- **note_detail.html / note_form.html / note_share.html / note_trash.html / note_chat.html / note_classify.html / note_organize.html / note_organize_picker.html / note_import.html / ima_browse.html**：套阅读气质 + 组件类。
- **知识库侧栏三级层级（明确规格）**：
  - 分区标签（文件夹/项目/标签）：10.5px 大写 letter-spacing .13em、`--ink-3`、下划线分隔。
  - 顶层文件夹：14px / 500 / `--ink-1` / 文件夹图标。
  - 子文件夹：12.5px / 400 / `--ink-3` / 缩进 23px + 左连接线 / 无图标。

### Phase 3 — 其余"看数据"页（仪表盘气质）
- **dashboard.html**（总览）、**finance.html**（财务，已有 Chart.js——图表改用 §3 图表 ramp、去掉默认三色）、**project_list.html**、**task_detail.html**、**cost_estimate.html**、**decisions.html**、**weekly_report.html**、**backups.html**。

### Phase 4 — 自媒体 & 学习模块
- **media_board / media_content / media_persona / media_topics**、**study_today / study_point / study_practice / study_review / study_exam / study_archetype / study_settings**。套 shell + 组件；board 类用看板组件，阅读类用阅读气质。

### Phase 5 — 表单 & 配置页（workflow 气质）
- **settings.html**（三个独立表单块——改密码/加用户/主设置，用户踩过坑，保持三块各自 POST）、**project_form.html**、**login.html**、**chat.html**（总 AI 对话，套 shell + 气泡样式）。走 finesse workflow：分区卡片、字段校验、提交确认。

### Phase 6 — 验收
- grep 全站无残留 emoji-as-icon、无裸 hex、无旧随机色类；深浅色各测一遍对比度；375px/560px/900px 三档移动端过一遍；finesse §8 + product §10 pre-flight。

---

## 7. 技术约束与已知坑（务必遵守）

- **本地 `tailwind.min.css` 是裁剪版**：无 `md:` 布局工具类（只有 `md:hover/focus`）；色系只有 blue/gray/green/indigo/pink/purple/red/yellow，**无 violet/slate/zinc/teal/amber/emerald/sky/rose**。→ 本设计的 accent/中性/语义色**全部走自定义 CSS 变量与组件类**，不依赖 tailwind 工具类表达。
- **改模板一律用 Edit/Write 工具**，**禁止用 PowerShell `-replace` 重写 UTF-8 文件**（会把中文变乱码，历史坑）。
- **Jinja2**：无 `tojson` 过滤器（用 `json.dumps() + |safe`）；`TemplateResponse` 必须三参数 `(request, "name.html", ctx)`；模板 dict 键名别用 `items/keys/values/get`（会撞方法）。
- **不改 `app/api` / `app/services` 逻辑**；本轮纯前端（base.html + 模板 + inline vanilla JS）。
- **深浅色初值 inline script** 要在 `<head>` 尽早执行，避免闪烁（照抄现有字号 script 模式）。
- **图标/字体自托管**走 `app/static/`，`main.py` 静态挂载已存在。

---

## 8. 验收标准

- [ ] 全站统一走 token + 组件类；`:root` 外无裸 hex（grep）。
- [ ] 无 emoji 当图标（保留的 motif 除外）；SVG 图标跟色跟深浅。
- [ ] 深色默认 + 浅色切换可用、持久化、无闪烁；两模式对比度均达 AA。
- [ ] 左侧栏 shell 在桌面常驻左侧，≤560px 折顶栏；触摸目标 ≥44px。
- [ ] 字阶各级明显可分；大标题宋体；数字 mono tabular。
- [ ] 看板与作战室为**分明的独立模块**；worker 实时脉冲，尊重 reduced-motion。
- [ ] 知识库侧栏三级层级五维度拉开；笔记卡操作悬停出、不再 6 个灰按钮平铺。
- [ ] 所有原功能入口无丢失（可通过菜单/悬停/浮层访问）。
- [ ] `app/api` / `app/services` 零改动。
- [ ] 移动端 375/560/900 三档无横向溢出（看板内容除外）。

---

## 9. 不在本轮范围

- 后端逻辑、API、数据模型（另一窗口/后续）。
- 新功能。
- 生产部署（用户自行 git push → 服务器 pull + pm2 restart）。
