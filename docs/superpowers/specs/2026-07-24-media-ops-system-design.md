# 自媒体运营系统 设计文档

**日期：** 2026-07-24
**所属项目：** ai-pm（AI 项目管理平台）
**模块代号：** media（自媒体运营）
**状态：** 设计已确认，待写实现计划

---

## 1. 背景与目标

### 1.1 要解决什么

在 ai-pm 中搭建一套自媒体运营系统，覆盖**选题 → 脚本 → 录制 → 剪辑 → 三平台发布 → 数据采集 → 复盘 → 资产沉淀 → 回到选题**的完整闭环。

平台范围：抖音、小红书、视频号。运营形态：一个 IP 三平台同步（架构支持多 IP 扩展）。

### 1.2 核心设计纲领

> **写作做轻、体系做重。**

传统内容生产每次都从零开始：想选题、定角度、想结构、想钩子、避雷、写稿——每次都重。

本系统把这些重活**前置沉淀在体系里**（人设、受众画像、原料、打法、教训、红线）。写脚本时 AI 只是**调用体系**，人只做表达微调。

**体系越重，单次创作越轻。任何一次创作产生的经验，都必须回流成体系资产，否则就是浪费。**

### 1.3 闭环的目的

不只是让数据好看，而是产出三样**资产**：

1. **可复制的打法** —— 方法论沉淀，下次直接套用
2. **记忆点** —— 招牌元素，让受众记住这个 IP
3. **差异化** —— 与同赛道对手的真实区别

### 1.4 成功指标

唯一指标：**每转一圈，体系重一分，写作轻一分。**

具体度量见 §7 飞轮健康度指标。

---

## 2. 架构决策

### 2.1 为什么做独立模块（而非复用 project/task）

**决策：** 新建独立 `media` 模块，仿照现有 `study` 模块的架构模式。

**理由：**
- 内容生命周期（选题/脚本/录制/剪辑/发布/复盘）与 task 的 6 状态（pending/running/reviewing/blocked/done/failed）语义不匹配，硬套会处处别扭
- 三平台数据维度、复盘归因、资产沉淀等字段在 task 表无处安放
- 独立模块不污染通用项目/任务体系
- `study` 模块已验证这套架构模式在本项目中可行

**被否决的方案：**
- 方案 B（复用 project+task）：改动最小但语义错位，长期成本高
- 方案 C（混合，资产存 notes）：notes 是自由文本，无法支撑打分、查重、预算截断等结构化需求

### 2.2 为什么以「人设」为组织单位

一切资产（受众画像、原料、打法、红线、内容、数据）都挂在 `persona_id` 下。

**理由：** 以后新增账号 = 新建人设，内容/数据/复盘天然隔离不会乱。平台作为 `media_account` 数据行而非硬编码枚举，新增 B站/快手只需加一行。

### 2.3 为什么人设是「条目化」而非固定字段

**人设不是先写好的静态档案，是被内容和数据反哺出来的活体。**

人设从 0 开始，每发一批内容，AI 复盘时提炼新条目（带证据和置信度）等人确认。目标人群变了，老条目 `archived` 不删、新条目 `active`，演化史完整留痕。

### 2.4 代码结构

```
app/api/media.py              # 所有路由
app/services/media_ai.py      # AI 六大能力
app/services/media_decision.py# 选题决策引擎（打分，纯计算）
app/services/media_metrics.py # 数据采集（抓取/识图/手填）
app/services/media_context.py # 上下文拼装 + 注入预算控制
templates/media_*.html        # 页面
```

拆成 5 个文件而非一个大文件：职责单一，改一个不影响另一个。决策引擎独立是因为它是**纯计算无 AI**，必须可单元测试。

---

## 3. 数据模型（17 张表）

| # | 表名 | 层 | 期 |
|---|---|---|---|
| 1 | `media_persona` | 人设 | 一期 |
| 2 | `media_persona_trait` | 人设 | 一期 |
| 3 | `media_account` | 人设 | 一期 |
| 4 | `media_audience` | 受众与生意 | 二期 |
| 5 | `media_anchor` | 受众与生意 | 二期 |
| 6 | `media_material` | 资产 | 二期 |
| 7 | `media_playbook` | 资产 | 二期 |
| 8 | `media_lesson` | 资产 | 二期 |
| 9 | `media_redline` | 资产 | 二期 |
| 10 | `media_topic` | 内容流转 | 一期 |
| 11 | `media_content` | 内容流转 | 一期 |
| 12 | `media_publish` | 内容流转 | 一期 |
| 13 | `media_metrics` | 内容流转 | 一期 |
| 14 | `media_review` | 复盘 | 一期 |
| 15 | `media_review_cycle` | 复盘 | 三期 |
| 16 | `media_case` | 复盘 | 三期 |
| 17 | `media_injection_log` | 复盘 | 三期 |

### 3.1 人设层

**`media_persona`（人设主体 — 只存身份和当前阶段）**
```
id, name(IP名), one_liner(一句话定位),
current_phase(当前阶段：冷启动/涨粉/转化/…),
status(active/paused), created_at, updated_at
```

**`media_persona_trait`（人设条目 — 人设的真正内容，可增删演化）**
```
id, persona_id,
dimension(维度，见下),
content(条目内容), brief(≤30字精简版，用于 AI 注入),
source(manual / ai_from_content / ai_from_review),
source_content_id(来源于哪条内容，可空),
evidence(证据), confidence(置信度 1-5),
phase_tag(在哪个阶段成立的),
status(active现行 / archived已过时),
created_at
```

`dimension` 取值：
- `positioning` 定位 / `audience` 受众 / `tone` 语气
- `topics` 选题方向 / `taboo` 内容禁区
- `signature` **记忆点** —— 口头禅、固定开场、招牌动作、视觉符号、结尾钩子
- `differentiator` **差异化** —— 与同赛道对手的不同之处

**`media_account`（人设下的平台账号）**
```
id, persona_id, platform(douyin/xhs/shipinhao/…),
account_name, account_url, fans_count,
platform_note(该平台差异化策略),
status(active/paused), created_at
```

### 3.2 受众与生意层

**`media_audience`（受众画像 — 内容和生意的双重筛子）**
```
id, persona_id,
segment(细分人群名),
who(他们是谁：年龄/职业/生活状态/一天怎么过),
anxiety(在焦虑什么 — 内容的钩子来源),
desire(渴望变成什么样),
objection(不行动的阻力/顾虑),
language(他们自己怎么说这件事 — 原话，用于文案措辞),
pay_willingness(付费意愿 1-5),
pay_scene(什么场景下会掏钱),
pay_ceiling(能接受的价格带),
evidence(从哪来), confidence(1-5),
status(active/archived), created_at
```

双重作用：
- **筛内容** —— 选题必须命中某个 segment 的 `anxiety`，命中不了的选题再热也是自嗨
- **筛生意** —— `pay_willingness × pay_scene` 高的 segment 才值得倾斜内容

`language` 字段是隐藏高价值项：受众原话直接进脚本，比任何文案技巧都有效。AI 从评论区自动采集。

**`media_anchor`（生意锚点 — 内容为什么值钱）**
```
id, persona_id, name(锚点名),
type(自有产品/服务/带货/广告/引流私域),
target_audience_ids(服务哪些 segment),
value_prop(解决什么问题), price_band(价格带),
path(从内容到成交的路径),
status(验证中/已跑通/已放弃),
evidence(转化数据), created_at
```

必须显性化的理由：没有锚点的内容，播放量再高也只是热闹。有锚点后决策引擎才能判断「这选题虽热但离变现太远」。

### 3.3 资产层

**`media_material`（人设原料库 — 写出"像人"的关键）**
```
id, persona_id,
type(story故事 / pit坑 / judgment判断 / opinion观点 / data数据素材 / quote金句),
title, detail(完整原料：时间/地点/细节/情绪), brief(≤30字),
emotion(情绪底色：懊悔/庆幸/愤怒/释然),
usable_scene(什么样的选题能用上它),
audience_hit(能打中哪个 segment 的哪个焦虑),
used_in(用过的 content_ids — 防重复), use_count,
status(active/已用旧/archived), created_at
```

| 类型 | 为什么值钱 |
|---|---|
| `story` 故事 | 唯一不可复制的资产。对手抄得走结构，抄不走你的经历 |
| `pit` 坑 | "我踩过"比"你要注意"可信一百倍 |
| `judgment` 判断 | 敢下的判断 = 差异化本身 |
| `opinion` 观点 | 反共识观点是最强钩子，也是记忆点来源 |
| `quote` 金句 | 可反复用作结尾钩子，形成 signature |

`used_in` / `use_count` 是关键设计：好故事被反复调用会让受众听腻，超阈值自动降权，逼迫补充新原料。**原料库的丰俭直接决定内容质量上限。**

原料入库三条路：①随手记（一句话存，AI 补结构化字段）②AI 从知识库笔记挖（复用 `list_kb_notes`）③复盘时 AI 发现反响好的即兴例子，提炼入库

**`media_playbook`（打法库 — 可复制的方法论）**
```
id, persona_id,
type(结构模板/选题公式/标题句式/开场钩子/平台适配技巧),
name, detail(具体怎么做), brief(≤30字),
evidence(哪几条内容验证的，content_ids),
hit_rate(命中率，随数据更新),
status(验证中/已验证/已淘汰),
created_at, updated_at
```

`hit_rate` 随每次数据采集自动更新，**没被数据验证的打法会自己淘汰，不靠人记忆。**

**`media_lesson`（经验教训 — playbook 之外的另一半）**
```
id, persona_id,
type(failure教训 / ops操作技巧 / platform平台特性 / insight受众洞察),
title, detail, brief(≤30字),
trigger_context(什么情况下该想起这条 — 用于按需注入),
evidence, source_content_id, source_review_id,
status(active/archived), created_at
```

| 类型 | 例子 |
|---|---|
| `failure` | "前 15 秒讲背景的 4 条内容完播率均低于 20%，必须 3 秒进主题" |
| `ops` | "视频号封面用竖版 3:4 比 16:9 点击率高" |
| `platform` | "小红书正文超 600 字阅读完成率骤降" |
| `insight` | "评论区高频词是'太难了'，受众要的是共情不是说教" |

**`media_redline`（红线 — 硬拦截，不是建议）**
```
id, persona_id(可空=全局),
category(平台违规/法律风险/人设禁区/竞品敏感/用户投诉过的),
rule(具体规则), brief(≤30字),
keywords(触发词，逗号分隔，用于机械扫描),
platform(适用平台，空=全平台),
severity(block硬红线-阻断发布 / warn警告),
source(manual / ai_from_incident / ai_from_platform_rule),
evidence, status(active/archived), created_at
```

**三道检查关卡：**
1. **脚本生成时** —— 红线注入 AI 提示词（事前预防）
2. **脚本保存后自动扫** —— 关键词机械扫描 + AI 语义审查，命中标红
3. **发布前强制扫** —— `severity=block` 未解决则发布按钮禁用，须人工确认「已知悉并放行」

红线也进闭环：内容被限流/删除/投诉 → 复盘时 AI 提炼候选红线 → 人确认入库。**踩过的坑不踩第二次。**

### 3.4 内容流转层

**`media_topic`（话题库 — 选题的蓄水池）**
```
id, persona_id, title(话题),
puzzle(核心谜题 — 见下),
source(ai_rec / manual / hot热点 / comment评论区挖掘 / competitor对标 / review复盘衍生),
reason(为什么值得做), angle(切入角度),
heat(热度 1-5), fit_score(人设契合度 1-5),
decision_score(决策引擎综合得分), decision_report(可解释理由),
related_trait_ids, related_playbook_id, related_audience_id, related_anchor_id,
status(pool候选 / adopted已采用 / rejected已弃 / expired过期),
adopted_content_id, rejected_reason(为什么弃，AI 下次不再推同类),
created_at, expires_at
```

**`puzzle` 选题谜题（核心概念）：**
每个选题背后必须是一个受众想解开的「谜」。不写成「聊聊育儿焦虑」这种平铺主题，而写成「为什么越努力的妈妈孩子越叛逆？」这种带悬念的谜题——天然自带钩子和完播动力。

**AI 推选题时必须给出谜题形式；给不出谜题的选题说明还没想透，不该开工。**

`rejected_reason` 是防 AI 重复推垃圾的关键：弃一次并说明原因，下次推荐时把弃单一起喂给 AI。

**话题五条进水管：**
| 来源 | 怎么进来 |
|---|---|
| AI 推荐 | 看板「AI 推选题」，基于人设+playbook+数据表现 |
| 人工添加 | 随时想到就丢进去（手机上也能加） |
| 热点 | AI 抓平台热榜（复用 `agent_tools.web_fetch`），按契合度打分过滤 |
| 评论区挖掘 | 采集已发内容评论，AI 找出高频提问/痛点 |
| 复盘衍生 | 某条爆了 → AI 建议"这方向可再做3条" |

**`media_content`（内容条目 — 核心表）**
```
id, persona_id, title(选题), puzzle(核心谜题), stage(状态机),
idea_source, idea_reason,
script(口播脚本), edit_note(剪辑要点), cover_idea(封面思路),
target_audience_id(瞄准哪个 segment), anchor_id(引向哪个锚点),
used_material_ids, used_playbook_ids,
topic_fingerprint(选题指纹，AI 提炼的核心语义标签，用于查重),
outcome(结果标签：hit/normal/flop/violation),
archived_status,
created_at, updated_at
```

**状态机 `stage`：**
```
idea(选题) → scripted(脚本就绪) → recording(待录/录制中)
→ editing(待剪/剪辑中) → ready(待发) → published(已发) → reviewed(已复盘)
```

**`media_publish`（一条内容 × 一个平台的发布记录）**
```
id, content_id, account_id,
publish_text(该平台的文案/标签，三平台可不同),
published_at, post_url, status(pending/published/failed)
```

一条内容三平台分发，每个平台文案和发布时间可以不同。

**`media_metrics`（数据快照）**
```
id, publish_id, views, likes, comments, shares, new_fans,
collected_by(auto/screenshot/manual), snapshot_at
```

挂在 publish 上，天然按平台分开，可多次采集看增长曲线。

**采集降级链：**
```
自动抓取(httpx / 复用 agent_tools) → 失败 → 截图上传(AI识图) → 仍失败 → 手动填表单
```
三条路径写同一张表，用 `collected_by` 标记来源。

### 3.5 复盘层

**`media_review`（L1 单条复盘 — 分平台 + 总盘）**
```
id, content_id, scope(overall/platform),
account_id(scope=platform 时填),
what_worked, what_failed, next_action,
proposed_traits / proposed_playbooks / proposed_lessons / proposed_redlines
    (AI 提炼的候选资产，JSON，待人确认后入库),
generated_by(ai/human), created_at
```

**`media_review_cycle`（L2/L3 周期复盘 — 飞轮的动力源）**
```
id, persona_id, level(L2/L3),
period_start, period_end, content_ids(纳入范围),
metrics_summary(汇总数据),
patterns(发现的规律，JSON),
hypotheses(本轮提出的待验证假设，JSON),
hypotheses_tested(上轮假设的验证结果，JSON),
proposed_traits / proposed_playbooks / proposed_lessons / proposed_redlines,
weight_suggestion(建议调整决策引擎权重),
created_at
```

**复盘三层级：**

| 层级 | 对象 | 回答什么 | 产出 |
|---|---|---|---|
| **L1 单条** | 1 条内容 × N 平台 | 这条为什么成/败？ | 平台复盘 N 份 + 总复盘 1 份 |
| **L2 周期** | 近 N 条 / 周 / 月 | **有什么规律？** | playbook、lesson、audience 修正 |
| **L3 阶段** | 一个人设阶段 | 人设该进化了吗？ | trait 归档/新增、phase 切换、anchor 调整 |

**L2 是飞轮真正的动力源。** 单条爆了可能是运气；10 条对比后发现「带真实故事的 7 条平均播放是无故事的 3 倍」——这才是可复制的方法论。L1 只能给假设，L2 才能验证成规律。

**假设-验证机制：** L2 输出的不只是结论，还有**下一轮要验证的假设**（"假设：前 3 秒抛问题能提升完播 → 下周 5 条中 3 条采用，对比验证"）。下一轮 L2 自动检验上轮假设。**让飞轮是科学的，不是玄学的。**

**`media_case`（爆款库 + 失败库）**
```
id, persona_id, content_id,
case_type(hit爆款 / flop失败 / normal基准),
threshold_basis(判定依据，如"播放量 3.2 倍于账号中位数"),
-- 逐层归因（爆款失败共用同一结构）--
topic_factor(选题层：谜题够不够狠、是否命中焦虑),
hook_factor(开场钩子：前3秒做了什么),
structure_factor(结构：套了哪个 playbook),
material_factor(原料：用了什么故事/判断),
emotion_factor(情绪曲线),
platform_factor(平台适配),
external_factor(外部：热点/推荐/时段/运气成分),
replicable(可复制性 1-5),
conclusion(一句话结论), created_at
```

**爆款和失败用同一张表的理由：** 归因维度完全相同只是方向相反，且对比分析必须在同一结构下才能做——爆款的 `hook_factor` vs 失败的 `hook_factor`，规律才浮出来。

**`replicable` 是最重要的字段：** 爆款分两种——蹭热点的（不可复制，`replicable=1`，别学）和方法论过硬的（`replicable=5`，立刻提炼成 playbook）。**不做这个区分，就会把运气当能力，飞轮空转。**

失败库价值不亚于爆款库：失败归类后直接生成 `lesson`，违规导致的生成 `redline`，决策引擎的 `risk_score` 拿它做参照。

**`media_injection_log`（注入可观测 — 见 §6）**
```
id, content_id, ai_type, injected_asset_ids(注入了什么),
token_count, output_quality(该内容最终数据表现), created_at
```

---

## 4. 选题决策引擎（核心引擎）

**文件：** `app/services/media_decision.py`
**性质：** 纯计算，无 AI 调用，必须可单元测试。

不是让 AI 随口推，而是**可解释的打分决策**。每个话题算一个 `decision_score`：

```
decision_score =
    fit_score(人设契合)              × w1
  + heat(热度/时效)                  × w2
  + evidence_score(同类历史数据表现)  × w3
  + playbook_match(有已验证打法可套)  × w4
  + gap_score(内容矩阵缺口)          × w5
  + audience_hit(命中高价值 segment 的焦虑) × w8
  + anchor_distance(离生意锚点多近)   × w9
  + material_ready(有无现成原料可用)  × w10
  − risk_score(红线风险)             × w6
  − fatigue(近期同质化疲劳)          × w7
  − dup_penalty(历史选题查重惩罚)    × w11
```

**每一项都有依据，不是玄学：**

| 项 | 依据来源 |
|---|---|
| `evidence_score` | `media_metrics` 真实数据（"该方向历史均播放 3.2 万，高于账号均值"） |
| `playbook_match` | 已验证打法（"可套'三段式反转开场'，hit_rate 78%"） |
| `gap_score` | 内容矩阵缺口（"已发 8 条干货、0 条故事，受众粘性需要故事"） |
| `fatigue` | 防连发同质（"近 5 条有 3 条同方向"） |
| `material_ready` | 手上有真故事的选题开工成本低、成品质量高，优先做 |
| `dup_penalty` | `topic_fingerprint` 比对历史内容 |

**权重 `w1~w11` 按 `persona.current_phase` 自动切换：** 冷启动期重 `heat`，涨粉期重 `fit`，转化期重 `evidence` 和 `anchor_distance`。权重表可在设置页调整，L2 复盘的 `weight_suggestion` 会给出调整建议。

**查重关卡（`w11` 的具体行为）：**
话题进决策前用 `topic_fingerprint` 比对全部历史内容：
- 撞到 `outcome=flop` → **强提示**："此方向 3 个月前做过，播放量仅 4000，失败归因是选题太宽泛。若要重做，请说明角度有何不同"
- 撞到 `outcome=violation` → 直接进红线检查
- 撞到 `outcome=hit` 且间隔足够久 → 提示"该方向曾爆过（8.2万），可考虑新角度重做"

**系统记得，人不用记得。**

**输出不是一个答案，是一份决策报告：**
> 推荐 T-042，得分 8.4。理由：契合"宝妈焦虑"人设条目(置信度5)、可套三段式开场(hit 78%)、故事类内容有缺口、有现成原料 M-021 未用过、无红线风险、距锚点"1v1陪跑"1 跳。
> 次选 T-017（7.9）；不建议 T-033（红线风险高）。

人只需拍板选哪个——**这就是"决策做轻"。**

---

## 5. 页面与 AI 介入点

### 5.1 页面（5 个）

**1. `/media` 内容看板** —— 主入口
按 `stage` 分 7 列的卡片流：选题 / 脚本 / 待录 / 待剪 / 待发 / 已发 / 已复盘。
一眼看到每条内容卡在哪、堵在哪。卡片显示标题、三平台小图标（已发亮起）、关键数据。
顶部：人设切换器 + 「✨ AI 推选题」按钮。

**2. `/media/content/{id}` 内容详情** —— 一条内容的一生
上下顺序即工作流，走到哪步展开哪块：
选题理由与谜题 → 脚本（可编辑，「AI 重写」，红线扫描结果标红）→ 剪辑要点 → 三平台发布卡（各自文案+状态+post_url，block 红线未解决则禁用发布）→ 数据区（三平台数据表 +「📷 截图识别」「🔄 自动抓取」「✍️ 手动填」）→ 复盘区（平台复盘 N 份 + 总复盘 1 份 + AI 提炼的候选资产待确认）

**3. `/media/persona/{id}` 人设档案** —— 资产库
- Tab 1「人设条目」：按 dimension 分组，每条带证据+置信度星级+来源标记，可确认/归档
- Tab 2「受众画像」：segment 卡片，含焦虑/原话/付费意愿
- Tab 3「原料库」：按 type 分组，显示 use_count（用多了的标灰）
- Tab 4「打法库」：按 type 分组，显示 hit_rate 和验证状态
- Tab 5「经验教训」：按 type 分组
- Tab 6「🚫 红线」：按 category 分组，block 的标红
- Tab 7「生意锚点」
- Tab 8「演化史」：archived 条目时间轴，看人设怎么长出来的

**4. `/media/topics` 话题库**
卡片池，按 `decision_score` 排序，可按 source 筛选。每张卡：话题+谜题+理由+得分+决策报告展开+「采用」「弃」。弃时弹窗填原因（AI 预填）。

**5. `/media/dashboard` 数据面板**
三平台对比、时间趋势（Chart.js，`/finance` 已有先例）、爆款榜、playbook 命中率排行、**飞轮健康度指标**（见 §7）。

### 5.2 AI 介入点（6 个，全部走现有 `ai_router`）

| # | 触发位置 | 输入 | 输出 |
|---|---|---|---|
| 1 | 看板「AI 推选题」 | 受众画像、数据表现、话题池、锚点、弃单原因 | 候选选题（含谜题+理由），勾选入库 |
| 2 | 内容详情「写脚本」 | 人设条目、记忆点、原料、playbook、lesson、红线、锚点 | 口播脚本（分镜/时长/钩子） |
| 3 | 发布卡「生成文案」 | 脚本 + 该平台特性 | 该平台的标题/正文/话题标签 |
| 4 | 数据区「截图识别」 | 后台截图 | 解析播放/点赞/评论/涨粉，写入 metrics |
| 5 | 复盘区「AI 复盘」 | 数据 + 脚本 + 同类历史对比 | N 份平台复盘 + 1 份总复盘 |
| 6 | 复盘后自动 | 复盘结论 + 现有资产（查重） | 候选 trait/playbook/lesson/redline |

**关键设计：#6 永远不自动写库。** AI 提炼，人拍板——符合"人只做关键决策"原则，也防止 AI 把偶然当规律污染人设。

识图能力复用已有 `ask_ai_vision`（Claude 优先 → OpenAI，DeepSeek 不支持识图）。

---

## 6. AI 注意力预算（关键约束）

### 6.1 问题

体系做重与 AI 注意力有限直接冲突。**本项目已踩过一次坑**（commit c14e812 强塞知识库清单 → 违反"不污染上下文"原则 → aed08da 改为关键词触发）。同样教训必须前置。

### 6.2 核心原则

**体系重在库里，不是重在提示词里。** 体系有 17 张表几百条资产，但**任何一次 AI 调用看到的都不超过 20 条**。重的是检索和沉淀，轻的是每次注入。

### 6.3 六个办法

**办法一：分工不分身（最有效）**

绝不让一个 AI 一次干完所有事。六个 AI 介入点各是独立调用，各拿各的上下文：

| AI | 看得到 | 看不到 |
|---|---|---|
| 选题 AI | 受众画像、数据表现、话题池、锚点 | 原料库、脚本细节、剪辑 |
| 脚本 AI | 人设条目、记忆点、原料、playbook、红线 | 数据表、话题池、财务 |
| 文案 AI | 脚本 + 该平台特性 | 人设全档、原料库、历史数据 |
| 识图 AI | 只有截图 | 其他全部 |
| 复盘 AI | 数据 + 脚本 + 同类对比 | 原料库、话题池 |
| 提炼 AI | 复盘结论 + 现有资产（查重用） | 其余全部 |

一个 AI 一件事，注意力天然集中。这比任何提示词技巧都管用。

**办法二：双版本资产**

每条体系资产存两个版本：
```
brief(≤30字，给 AI 常驻看的)   ← 注入提示词
detail(完整内容，给人看/AI深挖) ← AI 调工具才读
```

提示词里只放 brief 清单（20 条 ≈ 600 字，可接受），AI 觉得哪条需要展开就主动调 `read_asset(id)` 工具读 detail。**复用本项目已验证的模式**（`list_kb_notes` / `read_kb_note`）。

**办法三：硬预算 + 打分截断**

每个注入槽位有数量上限，写死在代码里：
```python
INJECTION_BUDGET = {
    "trait":     8,   # 按 confidence 降序
    "signature": 3,   # 必注入（少而硬）
    "playbook":  2,   # 只给最匹配的已验证打法
    "material":  3,   # 只给未用过且 audience_hit 最匹配的
    "lesson":    3,   # 只给 trigger_context 命中的
    "redline":  "all_block_only",  # 只注 severity=block
    "audience":  1,   # 只注本条瞄准的那个 segment
}
```

**超出预算不是报错，是按分数截断。** 体系涨到 500 条资产，注入量恒定不变。**这是"体系可以无限重"的前提。**

**办法四：红线不用 AI**

`redline.keywords` 走确定性关键词扫描（Python 正则，零 token、零遗漏、零幻觉），只有扫不出的语义风险才交给一次独立轻量 AI 审查。

**能用代码做确定性判断的，绝不消耗 AI 注意力。** 同理：查重用指纹比对、打分用公式计算，都是代码不是 AI。

**办法五：指令少而硬**

给 AI 的规则不超过 5 条铁律，每条都是可判定的硬约束：
```
1. 必须以谜题开场，3秒内抛出
2. 必须植入至少1个 signature
3. 必须用给定原料中的至少1个真实故事
4. 绝不触碰红线清单
5. 结尾引向指定锚点
```

20 条"建议"会让 AI 平均分配注意力导致每条都做不好；5 条"铁律"才会被真正执行。其余偏好放 brief 里让 AI 自行取舍。

**办法六：注入可观测 + 数据反哺（长期解）**

每次 AI 调用记录 `media_injection_log`。累积几十条后反过来算：**哪些注入真的提升了效果，哪些是噪音**。噪音项降权或移出预算。

**这是该难题唯一的科学解法——注入策略本身也进飞轮，用数据迭代，不靠拍脑袋。** 最优注入组合只能靠实测得出；系统要做的是让这件事**可测量**。

**兜底：一键对比**
脚本区提供「精简注入 / 完整注入」两个按钮，同一选题各生成一版直接对比。**人的判断本身就是最好的评估器**，几次对比后就知道该往哪边调。

---

## 7. 飞轮完整形态与健康度

### 7.1 回流路径

```
        ┌──────────── 体系（越来越重）────────────┐
        │ trait · signature · audience · material │
        │ playbook · lesson · redline · anchor    │
        └──┬──────────────────────────────▲───────┘
           │ 注入（写作变轻，≤20条）        │ 沉淀（人确认）
           ▼                               │
  决策引擎选题 → 脚本 → 录 → 剪 → 三平台发布 → 数据 → 复盘(L1/L2/L3)
```

每个资产表都指向决策引擎：
```
              ┌─────── 选题决策引擎（11 项打分）───────┐
              └─▲──▲──▲──▲──▲──▲──▲───────────────────┘
                │  │  │  │  │  │  │
             trait │ lesson │ case库 │
              playbook  redline  audience/anchor  历史指纹查重
```

### 7.2 飞轮健康度指标（`/media/dashboard`）

用来判断飞轮到底转没转起来：

| 指标 | 含义 |
|---|---|
| 资产增长曲线 | playbook/lesson/material/trait 累计条数（体系是否在变重） |
| 已验证 playbook 占比 | 验证中 vs 已验证 vs 已淘汰 |
| **假设验证率** | L2 提出的假设有多少被后续数据证实（方法论质量的直接度量） |
| **可复制爆款占比** | `replicable≥4` 的爆款 / 全部爆款（区分能力和运气） |
| 重复踩坑次数 | 应单调递减，不降说明回流断了 |
| 单条产出耗时 | 应逐渐下降（"写作做轻"的兑现证据） |

---

## 8. 技术约束（沿用项目既有规范）

- **前端：** 不引入新框架，vanilla JS + 本地 Tailwind
- **响应式：** 用 `@media (max-width: 767px)` 自定义 CSS，**不用 Tailwind `md:` 断点**（本地裁剪版不含）。移动端使用频率可能高于桌面端
- **颜色：** 沿用 Dieter Rams 重设计后的统一色系——blue-600（主操作）、violet-600（AI 专属）、amber-600（警告）。用新颜色前先查本地 tailwind.min.css 是否存在该色阶
- **已知坑：** `TemplateResponse` 必须三参数 `(request, "name.html", context)`
- **已知坑：** Jinja2 无 `tojson` 过滤器，用 `json.dumps()` 预序列化 + `| safe`
- **已知坑：** 不用 PowerShell `-replace` 改 UTF-8 模板文件（中文会乱码），一律用 Edit/Write
- **数据库：** SQLite，新表加进 `database.py` 的 `SCHEMA`，字段变更走 `MIGRATIONS` 列表
- **AI 调用：** 全部走现有 `ai_router`（三模型路由 + fallback + 费用记账 + `MAX_PROMPT_CHARS` 保护）
- **识图：** 复用 `ask_ai_vision`
- **网络抓取：** 复用 `agent_tools.web_fetch`

---

## 9. 分期建议

本设计范围较大，建议分三期实施，每期独立可用：

**一期（闭环骨架）** —— 让内容能跑完一圈
`persona` / `persona_trait` / `account` / `topic` / `content` / `publish` / `metrics` / `review`(L1)
页面：内容看板、内容详情、人设档案（条目 Tab）、话题库
AI：#1 推选题、#2 写脚本、#3 生成文案、#4 截图识别、#5 L1 复盘
注入预算机制从一期就实施（brief 字段、INJECTION_BUDGET）

**二期（资产与决策）** —— 让飞轮开始转
`audience` / `anchor` / `material` / `playbook` / `lesson` / `redline`
决策引擎 `media_decision.py`（11 项打分 + 查重）
AI：#6 提炼候选资产
人设档案补齐全部 Tab

**三期（规律与验证）** —— 让飞轮科学地转
`review_cycle`(L2/L3) / `case`(爆款失败库) / `injection_log`
数据面板 + 飞轮健康度指标
假设-验证机制、权重按 phase 自动切换

---

## 10. 待实践中调整的部分

用户明确认可：设计已足够，具体问题待第一版跑起来后再调。以下几点预期会需要实测调整：

1. **注入预算的具体数值** —— `INJECTION_BUDGET` 各项上限只能靠 `injection_log` 数据迭代
2. **决策引擎权重** —— `w1~w11` 初始值靠经验设定，靠 L2 复盘的 `weight_suggestion` 修正
3. **爆款/失败的判定阈值** —— 需累积足够数据后才能定出合理的账号中位数倍数
4. **原料 `use_count` 的降权阈值** —— 多少次算"用旧了"需实测
5. **内容详情页"一条内容的一生"的信息密度** —— 一期跑通后按实际使用习惯调整
