# 自媒体二期 · 人设框架地基 设计 spec

**日期：** 2026-08-08
**所属：** ai-pm / 自媒体模块（media）二期
**性质：** 正式设计 spec（已过用户逐节评审，待写 writing-plans 实现计划）
**前置文档：**
- 二期阶段总结 `docs/superpowers/specs/2026-08-04-media-phase2-brainstorm-summary.md`（走法 C、5 块拆分、10 条设计铁律）
- 🅐 单条生产线 spec `docs/superpowers/specs/2026-08-04-media-phase2-A-single-content-pipeline-design.md`
- 一期设计 `docs/superpowers/specs/2026-07-24-media-ops-system-design.md`

---

## 一、这块是什么，为什么先做

**目标：** 搭一个 **AI 访谈引导流程**，把用户当前账号的人设一次性梳理出来，落成 AI-PM 里第一个真资产（一批 `media_persona_trait` 条目）。

**为什么被提前到二期第一块**（见阶段总结 §九）：
1. 用户账号定位调整过几次，老 50–80 条文案分属不同"定位时代"，直接喂会污染当前人设 → 必须先把**当前人设**独立梳理清楚。
2. **不依赖那 80 条历史文案**（访谈从用户脑子里挖，随时能开始），而 🅐 的写稿注入源和结构曲库都要踩在人设这块地基上。
3. 用户本来就要重新梳理框架（定位变了）。

**本轮主产物（用户拍板）：** 访谈流程本身（B 选项）。框架的维度结构随访谈需要落定，不单独先做纯 schema。

---

## 二、地基核心原则：访谈是"人设登记表"的冷启动入口，不另起炉灶

访谈产出的东西，和 L1 复盘（`review_content`）产出的东西，是**同一种资产、进同一张表、过同一道人拍板闸**。

| | L1 复盘（已有） | 人设访谈（本轮做） |
|---|---|---|
| 触发 | 内容发布后有数据了 | 冷启动 / 定位变了，主动开 |
| 来源 | 从真实表现**反推** | 从用户脑子里**引导挖出** |
| 产物 | candidate traits | candidate traits |
| 落点 | `media_persona_trait` | 同一张表 |
| 维度 | 那几个 dimension | 同一套 dimension |
| 生效 | 人拍板 adopt | 同一道闸 |

**收益：** 不发明新 schema、不发明新注入逻辑、不发明新审核闸。访谈只是**在没有数据时，用提问代替数据**去播种人设；以后有数据了，L1 复盘接着精修同一批条目。一套注册表，两个入口。

---

## 三、框架维度：7 个访谈模块 → 8 个 dimension

复用 `media_ai.py` 里已有的 dimension 词表（`positioning|audience|tone|topics|taboo|signature|differentiator`），新增一个 `anchor`。

| 模块 | 挖什么 | → dimension | 继承的 v3 资产 |
|---|---|---|---|
| ① 你是谁·定位 | 一句话定位、跟同类的不同、当前阶段 | `positioning` `differentiator` | — |
| ② 说给谁听·受众 | 目标人群、痛点分层（**只当待验证假设**） | `audience` | ICP-canvas |
| ③ 讲什么·选题域 | 内容主场、能持续讲的域、不碰的方向 | `topics` | — |
| ④ 怎么说·声音 | 人称视角、自嘲/平视、真实腔调、口头禅 | `tone` | DNA-Gaga |
| ⑤ 招牌·记忆点 | 标志观点/口号/固定桥段（少而硬，单独占注入槽） | `signature` | 写作零件 |
| ⑥ 红线·禁忌 | 不编造经历、AI味/卖课味/焦虑营销警报、身份错位禁忌 | `taboo` | values.md · fact-check |
| ⑦ 生意锚点 | 这个号最终为什么/怎么变现、生意目标 | `anchor` ← **新增** | 🅔 生意锚点 |

这 7 个模块 = 用户记忆里"ip-strategist onboarding 6 模块"的落地版本 + 生意锚点。**走法 C**：把 ip-strategist 的判断标准/诊契行/口播方法论"翻译"进访谈提示词，AI-PM 数据库当唯一事实源，**不运行时读外部 skill 文件**。

`anchor` 只是 dimension 词表加一个值，`media_persona_trait` 表结构不动（`dimension` 是 TEXT）。

---

## 四、阶段是一等公民（单人设 + 阶段线）

用户账号是**一个 IP 随时间演化**，不是几个割裂的号。阶段模型（用户拍板 A）：

- `media_persona.current_phase` = 当前定位时代的名字（如"AI落地实战期"）
- 每条 trait 的 `phase_tag` = 它属于哪个时代
- **换阶段** = 起个新 `current_phase` 名 → 老阶段的**阶段性** trait `status` 改 `archived`（**归档不删**，随时回看）→ 重跑访谈铺当前阶段
- **注入只喂当前阶段**

### 4.1 关键细分：不是所有条目都绑阶段

| 类型 | 换阶段时 | phase_tag |
|---|---|---|
| 定位 / 受众 / 选题域 / 生意锚点 | 会变，归档旧的 | 打当前时代名 |
| 红线 / 价值观 / 声音腔调 | 通常**跨阶段永久**（"不编造"不会因为改定位就不成立） | **留空 = 永久，不随换阶段归档** |

**注入规则 = 当前阶段专属条目（`phase_tag == current_phase`）+ 所有永久条目（`phase_tag == ''`）。**

访谈提炼条目时，AI 按模块性质给默认 phase_tag：①②③⑦ 默认打当前阶段名；④⑤⑥ 默认留空（永久），用户拍板时可改。

### 4.2 对老文案的意义

以后喂那 80 条历史文案时，同一个 `phase_tag` 机制把"AI落地期"的稿子和更早时代的稿子分开。学结构曲库（🅑/打法库）时只学当前时代的，老时代自动不污染。**本 spec 不做老文案导入**，只保证 `phase_tag` 语义一致、下游能接。

### 4.3 换阶段动作（本 spec 落地最小版）

`archive_phase_traits(persona_id, old_phase)`：把该 persona 下 `phase_tag == old_phase` 且属于阶段性维度（positioning/differentiator/audience/topics/anchor）的 active trait 批量置 `archived`；永久条目（phase_tag 空）不动。随后更新 `current_phase`。

不建阶段历史表——过往阶段名由 `SELECT DISTINCT phase_tag` 派生（YAGNI）。

---

## 五、访谈跑法

每个模块的闭环（复用用户定过的"甲"一次性问答格式 + 已有人拍板闸）：

1. **AI 出题**：`persona_interview_questions(persona, module)` 一次性列这个模块的 5–8 个引导问题。
2. **用户一次答完**：可以"跳过/不知道"，AI 不逼。答案是纯文本 blob。
3. **AI 提炼**：`persona_interview_extract(persona, module, answers)` 把答案提炼成 candidate 条目（打好 dimension / brief / phase_tag / confidence，`evidence` 存**用户原话**）。**绝不自动入库。**
4. **用户逐条拍板**：采纳 / 改字 / 丢弃（复用已有 adopt trait 机制）。
5. 采纳的 → `status='active'` 进注册表 → 下一模块。

### 5.1 关键性质

- **可断点续做**：7 模块不必一坐做完，一个模块一次。人设详情页显示"已完成 N/7 模块"（由"该 persona 下已有 active/candidate trait 覆盖了哪些 dimension"派生，不新建进度表）。
- **天然守住"创作器≠审稿器"铁律**：AI 只负责问和提炼，**拍板的是人（人就是审稿器）**，不存在 AI 自写自评自打分。
- **诚实边界**：AI 只把用户答的话提炼成条目，**不替用户编造人设**。用户没答的模块就空着，不硬凑。

### 5.2 红线注入：不开保底槽（用户拍板）

`build_script_context` 维持现状：只有 `signature` 单独占槽，`taboo` 跟普通条目竞争那 8 个位。理由：**通用红线（不编造等）已经硬写在 `SCRIPT_SYSTEM` 提示词里永远在**，访谈提炼的 `taboo` 是**账号专属**禁忌，正常按 confidence 竞争即可，不需要额外保底机制。保持注入逻辑简单。

---

## 六、代码改动清单（聚焦，不碰逻辑主干）

| 文件 | 改动 |
|---|---|
| `app/services/media_ai.py` | 加 `persona_interview_questions(persona, module)` + `persona_interview_extract(persona, module, answers)`；dimension 词表（约 610 行枚举 + 任何 propose 提示词）加 `anchor` |
| `app/services/media_context.py` | `build_script_context` 注入过滤加一道**当前阶段**（当前阶段专属 + phase_tag 空的永久）；新增纯函数 `is_injectable(trait, current_phase)` 便于单测 |
| `app/services/media_flow.py`（或新纯函数） | `MODULE_DIMENSIONS` 映射常量（module→dimensions + 默认 phase 策略）；`archive_phase_traits` 纯逻辑 |
| `app/api/media.py` | 路由：`/media/persona/{id}/interview`（模块选择页）、`/interview/{module}/questions`、`/interview/{module}/extract`、`/persona/{id}/new-phase`；拍板复用已有 adopt trait 端点 |
| `templates/media_persona*.html` | `persona_interview.html`（finesse 设计系统 `.module/.btn.ai/CSS变量`，非 Tailwind）；人设详情页显示"已完成 N/7"进度 |

**不动：** 表结构（`media_persona` / `media_persona_trait` 现有列够用；`anchor` 走 dimension 值，`phase_tag`/`status`/`evidence`/`confidence` 已存在）。**不碰** `app/services` 里 L1 复盘等其它逻辑。改模板一律用 Edit/Write（禁 PowerShell -replace 毁中文）。

---

## 七、数据模型（复用现有，零新表）

`media_persona`（现有）：`current_phase` 承载当前定位时代名。

`media_persona_trait`（现有，字段全部够用）：
- `dimension`：新增可选值 `anchor`
- `phase_tag`：阶段性维度打当前阶段名；永久维度留空
- `source`：访谈来的标 `interview`（区别于 `manual` / L1 的 `ai_from_review`）
- `evidence`：存用户访谈原话
- `confidence`：AI 提炼时给初值，用户可改
- `status`：`candidate`（待拍板）→ `active`（采纳）/ 丢弃即不入库；换阶段 → `archived`

---

## 八、AI 能力规格

### `persona_interview_questions(persona, module) -> {questions: [...], cost, model}`
- 输入：persona 基本信息（name/one_liner/current_phase）、模块标识。
- 系统提示词内嵌该模块的挖掘目标 + ip-strategist 对应判断标准（如 ②受众要引导"痛点分层、只当假设"；⑥红线要引导"AI味/卖课味/编造经历"警报）。
- 输出：5–8 个引导问题（JSON array）。走 `extract_json`。

### `persona_interview_extract(persona, module, answers) -> {traits: [...], cost, model}`
- 输入：模块标识 + 用户一次性答完的文本。
- 系统提示词铁律：**只提炼用户答里有的，缺的标空不编**；每条给 `dimension`（限本模块允许的维度）/`content`/`brief`(≤30字)/`evidence`(原话)/`confidence`/`phase_tag`（按模块默认策略）。
- 输出：candidate traits（JSON array）。**不写库**，返给前端待拍板。
- 走注入预算无关（这是产出侧，不是注入侧）；但要记 `log_injection` 记录本次 AI 调用（ai_type=`persona_interview`）以便三期分析。

两个函数都用 `ask_ai` 现有签名，模型走 `resolve` 默认路由。

---

## 九、验收标准

1. 冷启动空库能进 `/media/persona/{id}/interview`，7 模块逐个出题、答题、提炼、拍板，采纳的条目出现在人设详情页对应 dimension 下。
2. 提炼出的条目 `phase_tag` 按模块默认正确（①②③⑦ = 当前阶段名；④⑤⑥ = 空）。
3. 断点续做：做完 ①③ 关掉，重进能看到"已完成 2/7"，继续做 ② 不重复已采纳条目。
4. `build_script_context` 注入验证：造两条 trait（一条当前阶段、一条别的阶段名），写稿注入只带当前阶段那条 + 永久条目；别的阶段条目不进 prompt。
5. `new-phase` 后：旧阶段的 positioning 类条目变 `archived` 且不再注入；永久红线仍在。
6. AI 诚实边界：给一段"跳过/不知道"的答案，extract 不硬编条目（返回空或极少）。

---

## 十、测试策略

无 pytest-asyncio，沿用项目惯例：
- **纯函数单测**：`MODULE_DIMENSIONS` 映射、`is_injectable(trait, current_phase)`（当前阶段/永久/别的阶段/archived 四种）、`archive_phase_traits` 的选择逻辑、`extract_json` 复用。
- **控制器 live 测**：`TestClient` + 伪造签名 session cookie（不输真实密码），隔离到临时 DB（monkeypatch `app.database.DB_PATH` + tmp_path，**绝不写真实 aipm.db**——见 🅐 执行教训：真库被锁导致连接泄漏死循环）。真调 AI 的能力（questions/extract）需配 key，逐模块 live 验一次。
- 跑测试用 `Start-Process` 直接重定向到文件，**不用 `python -m pytest | Select-Object`**（管道缓冲会被误判为挂起）。

---

## 十一、明确划在本 spec 外

- 老 80 条历史文案的**导入/打 era 标签**（属 🅑 结构曲库前置，下一块做，本 spec 只保证 `phase_tag` 语义能接）。
- 结构曲库 / 打法库提炼。
- 契约、功能 B 风格学习、靠真实数据的复盘归因 / benchmark 盲评。
- 红线保底注入槽（用户明确不做）。
- 阶段历史表（YAGNI，用 DISTINCT phase_tag 派生）。

---

## 十二、动手前必须遵守的约束回顾

- 走法 C：ip-strategist 是设计蓝图，翻译进提示词，**不做运行时依赖**。
- AI 提炼、**人拍板**才生效（candidate → active），绝不自动入库。
- 诚实红线：AI 不替用户编造人设，缺料标空。
- 改模板用 finesse 设计系统 + Edit/Write（禁 PowerShell -replace）。
- 测试隔离临时 DB，不污染真实 aipm.db。
