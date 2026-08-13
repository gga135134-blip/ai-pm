# L3 锚点策展设计（L3-v2：人设进化补完整）

**日期：** 2026-08-14
**状态：** 设计已定，待写实施计划
**触发词：** 「接 AI-PM」

---

## 1. 目标与定位

L3 阶段复盘（merge a06a02b）落地时砍掉了原设计三类进化动作里的第三类「anchor 调整」。本轮补上：让 L3 也能策展**生意锚点**（`media_anchor` 的 validating/proven/dropped 状态），使 L3 成为完整的"人设进化"复盘——**阶段 + trait + 锚点**三段齐全。

**一句话：** 把「锚点策展」并进现有 L3 run，成为报告页第四段。AI 对每个 active 锚点给定性观察 + 建议动作（跑通/放弃/保持），人点应用改 status。

### 1.1 诚实前提（brainstorm 拍板 A）

trait 靠 L2 规律印证、阶段靠真实播放/涨粉数据——都有数据基础。但**锚点是"生意押注"（卖课/陪跑营/带货），跑没跑通取决于转化/成交，而系统里没有按锚点记的转化数据**（expenses 台账、项目收款是项目侧的，没跟锚点挂钩）。所以 L3 **不假装用数据判锚点**：它把每个 active 锚点摆出来 + 能拿到的弱信号 + AI 定性观察，**人知道真实成交、由人拍板**。本质是"AI 帮你把该看的信息聚齐、结构化提醒你别让锚点状态烂着没人更新"。

### 1.2 三条设计原则（用户强调，贯穿全程）

- **不跟前面打架**：复用现成 `media_anchor` 表 + `/media/anchor` 页（那页本就能手动改状态，L3 不替代、只在复盘时聚齐提醒）。锚点应用**镜像已落地的 trait 策展**同一套人拍板模式。决策引擎照常消费更新后的 status（proven 1.0 / validating 0.7 / dropped 0.3 权重 + dropped_drift 护栏），**逻辑一行不改**。
- **不太麻烦人**：AI 只对**值得动的**锚点提 `to_proven`/`to_dropped`（有应用按钮）；没到火候的给 `keep`（只观察、无按钮）。人只点认同的。软上限每类 ≤3。
- **有边界**：只看 `validating` + `proven`，**不碰 `dropped`**（放弃一门生意是很贵的长周期验证——decision engine 的 dropped_drift 护栏就是这哲学；复活 dropped 是慎重手动动作，L3 不主动提议）。应用校验锚点归属 + 目标状态白名单；全程人拍板。

---

## 2. 范围（做 / 不做）

**做：**
- `media_phase_review` 加一列 `anchor_actions`（idempotent ALTER 迁移）。
- `run_l3_review` 里多产一段「锚点观察」：加载 active 锚点 + 纯计算弱信号 + AI 给 anchor_actions（候选绝不自动应用）。
- 应用路由 `apply-anchor`：人点击改 `media_anchor.status`（校验归属 + 目标白名单）。
- 报告页第四段「🎯 锚点策展」。

**不做（YAGNI / 边界）：**
- **锚点成交/转化数据管道**（brainstorm 拍板不做 B）——没有就诚实定性，不新建数据管道。
- **复活 dropped 锚点**——L3 不提议；要复活去 `/media/anchor` 手动改。
- **锚点↔受众关联**（target_audience_ids，phase2 已砍）——不引入。
- **独立锚点复盘入口**——并入 L3 run，不新增按钮。

---

## 3. 数据模型

`media_phase_review` 加一列（`app/database.py` 的 `MIGRATIONS` 追加一条 idempotent ALTER；表已存在，故**非零迁移**）：

```python
"ALTER TABLE media_phase_review ADD COLUMN anchor_actions TEXT DEFAULT '[]'",
```

`anchor_actions` JSON 结构（AI 产出，人拍板）：
```json
[{"anchor_id": "a-xxx", "name": "AI落地陪跑营", "type": "service",
  "from_status": "validating", "action": "to_proven",
  "observation": "近 6 条选题在往这个锚点靠，受众关注度在起来",
  "reason": "attention 信号强，可考虑标为跑通（你确认成交后）"}]
```
- `action` ∈ `to_proven` / `to_dropped` / `keep`。
- `keep` 只展示观察，无应用按钮。

---

## 4. 服务（`app/services/media_phase_review.py`）

### 4.1 纯计算：`count_topics_serving(anchor_id, topics) -> int`
统计"近期有几条选题在往这个锚点靠"——`topics` 里 `anchor_ids`(JSON list) 含 `anchor_id` 的条数。纯函数，可单测。（`anchor_ids` 是 `media_topic` 已有列，AI 打标存的。）

### 4.2 `run_l3_review` 扩展
在现有加载 traits 之后，追加：
1. 加载该 persona `media_anchor` 里 `status IN ('validating','proven')` 的锚点（**不含 dropped**）。
2. 加载该 persona 的 `media_topic`（供 `count_topics_serving` 算弱信号）。
3. 每个 active 锚点算 `serving_count`，拼进 prompt 新段「当前生意锚点 + 弱信号」。
4. `L3_SYSTEM` 加锚点策展规则（见 4.3）。AI 输出里多一个 `anchor_actions` 字段。
5. **校验**：`anchor_id` 必须在 active 锚点集合、`action` ∈ (`to_proven`,`to_dropped`,`keep`)；过滤瞎编 id / 非法 action。补 `name`/`type`/`from_status` 便于报告展示。
6. 存进 `media_phase_review.anchor_actions`。

锚点段随 L3 run 一起产，**共用现有门槛**（`L3_MIN_L2_CYCLES=3`，不足 force 越过也能看锚点观察）。锚点不依赖 L2 规律，即使阶段/trait 段没料，只要有 active 锚点就有观察。

### 4.3 `L3_SYSTEM` 追加（锚点策展段）
- **诚实**：你**看不到真实成交/转化**，只能看到 attention 弱信号（多少选题在往锚点靠）+ 锚点定义。**别假装判定生意成败**——给定性观察 + 建议，明说"最终成没成要人按真实成交确认"。
- **只给精华**：锚点动作每类 ≤3；没到火候给 `keep` 别硬推。
- **只看给定的 active 锚点，不碰 dropped，不造新锚点**（造锚点是 `/media/anchor` 页的活）。
- 每条给 `anchor_id`（必来自清单）+ `action`(to_proven/to_dropped/keep) + `observation` + `reason`。
- 输出 JSON 里 `anchor_actions` 追加进现有结构。

`get_phase_review` 的 `_JSON_FIELDS` 加 `anchor_actions`（解析成 list）。

---

## 5. 应用路由（`app/api/media.py`，镜像 apply-trait）

`POST /media/phase-review/{rid}/apply-anchor`（Form `anchor_id`, `target_status`）：
1. `get_phase_review` 拿 `rev`；`target_status` ∈ (`proven`,`dropped`) 才继续（`validating` 不作为应用目标——那是初始态）。
2. 校验锚点属于 `rev.persona_id`：`SELECT ... FROM media_anchor WHERE id=? AND persona_id=?`（谓词里校归属，防越权，同 apply-trait）。
3. `UPDATE media_anchor SET status=? WHERE id=?`。
4. 跳回报告页。

---

## 6. UI（报告页 `media_phase_review.html` 第四段）

在 trait 策展段之后加「🎯 锚点策展」module：
- 每条 `anchor_actions`：`[{{ANCHOR_STATUS_LABELS[from_status]}}] name（type）` + observation + reason。
- `action == to_proven`：绿色「标为已跑通」按钮（form post apply-anchor，`target_status=proven`，二次确认）。
- `action == to_dropped`：红色 `var(--down)`「标为已放弃」按钮（`target_status=dropped`，二次确认——放弃是重决策）。
- `action == keep`：只显示观察，无按钮。
- 空则 `<div class="empty">这轮没提锚点动作</div>`。
- LLM-origin 字段（observation/reason/name）走 `{{ }}` autoescape，无 `|safe`；类名/变量用现有（`.module/.mh/.inner/.btn/.empty`、`var(--down)`）。

（人设页 L3 区块不用改——锚点并入现有 L3 run，触发/历史列表复用。）

---

## 7. 质量与测试

- **纯计算可单测**：`count_topics_serving`（anchor_ids 含/不含）。
- **run 扩展测**：AI 返回 anchor_actions 含瞎编 id + 非法 action → 过滤只留合法；dropped 锚点不进候选（只加载 validating/proven）；候选存库不自动改 media_anchor.status。
- **apply-anchor 路由测**：合法 to_proven/to_dropped 改 status；非法 target（如 validating/瞎写）不改；跨 persona 锚点不改（归属校验）。
- **AI human-in-loop**：候选绝不自动应用。
- **四条标准动作**：人拍板 / 诚实（明说看不到成交）/ 只给精华 / 成本可见（沿用 L3 已有 log_injection，不重复记）。
- **回归**：不动决策引擎 / L1/L2 / trait·phase 已有逻辑；只**读** media_anchor/media_topic，只在人点 apply-anchor 时**写** media_anchor.status。
- **迁移**：idempotent ALTER（`try/except` 应用，已加过忽略）。
- **浏览器冒烟**（controller 亲跑）：报告页第四段锚点策展渲染、应用按钮在、无 Jinja/500。

---

## 8. 落点清单（实施用）

- `app/database.py`：`MIGRATIONS` 加一条 `ALTER TABLE media_phase_review ADD COLUMN anchor_actions ...`。
- `app/services/media_phase_review.py`：`count_topics_serving`（新纯函数）；`run_l3_review` 加载 active 锚点 + topics + 弱信号 + prompt 段 + 校验 + 存 `anchor_actions`；`L3_SYSTEM` 加锚点段；`get_phase_review` `_JSON_FIELDS` 加 `anchor_actions`；`_build_l3_prompt` 加锚点段参数。
- `app/api/media.py`：加 `POST /media/phase-review/{rid}/apply-anchor`。
- `app/templates/media_phase_review.html`：加第四段「🎯 锚点策展」。
- 测试：`tests/test_media_phase_review_calc.py`（+count_topics_serving）、`tests/test_media_phase_review_run.py`（+anchor 校验/不自动应用）、`tests/test_media_phase_review_apply.py`（+apply-anchor 归属/白名单）。

---

## 9. 开放问题 / 留后

- **弱信号目前只有 serving_count**（选题靠拢数）——以后若给锚点接了真成交数据，AI 判定可从"attention"升级为"conversion"，anchor_actions 的 evidence 更硬。
- **锚点↔受众关联**（target_audience_ids）仍未建；建了后弱信号可加"目标受众付费意愿"。
- **复活 dropped**：始终手动（/media/anchor），L3 不碰。
