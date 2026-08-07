# 自媒体二期 🅐 单条口播生产线升级 · 设计文档

**日期：** 2026-08-04
**所属项目：** ai-pm（AI 项目管理平台）
**模块代号：** media（自媒体运营）
**期号：** 二期 · 第 🅐 块（单条内容生产闭环升级）
**状态：** 设计已确认（brainstorm 收口），待写实现计划
**前置文档：**
- 一期设计 `docs/superpowers/specs/2026-07-24-media-ops-system-design.md`
- 二期 brainstorm 阶段总结 `docs/superpowers/specs/2026-08-04-media-phase2-brainstorm-summary.md`
- 用户 v3 失败复盘 `D:\GAGA-5-25\copy2video\自媒体智能体第三版_阶段总结_2026-08-03.md`
- 外部方法论 skill：`github.com/erduo1998-cell/ip-strategist`（诊契行盘 + 审稿标准 + 口播方法论）

---

## 1. 背景与目标

### 1.1 要解决什么

一期把「选题 → 脚本 → 录 → 剪 → 发 → 数据 → 复盘」的**物理闭环**跑通了，但**写脚本是一次性 AI 调用**——从选题直接吐一整篇稿，中间没有认知过程。

实测暴露的问题（brainstorm 已验证）：4 条样本 AI 只学会了**腔调**，但**结构老是"三件事"套模板**，因为它没真材料、没跟人聊过，只能退回安全结构。根因：**声音少样本可学，结构需语料库**；且一次性出稿没有"攒真料 → 定角度 → 独立审稿"的过程。

🅐 就是把"直接吐稿"这一步，升级成一条**有认知过程、但默认不累人**的生产线。

### 1.2 一句话目标

> **平时 AI 出稿、人定稿两步走；缺料才补、不满意才介入；越用越省心。**

不是每条都走一遍重流程（那会把人累死），而是：默认 AI 直接出稿，把"证据/角度/审稿"这些重活放到后台自动跑或按需叫出；人的真料和定稿改动持续沉淀，让 AI 手里的料越来越厚，需要人介入的越来越少。

### 1.3 与一期设计纲领一致

一期纲领「**写作做轻、体系做重**」在 🅐 里的兑现：认知过程（证据/角度/审稿）是"体系做重"，但通过**默认自动 + 按需叫出 + 注入预算**，保证"写作做轻"——人平时只看草稿、定稿。

---

## 2. 核心设计原则（动手时必须逐条遵守）

这些从 ip-strategist + v3 复盘 + brainstorm 共识汇聚，是 🅐 的宪法：

1. **⭐ 默认两步，别把人累死**（用户最强调的纠偏）。默认路径 = `选题 → AI 直接出草稿 → 人编辑定稿`。角度、审稿在后台自动，不摆用户面前。
2. **⭐ 采访是"缺料才补/不满意才叫"的工具，不是每次强制的关卡**。触发只有两种：① AI 出稿时自评真料不够、主动标缺口 ② 用户手动叫。
3. **⭐ 真料先行，绝不编造**（v3 断点一红线）。AI 只能用系统已记录的真料。缺料时**诚实标"这块没料"或发起小采访，绝不编本人经历/数字填结构**。这是红线，不是偏好。
4. **⭐ 创作器 ≠ 审稿器，禁止同一 agent 自写自评自打分**（用户强调的硬铁律，v3 断点二）。写稿、独立审稿是**不同的 AI 调用**，审稿只**指出**问题不动手改，修订是**单独一步**。
5. **⭐ 换脑审稿做成可配置策略**，默认 C（分层，省钱），用户可切 A（换模型，最独立最贵）/ B（同模型分身，最省最弱）。对应用户："前期先 C，不行换 A，沉淀再试 B"。
6. **只做一次定向修订**。审稿指出问题后至多让写稿 AI 改一次；第二次仍不好，回去换素材或角度，不继续硬润色措辞（v3 原则六）。
7. **⭐ 定稿存真**（口播特殊性）。AI 出的是草稿，人真人出镜会改口语化；**存档的永远是人定稿的真实版**（=实际念出来那版），AI 草稿单独另存一份。
8. **每条规则可落地**：触发条件 → 必需输入 → 执行动作 → 结构化产物 → 验收 → 失败处理 → 数据回流。答不出"在哪步执行/留什么产物/怎么回流"的，先不加。

---

## 3. 默认路径 vs 补救工具（🅐 的灵魂）

### 3.1 默认路径（顺的时候，人只干两步）

```
选题(已采用为 content)
   → 【AI 后台自动】自评真料是否够 → 选最佳角度 → 写草稿 → 独立审稿(默认必要时改一次)
   → 人看草稿 → 人编辑 → 定稿   ← 定稿 = stage 翻 scripted
```

用户视角只有：**打开这条内容，看到 AI 草稿（旁边附审稿意见和备选角度），改成自己要念的，点定稿。** 两步。

### 3.2 补救工具（只有不满意才逐级加码，全部可选）

| 触发 | 工具 | 动作 |
|---|---|---|
| 嫌角度不对 | 换角度 | AI 已备好 2-3 个备选，一键换选中角度并重出草稿 |
| 想微调方向 | 给提示重写 | 用户输入一句提示 → AI 带提示重写草稿 |
| AI 缺料 / 想喂新料 | 采访我 | **这时才采访**：AI 一次性列 5-8 个针对缺口的问题 → 用户一次答完 → 提炼进素材包 → 重出草稿 |
| 想再审一遍 | 重新审稿 | 按当前配置策略再跑一次独立审稿 |

### 3.3 系统后台自动扛的（不占用户精力）

- 出稿时 AI **自评真料够不够**，不够就在草稿上**标出缺口**（"这里我缺真实案例"），而不是偷偷编。
- **换脑审稿**默认开（纯 AI，成本有界，见 §6.3）。
- 用户每次的定稿改动、补的真料 → **自动沉淀**（§8 回流），下次更省。

### 3.4 与红线的关系（务必理解，别改错）

"AI 直接出稿"**不违反** v3"真料先行"红线。红线是"**不编造**"，不是"必须每次采访"。默认出稿时 AI 只用已有真料；料不够就**标缺口/发起采访**，两条诚实路径，绝不为填满结构造经历。采访是补料手段之一，不是唯一，也不是每次。

---

## 4. 状态机（做法 1：看板不动，加子阶段字段）

### 4.1 决策

一期物理 `stage`（`media_flow.STAGES`）**完全不动**：
```
idea → scripted → recording → editing → ready → published → reviewed
```
7 列看板保持粗颗粒。🅐 的认知过程用 `media_content.authoring_stage` **子字段**承载，只在 `stage=idea`（选题→脚本之间）活跃，进度**只在内容详情页展开**。

**被否决：** 把认知步骤拆成看板新列（列爆炸、伤手机、冲突 UI 重设计）；新增"创作中"大阶段（点进去才有内容，略绕）。

### 4.2 `authoring_stage` 取值（刻意做粗）

```
none      选题刚采用，未出稿
drafted   AI 已出草稿（含它后台自动做的：真料自评 + 选角度 + 审稿）
finalized 人已定稿  → 同时把 stage 从 idea 翻成 scripted，script = 定稿版
```

> 刻意只三档。"证据包 / 候选角度 / 审稿意见"不是用户要逐步点过的线性关卡，而是**详情页可展开的产物**（它们存在与否由对应数据行是否有决定）。补救工具触发时是瞬时操作，不单独占一个 stage。这样才对得上"默认两步"的现实。

### 4.3 落地位置

`media_flow.py` 加纯函数（可单测）：
- `AUTHORING_STAGES = ["none", "drafted", "finalized"]`
- `can_finalize(content) -> bool`（有 script 定稿内容才允许）
- `on_finalize(content)`：设 `authoring_stage=finalized`、`stage=scripted`、写 `finalized_at`

物理 `stage` 前进仍复用一期 `can_transition`。

---

## 5. 数据模型

### 5.1 `media_content` 新增列（走 `MIGRATIONS` 列表 ALTER TABLE）

```
authoring_stage TEXT DEFAULT 'none'    -- none/drafted/finalized
brief           TEXT DEFAULT ''        -- 本条任务简报(可空,默认由puzzle+idea_reason派生)
evidence_gap    TEXT DEFAULT ''        -- AI自评标注的真料缺口(空=料够)
selected_angle_id TEXT DEFAULT ''      -- 当前选中角度
ai_draft        TEXT DEFAULT ''        -- ⭐AI草稿原版(定稿时保留,功能B原料). script字段=人定稿版
revision_count  INTEGER DEFAULT 0      -- 定向修订次数,用于强制"至多一次"
finalized_at    DATETIME               -- 定稿时间
```

> **关键：`script` 语义微调** —— 一期 `script` 是"AI 写的脚本"；🅐 起 `script` 改为"**人定稿版**"，AI 草稿进 `ai_draft`。`ai_draft` 与 `script` 一对，就是功能 B（AI 学改稿）的现成原料。

### 5.2 新表 `media_evidence`（本条内容的真料素材包）

```
id, content_id, persona_id,
item(真料内容), item_type(experience经历/case案例/data数据/opinion观点/judgment判断),
source(interview采访得来/manual手填/material_reuse复用原料库),
from_material_id(若复用原料库,指向media_material.id,可空),
promoted_to_material_id(若已沉淀进原料库,回填,可空),
created_at
```

### 5.3 新表 `media_angle`（候选角度）

```
id, content_id,
angle(角度一句话), rationale(为什么这个角度打得中),
is_selected(0/1,默认AI选中的那个=1),
status(candidate/selected/rejected),
created_at
```

### 5.4 新表 `media_draft_review`（独立审稿意见，≠ 一期 media_review 的发布后复盘）

```
id, content_id,
reviewed_draft(审的哪一版草稿全文快照,防草稿变了对不上),
reviewer_strategy(layered/swap_model/same_model),
reviewer_model(实际用的模型,费用可追),
fact_flags(事实/数字/引用存疑,JSON),
persona_flags(不像本人/AI味/卖课味/焦虑词,JSON),
platform_flags(平台适配问题,JSON),
gap_flags(缺真料的地方,JSON),
risk_flags(红线/边界风险,JSON),
score(质量分1-5), verdict(pass直接可用/revise建议改一次/reject建议回素材或角度),
notes(总评), created_at
```

> **审稿器只写"发现",不动稿。** 修订是 `revise_draft` 单独一步（§6.2），产物物理隔离。

### 5.5 原料库雏形 `media_material`（🅐 与 🅑 的耦合点）

**为什么 🅐 就要建它**：让 AI "越用越省心"的正是**跨内容累积的真料池**。若真料只存在 per-content 的 `media_evidence`，下条选题又从零采访，飞轮不转。所以 🅐 建 `media_material` 表（沿用一期二期设计的核心列），并打通两个方向：

```
id, persona_id,
type(story故事/pit坑/judgment判断/opinion观点/data数据/quote金句),
title, detail(完整真料), brief(≤30字),
emotion(情绪底色), usable_scene(什么选题能用上),
audience_hit(打中哪个受众焦虑),
used_in(用过的content_ids JSON), use_count,
status(active/archived), created_at
```

**🅐 只需打通两条最小链路，完整原料库管理 UI 留 🅑：**
1. **出稿前读**：`write_script`/`propose_angles` 注入时，从 `media_material` 按 `usable_scene`/`audience_hit` 检索已有真料（走注入预算，见 §6.3），AI 优先复用。
2. **定稿后沉淀**：定稿时 AI **提炼**本条 `media_evidence` 里值得长期留的真料 → **建议**入 `media_material`（`proposed`，**人拍板才写库**，回填 `promoted_to_material_id`）。绝不自动入库。

> 若实现时觉得原料库雏形超出 🅐 工期，可退化为"仅 `media_evidence` per-content"先跑通，但必须在计划里显式标记"跨内容累积暂缺、飞轮省心效应未激活"，别默默砍掉让飞轮空转。

---

## 6. AI 能力（media_ai.py，全部独立调用、走 ai_router）

### 6.1 新增/升级的函数

| 函数 | 性质 | 输入 | 产物 |
|---|---|---|---|
| `interview_questions(db, content_id, model)` | 新增 | puzzle + 人设 + **原料库已有真料（只问缺口）** | 5-8 个针对性问题 |
| `extract_evidence(db, content_id, answers, model)` | 新增 | 用户一次性回答 | 结构化 `media_evidence` 条目 |
| `propose_angles(db, content_id, model)` | 新增 | 证据包 + 人设 | 2-3 个 `media_angle`，**默认选中最佳一个** |
| `write_script(db, content_id, mode, model)` | **升级** | 选中角度 + 证据包 + 人设(注入预算) | 写入 `ai_draft`；**真料不够则写 `evidence_gap` 不编造** |
| `critique_draft(db, content_id, model, strategy)` | 新增 | 草稿全文（**看不到"这是它自己写的"**） | `media_draft_review`（只挑毛病+打分，不改稿） |
| `revise_draft(db, content_id, review_id, model)` | 新增 | 草稿 + 审稿意见 | 改一次的新 `ai_draft`；`revision_count>=1` 则拒绝 |

> ⚠️ **命名避坑**：一期已有 `review_content()` = 发布后 L1 数据复盘，**别复用**。发布前草稿审稿一律走新的 `critique_draft()`。

### 6.2 创作 ≠ 审稿的物理隔离（硬铁律落地）

- `write_script` 和 `critique_draft` 是**两次独立 ai_router 调用**，各自独立 system prompt。
- `critique_draft` 的输入**只给草稿全文**，不告诉它"这是刚才那个 AI 写的"，避免自我背书。
- 审稿产物只进 `media_draft_review`（发现清单+分数+verdict），**不含改写后的文本**。
- 改写只由 `revise_draft` 做，且 `revision_count` 强制至多 1 次。

### 6.3 换脑审稿的可配置策略（settings.json）

设置项 `media_review_strategy`，默认 `layered`：

| 值 | 独立性 / 成本 | 行为 |
|---|---|---|
| `layered`（C，默认） | 中 / 省 | 审稿走独立调用+专用 system prompt；按路由规则选模型，有多个 key 时**优先选与写稿不同的模型**。角色分离但不强制换 provider |
| `swap_model`（A） | 高 / 贵 | **强制**审稿用与写稿不同的 provider（如写稿 DeepSeek → 审稿 Claude），最独立 |
| `same_model`（B） | 低 / 最省 | 同模型，仅靠独立调用+审稿专用 system prompt 分离角色 |

> 用户逻辑：前期用 C/A 要更强独立性抓问题；等体系/数据沉淀、信任建立后切 B 省成本。先做**全局一个开关**（现单人设够用），以后要按 persona 分再加列。

### 6.4 注入预算（沿用一期机制，别重造）

- 复用一期 `INJECTION_BUDGET`（media_context.py）和 `media_injection_log` 写入。
- 新调用登记各自的 `ai_type`：`interview` / `angles` / `draft` / `critique` / `revise`。
- 结构曲库（真实骨架 repertoire）**本轮先不提炼**（依赖用户 80 条历史语料，尚未就绪）；`write_script` 初期靠**人设声音 traits + ip-strategist 结构方法论原则**出稿。结构曲库提炼属打法库（🅑）+ 需用户供语料，并行/后续。**这是一条诚实边界：🅐 上线初期结构丰富度受限，随语料补齐提升。**

### 6.5 成本提示

🅐 一条内容 AI 调用从一期 1 次 → 约 4 次（证据提炼/角度/草稿/审稿，修订按需第 5 次）。默认审稿走 C（省）+ 只改一次 → 成本有界。缺料自评/采访只在需要时触发，不是每条都全跑。

---

## 7. ip-strategist 方法论如何被引用（走法 C）

**决策：设计时"翻译"进提示词，不做运行时依赖。** AI-PM 数据库 = 唯一事实源；ip-strategist = 判断蓝图。不在运行时读它的 markdown 文件。

| ip-strategist 资产 | 翻译进哪 |
|---|---|
| 诊断（诊）方法论、口播方法论、角度/钩子原型 | `propose_angles` + `write_script` 的 system prompt |
| 审稿标准（真实性/人格一致/不套路） | `critique_draft` 的检查维度 |
| v3 `fact-check-protocol` | `critique_draft` 的 `fact_flags` 判定规则 + `write_script` 的不编造硬约束 |
| v3 `DNA-Gaga` / `values` | 人设 traits（已在一期 `media_persona_trait`），注入即用 |

契约（契）本轮不做（§9），其"下注-验证"字段留到后续。

---

## 8. 数据回流（飞轮不断）

每一步产物都要有去处，否则违反"经验必须回流成资产"：

- **采访补的真料** → `media_evidence`（本条）→ 定稿时 AI 建议好的入 `media_material`（人拍板）→ 下条可复用。
- **人的定稿改动**（`ai_draft` vs `script` 差异）→ 本轮**只存**，是功能 B（AI 学改稿）现成原料，🅐 不做风格学习。
- **审稿发现的反复问题** → 留在 `media_draft_review`，后续（数据充足时）可提炼 lesson/redline（属 🅒，本轮不做分析）。

---

## 9. 明确不在 🅐 范围内（防蔓延）

- **契约**（发布前下注-可证伪）—— 用户选先跑通生产线，留后。
- **功能 B 的风格学习** —— 🅐 只存 `ai_draft`/`script` 差异，学习本身是独立功能 B。
- **靠真实数据的分析**：命中率、爆款/失败归因、benchmark 盲评胜率 —— 属 🅒🅓，随飞书数据回流后激活，非 🅐 前提。
- **结构曲库提炼** —— 依赖 80 条历史语料，属打法库（🅑）+ 用户供料，并行进行。
- **完整原料库管理 UI** —— 🅐 只建表+打通读写最小链路，管理界面留 🅑。

---

## 10. 页面与 UI 介入点

### 10.1 内容详情页 `/media/content/{id}` 新增"创作"区（stage=idea 时展开）

上下即工作流：
1. **草稿卡**：显示 `ai_draft`，旁边小标签标出 `evidence_gap`（若有）。按钮：`编辑定稿`（进编辑态，改完点定稿 → §4.3）、`给提示重写`、`采访我补料`。
2. **角度条**（可折叠）：列 `media_angle` 备选，当前选中高亮，点其它角度 = 换角度重出草稿。
3. **审稿意见卡**（可折叠）：显示 `media_draft_review` 的发现清单+分数+verdict，按钮 `照审稿改一次`（灰掉若 `revision_count>=1`）、`重新审稿`。
4. **素材包**（可折叠）：列 `media_evidence`，可手动加真料；定稿后显示"AI 建议入原料库"待确认项。

看板 `/media`（7 列）不加列，卡片可加个小圆点标 `authoring_stage`（none/drafted），点进详情才见细节。

### 10.2 设置页

新增 `media_review_strategy` 下拉（layered/swap_model/same_model，默认 layered），紧邻现有模型路由配置。

---

## 11. 技术约束（沿用一期，不重复解释）

- 前端 vanilla JS + 本地裁剪版 Tailwind；响应式用 `@media (max-width:767px)` 自定义 CSS，**不用 `md:`**；移动端使用频率可能更高。
- 颜色沿用统一色系：blue-600 主操作 / violet-600 AI 专属 / amber-600 警告；用新色阶前先查本地 tailwind.min.css。
- 已知坑：`TemplateResponse` 三参数 `(request, "x.html", ctx)`；Jinja2 无 `tojson`，用 `json.dumps()+| safe`；模板改动一律 Edit/Write，禁 PowerShell `-replace`（毁中文）；模板 dict 键别用 `items/keys/values/get`。
- 新表进 `database.py` 的 `SCHEMA`，`media_content` 加列进 `MIGRATIONS` 列表（ALTER TABLE）。
- AI 全走 `ai_router`（三模型路由+fallback+费用记账+`MAX_PROMPT_CHARS` 保护）。
- ⚠️ DeepSeek 模型名坑：一期踩过 `deepseek-chat` 被废，现用 `deepseek-v4-flash`，别写回旧名。
- AI 输出类型不可信：沿用一期 `_txt()`/`_clamp()` 兜底，新函数所有取值都走。
- 无 pytest-asyncio：核心逻辑（`media_flow` 的 authoring 函数、`extract_evidence`/角度选择的纯逻辑部分）写成**纯函数单测**；控制器用 TestClient + 伪造签名 session cookie live 测每个 AI 能力真调模型。

---

## 12. 验收标准（现在纯文本就能验，不等真实数据）

1. **默认两步跑通**：一个选题 → 打开详情 → AI 已出草稿 → 编辑 → 定稿 → `stage` 翻 `scripted`、`script`=定稿、`ai_draft`=原草稿都在。
2. **缺料不编造**：给一个 AI 明显没真料的选题 → 草稿标 `evidence_gap` 或提示采访，**不出现编造的"我曾经…"/假数字**（守红线）。
3. **一次性采访**：点采访 → AI 列 5-8 问 → 答完 → `media_evidence` 有条目 → 重出草稿用上了这些料。
4. **创作/审稿分离**：`write_script` 与 `critique_draft` 是两次不同调用；`media_draft_review` 只含发现+分数、不含改写文本；`revise_draft` 第二次调用被拒。
5. **审稿策略可切**：设置切 layered/swap_model/same_model，`reviewer_model` 相应变化并记进 `media_draft_review`。
6. **角度可换**：详情页列备选角度，换一个能重出对应草稿。
7. **定稿存真 + 差异留痕**：定稿后能取到 `(ai_draft, script)` 一对（功能 B 原料就位）。
8. **注入预算生效**：写稿注入条数不超一期 `INJECTION_BUDGET`；`media_injection_log` 记录新 `ai_type`。
9. **原料库雏形打通**（若纳入）：定稿后有"建议入原料库"待人确认项；已入库的真料下条选题能被检索复用。

**不能现在验、留真实数据后激活的**（写进设计但不作为 🅐 验收）：命中率、爆款/失败归因准度、benchmark 盲评胜率。

---

## 13. 待实测调整的部分

1. 采访问题数量（5-8）与提炼质量。
2. 审稿维度粒度：`critique_draft` 的 flags 分类会不会太细/太粗，是否让 AI 过度保守频繁 reject。
3. 三种审稿策略的真实成本/效果差异，用户切换的实际体感。
4. `evidence_gap` 自评的准确性：AI 会不会该标缺口时不标（硬写）或不缺也乱标。
5. 原料库雏形沉淀阈值：什么样的 evidence 值得建议入库，避免灌水。
6. 详情页"创作区"信息密度，跑通后按手机使用习惯调。
