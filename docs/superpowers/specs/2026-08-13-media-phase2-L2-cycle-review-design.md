# L2 周期复盘设计（飞轮的动力源）

**日期：** 2026-08-13
**状态：** 设计已定，待写实施计划
**触发词：** 「接 AI-PM」

---

## 1. 目标与定位

给自媒体飞轮补上**周期复盘（L2）**这一层。现状：

- **L1 单条复盘**（`review_content` / `media_review` / `media_case`）——已建。回答"这一条为什么成/败"。
- **L2 周期复盘**——本轮做。回答"**近 N 条有什么规律**"。原始系统设计（`2026-07-24-media-ops-system-design.md` §3.5）称其为"**飞轮真正的动力源**"：单条爆了可能是运气，10 条对比后发现的才是可复制的方法论。**L1 只能给假设，L2 才能验证成规律。**
- **L3 阶段复盘**——不在本轮范围，另起一轮。

**一句话：** 人设复盘区点「🔄 跑一轮周期复盘」→ 系统汇总上轮之后新发的内容数据 → AI 找规律、判上轮假设、提本轮新假设 + 候选资产 → 人拍板采纳。让飞轮从"单条运气"升级成"可验证的方法论"。

### 1.1 核心：假设-验证机制（L2 的灵魂）

L2 区别于"L1 批量堆叠"的关键，是它输出**下一轮要验证的假设**（例："假设：前 3 秒抛问题能提完播 → 下周 5 条中 3 条采用，对比验证"），并在下一轮**自动检验上轮假设**（成立 / 证伪 / 存疑）。这让飞轮是科学的，不是玄学的。本轮必须做完整闭环，不砍成纯聚合报告。

---

## 2. 范围（本轮做 / 不做）

**做：**
- 新表 `media_review_cycle` 存周期复盘（含假设结转字段）。
- 服务 `media_review_cycle.py`：定周期范围、数据门槛、纯计算 metrics 汇总、结转上轮假设、调 AI 产出复盘、候选存库不自动写。
- 假设-验证闭环：假设带稳定 id，下轮拉出判定。
- 触发路由 + 报告详情页 + 人设页复盘区历史轮次列表。
- 候选资产人拍板采纳：人设条目、受众修正（复用现成人拍板闸）。
- 优雅降级：没落点的产出（打法/权重/教训/红线）沉淀成文字建议。

**不做（YAGNI / 依赖未建）：**
- L3 阶段复盘（phase 切换 / trait 归档）——另起一轮。
- 打法库🅑 —— 未建，L2 的打法候选先当 advisory 建议。
- 决策引擎权重设置 UI —— 未建，权重调整建议先当 advisory 文字。
- 定时/自动触发 —— 只做手动按钮（贴合用户"不绑架时间"）。
- 教训库 / 红线库专表 —— 先当 advisory。

---

## 3. 数据模型

新表 `media_review_cycle`（走 `database.py` 的 `SCHEMA`，`CREATE TABLE IF NOT EXISTS`，**零 migration**）：

```sql
CREATE TABLE IF NOT EXISTS media_review_cycle (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    level TEXT DEFAULT 'L2',            -- 预留 L3
    seq INTEGER DEFAULT 1,             -- 第几轮，per persona 递增，方便"上轮/这轮"引用
    period_start DATETIME,             -- 上轮 period_end（首轮为空 = 从头）
    period_end DATETIME,               -- 本轮触发时刻
    content_ids TEXT DEFAULT '[]',     -- 实际纳入范围 JSON
    metrics_summary TEXT DEFAULT '{}', -- 纯计算汇总 JSON
    patterns TEXT DEFAULT '[]',        -- 发现的规律 JSON
    hypotheses TEXT DEFAULT '[]',      -- 本轮提出的假设 JSON（每条带稳定 id）
    hypotheses_tested TEXT DEFAULT '[]', -- 对上轮假设的判定 JSON
    proposed_traits TEXT DEFAULT '[]', -- 候选人设条目（人拍板）
    proposed_audience TEXT DEFAULT '[]', -- 受众修正候选（人拍板）
    advisory TEXT DEFAULT '{}',        -- 没落点的建议 JSON
    cost REAL DEFAULT 0,
    model TEXT DEFAULT '',
    generated_by TEXT DEFAULT 'ai',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

### 3.1 各 JSON 字段结构

**`metrics_summary`**（纯计算，非 AI）：
```json
{
  "content_count": 8,
  "avg": {"views": 12000, "likes": 340, "comments": 45, "shares": 20, "new_fans": 60},
  "median": {"views": 9000, ...},
  "hit_count": 2, "flop_count": 1,   // 复用 media_case 判定口径（vs 账号历史中位）
  "hit_content_ids": [...], "flop_content_ids": [...]
}
```

**`patterns`**（AI）：`[{"pattern": "带真实客户故事的 5 条平均播放是无故事的 2.8 倍", "evidence": "...", "confidence": "high|medium|low"}]`

**`hypotheses`**（AI，本轮提出，**每条带稳定 id**）：
```json
[{"id": "h-<uuid8>", "statement": "前 3 秒抛问题能提升完播",
  "how_to_test": "下轮 5 条中 3 条采用，对比留存", "basis": "本轮 2 条抛问题的完播偏高"}]
```

**`hypotheses_tested`**（AI，对**上轮** hypotheses 的判定）：
```json
[{"ref_id": "h-abc12345", "verdict": "confirmed|refuted|inconclusive",
  "evidence": "本轮 3 条采用，播放中位高 40%"}]
```

**`proposed_traits`**：结构对齐 L1 `review_content` 的 proposed_traits（`{dimension, content, brief, evidence, confidence}`），供复用 `persona_interview_adopt`。

**`proposed_audience`**：`[{segment, field, new_value, evidence}]`（修正现有受众某字段，或提示新 segment）。落地采纳走受众页现成路由。

**`advisory`**（没落点，纯文字建议）：
```json
{"playbooks": ["..."], "lessons": ["..."], "redlines": ["..."],
 "weight_suggestion": "涨粉期建议把 fit 权重从 4 提到 5，因本轮契合定位的内容播放显著更高"}
```

---

## 4. 服务 `media_review_cycle.py`（新）

### 4.1 `run_l2_cycle(db, persona_id, model="auto", force=False) -> dict`

流程：

1. **定周期范围**：查该 persona 上一轮 `level='L2'` 的 `period_end`（`MAX(created_at)` 那轮）。无则 `period_start=None`（从头）。`period_end = now`。`seq = 上轮 seq + 1`（无则 1）。
2. **拉纳入内容**：该 persona 下 `media_content` 满足——有 `media_publish.status='published'` 且该 publish 有 `media_metrics` 且 `snapshot_at`（或 content 归入本周期的时间）落在 `(period_start, period_end]`。以"内容已发且有数据"为纳入条件。
3. **数据门槛**：纳入 < `L2_MIN_CONTENTS`（=5）→ 返回 `{"ok": False, "warn": "才 X 条，规律不可靠，建议攒到 ~5 条再跑", "count": X}`，**不写库**。`force=True` 时越过门槛照跑（人坚持）。
4. **纯计算 `metrics_summary`**：条数、各指标均值/中位、爆款/flop 数——**复用 L1 已有的爆款判定口径**（vs 账号历史中位播放量，见 `review_content` 里的 median 逻辑，抽成可共享的小函数或在本模块重算）。
5. **结转上轮假设**：读上轮 `hypotheses` 塞进 prompt，要 AI 产出 `hypotheses_tested`。
6. **组 prompt + 调 AI**：`ask_ai(prompt, model=model, task_type="media_review_cycle", system_prompt=L2_SYSTEM, json_mode=True)`。prompt 含：内容清单（标题 + 谜题 + 各平台数据 + case_type 归因）、metrics_summary、上轮假设列表。
7. **解析**：`extract_json(..., expect="object")` → patterns / hypotheses / hypotheses_tested / proposed_traits / proposed_audience / advisory。给每条新 hypothesis 补 `id`（`h-<uuid8>`）。
8. **候选绝不写库**：全部存进 `media_review_cycle` 行。人拍板才 adopt。
9. **成本可见**：记 `log_injection`（沿用四条标准动作，与 review_content/挖精华一致）。
10. 写入 `media_review_cycle` 行，返回 `{"ok": True, "cycle_id": ..., "count": ..., "cost": ..., "model": ...}`。

### 4.2 辅助

- `list_cycles(db, persona_id) -> list[dict]`：历史轮次（seq 降序），列表用。
- `get_cycle(db, cycle_id) -> dict | None`：详情页，JSON 字段解析好返回。
- `_summarize_metrics(pubs_with_metrics, median) -> dict`：纯函数，可单测。
- `_prev_cycle(db, persona_id) -> dict | None`：取上一轮，用于周期范围 + 假设结转。

### 4.3 `L2_SYSTEM` 提示词要点

- 角色：资深自媒体操盘手，看一批已发内容的**对比**找可复制规律。
- **四条标准动作**：①只提候选/规律，绝不假设自己能写库；②诚实——规律必须有本批数据支撑，`evidence` 引具体内容/数字，样本少就标 `confidence: low`，不硬凑；③只给精华（规律 ≤5 条、新假设 ≤3 条、候选条目每类 ≤3）；④别把偶然当规律。
- **假设-验证**：给出上轮假设时，逐条判 `confirmed/refuted/inconclusive` 并给本批证据；数据不足以判 → `inconclusive`（诚实，不硬判）。
- **落点分流**：人设条目候选进 `proposed_traits`；受众修正进 `proposed_audience`；打法/教训/红线/权重建议进 `advisory`（明确这些暂无自动落点，先当建议）。
- 输出严格 JSON（结构见 §3.1）。

---

## 5. 路由（`app/api/media.py`）

- `POST /media/persona/{pid}/l2-review`：调 `run_l2_cycle`，返回 JSON（`cycle_id` 或 `warn`）。带 `force` 表单参支持越过门槛。try/except 包裹防前端崩。
- `GET /media/review-cycle/{cid}`：报告详情页 `media_review_cycle.html`。
- 采纳复用现成路由：
  - 候选人设条目 → `POST /media/persona/{pid}/interview/adopt`，`source="l2_review"`。**白名单（`api/media.py:217`）加 `l2_review`**（沿用命名约定：persona_trait 表用英文 slug，新增 source 必须进白名单否则被回落成 interview）。
  - 受众修正 → 受众页现成采纳/编辑路由（`POST /media/audience/adopt` 或字段编辑）。
- 历史轮次列表：进人设页复盘区（见 §6）或 `GET /media/review-cycles`（可选，先内嵌人设页）。

---

## 6. UI

### 6.1 人设页复盘区（`media_persona.html`）

现有"让 AI 复盘"（功能B 学改稿）区已建立"复盘区"模式，照抄扩展：

- 加「🔄 跑一轮周期复盘」按钮 + 简短说明（"汇总上次之后新发的内容，找规律、验假设"）。
- 点击 → AJAX POST → 门槛不足弹提示（可"仍要跑"= force）；成功跳/展开报告。
- 下方历史轮次列表：`第 N 轮 · 纳入 X 条 · 日期`，点进详情页。
- **JS 不把 SVG 图标塞进字符串**（`innerHTML='{{ ic.icon(...) }}'` 老坑，会 SyntaxError）；失败路径存 `const orig=btn.innerHTML` 还原。

### 6.2 报告详情页（`media_review_cycle.html`，新）

- **头部**：第 N 轮 · 周期 `period_start→period_end` · 纳入 X 条 · 成本 · 模型。
- **汇总卡**：条数 / 各指标均值中位 / 爆款·flop 数。
- **规律列表**：pattern + evidence + confidence 徽标。
- **假设台账**（核心）：
  - "上轮假设的验证结果"：每条上轮假设 + verdict（confirmed 绿 / refuted 红 / inconclusive 灰）+ 本批证据。
  - "本轮新提出的假设"：statement + how_to_test + basis。
- **候选资产（人拍板）**：人设条目候选（逐条采纳按钮，走 interview/adopt）、受众修正候选（跳受众页或就地采纳）。
- **建议（advisory）**：打法/教训/红线/权重建议，标"⚙️ 暂无自动落点，待打法库/权重 UI 建成后可落地"。
- Markdown 用现成 marked.js 渲染。

---

## 7. 优雅降级（沿用决策引擎哲学）

L2 产出的落点盘点：

| L2 产出 | 落点 | 本轮处理 |
|---|---|---|
| 候选人设条目 | `media_persona_trait`（人拍板 adopt） | ✅ 复用现成闸 |
| 受众修正 | `media_audience` | ✅ 复用现成路由 |
| 假设 / 规律 / 汇总 | `media_review_cycle`（本轮新建） | ✅ 自己存、结转下轮 |
| 候选打法 | 打法库🅑 未建 | ⚙️ advisory 文字，待落点 |
| 权重调整建议 | 决策引擎权重硬编码、无 UI | ⚙️ advisory 文字，待落点 |
| 候选教训 / 红线 | 无专表 | ⚙️ advisory 文字，待落点 |

等打法库 / 权重 UI 建成，把 L2 的对应路接上去即可（就像决策引擎权重从 0 改上去）。L2 现在就能真正驱动"人设 + 受众"两个已建资产进化，其余沉淀成建议不浪费。

---

## 8. 质量与测试

- **纯计算可单测**：`_summarize_metrics`（均值/中位/爆款计数）、周期范围推导（`_prev_cycle` + period_start/end）、数据门槛（< 5 返回 warn 不写库 / force 越过）、新假设补 id、`hypotheses_tested` 结构容错。
- **AI 部分 human-in-loop**：候选绝不自动写库；用测试同款签名 cookie 登录、可 stub / 真调 DeepSeek 验端到端。
- **四条标准动作**：人拍板 / 诚实（evidence 引原文、样本少标 low）/ 只给精华（软上限）/ 成本可见（log_injection）。
- **回归**：不动 L1 `review_content` / 功能B / 决策引擎逻辑；trait adopt 白名单只**增** `l2_review` 不改既有分支。
- **零 migration**：新表走 SCHEMA。

---

## 9. 落点清单（实施时定位用）

- `app/database.py`：`SCHEMA` 加 `media_review_cycle` 表。
- `app/services/media_review_cycle.py`：新建（`run_l2_cycle` / `list_cycles` / `get_cycle` / `_summarize_metrics` / `_prev_cycle` / `L2_SYSTEM`）。
- `app/api/media.py`：加 `POST /media/persona/{pid}/l2-review`、`GET /media/review-cycle/{cid}`；trait adopt 白名单（`:217`）加 `l2_review`。
- `app/templates/media_persona.html`：复盘区加 L2 按钮 + 历史轮次列表。
- `app/templates/media_review_cycle.html`：新建报告详情页。
- 测试：`tests/test_media_review_cycle.py`（纯函数）+ `tests/test_media_routes.py`（路由）。

---

## 10. 开放问题 / 留后

- **数据门槛 5 条**是初值，用户实际发文频率低可下调；报告标注样本量让人自己判可信度。
- **纳入内容的时间判定**：以 metrics `snapshot_at` 还是 content 发布时间为准，实施时对齐 L1 口径确认（避免同一条被两轮重复纳入——用 `content_ids` 去重 + period 边界）。
- **advisory 的 weight_suggestion** 现在纯文字；打法库 / 权重 UI 建成后接为可采纳动作。
- **L3 阶段复盘**：L2 攒够几轮规律后触发人设进化，另起一轮（表已留 `level` 字段）。
