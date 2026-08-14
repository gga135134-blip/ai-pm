# 自媒体模块 UI 重做 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把自媒体 14 个散装页面重做成一条有主次、有状态、可返回可前进的引导式工作流。

**Architecture:** 新建一个只读取数接口 `app/api/media_ui.py`（不碰 `media.py`）提供四步状态；新建 `_media_shell.html` Jinja2 macro 统一输出「人设上下文条 + 主线步骤条 + 底部步骤导航」，替换 11 处各页手写导航；各页面按主线/体系库两类套用组件。

**Tech Stack:** FastAPI + Jinja2 + 全站设计系统 token（`base.html`）+ vanilla JS。无新框架、无构建。

**设计文档（每个 Task 必须对照）：** `docs/superpowers/specs/2026-07-26-media-ui-redesign-design.md`
**可视化基准：** 结构 https://claude.ai/code/artifact/358716be-b236-4904-a1eb-5375cad9ca65 ｜ 体系信息 https://claude.ai/code/artifact/c5694bae-c0e3-4f0d-8df4-b50b5749fe58

## Global Constraints

- **严禁修改 `app/api/media.py` 与 `app/services/media_*.py`**（另一窗口正在做功能，冲突风险）。唯一允许的后端改动：**新建** `app/api/media_ui.py`，以及在 `app/main.py` 加 **两行**（import 与 `include_router`）。
- 不改内容状态机的阶段名、顺序、前进规则（服务端强制）。`STAGES = ["idea","scripted","recording","editing","ready","published","reviewed"]`，标签见 `app/services/media_flow.py::STAGE_LABELS`。
- **不得编造数据**：任何数字必须来自真实查询或模板 context；拿不到就不显示该数字。
- 沿用全站设计系统 token（定义在 `base.html` 的 `:root` / `[data-theme="light"]`）。**无裸 hex**（token 定义块除外）、**无 emoji 当图标**（用 `{% import "_icons.html" as ic %}` + `{{ ic.icon('name') }}`）。
- **`--up`/`--down` 仅语义用途**（绿=成功/完成，红=错误/危险）；提醒/紧急用 `--warn`；分类用中性或 accent。
- **CSS 作用域坑**：`base.html` 里写成 `.note-h .core{...}` 的规则只在 `.note-h` 祖先内生效。在别处用同名 class 必须自带页面级规则，否则静默失去样式。`.pill` 在 base 只有 `.run` 一个变体。
- **移动端优先**：触摸目标 ≥44px；375px 无横向溢出；步骤条横向滚动。
- 改模板一律用 Edit/Write；**严禁 PowerShell `-replace`**（会毁中文 UTF-8）。
- **四态视觉**（见 spec §4）：已完成=绿勾+数量；进行中=`--accent` 高亮；未开始=常规灰标签；不可用=`opacity:.45`+锁+**必须用 `--warn` 写明原因**。

## 每个 Task 的验证循环（本项目无 UI 单测）

①`python -c` 编译模板 ②`curl` 对应路由取 200 且无 `Traceback|UndefinedError` ③grep 检查（无裸 hex/emoji/旧类）④通过后 commit。
**dev server 由控制方管理**——实现者不得启动/重启/杀死服务，不得开浏览器。

---

## 文件结构

- **Create** `app/api/media_ui.py` — 唯一职责：只读返回当前人设的四步状态 JSON。
- **Modify** `app/main.py` — 两行注册。
- **Create** `app/templates/_media_shell.html` — 唯一职责：输出人设条 + 步骤条 + 底部导航的 Jinja2 macro。
- **Modify** `app/templates/base.html` — 新增 §6 组件类。
- **Modify** `app/templates/media_*.html`（14 个）— 套用 shell 与组件。

---

## Task 1: 步骤状态接口（唯一后端改动）

**Files:**
- Create: `app/api/media_ui.py`
- Modify: `app/main.py`（第 17 行 import 列表、第 41 行后 include_router）

**Interfaces:**
- Produces: `GET /media/ui/steps` → JSON。无当前人设时返回 `{"ok": false}`。有则：
```json
{"ok": true, "persona_id": "...",
 "steps": {
   "topic":   {"done": true,  "count": 3, "label": "采用 3 条"},
   "content": {"done": false, "count": 2, "label": "2 条在做"},
   "publish": {"done": false, "count": 1, "label": "待发 1"},
   "review":  {"done": false, "count": 0, "label": "发布后解锁", "locked": true, "reason": "要先发布才能复盘"}
 },
 "libs": {"persona": 11, "audience": 2, "anchor": 1, "material": 24, "playbook": 0, "legacy": 38},
 "libs_empty": ["playbook"]}
```

- [ ] **Step 1: 新建 `app/api/media_ui.py`**（完整内容如下，不要改动 SQL 之外的结构）

```python
"""自媒体 UI 专用只读取数：四步状态 + 体系库存量。

单独成文件是为了不与 media.py 抢改动（功能开发在另一条线）。
本文件只读、不写、不改业务逻辑。
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.database import get_db

router = APIRouter()


async def _current_persona_id(request, db):
    """与 media.py 同款语义：cookie 优先，回落第一个 active 人设。"""
    pid = request.cookies.get("media_persona")
    if pid:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        if await cur.fetchone():
            return pid
    cur = await db.execute(
        "SELECT id FROM media_persona WHERE status='active' ORDER BY created_at LIMIT 1")
    row = await cur.fetchone()
    return row["id"] if row else None


async def _scalar(db, sql, args):
    cur = await db.execute(sql, args)
    row = await cur.fetchone()
    return (row[0] if row and row[0] is not None else 0)


@router.get("/media/ui/steps")
async def media_ui_steps(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False})

        adopted = await _scalar(
            db, "SELECT COUNT(*) FROM media_topic WHERE persona_id=? AND status='adopted'", (pid,))
        making = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? "
                "AND stage IN ('scripted','recording','editing')", (pid,))
        ready = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? AND stage='ready'", (pid,))
        published = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? "
                "AND stage IN ('published','reviewed')", (pid,))
        reviewed = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? AND stage='reviewed'", (pid,))

        libs = {
            "persona": await _scalar(
                db, "SELECT COUNT(*) FROM media_persona_trait WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "audience": await _scalar(
                db, "SELECT COUNT(*) FROM media_audience WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "anchor": await _scalar(
                db, "SELECT COUNT(*) FROM media_anchor WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "material": await _scalar(
                db, "SELECT COUNT(*) FROM media_material WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "playbook": await _scalar(db, "SELECT COUNT(*) FROM media_playbook", ()),
            "legacy": await _scalar(
                db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? AND source='legacy'", (pid,)),
        }

        steps = {
            "topic": {"done": adopted > 0, "count": adopted,
                      "label": f"采用 {adopted} 条" if adopted else "还没采用选题"},
            "content": {"done": making == 0 and published > 0, "count": making,
                        "label": f"{making} 条在做" if making else "没有在做的内容"},
            "publish": {"done": published > 0, "count": ready,
                        "label": f"待发 {ready}" if ready else (f"已发 {published}" if published else "还没有待发")},
            "review": {"done": reviewed > 0, "count": reviewed,
                       "label": f"已复盘 {reviewed}" if reviewed else "发布后解锁"},
        }
        if published == 0:
            steps["review"]["locked"] = True
            steps["review"]["reason"] = "要先发布内容，才能复盘"

        return JSONResponse({"ok": True, "persona_id": pid, "steps": steps,
                             "libs": libs,
                             "libs_empty": [k for k, v in libs.items() if not v]})
    finally:
        await db.close()
```

- [ ] **Step 2: 在 `app/main.py` 注册**

把第 17 行末尾的 `media` 改为 `media, media_ui`：
```python
from app.api import dashboard, projects, tasks, settings, agents, notes, chat, auth, finance, study, media, media_ui
```
在 `app.include_router(media.router)` 之后加一行：
```python
app.include_router(media_ui.router)
```

- [ ] **Step 3: 校验表名/列名真实存在**（防止 SQL 写错表）

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3
c=sqlite3.connect('data/aipm.db')
names=[r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'media%'\")]
print('TABLES:', names)
for t in ['media_topic','media_content','media_persona_trait','media_audience','media_anchor','media_material','media_playbook']:
    if t in names:
        print(t, '->', [d[1] for d in c.execute(f'PRAGMA table_info({t})')])
    else:
        print(t, '-> MISSING')"
```
Expected: 全部表存在，且 `media_topic` 有 `status`，`media_content` 有 `stage`/`source`，各库表有 `persona_id`。
**若某表或列名不符**：以实际 schema 为准修正 Step 1 的 SQL（例如库表没有 `status` 列就去掉 `COALESCE(status,...)` 条件），并在报告中写明改了什么。**不得**为了让 SQL 跑通而编造数字或吞掉异常。

- [ ] **Step 4: 验证接口**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from fastapi.testclient import TestClient
from app.main import app
c=TestClient(app)
r=c.get('/media/ui/steps')
print('status',r.status_code); print(r.text[:400])"
```
Expected: 200，输出 JSON（未登录被重定向到 /login 也算通过，说明路由已注册且无异常；此时改用 `python -c "import app.main"` 确认无 import 错误即可）。

- [ ] **Step 5: Commit**

```bash
git add app/api/media_ui.py app/main.py
git commit -m "feat(media-ui): 新增只读步骤状态接口 /media/ui/steps"
```

---

## Task 2: shell 组件样式 + macro（消灭 11 处手写导航的核心）

**Files:**
- Modify: `app/templates/base.html`（在现有组件类之后新增）
- Create: `app/templates/_media_shell.html`

**Interfaces:**
- Consumes: Task 1 的 `GET /media/ui/steps`。
- Produces: macro `media_shell(current, persona=None, persona_id=None, back=None, next=None)`
  - `current`：`'topic'|'content'|'publish'|'review'|'lib'`（`'lib'` = 体系库页，不高亮任何主线步）
  - `persona`：人设 dict（有则显示名字/期数），没有就传 `persona_id`
  - `back` / `next`：`{'href': '/media/board', 'label': '内容'}`，为 `None` 则该按钮不出现
  - 调用方式：`{% import "_media_shell.html" as shell %}` 然后 `{{ shell.media_shell('content', persona=persona) }}`
- Produces（CSS 类）：`.pbar` `.rail` `.step`(+`.done/.now/.todo/.locked`) `.stepnav` `.inj` `.libpanel`

- [ ] **Step 1: 在 `base.html` 的 `<style>` 末尾（`</style>` 之前）加入组件样式**

```css
/* ===== 自媒体工作流组件 ===== */
.pbar{ display:flex; align-items:center; gap:10px; padding:10px 14px; background:var(--panel-2);
  border:1px solid var(--border); border-radius:var(--r-sm); margin-bottom:14px; flex-wrap:wrap; }
.pbar .who{ font-weight:600; font-size:14px; }
.pbar .acc{ font-size:12.5px; color:var(--ink-3); }
.pbar .sp{ flex:1; }
.pbar .lib{ font-size:12.5px; color:var(--ai); border:1px solid transparent; padding:6px 12px;
  border-radius:999px; background:var(--ai-soft); font-weight:600; cursor:pointer;
  display:inline-flex; align-items:center; gap:6px; position:relative; }
.pbar .lib .gap{ position:absolute; top:3px; right:5px; width:7px; height:7px; border-radius:50%;
  background:var(--warn); }
.pbar .sw{ font-size:12.5px; color:var(--ink-3); text-decoration:none;
  display:inline-flex; align-items:center; gap:5px; }
.pbar .sw:hover{ color:var(--ink-1); }

.rail{ display:flex; gap:8px; overflow-x:auto; -webkit-overflow-scrolling:touch;
  padding-bottom:4px; margin-bottom:18px; }
.step{ flex:1 0 auto; min-width:140px; border:1px solid var(--border); border-radius:var(--r-sm);
  padding:10px 12px; background:var(--bg); text-decoration:none; color:inherit; display:block; }
.step .n{ font-size:10.5px; letter-spacing:.1em; color:var(--ink-3); font-weight:600;
  display:flex; align-items:center; gap:5px; }
.step .n .icon{ font-size:12px; }
.step .t{ font-size:14px; font-weight:600; margin-top:3px; }
.step .c{ font-size:11.5px; color:var(--ink-3); margin-top:2px; }
.step:hover{ border-color:var(--accent); }
.step.done .n{ color:var(--up); }
.step.now{ border-color:var(--accent); background:var(--accent-soft); }
.step.now .n, .step.now .t{ color:var(--accent); }
.step.todo .t{ color:var(--ink-2); }
.step.locked{ opacity:.45; cursor:not-allowed; pointer-events:none; }
.step.locked .t{ color:var(--ink-3); }

.stepnav{ display:flex; align-items:center; gap:10px; margin-top:22px; padding-top:16px;
  border-top:1px solid var(--border); flex-wrap:wrap; }
.stepnav .sp{ flex:1; }
.stepnav .why{ flex-basis:100%; font-size:11.5px; color:var(--warn); margin-top:2px; }

.inj{ font-size:11.5px; color:var(--ink-3); display:inline-flex; align-items:center;
  gap:6px; flex-wrap:wrap; }
.inj b{ color:var(--ink-2); font-weight:600; }
.inj .zero{ color:var(--warn); font-weight:600; }
.inj .more{ color:var(--ai); cursor:pointer; text-decoration:underline dotted; text-underline-offset:2px; }

.libpanel{ display:none; background:var(--bg); border:1px solid var(--ai); border-radius:var(--r);
  padding:15px 17px; margin-bottom:14px; }
.libpanel.open{ display:block; }
.libpanel .lh{ display:flex; align-items:center; gap:8px; margin-bottom:11px; font-size:14px; font-weight:600; }
.libpanel .lh .icon{ color:var(--ai); }
.libgrid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(158px,1fr)); gap:10px; }
.libcard{ border:1px solid var(--border); border-radius:var(--r-sm); padding:11px 12px;
  background:var(--panel-2); text-decoration:none; color:inherit; display:block; }
.libcard:hover{ border-color:var(--accent); }
.libcard .rn{ font-size:13px; font-weight:600; display:flex; align-items:center; gap:7px; }
.libcard .rn .icon{ color:var(--ai); font-size:14px; }
.libcard .rs{ font-size:11.5px; color:var(--ink-3); margin-top:4px; }
.libcard .rs.empty{ color:var(--warn); }

@media (max-width:767px){
  .pbar .acc{ display:none; }
  .stepnav .btn{ flex:1; justify-content:center; min-height:44px; }
  .libpanel{ position:fixed; inset:0; z-index:60; border-radius:0; overflow:auto; margin:0; }
}
```

- [ ] **Step 2: 新建 `app/templates/_media_shell.html`**

```jinja
{% import "_icons.html" as ic %}

{#- 自媒体工作流外壳：人设条 + 主线步骤条 + 体系库面板。
    current: topic|content|publish|review|lib
    persona: 人设 dict（可空）；persona_id: 没有 persona 对象时传 id -#}
{% macro media_shell(current, persona=None, persona_id=None) -%}
{% set steps = [
   ('topic','1 · 选题','/media/topics'),
   ('content','2 · 内容','/media/board'),
   ('publish','3 · 发布','/media/board?view=publish'),
   ('review','4 · 复盘','/media/persona')
] %}
<div class="pbar">
  {% if persona %}
    <span class="who">{{ persona.name }}</span>
    {% if persona.current_phase %}<span class="pill run">{{ persona.current_phase }} 期</span>{% endif %}
  {% else %}
    <span class="who">当前人设</span>
  {% endif %}
  <span class="acc" id="ms-acc"></span>
  <span class="sp"></span>
  <span class="lib" onclick="msToggleLibs()">{{ ic.icon('kanban') }}体系库<span class="gap" id="ms-gap" style="display:none"></span></span>
  <a class="sw" href="/media">切换人设 {{ ic.icon('chevron') }}</a>
</div>

<div class="libpanel" id="ms-libs">
  <div class="lh">{{ ic.icon('kanban') }}体系库
    <span class="sp" style="flex:1"></span>
    <button class="iconbtn" onclick="msToggleLibs()" aria-label="关闭">{{ ic.icon('x') }}</button>
  </div>
  <div class="libgrid">
    <a class="libcard" href="/media/persona"><div class="rn">{{ ic.icon('robot') }}人设</div><div class="rs" data-lib="persona">—</div></a>
    <a class="libcard" href="/media/audience"><div class="rn">{{ ic.icon('eye') }}受众</div><div class="rs" data-lib="audience">—</div></a>
    <a class="libcard" href="/media/anchor"><div class="rn">{{ ic.icon('dollar') }}生意锚点</div><div class="rs" data-lib="anchor">—</div></a>
    <a class="libcard" href="/media/materials"><div class="rn">{{ ic.icon('folder') }}原料库</div><div class="rs" data-lib="material">—</div></a>
    <a class="libcard" href="/media/playbook"><div class="rn">{{ ic.icon('tag') }}打法库</div><div class="rs" data-lib="playbook">—</div></a>
    <a class="libcard" href="/media/legacy"><div class="rn">{{ ic.icon('doc') }}老文案</div><div class="rs" data-lib="legacy">—</div></a>
  </div>
</div>

<div class="rail" id="ms-rail">
  {% for key, label, href in steps %}
  <a class="step {% if key == current %}now{% else %}todo{% endif %}" data-step="{{ key }}" href="{{ href }}">
    <span class="n" data-n>{% if key == current %}{{ ic.icon('clock') }}进行中{% else %}未开始{% endif %}</span>
    <span class="t">{{ label }}</span>
    <span class="c" data-c></span>
  </a>
  {% endfor %}
</div>

<script>
function msToggleLibs(){ document.getElementById('ms-libs').classList.toggle('open'); }
(function(){
  var CUR = {{ current|tojson if current is defined else '""' }};
  fetch('/media/ui/steps').then(function(r){ return r.json(); }).then(function(d){
    if(!d || !d.ok) return;
    /* 步骤状态 */
    document.querySelectorAll('#ms-rail .step').forEach(function(el){
      var k = el.dataset.step, s = d.steps[k];
      if(!s) return;
      el.querySelector('[data-c]').textContent = s.label || '';
      if(k === CUR) return;                       /* 当前步保持 now，不被覆盖 */
      var n = el.querySelector('[data-n]');
      if(s.locked){
        el.classList.remove('todo'); el.classList.add('locked');
        el.setAttribute('title', s.reason || '');
        n.textContent = '不可用';
        el.querySelector('[data-c]').textContent = s.reason || s.label || '';
      } else if(s.done){
        el.classList.remove('todo'); el.classList.add('done');
        n.textContent = '已完成';
      }
    });
    /* 体系库存量 + 缺口点 */
    document.querySelectorAll('#ms-libs [data-lib]').forEach(function(el){
      var v = d.libs[el.dataset.lib];
      if(v === undefined) return;
      el.textContent = v ? (v + ' 条') : '还没沉淀，去建一条';
      if(!v) el.classList.add('empty');
    });
    if(d.libs_empty && d.libs_empty.length){
      document.getElementById('ms-gap').style.display = 'block';
    }
  }).catch(function(){});
})();
</script>
{%- endmacro %}

{#- 底部步骤导航。back/next 传 {'href':..., 'label':...}；
    next_disabled 为真时按钮禁用并在下方用 --warn 说明 reason -#}
{% macro step_nav(back=None, next=None, next_disabled=False, reason='') -%}
<div class="stepnav">
  {% if back %}<a class="btn ghost" href="{{ back.href }}">{{ ic.icon('chevron', 'flip') }}回{{ back.label }}</a>{% endif %}
  <span class="sp"></span>
  {% if next %}
    {% if next_disabled %}
      <button class="btn primary" disabled>下一步：{{ next.label }}</button>
    {% else %}
      <a class="btn primary" href="{{ next.href }}">下一步：{{ next.label }} {{ ic.icon('chevron') }}</a>
    {% endif %}
  {% endif %}
  {% if next_disabled and reason %}<span class="why">{{ reason }}</span>{% endif %}
</div>
{%- endmacro %}
```

- [ ] **Step 3: 加 `.flip` 工具类**（左向箭头用；加在 base.html 组件样式里）

```css
.icon.flip{ transform:rotate(180deg); }
```

- [ ] **Step 4: 确认 Jinja2 有无 `tojson`**（本项目历史上没有该过滤器）

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
e=Environment(loader=FileSystemLoader('app/templates'))
print('tojson' in e.filters)"
```
若输出 `False`：把 `_media_shell.html` 里的 `{{ current|tojson ... }}` 改为 `'{{ current }}'`（直接加引号的字符串字面量），并在报告中写明。

- [ ] **Step 5: 编译验证**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
e=Environment(loader=FileSystemLoader('app/templates'))
for t in ['base.html','_media_shell.html']: e.get_template(t); print(t,'OK')"
```

- [ ] **Step 6: Commit**

```bash
git add app/templates/base.html app/templates/_media_shell.html
git commit -m "feat(media-ui): 工作流 shell 组件（人设条+步骤条+体系库面板+步骤导航）"
```

---

## Task 3: 主线页接入 shell（选题 / 内容 / 发布）

**Files:**
- Modify: `app/templates/media_topics.html`（165 行）
- Modify: `app/templates/media_board.html`（214 行）

**Interfaces:** Consumes Task 2 的 `media_shell` / `step_nav`。

- [ ] **Step 1: 读两个文件，逐项记录现有 JS 函数、元素 ID、表单 action、fetch 端点、每处手写导航链接**，写进报告。手写导航要被 shell 替换，其余一律保留。

- [ ] **Step 2: `media_topics.html` 接入**
  - 顶部加 `{% import "_media_shell.html" as shell %}`。
  - `{% block content %}` 开头调用 `{{ shell.media_shell('topic', persona_id=persona_id) }}`（该页 context 只有 `persona_id`，无 `persona` 对象）。
  - 删除页面自己拼的返回/跳转链接行。
  - 页面主标题用 spec §5 的层级：`<h1>` 24px `--display`；下方一句解释 13px `--ink-3`：「AI 推选题，你挑能用的采用；采用后进入内容制作。」
  - 结尾调用 `{{ shell.step_nav(next={'href':'/media/board','label':'内容'}) }}`（选题是第一步，无 back）。

- [ ] **Step 3: `media_board.html` 接入（同时承载"内容"与"发布"两步）**
  - 顶部加 import。
  - 该页 context 有 `persona` 对象：读取 URL 查询参数决定当前步——`{% set view = request.query_params.get('view') %}`，`view == 'publish'` 时传 `'publish'`，否则 `'content'`。
  - `view == 'publish'` 时只渲染 `stage in ['ready','published']` 的列；否则渲染全部列（保持现有行为）。**列数据来自已有的 `columns`，用 Jinja 过滤，不改后端。**
  - 标题与解释随 view 切换：内容→「内容 / 把采用的选题做成能发的内容：AI 写脚本 → 你录 → 你剪 → 生成三平台文案。」；发布→「发布 / 生成三平台差异化文案，发完标记已发并回填数据。」
  - 底部：内容视图 `{{ shell.step_nav(back={'href':'/media/topics','label':'选题'}, next={'href':'/media/board?view=publish','label':'发布'}) }}`；发布视图 `{{ shell.step_nav(back={'href':'/media/board','label':'内容'}, next={'href':'/media/persona','label':'复盘'}) }}`。
  - 删除页面里那一排手写的 8 个跳转链接（体系库入口已由 shell 提供）。

- [ ] **Step 4: 验证**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
e=Environment(loader=FileSystemLoader('app/templates'))
for t in ['media_topics.html','media_board.html']: e.get_template(t); print(t,'OK')"
grep -n "teal-\|indigo-\|orange-\|bg-purple-\|bg-blue-\|text-gray-\|bg-white" app/templates/media_topics.html app/templates/media_board.html
```
Expected: 两个 OK；grep 无输出。

- [ ] **Step 5: Commit**

```bash
git add app/templates/media_topics.html app/templates/media_board.html
git commit -m "feat(media-ui): 选题/内容/发布接入工作流 shell"
```

---

## Task 4: 内容详情页 + AI 注入说明（最大文件）

**Files:**
- Modify: `app/templates/media_content.html`（727 行）

**Interfaces:** Consumes `media_shell` / `step_nav` / `.inj`。

- [ ] **Step 1: 完整读该文件**，逐项列出所有 JS 函数、元素 ID、表单 action、fetch 端点、`<details>` 折叠块、阶段推进控件、AI 动作按钮位置，写进报告。**这些必须全部保留可用。**

- [ ] **Step 2: 接入 shell**
  - 顶部 import；`{{ shell.media_shell('content', persona=persona) }}`。
  - 保留现有面包屑逻辑或改由 shell 承担，二选一，不要重复两条。
  - 底部 `{{ shell.step_nav(back={'href':'/media/board','label':'内容看板'}) }}`（单条内容详情不设"下一步"，其阶段推进由页内已有控件负责）。

- [ ] **Step 3: 在每个 AI 动作按钮后加注入说明**（方案 B 的落点）

在「AI 写脚本」「AI 平台文案」「AI 复盘」等按钮所在行，按钮之后插入：
```html
<span class="inj" data-inj>
  {{ ic.icon('check') }}已注入 <span data-inj-body>—</span>
  <span class="more" onclick="msShowInj()">看详情</span>
</span>
```
并在页面脚本区加（复用 shell 已取的数据源，避免重复请求）：
```html
<script>
fetch('/media/ui/steps').then(function(r){return r.json();}).then(function(d){
  if(!d || !d.ok) return;
  var L = d.libs, parts = [];
  [['persona','人设'],['material','原料'],['playbook','打法']].forEach(function(p){
    var v = L[p[0]] || 0;
    parts.push(v ? ('<b>' + p[1] + ' ' + v + ' 条</b>')
                 : ('<span class="zero">' + p[1] + ' 0</span>'));
  });
  document.querySelectorAll('[data-inj-body]').forEach(function(el){ el.innerHTML = parts.join(' · '); });
}).catch(function(){});
function msShowInj(){ document.getElementById('ms-libs').classList.add('open'); }
</script>
```
> **诚实性要求**：这里显示的是**体系库存量**（AI 可取用的池子），不是"本次实际注入的条数"——后端没有暴露单次注入明细，**不得**把存量说成"本次注入"。文案固定为「已注入」+ 库名 + 存量，若将来后端提供单次明细再替换。**在报告中明确记录这一点。**

- [ ] **Step 4: 文字层级整理** —— 页面主标题 24px `--display`；每个区块标题 15px/600；说明 12.5px `--ink-3`；正文 13.5px `--ink-2`。区块之间用 `.module` 或 `.card` 分隔，让 727 行的长页读起来分块。

- [ ] **Step 5: 验证**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
Environment(loader=FileSystemLoader('app/templates')).get_template('media_content.html'); print('OK')"
grep -c "teal-\|indigo-\|orange-\|bg-purple-\|bg-blue-\|text-gray-\|bg-white" app/templates/media_content.html
```
Expected: OK；grep 计数为 0。

- [ ] **Step 6: Commit**

```bash
git add app/templates/media_content.html
git commit -m "feat(media-ui): 内容详情页接入 shell + AI 注入说明"
```

---

## Task 4.5: 复盘落地页（执行中发现的缺口，用户确认新增）

**背景：** 复盘只有 `/media/review-cycle/{id}` 和 `/media/phase-review/{id}` 两个详情路由，没有列表/发起页——入口藏在人设档案页里。步骤条第 4 步无处可去。

**Files:**
- Modify: `app/api/media_ui.py`（新增只读路由，仍不碰 `media.py`）
- Create: `app/templates/media_review_home.html`

**Interfaces:**
- Produces: `GET /media/review` → 渲染 `media_review_home.html`，context：`persona`（dict 或 None）、`cycles`（周期复盘列表）、`phases`（阶段复盘列表）。

- [ ] **Step 1: 确认复盘表结构**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
import sqlite3; c=sqlite3.connect('data/aipm.db')
for t in ['media_review_cycle','media_phase_review']:
    print(t, [d[1] for d in c.execute(f'PRAGMA table_info({t})')])"
```
以实际列名为准写下面的查询；对不上就改查询，**不得编造字段**。

- [ ] **Step 2: 在 `app/api/media_ui.py` 末尾加只读路由**（复用文件里已有的 `_current_persona_id`）

```python
@router.get("/media/review")
async def media_review_home(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona, cycles, phases = None, [], []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_review_cycle WHERE persona_id=? ORDER BY created_at DESC", (pid,))
            cycles = [dict(r) for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT * FROM media_phase_review WHERE persona_id=? ORDER BY created_at DESC", (pid,))
            phases = [dict(r) for r in await cur.fetchall()]
        ctx = {"request": request, "persona": persona, "cycles": cycles, "phases": phases}
        return request.app.state.templates.TemplateResponse(request, "media_review_home.html", ctx)
    finally:
        await db.close()
```

- [ ] **Step 3: 新建 `media_review_home.html`** —— 挂 `shell.media_shell('review', persona=persona)`；标题「复盘」+ 解释「把已发内容的表现总结成经验，沉淀回体系库，喂给下一轮选题。」；两个 `.module` 分别列周期复盘与阶段复盘（每条链到已有详情页 `/media/review-cycle/{id}`、`/media/phase-review/{id}`）；各自空状态用 `.empty`；发起复盘的按钮**指向人设档案页已有的发起入口**（`/media/persona`），不复制其 POST 逻辑。底部 `{{ shell.step_nav(back={'href':'/media/board?view=publish','label':'发布'}) }}`。

- [ ] **Step 4: 把 shell 第 4 步指向新页** —— `_media_shell.html` 里 `('review','4 · 复盘','/media/persona')` 改为 `('review','4 · 复盘','/media/review')`。

- [ ] **Step 5: 验证**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
Environment(loader=FileSystemLoader('app/templates')).get_template('media_review_home.html'); print('OK')"
python -c "
from fastapi.testclient import TestClient; from app.main import app
print('/media/review ->', TestClient(app).get('/media/review', follow_redirects=False).status_code)"
```
Expected: OK；路由返回 200 或 302（登录重定向）。

- [ ] **Step 6: Commit**

```bash
git add app/api/media_ui.py app/templates/media_review_home.html app/templates/_media_shell.html
git commit -m "feat(media-ui): 新增复盘落地页，补上第4步的落点"
```

---

## Task 5: 复盘三页接入 shell

**Files:**
- Modify: `app/templates/media_phase_review.html`（108 行）
- Modify: `app/templates/media_review_cycle.html`（110 行）
- Modify: `app/templates/media_feishu_review.html`（66 行，**尚未套设计系统**）

- [ ] **Step 1: 逐个读文件**，记录 JS 函数、元素 ID、表单 action、fetch 端点，写进报告。

- [ ] **Step 2: 三页统一接入**
  - `{% import "_media_shell.html" as shell %}` + `{{ shell.media_shell('review', persona=persona) }}`（若该页 context 无 `persona`，改传 `persona_id=persona_id`；两者都没有就都不传，shell 会显示「当前人设」）。
  - `media_feishu_review.html` 额外需要**完整套上设计系统**：`{% import "_icons.html" as ic %}`、emoji 换 `ic.icon(...)`、颜色改 token、卡片用 `.card`、表单用 label-above。
  - 底部 `{{ shell.step_nav(back={'href':'/media/board?view=publish','label':'发布'}) }}`。
  - 删除各页手写的返回链接。

- [ ] **Step 3: 验证**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
e=Environment(loader=FileSystemLoader('app/templates'))
for t in ['media_phase_review.html','media_review_cycle.html','media_feishu_review.html']: e.get_template(t); print(t,'OK')"
grep -n "teal-\|indigo-\|orange-\|bg-purple-\|bg-blue-\|text-gray-\|bg-white" app/templates/media_phase_review.html app/templates/media_review_cycle.html app/templates/media_feishu_review.html
```
Expected: 三个 OK；grep 无输出。

- [ ] **Step 4: Commit**

```bash
git add app/templates/media_phase_review.html app/templates/media_review_cycle.html app/templates/media_feishu_review.html
git commit -m "feat(media-ui): 复盘三页接入 shell，飞书复盘补齐设计系统"
```

---

## Task 6: 体系库六页接入 shell（消灭死胡同）

**Files:**
- Modify: `app/templates/media_persona.html`（302 行）
- Modify: `app/templates/media_persona_interview.html`（110 行，**未套设计系统**）
- Modify: `app/templates/media_audience.html`（136 行）
- Modify: `app/templates/media_anchor.html`（134 行）
- Modify: `app/templates/media_materials.html`（130 行）
- Modify: `app/templates/media_playbook.html`（24 行，**未套设计系统，且是死胡同**）
- Modify: `app/templates/media_legacy.html`（22 行，**未套设计系统**）

- [ ] **Step 1: 逐个读文件**，记录 JS 函数、元素 ID、表单 action、fetch 端点、feature 入口，写进报告。**一个功能都不能丢。**

- [ ] **Step 2: 七页统一接入**
  - `{% import "_media_shell.html" as shell %}` + `{{ shell.media_shell('lib', persona=persona) }}`（无 `persona` 对象则传 `persona_id`）。`'lib'` 不高亮任何主线步。
  - `{% block topbar %}` 放面包屑：`自媒体 / 体系库 / <本库名>`。
  - **底部必须有返回**（这是"消灭死胡同"的验收点）：
    ```jinja
    {{ shell.step_nav(back={'href':'/media/board','label':'内容看板'}) }}
    ```
    `media_playbook.html` 与 `media_legacy.html` 重点验证——它们目前零出口。
  - 四个未套设计系统的页面（`media_persona_interview` / `media_playbook` / `media_legacy` + 已在 Task 5 处理的 feishu）：补 `_icons.html` import、emoji 换 SVG、颜色改 token、列表用 `.card`/`.frow`、表单 label-above、空状态用 `.empty`。
  - 各页主标题 24px `--display` + 一句 13px `--ink-3` 解释这个库是干嘛的、喂给谁。

- [ ] **Step 3: 验证**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from jinja2 import Environment, FileSystemLoader
e=Environment(loader=FileSystemLoader('app/templates'))
for t in ['media_persona.html','media_persona_interview.html','media_audience.html','media_anchor.html','media_materials.html','media_playbook.html','media_legacy.html']:
    e.get_template(t); print(t,'OK')"
echo '--- 死胡同检查：每页都应有返回链接 ---'
for f in media_playbook media_legacy media_persona_interview media_materials media_anchor media_audience media_persona; do
  echo -n "$f: "; grep -c "step_nav\|href=\"/media" app/templates/$f.html; done
```
Expected: 七个 OK；每个文件的计数 ≥1。

- [ ] **Step 4: Commit**

```bash
git add app/templates/media_persona.html app/templates/media_persona_interview.html app/templates/media_audience.html app/templates/media_anchor.html app/templates/media_materials.html app/templates/media_playbook.html app/templates/media_legacy.html
git commit -m "feat(media-ui): 体系库六页接入 shell，补齐设计系统，消灭死胡同"
```

---

## Task 7: 人设总览页 + 全模块验收

**Files:**
- Modify: `app/templates/media_overview.html`（44 行）
- 视发现的问题回改对应模板

- [ ] **Step 1: `media_overview.html`** —— 这是"还没选人设"的门，**不挂步骤条**（挂了没有上下文）。只做：标题 24px `--display`「人设总览」+ 一句解释「选一个人设进工作区，或新建一个。」+ 人设卡片列表 + 新建浮层。卡片里的统计数字沿用现有 context（`p.total` / `p.published` / `p.winners`），不新增。

- [ ] **Step 2: 全模块 grep 验收**

```bash
cd /d/GAGA-5-25/ai-pm
echo '--- 旧色类 ---'; grep -rln "teal-\|indigo-\|orange-\|bg-purple-\|border-purple-\|bg-blue-\|text-gray-\|bg-white" app/templates/media_*.html || echo none
echo '--- emoji ---'; grep -rl "📁\|⭐\|🔍\|🤖\|📊\|💰\|🎬\|🎓\|📚\|🔗\|📄\|✅\|❌\|🔧\|🛰️" app/templates/media_*.html || echo none
echo '--- 裸 hex ---'; grep -rn "#[0-9a-fA-F]\{6\}" app/templates/media_*.html | head
echo '--- 仍在手写导航的页（应大幅减少）---'; grep -ln "href=\"/media/board\"" app/templates/media_*.html | wc -l
echo '--- 全部模板编译 ---'
python -c "
from jinja2 import Environment, FileSystemLoader
import os
e=Environment(loader=FileSystemLoader('app/templates'))
bad=[]
for t in sorted(os.listdir('app/templates')):
    if t.endswith('.html'):
        try: e.get_template(t)
        except Exception as ex: bad.append((t,str(ex)[:60]))
print('FAILED:', bad if bad else 'none')"
```
Expected: 旧色类/emoji 为 none；裸 hex 只出现在 token 定义处；全部模板编译通过。

- [ ] **Step 3: 路由冒烟**

```bash
cd /d/GAGA-5-25/ai-pm && python -c "
from fastapi.testclient import TestClient
from app.main import app
c=TestClient(app)
for r in ['/media','/media/board','/media/topics','/media/persona','/media/audience','/media/anchor','/media/materials','/media/playbook','/media/legacy','/media/ui/steps']:
    x=c.get(r)
    bad='ERR' if ('Traceback' in x.text or 'UndefinedError' in x.text) else ''
    print(r, x.status_code, bad)"
```
Expected: 全部 200 或 3xx（重定向到登录也算通过），无 ERR。

- [ ] **Step 4: 逐条对照 spec §10 验收清单**打勾，未达标的回改。

- [ ] **Step 5: Commit**

```bash
git add app/templates/media_overview.html
git commit -m "feat(media-ui): 人设总览页 + 全模块验收"
```

---

## Self-Review（本计划对照 spec）

- **spec §3.1 层级** → Task 2 的 `media_shell`（人设条+步骤条）、Task 7（overview 不挂步骤条）。
- **spec §3.2 四步映射** → Task 1（状态数据）、Task 3（选题/内容/发布，含 `view=publish` 筛选）、Task 5（复盘）。
- **spec §3.3 体系库统一入口** → Task 2 的 `.libpanel` + Task 6（六页接入、消灭死胡同）。
- **spec §4 状态词汇表** → Task 2 的 `.step.done/.now/.todo/.locked` 样式 + Task 1 提供 `locked`/`reason`。
- **spec §5 文字层级** → Task 3/4/5/6/7 各自的标题-解释-正文要求。
- **spec §6 五个组件** → Task 2 全部定义（`.pbar` `.rail/.step` `.stepnav` `.inj` `.libpanel`）。
- **spec §7 逐页范围** → Task 3–7 覆盖全部 14 页。
- **spec §8 移动端** → Task 2 的 `@media (max-width:767px)` 块（隐藏账号、按钮撑满 44px、体系库全屏浮层）+ 各 Task 验证。
- **spec §9 约束** → Global Constraints；Task 1 明确只新建文件 + main.py 两行。
- **spec §10 验收** → Task 7。
- **Placeholder 扫描**：无 TBD/TODO；共享代码（接口、macro、CSS）全文给出；页面转换任务给出"改什么+用哪些已定义类+验证什么"（实现者读实时模板，逐页全文既不可能也会过时）。
- **命名一致性**：`media_shell(current, persona, persona_id)`、`step_nav(back, next, next_disabled, reason)`、步骤 key `topic/content/publish/review/lib`、接口 `/media/ui/steps` — 全计划统一。
- **已知风险已在计划内处理**：Task 1 Step 3 先校验真实 schema 再定 SQL；Task 2 Step 4 先检测 `tojson` 是否存在；Task 4 Step 3 明确"存量≠本次注入"的诚实性边界。
