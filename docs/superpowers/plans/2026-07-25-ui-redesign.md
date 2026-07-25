# AI-PM 全局 UI 重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 ai-pm 全站换上一套高质感、有识别度的 finesse Steel-Cyan 设计系统（token + 组件 + 图标 + 左侧栏 shell + 深浅色），纯前端、不改后端逻辑。

**Architecture:** 在 `base.html` 注入一层设计 token（CSS 变量）+ 组件类 + 图标 macro + 左侧栏 shell + 深浅色切换；各页面模板从"裸 tailwind 工具类"改用这套语义组件类。深色默认、浅色可切。不碰 `app/api` / `app/services`。

**Tech Stack:** FastAPI + Jinja2 + 本地裁剪版 tailwind.min.css（仅兜底，不依赖其表达配色）+ 纯 CSS 变量 + vanilla JS。无构建、无新框架。

**设计文档（像素基准 + 全规格，每个 Task 都要对照）：** `docs/superpowers/specs/2026-07-25-ui-redesign-design.md`
**可视化预览（组件/配色/字阶的像素基准）：** https://claude.ai/code/artifact/0338e91e-d062-4c8f-9c76-b841c13e8779

## Global Constraints

- **只改模板 / 前端**：`app/templates/*.html`（结构+样式+inline vanilla JS）与 `app/static/`。**严禁改 `app/api`、`app/services` 的逻辑**（另一窗口负责功能）。
- **改模板一律用 Edit/Write 工具**；**严禁 PowerShell `-replace` 重写 UTF-8 文件**（会把中文变乱码）。
- **不引新前端框架、不加构建、不改 `tailwind.min.css`**；配色/中性/语义色全走自定义 CSS 变量，不依赖裁剪版 tailwind 工具类。
- **Jinja2 坑**：`TemplateResponse` 三参数 `(request, "x.html", ctx)`；无 `tojson`（用 `json.dumps()+|safe`）；模板 dict 键别用 `items/keys/values/get`。
- **颜色 token（深色默认，落 base.html）**：`--page:#0a0e0f --bg:#12181a --panel-2:#192124 --border:rgba(255,255,255,.08) --ink-1:#e8f0f1 --ink-2:#a6b3b5 --ink-3:#7e8c8f --accent:#2FD3C5 --accent-soft:rgba(47,211,197,.14) --on-accent:#06110f --ai:#9B85FF --ai-soft:rgba(155,133,255,.15) --up:#3FCB86 --down:#FF6257 --warn:#F0A868 --glow:rgba(47,211,197,.28)`。浅色见 spec §3.1。
- **字阶固定档**：数据 31 mono / 页面标题 26 `--display`(宋体) / 模块 18 / 列表项 16 / 正文 14 / 说明 12 / 标签 11.5 大写。
- **图标调用约定**：任何用图标的模板，在顶部（`{% extends %}` 之后）加 `{% import "_icons.html" as ic %}`，用 `{{ ic.icon('name') }}`。**不要**把宏塞进 base.html（Jinja 子模板取不到父模板宏）。
- **验收硬线**：`:root` 外无裸 hex；无 emoji 当图标；深浅色对比度 AA；触摸目标 ≥44px；原功能入口零丢失；`app/api`/`app/services` 零改动。

## 每个 Task 的"验证循环"（替代 TDD）

因无 UI 单测，每个 Task 的验证 = ①`python run.py` 起本地服务（端口 8000，另一窗口已停）②浏览器打开对应页面截图肉眼核对 ③跑该 Task 列出的 grep 检查 ④深浅色各切一次 ⑤≤560/≤900 两档改窗口宽度核对不溢出 ⑥通过后 commit。

---

## 文件结构（本轮涉及）

- `app/templates/base.html` — 设计系统单一来源：token + shell + 组件类 + 图标 macro + 深浅色切换。**所有页面继承它。**
- `app/static/icons.svg`（可选 sprite 方案）或 base.html 内 `{% macro icon() %}` — 图标定义。
- `app/templates/*.html`（约 40 个）— 逐页套组件类。
- 不新增后端文件。

---

## Task 1: 图标系统（内联 SVG，替换全站 emoji）

**Files:**
- Create: `app/templates/_icons.html`（存 `{% macro icon(name, cls='') %}`）

**Interfaces:**
- Produces: Jinja2 macro `icon(name, cls='')` → 输出 `<svg class="icon {{cls}}" viewBox="0 0 24 24" ...><path.../></svg>`，`stroke:currentColor; width:1em; height:1em`。
- **调用约定（全 Task 统一）**：使用宏的模板在顶部（`{% extends %}` 之后）加 `{% import "_icons.html" as ic %}`，然后 `{{ ic.icon('folder') }}`。
  > ⚠️ Jinja2 里 base.html 定义的宏**不会**自动传给 `{% extends %}` 它的子模板——所以宏放独立文件、按需 import，而不是塞进 base.html。
- 图标名集合：`folder file doc search plus check clock play pause robot satellite chart kanban tag edit move share trash copy settings gear chevron arrow-up arrow-down dollar bell menu x star eye sun dots`。

- [ ] **Step 1: 新建 `app/templates/_icons.html`，内容为整段 macro 定义**（文件只含这个 macro）：

```jinja
{% macro icon(name, cls='') -%}
<svg class="icon {{ cls }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
{%- if name == 'folder' %}<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
{%- elif name == 'file' %}<path d="M14 3v5h5"/><path d="M6 3h8l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>
{%- elif name == 'doc' %}<path d="M4 5a2 2 0 0 1 2-2h11v18H6a2 2 0 0 1-2-2z"/><path d="M8 7h6M8 11h6M8 15h4"/>
{%- elif name == 'search' %}<circle cx="11" cy="11" r="7"/><path d="m20 20-3-3"/>
{%- elif name == 'plus' %}<path d="M12 5v14M5 12h14"/>
{%- elif name == 'check' %}<path d="M20 6 9 17l-5-5"/>
{%- elif name == 'clock' %}<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>
{%- elif name == 'play' %}<path d="M5 3l14 9-14 9z"/>
{%- elif name == 'pause' %}<path d="M8 5v14M16 5v14"/>
{%- elif name == 'robot' %}<path d="M12 2v20M6 6h9a3 3 0 0 1 0 6H6h11"/>
{%- elif name == 'satellite' %}<path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 6v6l4 2"/><circle cx="19" cy="5" r="2"/>
{%- elif name == 'chart' %}<path d="M3 13h4l2 5 4-12 2 7h6"/>
{%- elif name == 'kanban' %}<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="11" rx="1"/><rect x="17" y="4" width="4" height="16" rx="1"/>
{%- elif name == 'tag' %}<path d="M3 12l9-9 9 9-9 9z"/>
{%- elif name == 'edit' %}<path d="M4 20h4L18 10l-4-4L4 16z"/><path d="M13 5l4 4"/>
{%- elif name == 'move' %}<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
{%- elif name == 'share' %}<circle cx="6" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 11l8-4M8 13l8 4"/>
{%- elif name == 'trash' %}<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>
{%- elif name == 'copy' %}<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>
{%- elif name == 'settings' or name == 'gear' %}<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 0 1-4 0v-.2A1.6 1.6 0 0 0 6 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 13.9H3a2 2 0 0 1 0-4h.1A1.6 1.6 0 0 0 4.6 8L4.5 8a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 4.6V4a2 2 0 0 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8z"/>
{%- elif name == 'chevron' %}<path d="M9 6l6 6-6 6"/>
{%- elif name == 'arrow-up' %}<path d="M12 19V5M6 11l6-6 6 6"/>
{%- elif name == 'arrow-down' %}<path d="M12 5v14M6 13l6 6 6-6"/>
{%- elif name == 'dollar' %}<path d="M12 2v20M6 6h9a3 3 0 0 1 0 6H6h11"/>
{%- elif name == 'bell' %}<path d="M6 9a6 6 0 0 1 12 0c0 7 2 8 2 8H4s2-1 2-8"/><path d="M10 20a2 2 0 0 0 4 0"/>
{%- elif name == 'menu' %}<path d="M4 6h16M4 12h16M4 18h16"/>
{%- elif name == 'x' %}<path d="M6 6l12 12M18 6 6 18"/>
{%- elif name == 'star' %}<path d="M12 3l2.9 6 6.1.9-4.5 4.3 1 6.1L12 18l-5.5 2.3 1-6.1L3 9.9 9.1 9z"/>
{%- elif name == 'eye' %}<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>
{%- elif name == 'sun' %}<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M17.7 6.3l1.4-1.4M4.9 19.1l1.4-1.4"/>
{%- elif name == 'dots' %}<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>
{%- else %}<circle cx="12" cy="12" r="9"/>
{%- endif %}
</svg>
{%- endmacro %}
```

- [ ] **Step 2: 加 `.icon` 基础样式**（放进 Task 2 的 `<style>`，此处先约定）

```css
.icon{ width:1em; height:1em; display:inline-block; vertical-align:-.14em; stroke-width:1.9; fill:none; flex:none; }
```

- [ ] **Step 3: 验证**（不起服务）：用最小 Jinja2 脚本渲染宏，确认无语法错误且输出 `<svg>`：
```bash
cd /d/GAGA-5-25/ai-pm && python -c "from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('app/templates')); m=e.get_template('_icons.html').module; print(str(m.icon('folder'))[:60]); print(str(m.icon('robot'))[:60])"
```
Expected: 打印出两段以 `<svg class=\"icon` 开头的字符串，无异常。

- [ ] **Step 4: Commit**

```bash
git add app/templates/_icons.html
git commit -m "feat(ui): 内联 SVG 图标 macro(_icons.html)，准备替换全站 emoji"
```

---

## Task 2: base.html — 设计系统底座（token + 左侧栏 shell + 深浅色 + 组件类）

**Files:**
- Modify: `app/templates/base.html`（重写 `<style>`、`<nav>`→左侧栏、加深浅色 script；保留字号缩放机制、所有导航链接、登出）

**Interfaces:**
- Produces：全站可用的 CSS 变量（Global Constraints 里的 token）+ 组件类：`.btn/.btn.primary/.btn.ai/.btn.ghost .card .kpi .module/.mh/.inner .pill.<state> .tab .cols/.card/.col-empty .worker/.war .note/.note-acts .tag/.tag.ai .empty .skeleton .field/.label/.input .icon`。布局类：`.app-shell .side .main .topbar .scroll`。
- Produces：`data-theme` 在 `<html>` 上，`toggleTheme()` 全局函数，localStorage key `ui-theme`。

- [ ] **Step 1: 重写 `<head>` 内 `<style>`** —— 用 spec §3 + 预览 artifact 的完整 CSS。包含：`:root`（深色 token）+ `[data-theme="light"]`（浅色 token，见 spec §3.1）+ `.icon` + 全部组件类 + `.app-shell/.side/.main/.topbar/.scroll` 布局 + `@media(max-width:900px)` 内容堆叠 + `@media(max-width:560px)` 侧栏折顶栏 + 全局 `:focus-visible`、`:disabled`、`.skeleton`。**保留**现有 `--ui-font-px` rem 缩放相关样式与 `.font-size-select`。所有值引用变量，无裸 hex。

  > 组件类的精确样式以预览 artifact 的 CSS 为准（`.side .main .topbar .kpi .module .worker .note .frow` 等已在预览中写好），执行时对照 artifact 源码平移，把 `.dsp` 作用域前缀去掉、改为全局类。

- [ ] **Step 2: 加深浅色初始化 inline script**（放 `<head>` 顶，紧跟现有字号 script，避免闪烁）

```html
<script>
  (function(){
    try{
      var t = localStorage.getItem('ui-theme');
      if(!t){ t = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
      if(t === 'light'){ document.documentElement.setAttribute('data-theme','light'); }
    }catch(e){}
  })();
  function toggleTheme(){
    var el = document.documentElement;
    var light = el.getAttribute('data-theme') === 'light';
    if(light){ el.removeAttribute('data-theme'); localStorage.setItem('ui-theme','dark'); }
    else{ el.setAttribute('data-theme','light'); localStorage.setItem('ui-theme','light'); }
  }
</script>
```

- [ ] **Step 3: 把 `<nav>`（顶栏）改写为左侧栏 shell**。先在 base.html 顶部加 `{% import "_icons.html" as ic %}`。结构：`<div class="app-shell"><aside class="side">…</aside><div class="main"><div class="topbar">…</div><main class="scroll">{% block content %}{% endblock %}</main></div></div>`。
  - side：brand（`◆ AI-PM`）+ 分组菜单（工作区：AI Chat/总览/项目/知识库/自媒体/学习/财务，各 `{{ ic.icon(...) }}` + 文字，用 `{% if path... %}on{% endif %}` 保留现有高亮逻辑）+ 底部固定组（设置链接 + 字号 select + `<button onclick="toggleTheme()">{{ ic.icon('sun') }} 深/浅色</button>` + 退出）。
  - topbar：留面包屑占位 `{% block topbar %}{% endblock %}` + 搜索占位。
  - **保留**：所有原链接 href、`path.startswith` 高亮、登出、字号 select（含移动端那份）。移除死 `md:hidden` 等无效类。
  - 移动端：≤560px 时 `.side` 变顶部横向滚动条（CSS 已含），保留汉堡逻辑或改为直接横向滚动（二选一，横向滚动更简单）。

- [ ] **Step 4: 验证**
  - `python run.py` → 打开 `/`、`/projects`、`/notes`：左侧栏在左、菜单高亮正确、字号 select 仍工作。
  - 点深浅色按钮：整站切换、刷新保持、无白屏闪烁。
  - grep：`grep -rn "md:hidden\|md:flex\|md:grid" app/templates/base.html` 应为空（死类已清）。
  - 窗口拉到 500px：侧栏折成顶栏、不横向溢出。

- [ ] **Step 5: Commit**

```bash
git add app/templates/base.html
git commit -m "feat(ui): base.html 设计系统底座——token+左侧栏shell+深浅色+组件类"
```

---

## Task 3: project_detail.html — 旗舰（KPI + 模块化看板 + 独立作战室）

**Files:**
- Modify: `app/templates/project_detail.html`

**Interfaces:** Consumes Task 2 的组件类与 `icon()`。

- [ ] **Step 1: 读现有模板**，列出所有功能入口（AI 拆解/启停自动执行/预算条/看板列/任务树/资料库 tab/记录 tab/AI 指挥 tab/作战室轮询/项目 AI 对话/FAB/编辑/更多菜单），确保后面一个不丢。
- [ ] **Step 2: 重写视觉结构**（保持上轮 3 层信息架构，只换组件）：
  - 头部：项目名用 `.pname`（`--display` 宋体 26）+ `.pill.<状态>` + `.pcode` 编号 + 核心档 `{{ icon('star') }}` + 右侧 AI 费用（mono）+ `.btn.ai`(AI 拆解) + `.btn.primary`(启动)。
  - KPI 行：4 张 `.kpi`（任务总数/进行中/已完成/本月AI费用），图标 chip + delta + 31px mono 大数 + 费用卡带 sparkline。
  - 看板：包进 `.module`（`.mh` 标题"看板" + 添加任务按钮），列用 `.cols`，任务卡 `.card` 顶 2px lane 编码状态，空列折叠 `.col-empty`。
  - 作战室：独立 `.module.war`（青渐变头 + 青描边），`.worker` 行含 live 脉冲灯（尊重 `prefers-reduced-motion`）、mono 步骤、大号 mono 进度%。**保留 2 秒轮询 JS 逻辑，只改渲染的 DOM 结构/类名。**
  - 任务树 / 资料库 / 记录 / AI 指挥 tab：套 `.tab` + 相应组件；资料库列表用 `.note` 或紧凑行。
  - FAB、更多菜单、手动添加浮层：套 `.btn` + 浮层样式。
- [ ] **Step 3: 验证**：打开某项目详情页——首屏见 KPI+看板、作战室明显独立、深浅色都正常、轮询仍更新 worker、所有 tab/按钮可点、功能入口齐全。500px 下看板横向滚动、tab 横向滚动。grep 该文件无裸 hex、无 emoji、无旧 teal/indigo/orange/purple 类。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 项目详情页套设计系统——KPI+模块化看板+独立作战室"`

---

## Task 4: notes.html — 知识库主页面（重点治）

**Files:**
- Modify: `app/templates/notes.html`

- [ ] **Step 1: 读现有模板**，记下顶部 8 个操作按钮各自触发的路由/modal（新建/导入/AI助手/AI分类/AI整理/周报/回收站/备份）、侧边栏（搜索/文件夹树/项目/标签）、笔记列表行的所有操作（改名/移动/复制链接/分享/软删除/批量）。**一个功能都不删，只收纳/换形。**
- [ ] **Step 2: 重写**：
  - 顶部操作精简：`.btn.primary`(+新建▾ 下拉含 新建笔记/AI分类/AI整理) + `.btn`(导入) + `.btn.ghost`(⋯更多 下拉含 周报/回收站/备份/IMA) + 右侧搜索。去掉 8 个随机色按钮。
  - 宽侧栏 `.kb-side`（248px，独立卡，顶部搜索框），三组 `.kb-grp` 各带 `.gh` 分区标签（下划线）：文件夹/项目/标签。**三级层级**：分区标签 10.5 大写 / 顶层文件夹 `.frow` 14+500+ink-1+文件夹图标 / 子文件夹 `.frow.child` 12.5+400+ink-3+缩进23+左连接线+无图标。
  - 笔记列表：每条 `.note`（标题16 / 摘要13.5两行截断 / 元信息11.5 + `.tag`/`.tag.ai`）；操作按钮改 `.note-acts` **悬停才出**的图标按钮（改名/移动/分享/删除用 `icon()`）；核心档 `{{ icon('star') }}` 金色。批量操作栏保留。
  - IMA 卡从首屏横幅收进侧栏底部或"⋯更多"。
- [ ] **Step 3: 验证**：知识库首屏——顶部≤3主按钮、宽侧栏三级层级一眼分主次、笔记卡悬停出操作、AI 生成笔记紫标、深浅色正常、搜索/筛选/批量/所有下拉功能可用。360px 顶部无横向溢出。grep 无旧随机色类、无 emoji 图标、无裸 hex。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 知识库主页——宽侧栏三级层级+笔记卡悬停操作+精简顶部"`

---

## Task 5: 知识库其余页面（阅读气质）

**Files (逐个 Modify):** `note_detail.html note_form.html note_share.html note_trash.html note_chat.html note_classify.html note_organize.html note_organize_picker.html note_import.html ima_browse.html`

- [ ] **Step 1**：逐页读结构、记功能入口。
- [ ] **Step 2**：每页套 shell + 组件类：正文/表单用阅读气质（宽松排版、`.field/.label/.input`、`.card`）；`note_detail` 的 Markdown 正文区保留 marked.js 渲染，只调容器与排版；`note_share` 是无导航独立页（保留白名单行为），仅套 token + 排版；`ima_browse` 三区列表套 `.card`/`.frow`；emoji 全换 `icon()`。
- [ ] **Step 3: 验证**：逐页打开核对功能不丢、深浅色正常、无 emoji/裸 hex/旧色类。分享页 `/s/<token>` 免登录仍可访问。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 知识库次级页面套阅读气质设计系统"`

---

## Task 6: 总览 + 项目列表（仪表盘气质）

**Files:** `dashboard.html` `project_list.html`

- [ ] **Step 1**：读结构、记入口。
- [ ] **Step 2**：`dashboard`（总览）用 KPI 行 + `.module` 分区 + `.card`；项目卡用 `.card` + `.pill` 状态 + mono 费用。`project_list` 每项目 `.card`（名 16 / 状态 pill / 进度 / 费用 mono / 编号），网格布局。emoji→`icon()`。
- [ ] **Step 3: 验证**：两页打开核对、深浅色、900/560 响应式、grep 清洁。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 总览+项目列表套仪表盘气质"`

---

## Task 7: 财务 + 记录类页面

**Files:** `finance.html` `decisions.html` `weekly_report.html` `backups.html` `task_detail.html` `cost_estimate.html`

- [ ] **Step 1**：读结构、记入口。注意 `finance.html` 有 Chart.js。
- [ ] **Step 2**：
  - `finance`：汇总卡→`.kpi`；对比条/明细→`.card`+`.module`；**Chart.js 配色改用 token**（`--accent`、`--accent-2` 派生，去掉默认蓝绿橙三色；图表网格线低对比、数据≥3:1）。深浅色切换时图表色可读（用 CSS 变量取值或深浅各配一组）。
  - `decisions`（AI/人工两线）、`weekly_report`、`backups`、`task_detail`、`cost_estimate`：套 `.card/.module/.pill/.tag/.btn` + `icon()`。
- [ ] **Step 3: 验证**：财务图表两模式都可读、其余页功能齐、grep 清洁、响应式过。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 财务(图表配色)+记录类页面套设计系统"`

---

## Task 8: 自媒体模块

**Files:** `media_board.html` `media_content.html` `media_persona.html` `media_topics.html`

- [ ] **Step 1**：读结构、记入口（选题/内容状态机/人设/平台差异化文案等）。
- [ ] **Step 2**：`media_board` 用看板/`.module` 组件；`media_content` 状态机步进用 `.pill`/`.tab`；`media_persona`/`media_topics` 用 `.card`/`.note`/`.tag`。emoji→`icon()`。
- [ ] **Step 3: 验证**：四页功能齐、深浅色、响应式、grep 清洁。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 自媒体模块套设计系统"`

---

## Task 9: 学习模块

**Files:** `study_today.html` `study_point.html` `study_practice.html` `study_review.html` `study_exam.html` `study_archetype.html` `study_settings.html`

- [ ] **Step 1**：读结构、记入口。
- [ ] **Step 2**：每页套 shell + 组件类；刷题/复习类用阅读气质 + `.card`；`study_settings` 走表单组件；`study_today` 可用 KPI/进度。emoji→`icon()`。
- [ ] **Step 3: 验证**：七页功能齐、深浅色、响应式、grep 清洁。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 学习模块套设计系统"`

---

## Task 10: 表单/配置/对话页（workflow 气质）

**Files:** `settings.html` `project_form.html` `login.html` `chat.html`

- [ ] **Step 1**：读结构。**`settings.html` 关键：三个独立表单块（改密码/加用户/主设置）各自 POST，绝不合并**（用户踩过坑）。
- [ ] **Step 2**：
  - `settings`：每块 `.module`（分区卡片标题）+ `.field/.label/.input`，每块自己的保存按钮，字段校验、焦点环。
  - `project_form`：workflow 表单卡片。
  - `login`：居中卡片 + token 化，独立页（无 shell 或极简 shell）。
  - `chat`（总 AI 对话）：套 shell + 气泡（用户/AI 区分，AI 用 `--ai` 系）+ `icon()`。
- [ ] **Step 3: 验证**：settings 三块各自保存正常、登录页可用、chat 收发正常、深浅色、响应式、grep 清洁。
- [ ] **Step 4: Commit** `git commit -m "feat(ui): 表单/配置/对话页套设计系统"`

---

## Task 11: 全站验收 + 收尾

**Files:** 视发现的问题回改对应模板。

- [ ] **Step 1: 全站 grep 清洁扫描**
```bash
# 裸 hex（:root/[data-theme] 定义处除外，人工核对命中）
grep -rn "#[0-9a-fA-F]\{6\}" app/templates/ | grep -v "base.html"
# emoji 当图标（抽查常见几个）
grep -rn "📁\|⭐\|🔍\|📷\|🗂️\|🤖\|📊\|💰\|🎬\|🎓" app/templates/
# 旧随机色 tailwind 类
grep -rn "teal-\|indigo-\|orange-\|bg-purple-\|border-purple-" app/templates/
```
命中的逐个清掉（保留刻意的 🛰️ 作战室 motif 与 base.html token 定义）。
- [ ] **Step 2: 深浅色 + 响应式全过**：每个主页面切深/浅各一次核对对比度；375/560/900 三档核对无横向溢出、触摸目标 ≥44px。
- [ ] **Step 3: finesse pre-flight**：对照 spec §8 验收标准逐条打勾；对照 `product-ui.md` §10 + `preflight.md`（作战室/看板/知识库三处重点）。
- [ ] **Step 4: 回归**：AI 费用实时、授权模式、worker 实时状态、项目 AI 对话、settings 三块保存、分享页免登录——逐项确认未丢。确认 `git diff --stat` 只动 `app/templates/` 与 `app/static/`，`app/api`/`app/services` 零改动。
- [ ] **Step 5: Commit** `git commit -m "chore(ui): 全站 UI 重设计验收——grep清洁+响应式+回归"`

---

## Self-Review（本计划对照 spec）

- **Spec §3 token** → Task 2 Step1/2（全部变量+深浅色+字阶+字体+图标基样式）。
- **Spec §3.4 图标** → Task 1（macro + 30 图标 + 全站替换分散在各页 Task）。
- **Spec §4 shell/深浅色/移动端** → Task 2 Step3/4 + 各页 Step 响应式验证。
- **Spec §5 组件库** → Task 2 定义，Task 3–10 应用。
- **Spec §6 逐页范围** → Task 3(旗舰)/4–5(知识库)/6–7(看数据)/8(自媒体)/9(学习)/10(表单) 全覆盖 40 模板。
- **Spec §7 约束** → Global Constraints + 各页"只改前端、Edit/Write、不碰后端"。
- **Spec §8 验收** → Task 11。
- **Placeholder 扫描**：无 TBD/TODO；共享系统代码（icon macro、token、theme script）已完整给出；页面转换任务给出具体结构变更清单而非"美化一下"（因执行 subagent 读实时模板，逐页全 HTML 既不可能也会过时——以"具体改什么 + 用哪些已定义类 + 验证什么"约束）。
- **类型/类名一致性**：`icon(name,cls)`、`toggleTheme()`、`data-theme`、组件类名全计划统一。

---

**Plan complete.** 两种执行方式，见下条消息。
