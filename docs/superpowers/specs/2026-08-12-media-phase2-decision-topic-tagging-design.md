# 决策引擎迭代 · AI 给话题打受众/锚点标 — 设计

**日期：** 2026-08-12
**性质：** 决策引擎 V1（`media_decision.py`）的第一次迭代。把 `audience_hit` / `anchor_distance` 两项打分从"中文字面重叠猜测"升级成"AI 语义标注"。
**依赖：** 决策引擎 V1（merge 1824deb）、受众画像 + 生意锚点富表（merge 18115fa）已上线。

---

## 1. 背景与问题

决策引擎 V1 给选题池话题打分，其中：

- `audience_hit`：话题文本 vs 受众 segment 的"焦虑 + 原话"做中文 2-gram 字符集 Jaccard 重叠。
- `anchor_distance`：话题文本 vs 锚点的"名字 + 价值主张"做同样的重叠。

**问题**：字面重叠是弱代理。Jaccard 值通常很小（很少超 0.3），且字面不同、语义相同的情况完全抓不到（"减脂" vs "瘦身"）。这让两项本该有力的因子长期打小分、判断不准。

**目标**：让 AI 在上游（推选题/补标时）直接判断"这话题命中哪个 segment、服务哪个锚点"，把结果标注（id 列表）存到话题上。决策引擎优先读语义标，读不到才回落字面重叠。

**范围界定（YAGNI）**：本次只做 `audience_hit` 和 `anchor_distance` 两项的语义化。`fit`（定位+一致）维持用人设条目重叠，不动。`heat` 等其它软肋不在本次范围。

---

## 2. 设计原则（锁死，不可违背）

1. **决策引擎保持纯函数**：`media_decision.py` 不引 AI、不引 DB。语义标在推选题/补标时算好存库，引擎只消费存好的标。这是引擎可单测、不崩的根基。
2. **只打勾不打分**（用户拍板）：AI 只返回"命中哪些 id"，不给命中强度。得分强度来自资产自身已存字段（segment 的 `pay_willingness`、anchor 的 `status`）——"强度早在资料里，AI 别多嘴"。
3. **诚实不编造**：AI 只能从给定的 id 里选，不沾就返回空；标注提示词带诚实红线（只标真命中、不硬凑）。返回的 id 必须校验，瞎编的丢弃。
4. **成本可见**：每次 AI 标注调用记 `log_injection`。
5. **向后兼容 + 优雅降级**：老话题不变，没标的照旧走字面重叠；纯 ADD COLUMN 迁移，零风险。

---

## 3. 数据模型

`media_topic` 表加三列（`MIGRATIONS` 里 `ALTER TABLE ... ADD COLUMN`，带默认值，向后兼容）：

| 列 | 类型/默认 | 含义 |
|---|---|---|
| `audience_ids` | `TEXT DEFAULT '[]'` | 命中的 `media_audience.id` 列表（JSON 数组字符串） |
| `anchor_ids` | `TEXT DEFAULT '[]'` | 服务的 `media_anchor.id` 列表（JSON 数组字符串） |
| `tagged` | `INTEGER DEFAULT 0` | 是否已被 AI 标注过。**关键防呆位** |

**为什么需要 `tagged`**：空列表 `[]` 有两种截然不同的含义，决策引擎行为必须区分：

- `tagged=1` 且 `audience_ids=[]` → AI 看过了，判定这话题**确实不沾任何受众** → `audience_hit` 记 0（诚实，不回落）。
- `tagged=0` 且 `audience_ids=[]` → 这话题**从没被标过**（老话题/刚手动加的） → 回落到字面重叠（别冤枉它）。

---

## 4. AI 标注（`media_ai.py`）

### 4.1 公共 helper

- `_build_asset_menu(db, persona_id)` → 返回：
  - 受众列表（`id, segment, who, anxiety, language, pay_willingness`，`status='active'`）
  - 锚点列表（`id, name, type, value_prop, status`，只取 `status IN ('validating','proven')`——`dropped` 的锚点是已放弃的生意方向，不该再往上标）
  - 拼好的"资产菜单"文本（供塞进提示词，每条带 id）
  - 合法 id 集合（`valid_aud_ids`, `valid_anc_ids`）
- `_clean_ids(raw, valid_set)` → 把 AI 返回的 id 列表过滤成"只保留合法 id"的列表（纯函数，可单测）。

### 4.2 推选题路径（零额外成本）

改 `recommend_topics`：

1. 调 `_build_asset_menu`，把资产菜单塞进推选题提示词。
2. `RECOMMEND_SYSTEM` 输出格式加 `"audience_ids":[...],"anchor_ids":[...]`，并说明：只能从给定的资产 id 里选，命中就填 id，不沾就留空数组；诚实不硬凑。
3. 入库时对每个话题 `_clean_ids` 校验两个 id 列表，存入 `audience_ids`/`anchor_ids`，置 `tagged=1`。
4. 同一次 AI 调用完成推选题 + 打标，不额外花钱。

**边界**：人设零受众/零锚点 → 菜单为空，提示词说明"当前无资产可标"，AI 返回空数组即可，不崩。

### 4.3 补标路径

新函数 `tag_topics(db, persona_id, model="auto")`：

1. 调 `_build_asset_menu`。
2. 查该人设 `status='pool'` 且 `tagged=0` 的话题（title + puzzle）。无则直接返回 count=0。
3. 走 `TAG_SYSTEM`（诚实红线：只标真命中、不硬凑、只返回给定 id、只输出 JSON）。提示词给资产菜单 + 待标话题列表，要求返回 `[{topic 标识, audience_ids, anchor_ids}]`。
4. 解析 + `_clean_ids` 校验，逐条 `UPDATE media_topic SET audience_ids=?, anchor_ids=?, tagged=1`。
5. `log_injection` 记成本。返回 `{ok, count, cost, model, error}`。

**话题标识对应**：提示词里给话题带上稳定序号或 id，让 AI 回填时能对上。倾向传 topic.id 让 AI 原样带回，回填时按 id 匹配（防错位）。

---

## 5. 路由（`media.py`）

- **新增** `POST /media/topics/tag`：调 `tag_topics(db, persona_id, model)`，返回 count/cost 的 JSON。try/except 兜底防前端崩。
- **选题页** `media_topics.html`：在「🧮 决策排序」按钮旁加「🏷️ AI标注」按钮 + AJAX（调 `/media/topics/tag`，完成后刷新/提示 N 条已标）。沿用现有 finesse 类与 escapeHtml 惯例，**不把 SVG 图标塞进 JS 字符串**（避开已知 `<script>` 崩坑）。
- **rank 路由**：加载话题时对 `audience_ids`/`anchor_ids` 做 `json.loads`，带上 `tagged` 字段，一并放进传给引擎的话题 dict。

---

## 6. 决策引擎（`media_decision.py`，仍纯函数）

`score_topic` 里 `audience_hit` 与 `anchor_distance` 改成三分支。引擎从 `ctx["audiences"]` / `ctx["anchors"]` 按 id 建 `id→dict` 映射查全字段。

### audience_hit
- **tagged 且有命中**：`value = max(命中 segment 的 pay_willingness/5)`。note：`AI判定命中'segment名'，付费意愿★★★`。
- **tagged 但空**：`value = 0`，note：`AI判定未明显命中受众`。
- **未标**（`tagged=0`）：回落现有 2-gram 重叠逻辑（`overlap × pay_willingness/5`）。

### anchor_distance
- **tagged 且有命中**：`value = max(命中锚点的状态权重)`，状态权重 `proven→1.0 / validating→0.7 / dropped→0.3`。note：`AI判定服务锚点'name'（proven）`。
- **tagged 但空**：`value = 0`，note：`AI判定离生意锚点较远`。
- **未标**：回落现有重叠逻辑。

### 兼容处理
- 引擎读 `topic.get("audience_ids")` / `anchor_ids` 时容错：既接受已解析的 list，也接受 JSON 字符串（防调用方忘了解析）。
- 引擎读 `topic.get("tagged")`，缺省当 0（老调用方/老数据无缝）。
- `fit`、`material_ready`、`risk`、`fatigue`、`dup_penalty`、C 类三项**一律不动**。

---

## 7. 测试

**纯函数（无 AI，可直接单测）：**
- `test_media_decision.py` 新增：
  - tagged 话题 audience_hit 用 pay_willingness 而非重叠
  - tagged 话题命中多 segment 取最高付费意愿
  - tagged 但空 → audience_hit=0（不回落）
  - 未标话题 → 回落字面重叠（老行为不变）
  - anchor 状态权重（proven > validating > dropped）
  - id→dict 查找 + audience_ids 传 JSON 字符串时的容错
- `_clean_ids` 过滤瞎编 id 的纯函数测（合法保留、非法丢弃、空输入）。

**路由 / AI 能力（沿用项目一贯做法）：**
- `test_media_routes.py` 新增 `/media/topics/tag` 路由测（测试签名 cookie，无真实密码）。AI 真调部分走浏览器 live 验证。

**浏览器 live 验证：** `preview_start` 起本地 server，用测试同款签名 cookie 登录，选题页点「🏷️ AI标注」→ 看话题被打上受众/锚点标、`tagged` 置 1；再点「🧮 决策排序」→ 决策报告里 audience_hit/anchor_distance 的 note 变成"AI判定命中…"而非字面重叠；console 无错、无 `<script>` 崩。

---

## 8. 代码落点小结

| 文件 | 改动 |
|---|---|
| `app/database.py` | `MIGRATIONS` +3 列（audience_ids / anchor_ids / tagged） |
| `app/services/media_ai.py` | `_build_asset_menu` / `_clean_ids` 新 helper；`recommend_topics` 折入打标；`RECOMMEND_SYSTEM` 输出格式加两 id 字段；新 `tag_topics` + `TAG_SYSTEM` |
| `app/services/media_decision.py` | `score_topic` 的 audience_hit / anchor_distance 三分支；id→dict 映射；容错解析 |
| `app/api/media.py` | 新 `POST /media/topics/tag`；rank 路由解析两 id 列 + tagged |
| `app/templates/media_topics.html` | 「🏷️ AI标注」按钮 + AJAX（不塞 SVG 进 JS） |
| `tests/test_media_decision.py` | +6 左右纯函数测 |
| `tests/test_media_routes.py` | +1 路由测 |

---

## 9. 非目标（本次不做）

- `fit` / `heat` 语义化或接真实数据源（另立迭代）。
- 打法库 / metrics（w3/w4 数据源）。
- 锚点 ↔ segment 关联建模。
- 自动定时补标（用户手动点按钮即可）。
