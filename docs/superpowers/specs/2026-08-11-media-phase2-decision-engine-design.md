# 自媒体二期 · 决策引擎 V1 设计 spec

**日期：** 2026-08-11
**所属：** ai-pm / 自媒体模块（media）二期 · 决策层
**性质：** 正式设计 spec（已过用户逐节口头评审，待写 writing-plans 实现计划）
**前置文档：**
- 一期设计 `docs/superpowers/specs/2026-07-24-media-ops-system-design.md`（§四 决策引擎原始设计，11 项打分公式）
- 资产层已完成：人设框架 / 原料库 / 受众画像 / 生意锚点（都是本引擎的输入）

---

## 一、这块是什么，为什么做

**目标：** 给选题池里的话题**可解释地打分排序**，输出一份"推荐 T-042，得分 8.4，理由：命中某 segment 焦虑 + 有现成原料 + 无红线…"的**决策报告**，让人只需拍板选哪个——**这就是飞轮"决策做轻"那半**。

**为什么现在做：** 决策引擎要吃的资产（人设条目 / 受众画像 / 生意锚点 / 原料库 / 红线）**现在全齐了**。这是资产层的收敛点：前面攒的所有体系，在这里第一次被用来"替人算清楚该做哪个选题"。

**核心性质（spec §四原文）：** `media_decision.py` **纯计算，无 AI 调用，必须可单元测试**。不是让 AI 随口推，而是可解释的打分。

---

## 二、核心决策（brainstorm 拍板）

| 决策点 | 选定 | 理由 |
|---|---|---|
| 缺数据源的项（打法库未建、metrics 稀疏） | **全量 11 项公式 + 优雅降级** | 缺的项贡献 0/中性，报告**诚实标注**"暂无数据"；以后数据源建了直接接入，不改公式 |
| 语义匹配（audience_hit/anchor 等） | **引擎纯计算，关键词/文本重叠粗匹配** | 不接 AI 又能用上新资产的唯一办法；粗但可解释、不编造；以后可升级成 AI 生成话题时打标 |
| 权重 | **按阶段硬编码三套预设常量** | 冷启动/涨粉/转化，YAGNI 不做设置页 UI |
| 触发与呈现 | **选题页「决策排序」按钮** | 与「AI 推选题」分开：一个生成、一个打分。纯函数算全池→写库→按分排序→展开看报告 |
| 迁移 | **零 migration** | `media_topic` 已有 `decision_score REAL` + `decision_report TEXT` 两列 |

**锁死原则：**
1. **纯计算无 AI** —— `media_decision.py` 不调任何模型，全部可单测。
2. **诚实不编造** —— 缺数据源的因子在报告里明确标"暂无数据"，绝不假装打了分。
3. **可解释** —— 每个话题的 `decision_report` 逐项列出算了什么、依据是什么。
4. **不动 AI 推选题、不动写稿、不动 finalize。**

---

## 三、11 项因子 —— 三类处理

`decision_score = fit×w1 + heat×w2 + evidence×w3 + playbook×w4 + gap×w5 + audience_hit×w8 + anchor_distance×w9 + material_ready×w10 − risk×w6 − fatigue×w7 − dup_penalty×w11`

所有因子归一化到 **0–1**（减项也算 0–1，乘以负权重）。

### A 类 —— 直接用 media_topic 已存字段（AI 生成话题时已给）

| 因子 | 来源 | 归一化 |
|---|---|---|
| `fit_score`（w1） | `topic.fit_score`（1-5） | (v-1)/4 |
| `heat`（w2） | `topic.heat`（1-5） | (v-1)/4 |

### B 类 —— 引擎纯计算（关键词/文本重叠，用已建资产）

匹配方法：中文按 2-gram 字符集重叠打分（`_overlap(text_a, text_b) -> 0..1`，纯函数，可单测）。话题文本 = `title + " " + puzzle`。

| 因子 | 算法 | 报告依据示例 |
|---|---|---|
| `audience_hit`（w8） | 话题文本 vs 每个 `media_audience` 的 `anxiety+language`，取最高重叠，再乘该 segment `pay_willingness/5` 加权 | "命中'焦虑老板'的焦虑，付费意愿★★★★" |
| `anchor_distance`（w9） | 话题文本 vs 每个 `media_anchor`（status≠dropped）的 `name+value_prop`，取最高重叠 | "贴近锚点'1v1陪跑'" |
| `material_ready`（w10） | 话题文本 vs 每条 active `media_material` 的 `brief+title`，有重叠超阈值即 ready，分=最高重叠 | "有现成原料'鞋厂客服AI'可用" |
| `risk_score`（w6，减） | 话题文本 vs 每条 `taboo` 维度 trait 的 `content+brief`，命中即高风险 | "⚠️ 撞红线'不编造经历'" |
| `fatigue`（w7，减） | 近 N=5 条内容（media_content，按 created_at）与话题文本重叠均值 | "近 5 条有 3 条同方向" |
| `dup_penalty`（w11，减） | 话题文本 vs 历史 media_content 的 `title+topic_fingerprint`，最高重叠为 dup 分；且带 `outcome` 提示 | "此方向做过，outcome=flop，换角度？" |

**dup 特殊提示（spec §查重关卡）：** 撞到的历史内容 `outcome=flop` → 报告加**强提示**；`outcome=hit` → 提示"曾爆过，可换新角度重做"。（v1 只在报告里提示，不硬拦。）

### C 类 —— 缺数据源，v1 优雅降级（贡献 0，报告诚实标注）

| 因子 | 现状 | v1 处理 |
|---|---|---|
| `playbook_match`（w4） | 打法库表未建 | 恒 0，报告标"⚙️ 打法库未建，此项未计" |
| `evidence_score`（w3） | media_metrics 数据稀疏 | 恒 0，报告标"⚙️ 历史数据不足，此项未计" |
| `gap_score`（w5） | 内容矩阵分类未做 | 恒 0，报告标"⚙️ 内容缺口分析待补" |

> 降级项贡献 0 分，但**分母不含它们的权重**（见 §四归一化），避免因缺数据把总分整体拉低造成误导。

---

## 四、总分归一化与权重

- 每个因子输出 0–1。加项正贡献、减项负贡献。
- **总分 = Σ(active因子 signed_value × weight) / Σ(active因子 |weight|)**，再 ×10 显示为 0–10 分。
- "active因子" = 非 C 类降级的因子。C 类不进分子也不进分母，所以缺数据不拉低总分（诚实：算了的项之间公平比较）。
- 减项（risk/fatigue/dup）：signed_value = −factor，正常进公式。

**权重按阶段（`persona.current_phase`）三套预设常量**（初始值靠经验，spec §开放问题 2 说明靠 L2 复盘迭代）：

```python
WEIGHTS = {
  "冷启动": {"fit":2,"heat":3,"audience_hit":2,"anchor_distance":1,"material_ready":2,
            "risk":3,"fatigue":1,"dup_penalty":2,"evidence":0,"playbook":0,"gap":0},
  "涨粉":   {"fit":3,"heat":2,"audience_hit":3,"anchor_distance":1,"material_ready":2,
            "risk":3,"fatigue":2,"dup_penalty":2,"evidence":0,"playbook":0,"gap":0},
  "转化":   {"fit":2,"heat":1,"audience_hit":3,"anchor_distance":3,"material_ready":2,
            "risk":3,"fatigue":2,"dup_penalty":2,"evidence":0,"playbook":0,"gap":0},
}
```
（evidence/playbook/gap 权重当前写 0，等数据源建了改这里即可。未知阶段回落"涨粉"。）

---

## 五、组件与接口

### 5.1 `app/services/media_decision.py`（新，纯计算）

```python
def _overlap(a: str, b: str) -> float:
    """两段中文文本的 2-gram 字符集 Jaccard 重叠，0..1。纯函数。"""

WEIGHTS = {...}  # §四

def build_decision_context(traits, audiences, anchors, materials, recent_contents,
                           history_contents) -> dict:
    """把该人设的资产打包成打分上下文（纯数据，调用方从 DB 查好传入）。"""

def score_topic(topic: dict, ctx: dict, phase: str) -> dict:
    """给一个话题打分。返回 {"score": float(0-10), "report": str, "factors": {name: {value, note}}}。
    纯函数，无 DB / 无 AI。"""

def rank_pool(topics: list[dict], ctx: dict, phase: str) -> list[dict]:
    """给一批话题打分，返回带 score/report 的列表，按 score 降序。"""
```

- `report` 文案：一行总分 + 逐因子"名称：分值 依据"，A/B 类列真实依据，C 类列"⚙️ 未计"标注，dup 命中 flop/hit 加强提示。
- `factors` 保留结构化明细供测试断言。

### 5.2 `app/api/media.py` 新增路由

```python
@router.post("/media/topics/rank")
async def topics_rank():
    """一键给选题池全部 pool 话题打分，写 decision_score + decision_report。"""
    # 1. _first_persona_id
    # 2. 查该人设：active traits(含taboo) / active audiences / anchors(status!=dropped) /
    #    active materials / 近5条 content / 全部 content(带 outcome/fingerprint)
    # 3. build_decision_context + rank_pool
    # 4. UPDATE media_topic SET decision_score=?, decision_report=? WHERE id=?
    # 5. RedirectResponse 回 /media/topics
```

### 5.3 `app/templates/media_topics.html`

- 顶部加「🧮 决策排序」按钮（form POST /media/topics/rank）。
- 每个话题卡：显示 `decision_score`（醒目），`decision_report` 放 `<details>` 展开看。
- 列表已按 decision_score DESC 排（现有 topics_home 排序即是）。
- 无 `<script>` 复杂 JS（就一个 POST 按钮 + details），避 JS 崩坑。

---

## 六、边界与错误处理

- **选题池空**：按钮照常，rank_pool 返回空，页面无变化。
- **资产全空**（新人设没建任何 trait/audience/anchor/material）：所有 A/B 因子低分或中性，报告如实反映"资产不足，打分参考性低"。
- **降级项**：C 类恒 0 且不进分母，报告标注，不误导。
- **归一化除零**：Σ|weight| 为 0 时（理论不会，阶段预设都有正权重）返回 score=0，报告标"无有效权重"。
- **中文 2-gram**：文本 <2 字时 _overlap 返回 0（不报错）。

---

## 七、测试策略（纯函数，TDD 覆盖率高）

`tests/test_media_decision.py`（新，全部纯函数，不需 DB/AI/asyncio）：

1. `_overlap`：完全重叠=1、无重叠=0、部分重叠介于中间、短文本不崩。
2. `score_topic` A 类：fit=5/heat=5 → 对应因子归一 1.0。
3. `score_topic` B 类命中：话题文本含某 segment anxiety 关键词 → audience_hit>0 且报告含该 segment 名 + 付费加权。
4. `score_topic` B 类风险：话题撞 taboo → risk 因子拉低总分，报告含红线提示。
5. `score_topic` dup：话题撞 outcome=flop 历史 → 报告含强提示。
6. `score_topic` C 类降级：playbook/evidence/gap 恒 0，报告含"未计"标注，且不进分母（同样话题去掉这些权重后总分不变）。
7. 权重按阶段切换：同话题在冷启动 vs 转化下总分不同（heat 重 vs anchor 重）。
8. `rank_pool` 排序：多话题按 score 降序。
9. 路由 `/media/topics/rank`（TestClient）：写 decision_score/report 到 media_topic，重定向。

---

## 八、代码落点

| 文件 | 改动 |
|---|---|
| `app/services/media_decision.py` | 新建（纯计算：_overlap / WEIGHTS / build_decision_context / score_topic / rank_pool） |
| `app/api/media.py` | +1 路由 `/media/topics/rank` |
| `app/templates/media_topics.html` | +「决策排序」按钮 + 报告 `<details>` |
| `tests/test_media_decision.py` | 新建（8 纯函数测 + 1 路由测） |

**零 migration、不动 AI 推选题、不动写稿、不动 finalize。**

---

## 九、明确不做（YAGNI / 留后）

- **打法库 / 教训库 / 内容矩阵缺口分析** —— 各自独立后续块；建了再把 w4/w5 接上。
- **权重设置页 UI** —— 硬编码预设，L2 复盘迭代权重是后面的事。
- **升级 AI 推选题让它给话题打 audience/anchor 标** —— v1 引擎用关键词粗匹配代替；以后可让 AI 生成时直接标更准。
- **决策引擎自动跑（新话题入池即打分）** —— v1 只手动「决策排序」按钮触发。
- **硬拦红线/查重** —— v1 只在报告里提示，人拍板。

---

## 十、未决 / 留后的 Minor（非阻塞）

1. 2-gram Jaccard 对中文是很粗的语义近似，会有误命中/漏命中；靠人看报告纠偏，以后可升级 AI 打标。
2. 权重预设是拍脑袋初值，靠实际使用 + L2 复盘调。
3. `material_ready` / dup 的重叠阈值待实测调。
4. 归一化"降级项不进分母"可能让资产极少的新人设分数偏高（少数因子满分），报告已如实标注资产不足。
