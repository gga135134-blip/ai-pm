# 教训/红线库：让纠正开始复利

日期：2026-08-31
状态：设计已确认，待实施
范围：ai-pm 自媒体模块（项目①，共四项目拆分中的第一项）

---

## 1. 背景与问题

用户原话：**「AI 的能力每次都要我说，都要我改，花了很多时间去改。」**

诊断：**用户的每一次纠正都是一次性消费，没有落点。**

现有的库存的全是「要做什么」——
`media_persona_trait`（人设调性）、`media_material`（可用故事）、
`media_playbook`（有效打法）、`media_audience`、`media_anchor`。

**没有任何一张表存「不要做什么」。** 而用户日常纠正的绝大多数都是「不要」。

结果：同一个毛病 AI 无限次重犯，用户无限次纠正。

### 1.1 这不是「范围没设好」

用户提出过一个合理疑问：人设/受众/锚点/打法已经把范围收窄了，
输出是否已经足够精准？

**方向精准，手感不精准。** 两类不同的错误：

| 错误类型 | 现有机制 | 例子 |
|---|---|---|
| 说错了东西（方向） | 人设/受众/锚点已覆盖 | 讲了无关话题 |
| 说得不对味（手感） | **无任何机制** | 开头太软、书面语、讲太深 |

用户仍在每天纠正，本身即是「现有机制拦不住第二类」的实证。

### 1.2 槽位早已预留

`app/services/media_context.py:22`：

```python
INJECTION_BUDGET = {
    ...
    "lesson": 3,      # 二期：只给 trigger_context 命中的
}
```

注入配额和匹配策略（`trigger_context` 命中）当初就设计好了，
只是表没建、函数没写。本项目是**填上自己挖的坑**，不是引入新概念。

### 1.3 复盘产出无处可去

`app/services/media_review_cycle.py:124` L2_SYSTEM 白纸黑字：

> `打法/教训/红线/权重调整建议 → advisory（这些暂无自动落点，先当文字建议）`

每轮 L2 花钱产出的教训与红线，落进 `media_review_cycle.advisory` JSON 字段，
复盘页渲染出来给人看一眼（`media_review_cycle.html:106`），
**下游没有任何代码读取**。买到的洞见当装饰品。

### 1.4 对话中的判断全部蒸发

用户在助手对话里说「这个角度不行，我们受众不吃这套」——
这是浓度最高的知识（几个月实战换来的判断）。
它进 `media_assistant_message` 表，10 条滑动窗口之后滑出，永不复现。
没有任何机制把对话里的判断沉淀成库。

---

## 2. 目标与非目标

### 目标

1. 建立「教训 / 红线」库，让「不要做什么」有地方存
2. 写稿时按注意力纪律注入相关的少数几条
3. L2 复盘产出的 advisory 有采纳落点
4. 助手对话中的判断可一键沉淀
5. 可观测：知道哪条真的在被用，哪条是死条目

### 非目标（本轮明确不做）

| 不做 | 理由 |
|---|---|
| AI 自动写入本子 | 宪法第 2 条：AI 只提候选，人 adopt 才入库。库脏了决策引擎全废 |
| `revise_draft` / `critique_draft` 注入 | 先在 `write_script` 单点验证有效性再铺开。一次只动一处，出问题好定位 |
| L2 `weight_suggestion` 自动改 `WEIGHTS` | 权重自适应的系统难 debug——出问题时分不清是模型退化还是权重漂移 |
| 红线跨人设共享 | 对齐宪法第 4 条：方法论（打法）共享，人设相关的独享。不预留 `scope` 列（YAGNI） |
| 定时自动跑 / 收件箱 / 预算闸 | 属项目③（自动驾驶），依赖本项目与项目②的工具层 |

---

## 3. 数据模型

新表 `media_lesson`，形状对齐 `media_playbook`（同类资产，保持一致）。

```sql
CREATE TABLE IF NOT EXISTS media_lesson (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    kind TEXT DEFAULT 'lesson',       -- lesson=教训 / redline=红线
    brief TEXT DEFAULT '',            -- 一句话。注入时只用这个
    detail TEXT DEFAULT '',           -- 展开说明，只给人看，不进 prompt
    trigger_context TEXT DEFAULT '',  -- 什么情况下适用（教训匹配用；红线可空）
    evidence TEXT DEFAULT '',         -- 来源原文/数据，防编造
    source TEXT DEFAULT '',           -- l2_advisory / assistant / manual
    status TEXT DEFAULT 'active',     -- active / archived
    hit_count INTEGER DEFAULT 0,      -- 被注入次数
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

### 设计决定

**① 教训与红线同表，`kind` 区分。**
结构完全相同，差别仅在强度（红线=绝对不许，教训=注意）。
分两表属重复。UI 分两块渲染。

**② 按人设独享。** 对齐宪法第 4 条。「这个受众不吃这套」是人设特有的。
不加 `scope` 列——将来真需要共享再单独一轮。

**③ `brief` / `detail` 分离，注入只用 `brief`。**
严格照抄现有 `render_brief_list`（`media_context.py:52`）的
「detail 留给 AI 按需读取」模式。这是注意力纪律，不可破。

**④ `archived` 而非硬删。** 归档的条目不注入但保留证据链，
与 `media_material.status` 同构。真要删走 UI 删除。

---

## 4. 注入机制

### 4.1 配额

`INJECTION_BUDGET` 新增一个槽位（`lesson: 3` 已存在，沿用）：

```python
INJECTION_BUDGET = {
    ...
    "lesson": 3,       # 教训：按 trigger_context 匹配度取前 3
    "redline": 2,      # 红线：无条件带，不做匹配
}
```

### 4.2 红线与教训不共享配额

**红线单独占槽。** 先例是记忆点（`media_context.py:57`）：

> `signature（记忆点）单独占预算槽，不与普通条目竞争 ——
>  记忆点是 IP 资产的核心，绝不能被高置信度的普通条目挤掉。`

同理：红线是硬约束，不能被一条恰好匹配度高的教训挤掉。
若共享配额，存在「3 条教训占满槽位、红线一条未进」的情形，红线失效。

**红线不做匹配。** 红线语义即「任何时候都不许」，
对它做适用性判断自相矛盾。无条件注入，最多 2 条（按 `created_at` 升序取前 2）。

**副作用（有意为之）：红线上限 2 条。**
UI 在红线达 2 条后提示「注入只带前 2 条，建议合并或降级为教训」。
逼迫红线保持少而硬——红线一多就不值钱。

### 4.3 教训匹配算法

拿 `trigger_context` 与「本条内容的 title + puzzle + idea_reason」
计算 bigram 重合度，降序取前 3。

**直接复用 `media_decision._overlap`**（`media_decision.py:38`），不新造轮子。
`media_decision` 是纯计算模块（仅 `import json`），
`media_context` 导入它无循环依赖风险。

`trigger_context` 为空的教训视为重合度 0，排在最后（不主动排除，
配额有余时仍可进——但正常情况下会被有 trigger 的挤掉）。

### 4.4 新增函数

放进 `media_context.py`，与 `select_materials` / `render_material_block` 并排：

```python
def select_redlines(lessons: list[dict]) -> list[dict]:
    """红线：从传入列表里筛 kind=='redline'，
    无条件取前 INJECTION_BUDGET['redline'] 条（created_at 升序）。"""

def select_lessons(lessons: list[dict], topic_text: str) -> list[dict]:
    """教训：从传入列表里筛 kind=='lesson'，按 trigger_context 与 topic_text 的
    bigram 重合度降序，取前 INJECTION_BUDGET['lesson'] 条。"""

def render_lesson_block(redlines: list[dict], lessons: list[dict]) -> str:
    """渲染注入块。两者皆空返回空串（不加空标题）。"""
```

**分工明确**：两个 select 函数都接收「同一份该人设的 active 条目列表」，
各自按 `kind` 筛选。调用方只查一次库，不查两次：

```sql
SELECT * FROM media_lesson WHERE persona_id=? AND status='active'
```

`status != 'active'`（归档）的条目由这条 SQL 排除，select 函数不再重复判断。

渲染格式：

```
【红线（绝对不许违反）】
- 不许编造数据或案例，没有真料就标缺口
- 不许用「赋能/抓手/闭环」这类词

【教训（这次特别注意）】
- 开头别铺垫，第一句就抛冲突
- 讲方法论时别往技术细节钻
```

### 4.5 注入位置

在 `write_script` 的 `parts` 列表中，
插在 `【本次重写要求】`（hint）**之后**、`请写出这条内容的口播脚本。`**之前**。

理由：近因效应——LLM 对靠近提示词末尾的约束更敏感。
红线放开头会被前面的人设/素材/打法冲淡。

`mode == "lean"` 时**不注入**（lean 模式的语义就是只给身份行做对照）。

### 4.6 可观测性

- 被注入的 lesson id 并入 `injected_ids`，走现有 `log_injection`
- 每条被注入的记录 `hit_count += 1`
- UI 标出 `hit_count == 0` 的条目——说明 `trigger_context` 写歪了，
  或这条根本没必要存在，该删

**计数时机（明确）**：`hit_count` 与 `log_injection` 在**同一处**递增——
即 AI 调用**成功返回后**。报错、费用保护、空返回重试失败的路径**都不计数**。
理由：`hit_count` 要回答的是「这条真的参与过一次成品生产吗」，
不是「它被拼进过几次提示词」。空返回重试成功时只计一次，不按调用次数翻倍。

---

## 5. L2 advisory 落点

### 5.1 现状

`media_review_cycle.html:106` 已经在渲染 `cyc.advisory.lessons`。
只是渲染完就结束了。

### 5.2 改法

在复盘页已有的 advisory 教训/红线条目旁，加「**采纳进本子**」按钮。
点击 → `POST /media/lesson/adopt` → 写入 `media_lesson`（`source='l2_advisory'`）。

**人点才入库**，符合宪法第 2 条。走助手动作日志（`log_action`）留痕，可撤。

### 5.3 L2 输出结构化（向后兼容）

现在 `advisory.lessons` 是**纯字符串数组**，缺 `trigger_context` 与 `evidence`。
没有 trigger 就无法做适用性匹配。

改 `L2_SYSTEM` 的输出契约：

```json
"advisory": {
  "playbooks": [],
  "lessons": [{"brief":"", "trigger_context":"", "evidence":""}],
  "redlines": [{"brief":"", "evidence":""}],
  "weight_suggestion": ""
}
```

**向后兼容**：读取侧做归一化——遇到字符串元素，
当作 `{"brief": <str>, "trigger_context": "", "evidence": ""}` 处理。
旧复盘记录照常显示、照常可采纳，只是采纳时需人工补适用场景。
**不做数据迁移。**

---

## 6. 对话沉淀

### 6.1 机制

新增助手工具 `propose_lesson`，归入 `_CORE`（需人确认档）：

```python
_schema("propose_lesson",
        "把一条教训或红线记进本子（需人确认）。",
        {"kind": {"type": "string", "description": "lesson 或 redline"},
         "brief": {"type": "string"},
         "trigger_context": {"type": "string"},
         "evidence": {"type": "string"}},
        ["brief"])
```

复用现有 `_core_stage`（`media_agent_tools.py:262`）：
落 `pending` → 出待确认卡 → 用户点确认才执行 → `apply_action` 写库 → 可撤。
`media_assistant.apply_action` / `revert_action` 各加一个 `action_type` 分支
（对齐宪法第 7 条：动作日志是通用留痕/撤销底座，不另造机制）。

### 6.2 助手主动性

`MEDIA_ASSISTANT_SYSTEM` 增加一条指引：

> 当用户表达的是**否定性判断**（「这样不行」「别这么写」「我们的人不吃这套」），
> 而不只是一次性的改稿要求时，在完成改动后**主动提议**把它记进本子。
> 一次只提一条，提完就停，用户不点就算了，别追问。

预期交互：

```
用户：这个开头太软了，我们的人不吃铺垫
AI：（改了稿）
    💡 顺便——「开头别铺垫，第一句抛冲突」要不要记进本子？以后写稿我都带着。
    〔记下来〕〔不用〕
```

### 6.3 为什么这块最值钱

用户随口一句纠正，是几个月实战换来的判断，
是整个系统里浓度最高的知识来源。现在 100% 在蒸发。
而实现成本接近于零——待确认卡机制已稳定运行一个多月。

---

## 7. UI

新页面 `/media/lessons`，入口挂在媒体导航（`_media_shell.html`）。

```
🚫 红线（2/2 已满）                ⚠️ 教训（12 条）
────────────────────              ────────────────────
不许编造数据或案例                   开头别铺垫，第一句抛冲突
  来自 8/20 复盘 · 用过 34 次          什么时候：所有口播
                                     来自 对话 · 用过 12 次
不许用「赋能/抓手」这类词
  来自 你 8/25 说的 · 用过 34 次      讲方法论别往技术细节钻
                                     什么时候：方法论类内容
＋ 手动加一条                         来自 8/20 复盘 · 用过 0 次 ⚠️
```

- 红线已满 2 条时，新增按钮旁提示「注入只带前 2 条，建议合并或降级为教训」
  （**只提示，不阻止**——存着不注入也有档案价值）
- `hit_count == 0` 的条目标 ⚠️ 并给一句解释
- 支持手动增 / 改 / 归档 / 删除（不必等 AI 提议）
- 点条目展开 `detail` 与 `evidence`
- 「什么时候适用」输入框旁给一句提示：
  **「用会出现在选题标题里的词——匹配看的是字面重合，不认同义词」**
  （对应 §10.1 的已知限制，把限制变成可操作的指引）

样式跟随现有媒体页（本地裁剪版 tailwind，缺色阶在 `base.html` 补）。
改模板一律用编辑器工具，不用 PowerShell -replace（中文乱码）。

---

## 8. 测试策略

沿用项目现有两种模式（见「运维备忘」）：

| 被测对象 | 模式 |
|---|---|
| `select_redlines` / `select_lessons` / `render_lesson_block` | 纯函数，直接测 |
| `media_lesson` CRUD、advisory 归一化 | 内存 DB（`tests/media_helpers.py::make_db`） |
| `write_script` 注入路径、路由 | tmp-`DB_PATH` 模块 fixture + TestClient + 签名 session cookie |

必须覆盖的关键用例：

1. 红线超过 2 条时只注入前 2 条
2. 红线与教训**不互相挤占**（3 条高匹配教训 + 2 条红线 → 5 条全进）
3. `trigger_context` 空的教训排在有 trigger 的之后
4. 两者皆空时 `render_lesson_block` 返回空串（不产生空标题）
5. `mode == "lean"` 不注入
6. `advisory.lessons` 为旧格式（纯字符串）时归一化不报错
7. `propose_lesson` 只落 pending，不直接写库
8. 采纳后 `hit_count` 随注入递增；归档后不再注入

---

## 9. 迁移

- 新表走 `SCHEMA`（`CREATE TABLE IF NOT EXISTS`），重启自动建，零手动
- `INJECTION_BUDGET` 加一个 key，无迁移
- L2 输出契约变更**不需要数据迁移**（读取侧归一化兼容旧格式）

---

## 10. 已知限制（诚实边界）

1. **匹配是字面重合，不是语义。** `_overlap` 是 bigram 字符重叠，
   「方法论」匹配得上「方法」，但匹配不上「套路」。
   够用，但用户写 `trigger_context` 时要用**会出现在选题里的词**。
   UI 上给这句提示。语义匹配（向量）属未来考虑，不在本轮。

2. **红线上限 2 条是硬约束。** 第 3 条红线可以存，但不会被注入。
   这是有意的设计压力，不是缺陷。

3. **本轮只有 `write_script` 消费本子。** 改稿、批评、推选题都还看不到。
   验证有效后再单独一轮铺开。

4. **有效性要两周后才看得出来。** `hit_count` 需要积累。
   上线后第一件事不是加功能，是观察哪些条目 hit_count 长期为 0。

---

## 11. 与其余三个项目的关系

本项目是四项目拆分中的**项目①**，执行顺序：

| 项目 | 内容 | 体量 | 状态 |
|---|---|---|---|
| **① 让它记住** | 本文档 | ~700 行 | **本轮** |
| ② 工具层 | 把现有按钮包成助手工具 + 批量弃选题 | ~500 行 | 待做 |
| ③ 自动驾驶 | 心跳 + 收件箱 + 预算闸 | ~1000 行 | 依赖 ①② |
| ④ 数据采集 | 后台导出导入 + 抓取健壮化 | ~900 行 + 长期维护 | 独立线，需先调研 |

①先于②的理由：②省的是「找按钮」的时间（分钟级），
①省的是「重复纠正同一毛病」的时间（小时级）。止血优先。

**④ 待办**：本项目完成后调研 GitHub 上数据采集的现状与最新方案，
再与用户确定该项目的范围与可行性。
