# L3 阶段复盘设计（人设进化层）

**日期：** 2026-08-14
**状态：** 设计已定，待写实施计划
**触发词：** 「接 AI-PM」

---

## 1. 目标与定位

补上复盘三层的最顶层 **L3 阶段复盘**。现状：

- **L1 单条复盘** — 已有。这一条为什么成/败。
- **L2 周期复盘** — 已有（merge 8383445）。近 N 条有什么规律 + 假设-验证闭环。
- **L3 阶段复盘** — 本轮做。**元复盘**：回看一个阶段积累的 L2 规律 + 数据趋势，回答"**人设该进化了吗**"。

**L3 的独特价值：** 它是唯一能**真动人设**的复盘层——建议切换阶段（改 `persona.current_phase`，决策引擎权重随之切换）、策展 trait 注册表（归档陈旧 / 晋升被反复印证的）。全部人拍板。

**一句话：** 人设页点「🧭 阶段复盘」→ 系统回看攒够的 L2 轮次（规律 + 假设结果 + 趋势）→ 摆出"阶段退出信号"的实际数据 + AI 解读 → 建议「进下一阶段 / 原地」+「哪些 trait 该归档/晋升」→ 人逐条拍板真动人设。

---

## 2. 范围（本轮做 / 不做）

**做（brainstorm 拍板）：**
- 新表 `media_phase_review` 存每轮 L3。
- 服务 `media_phase_review.py`：定纳入范围、门槛、纯计算趋势 + 阶段退出信号、调 AI 给阶段建议 + trait 策展动作，候选绝不自动应用。
- **两类进化动作**：①阶段切换建议（前进/原地）②trait 归档/晋升（策展现有注册表）。
- 应用路由（人拍板）：切阶段 → `persona.current_phase`；trait 归档/晋升 → `trait.status`/`confidence`。
- 触发按钮 + 报告详情页 + 人设页历史列表（跟 L2 区块并列）。

**不做（YAGNI / 边界）：**
- **anchor 调整**（生意锚点状态）——与受众/锚点资产耦合更深，单独一轮再做。
- **L3 造新 trait**——造新是 L2 的活（`proposed_traits`）。L3 只**策展现有**注册表，分工干净。
- **阶段倒退**（转→涨→冷）——异常情况，交给人手动改 current_phase，L3 只建议前进或原地。
- **定时/自动触发**——只手动按钮。
- **退出信号阈值设置 UI**——硬编码常量（像决策引擎 WEIGHTS），以后要调再说。

---

## 3. 数据模型

新表 `media_phase_review`（走 `app/database.py` 的 `SCHEMA`，`CREATE TABLE IF NOT EXISTS`，**零 migration**）。L3 产出（阶段建议 + trait 动作）与 L2（规律/假设）差别大，单独一张表比塞进 `media_review_cycle` 的通用列干净；`media_review_cycle` 保持 L2 专用（其 `level` 字段实际只用 'L2'）。

```sql
CREATE TABLE IF NOT EXISTS media_phase_review (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    seq INTEGER DEFAULT 1,             -- 第几轮 L3，per persona 递增
    phase_from TEXT DEFAULT '',        -- 复盘时的 current_phase
    l2_cycle_ids TEXT DEFAULT '[]',    -- 纳入的 L2 轮 id JSON
    metrics_trend TEXT DEFAULT '{}',   -- 期间趋势纯计算 JSON
    phase_signals TEXT DEFAULT '[]',   -- 阶段退出信号实际数据 JSON
    phase_reco TEXT DEFAULT 'stay',    -- 'advance' | 'stay'
    phase_to TEXT DEFAULT '',          -- advance 时的建议目标阶段
    phase_reason TEXT DEFAULT '',      -- AI 理由
    trait_actions TEXT DEFAULT '[]',   -- trait 策展动作 JSON
    cost REAL DEFAULT 0,
    model TEXT DEFAULT '',
    generated_by TEXT DEFAULT 'ai',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

### 3.1 JSON 字段结构

**`metrics_trend`**（纯计算，非 AI）：每个纳入 L2 轮的均值序列，供报告页看趋势。
```json
{"series": [
  {"seq": 1, "avg_views": 8000, "avg_new_fans": 30, "hit_count": 0},
  {"seq": 2, "avg_views": 12000, "avg_new_fans": 55, "hit_count": 1}
]}
```

**`phase_signals`**（纯计算）：当前阶段"退出信号"的实际数据。
```json
[{"signal": "累计爆款数", "value": 2, "ref": 1, "met": true},
 {"signal": "最近一轮均值播放", "value": 12000, "ref": 3000, "met": true}]
```

**`trait_actions`**（AI，人拍板）：
```json
[{"trait_id": "t-xxx", "dimension": "signature", "content": "爱用反问开场",
  "action": "promote", "evidence": "近3轮 L2 有2轮规律印证反问开场完播高",
  "reason": "反复被数据印证，值得提置信"}]
```

---

## 4. 阶段退出信号（显式定义，纯计算）

阶段序列：`冷启动` → `涨粉` → `转化`（`转化` 为终点，无 exit）。信号是**参考线不是硬闸**——摆数据，AI 结合 L2 规律解读，人拍板。阈值做成常量，可调。

**`PHASE_ORDER = ["冷启动", "涨粉", "转化"]`**

**常量（初值，可调）：**
- `L3_MIN_L2_CYCLES = 3`
- `COLD_VIEWS_BASELINE = 3000`（冷启动毕业的均值播放参考线）
- `COLD_HIT_MIN = 1`（冷启动毕业的累计爆款参考）
- `GROWTH_CONFIRMED_MIN = 2`（涨粉毕业的累计 confirmed 假设参考）

**冷启动 → 涨粉（方向已验证）：**
- `累计爆款数`：Σ 各纳入 L2 轮 `metrics_summary.hit_count` ≥ `COLD_HIT_MIN`
- `最近一轮均值播放`：最后一个纳入 L2 轮 `metrics_summary.avg.views` ≥ `COLD_VIEWS_BASELINE`

**涨粉 → 转化（涨粉稳 + 有可复制打法）：**
- `新增粉丝持续为正`：每个纳入 L2 轮 `metrics_summary.avg.new_fans` > 0（全为正 → met）
- `累计已验证假设`：Σ 各纳入 L2 轮 `hypotheses_tested` 里 `verdict=='confirmed'` 条数 ≥ `GROWTH_CONFIRMED_MIN`

**转化：** 终点阶段。`phase_exit_signals` 返回空信号 + `phase_reco` 只可能 'stay'；L3 此阶段仅做 trait 策展。

`phase_exit_signals(phase_from, l2_cycles) -> list[dict]` 纯函数，从 L2 轮的 `metrics_summary`/`hypotheses_tested` 算出上述信号的 value/ref/met。

---

## 5. 服务 `media_phase_review.py`（新）

### 5.1 `run_l3_review(db, persona_id, model="auto", force=False) -> dict`

1. **定纳入范围**：查上一轮 L3（`MAX(seq)`）→ 纳入"上轮之后新建的 L2 轮"。取该 persona 的 `media_review_cycle`（level='L2'）中 `created_at > 上轮 L3.created_at`（无上轮 → 全部）。`seq = 上轮+1`。`phase_from = persona.current_phase`。
2. **门槛**：纳入 L2 轮数 < `L3_MIN_L2_CYCLES` 且 not force → `{"ok": False, "warn": "才 N 轮 L2，还看不出阶段级趋势，建议攒到 ~3 轮再跑", "count": N}`，不写库。
3. **纯计算**：`metrics_trend`（各轮均值序列，从 `metrics_summary` 取）+ `phase_signals`（§4）。
4. **load 当前 active traits**（`media_persona_trait status='active'`）供 AI 策展。
5. **组 prompt + 调 AI**：`ask_ai(prompt, task_type="media_phase_review", system_prompt=L3_SYSTEM, json_mode=True)`。prompt 含：phase_from + 阶段目标 + 退出信号实际数据 + 区间 L2 规律汇总（各轮 patterns + confirmed 假设）+ 当前 trait 注册表清单。
6. **解析**：`phase_reco`（advance/stay）/`phase_to`/`phase_reason`/`trait_actions`。**校验**：`phase_to` 只能是 `PHASE_ORDER` 里 `phase_from` 的下一个（否则回落 stay，防 AI 乱跳/倒退）；`trait_actions` 的 `trait_id` 必须在当前 active traits 里（过滤 AI 瞎编 id，沿用 L2 `_clean_ids` 教训）；`action` 只认 archive/promote。
7. **候选绝不自动应用**：全存进 `media_phase_review` 行。
8. **成本可见**：`log_injection(db, "", "media_phase_review", [l2 ids], tokens)`。
9. 写行，返回 `{"ok": True, "review_id", "seq", "count", "cost", "model"}`。

### 5.2 辅助
- `list_phase_reviews(db, persona_id)` / `get_phase_review(db, id)`（JSON 解析）。
- `summarize_trend(l2_cycles) -> dict` 纯函数。
- `phase_exit_signals(phase_from, l2_cycles) -> list` 纯函数。
- `_next_phase(phase_from) -> str | None`（PHASE_ORDER 下一个，终点返回 None）。

### 5.3 `L3_SYSTEM` 提示词要点
- 角色：站在阶段高度，看一个阶段积累的规律 + 趋势，判断人设是否该进化。
- **四条标准动作**：①只提建议/动作候选，绝不假装能改人设；②诚实——阶段建议必须对着退出信号的实际数据说话，trait 动作必须引具体 L2 规律做 evidence，够不着就别硬推；③只给精华（trait 动作 ≤ 每类 3 条，别把整个注册表翻个底朝天）；④别把偶然当趋势——一轮好不代表阶段到了。
- **阶段建议**：**必须以退出信号的实际数据（账号真实已发数据算出）为主要依据**，L2 规律作为佐证；**多数信号未达参考线就倾向 stay**——人设进化要账号真实数据达到一定程度才发生，绝不凭 AI 语感激进推进。数据不足支撑前进就 stay（诚实）。只前进或原地，绝不建议倒退。最终切换与否由人点按钮决定。
- **trait 策展**：只对给定的现有 trait 注册表做 archive/promote，**不造新**（造新是 L2 的活）。archive=陈旧/被新规律矛盾；promote=被近几轮 L2 规律反复印证。每条带 trait_id + evidence + reason。
- 严格 JSON（结构见 §3.1）。

---

## 6. 应用路由（人拍板真动人设，`app/api/media.py`）

- `POST /media/persona/{pid}/l3-review`（Form `force: int = 0`）→ `run_l3_review` → JSON（try/except 包裹）。
- `GET /media/phase-review/{rid}` → 报告页 `media_phase_review.html`（not found 跳 `/media/persona`）。
- `POST /media/phase-review/{rid}/apply-phase` → 读该行 `phase_to`，校验是 `PHASE_ORDER` 里 `persona.current_phase` 的下一个才应用（`UPDATE media_persona SET current_phase=phase_to`），跳回报告页。**幂等**：已是目标阶段则无操作。
- `POST /media/phase-review/{rid}/apply-trait`（Form `trait_id`, `action`）→ archive：`UPDATE media_persona_trait SET status='archived'`；promote：`confidence=MIN(5,confidence+1)`。校验 trait 属于该 persona。跳回报告页。
- 删除（对称 L2，可选但推荐）：`POST /media/phase-review/{rid}/delete` → 删行（L3 不参与去重铁律，删了只是清记录；纳入的 L2 轮由 created_at 区间界定，删 L3 后下轮会重新纳入那些 L2 轮，行为合理）。

---

## 7. UI

### 7.1 人设页（`media_persona.html`）
在 L2「🔄 周期复盘」区块下方并列加「🧭 阶段复盘（L3）」区块：按钮 + 简短说明（"回看攒够的周期复盘，判断人设该不该进下一阶段"）+ 历史轮次列表（`第 N 轮 · 纳入 X 轮L2 · 日期` 链接）。AJAX 触发，门槛不足弹提示 + "仍要跑"（force）。**JS 不塞 SVG 进字符串**（存 orig 还原）。context 由 `persona_detail` 加 `l3_reviews = await list_phase_reviews(db, pid)`。

### 7.2 报告页（`media_phase_review.html`，新）
- 头部：第 N 轮 · 当前阶段 phase_from · 纳入 X 轮 L2 · 成本 · 模型。
- **阶段建议卡**：advance（→ phase_to）/ stay + phase_reason；advance 时「✅ 应用切换到 {{phase_to}} 阶段」按钮（二次确认，说明会改决策权重）。
- **退出信号表**：signal / 实际值 / 参考线 / 达标✓✗。
- **趋势**：各纳入 L2 轮的均值播放/新增粉丝序列（Markdown 表或简单条，别引新库；用现成风格）。
- **trait 策展列表**：每条 [归档/晋升] + trait 内容 + evidence + reason + 应用按钮（归档红色系 `--down`，晋升正色）。
- 「🗑 删除这轮」（对称 L2，二次确认）。
- LLM-origin 字段全走 `{{ }}` autoescape，无 `|safe`；类名用 base.html 现有的（`.module/.mh/.inner/.btn/.tag`，红色 `var(--down)`——**不是** `--danger`）。

---

## 8. 质量与测试

- **纯计算可单测**：`summarize_trend`、`phase_exit_signals`（各阶段信号 value/met 正确）、`_next_phase`、纳入范围（上轮 L3 之后的 L2 轮）、门槛（<3 warn 不写库 / force 越过）、AI 输出校验（phase_to 只能下一个、trait_id 过滤瞎编、action 白名单）。
- **应用路由测**：apply-phase 改 persona.current_phase（且拒绝非法目标）、apply-trait archive/promote 改 trait.status/confidence、delete 删行。
- **AI human-in-loop**：候选绝不自动应用；stub / 真调 DeepSeek 验端到端。
- **四条标准动作**：人拍板 / 诚实（对着信号数据 + 引 L2 规律）/ 只给精华 / 成本可见。
- **回归**：不动 L1/L2/功能B/决策引擎逻辑；只**读** L2 cycles + persona/trait，只在人点击时**写** persona/trait。
- **零 migration**：新表走 SCHEMA。
- **浏览器冒烟**（controller 亲跑）：人设页 L3 区块 + 报告页各区块渲染、应用按钮在，无 console/Jinja 错。

---

## 9. 落点清单（实施时定位用）

- `app/database.py`：`SCHEMA` 加 `media_phase_review` 表。
- `app/services/media_phase_review.py`：新建（`run_l3_review`/`list_phase_reviews`/`get_phase_review`/`summarize_trend`/`phase_exit_signals`/`_next_phase`/`PHASE_ORDER`/常量/`L3_SYSTEM`）。
- `app/api/media.py`：加 5 路由（l3-review 触发 / phase-review 详情 / apply-phase / apply-trait / delete）+ import。
- `app/templates/media_persona.html`：加 L3 区块（按钮 + 历史列表 + runL3 JS）；`persona_detail` context 加 `l3_reviews`。
- `app/templates/media_phase_review.html`：新建报告页。
- 测试：`tests/test_media_phase_review_calc.py`（纯函数）+ `tests/test_media_phase_review_run.py`（编排 + AI 校验）+ `tests/test_media_phase_review_routes.py`（触发 + 详情 + apply-phase/trait + delete）。

---

## 10. 开放问题 / 留后

- **退出信号阈值**（COLD_VIEWS_BASELINE 等）是拍脑袋初值，用户实际数据出来后调；报告页摆实际值让人自己判，阈值只当参考线。
- **anchor 调整**：单独一轮做（L3-v2）。
- **趋势可视化**：先用表/简单条，以后要 Chart.js 再说（财务页已有 Chart.js 先例）。
- **多阶段历史**：L3 只看"上轮 L3 之后的 L2 轮"；跨越多个 L3 的长期人设演化史，以后要看再做时间线。
