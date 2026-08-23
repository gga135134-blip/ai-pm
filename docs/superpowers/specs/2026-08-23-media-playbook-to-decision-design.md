# 打法库🅒接决策引擎 — 设计

日期：2026-08-23
状态：设计已与用户敲定，待写实施计划

## 背景与目标

决策引擎（`app/services/media_decision.py`）给选题池里的话题做可解释打分（纯计算、不调 AI、不碰 DB）。其中 `playbook` 因子自建库起一直恒为 0，note 写着"打法库未建"——但打法库（`media_playbook` 表）早已建好并有数据。

本轮目标：**让决策引擎打分时把"这条选题有没有一条已验证的打法可套"计入分数**，方式与现有"受众/锚点"因子完全同构（AI 打标 → 纯打分器读标）。

范围仅限 🅒（决策引擎）。🅓（打法接写稿）已于 2026-08-14 单独完成，不在本轮。

## 已敲定的设计决策（与用户逐条确认）

1. **匹配方式 = B（AI 打标）**：扩展现有 `tag_topics`，让 AI 顺带给每个话题挑**最贴的一条**打法写进 `topic.playbook_id`；纯打分器只读标。放弃纯文本 2-gram 重叠（中文匹打法名字太糙）。
2. **因子值按打法 status 加权**：`proven = 1.0`、`validating = 0.6`、没命中 = 0。与引擎里锚点的 `_ANCHOR_STATUS_W` 同一路子。
3. **三阶段权重**：`冷启动 1 / 涨粉 2 / 转化 2`。playbook 是"辅助杠杆"不是主导因子，量级对齐 `material_ready`，不盖过 fit/audience_hit/anchor_distance（3-4）。理由（用户）：当前爆款少、可复制打法本就不多，给小权重合适；库攒厚后 L2 复盘再抬（改 `WEIGHTS` 即可）。
4. **不做来源维度**：现在打法 `source` 只有 `legacy_mine`（自己的）。"对标/拆解别人爆款给更高分"这个点用户认可但**本轮不做**——系统还产不出对标打法，等以后单独一轮建产出+权重。本轮所有打法一视同仁按 status 加权。

## 架构与改动点

### 1. 数据（`app/database.py`）
- `media_topic` 加一列 `playbook_id TEXT DEFAULT ''`（可空）。走 MIGRATIONS 幂等 ALTER，重启自动跑。
- 一个选题最多挂**一条**打法（单值不是数组，守"只注一条"注意力纪律）。

### 2. AI 打标（`app/services/media_ai.py`）
- `_build_asset_menu`：加一段【打法库】，列出该人设 `status IN ('validating','proven')` 的打法：`id｜name｜结构摘要(structure 截断)｜status`。返回值加 `valid_pb` 集合。dropped/其它状态不进菜单。
- `TAG_SYSTEM`：加铁律"打法只挑一条最贴的、真命中才填、不沾留空、只能从给定 id 选"；输出格式加字段 `"playbook_id":""`（单个字符串）。
- `tag_topics`：解析每个话题的 `playbook_id`，用 `valid_pb` 清洗（越界/瞎编的 id 丢弃→空），写进 `media_topic.playbook_id`（与 audience_ids/anchor_ids 同一条 UPDATE）。`log_injection` 的注入 id 集合并入 playbook id。

### 3. 打分器（`app/services/media_decision.py`）
- 加常量 `_PLAYBOOK_STATUS_W = {"proven": 1.0, "validating": 0.6}`。
- `build_decision_context`：加 `playbooks` 入参（list of `{id,name,status,...}`），打包进 ctx。
- `score_topic`：
  - `pb_id = topic.get("playbook_id")`；在 `ctx["playbooks"]` 按 id 查。
  - 查到：`value = _PLAYBOOK_STATUS_W.get(status, 0.0)`；note = `匹配到打法《{name}》（{status}）`。
  - 没标/查不到：`value = 0.0`；note = `无匹配打法`。
- **关键接线**（当前缺这几处才导致"未计"）：
  - **命中才计入分母（重要语义，与用户敲定）**：`playbook` 只在 `value>0`（真匹配到打法）时才加进 `pos` 正项列表参与归一化。没匹配的选题 `pos` 维持现状 `["fit","heat","audience_hit","anchor_distance","material_ready"]`，绝对分与今天完全一致，不被稀释。实现：算完 factor 后 `pos = [...] ; if factors["playbook"]["value"] > 0: pos = pos + ["playbook"]`。理由：playbook 是"加分项/辅助杠杆"，当前爆款少、多数选题无匹配打法；若永久进分母会让整池分数普遍下滑（排序不变但绝对分降 ~13%），造成困惑。命中才计入 = 有打法纯加分、没打法不受影响。
  - 报告：正项循环保持 `["fit","heat","audience_hit","anchor_distance","material_ready"]` 不变；其后单独加一行 `if factors["playbook"]["value"] > 0: lines.append("＋"+note)`（命中才显示 `＋匹配到打法《X》（proven）`，没命中不输出该行避免噪音）。
  - C 类报告循环 line 265 从 `["evidence","playbook","gap"]` 改成 `["evidence","gap"]`（playbook 不再是"未计"的降级项，已成真因子，改由上面命中条件行输出）。
- `WEIGHTS` 三套预设把 `playbook` 从 0 改成 `冷启动1 / 涨粉2 / 转化2`。

### 4. 调用侧（`app/api/media.py:1062-1068` 决策打分路由）
- 精确位置：该路由查 traits/audiences/anchors/materials/recent/history/topics 后，`build_decision_context(...)` → `rank_pool(topics, ctx, phase)` → 逐条 `UPDATE decision_score`。topics 是 `SELECT *`，新列 `playbook_id` 自动带出，无需改 topics 查询。
- 在 `build_decision_context` 调用前加一条 playbook 查询：`SELECT id,name,status FROM media_playbook WHERE persona_id=? AND status IN ('validating','proven')`，结果作为新入参 `playbooks=` 传进 `build_decision_context`。
- `build_decision_context` 签名在 `dropped_anchors` 后追加 `playbooks=None` 关键字参数（现有调用不受影响，向后兼容）。`rank_pool` 无需改（它只透传 ctx+phase）。

## 数据流

```
[打标] tag_topics: 未打标话题 + 资产菜单(含打法库)
   → AI 返 {id, audience_ids, anchor_ids, dropped_drift_ids, playbook_id}
   → 清洗 → 写 media_topic.playbook_id, tagged=1
                    ↓
[打分] 决策路由: 查 playbooks 传入 build_decision_context
   → score_topic 读 topic.playbook_id → 查 status → _PLAYBOOK_STATUS_W → 因子值
   → 进 pos 归一化(权重按阶段) → 影响 score + 报告显示《打法名》
```

## 测试（TDD）

- **打分器单测**（纯函数，`make_db` 无关，直接构造 ctx）：
  - topic 挂 proven 打法 → playbook 因子 value=1.0，进 pos 求和，score 反映；报告含 `匹配到打法《…》`。
  - 挂 validating → 0.6。
  - 没 playbook_id / id 查不到 → 0.0，报告无该行（或"无匹配打法"不进正项输出）。
  - 三阶段权重生效（同一 topic 换 phase，playbook 贡献随 1/2/2 变）。
- **`tag_topics` 单测**（用 tmp-DB_PATH 模块 fixture，媒体工具铁律）：
  - mock AI 返合法 `playbook_id` → 写进 topic。
  - mock AI 返越界/瞎编 id → 清洗成空。
  - 无打法资产时菜单降级、playbook_id 留空不报错。
- **集成**：决策路由把 playbooks 传进 ctx（可在路由层测或 controller 冒烟验报告出现打法行）。

## 不在本轮（YAGNI / 已确认）

- 已打标(tagged=1)的老话题不会自动补挂新加的打法——与受众/锚点现有行为一致，不改。
- 一个选题只挂一条打法（设计如此）。
- 不碰写稿（🅓 已完成）。
- 不做打法来源维度（对标/拆解别人）——本轮不产出对标打法，权重差异化留后。

## 迁移与部署

- 一列 ALTER（`media_topic.playbook_id`），走 MIGRATIONS 幂等，服务器 `git pull && systemctl restart ai-pm` 自动跑。
- 无破坏性变更；老话题 playbook_id 为空=因子 0，行为等同现状，平滑上线。
