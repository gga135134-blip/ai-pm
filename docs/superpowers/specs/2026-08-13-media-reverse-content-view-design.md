# 反向补录内容视图 + 从转写稿挖精华 — 设计

**日期：** 2026-08-13
**性质：** 功能C（视频反向入库）的配套——给"反向补录的已发内容"换一套贴合其身份的界面，并从转写稿挖精华（内容素材 + 口头禅）。
**前置：** 功能C 已上线（`idea_source='video_reverse'` 的 media_content）；人设框架（signature 维度 + 人拍板 adopt）；原料库（media_material + MATERIAL_TYPES + 人拍板）。

---

## 一、背景与目标

反向补录的内容是**一份"已发实况档案"**，可它现在掉进了"创作台"界面（口播脚本编辑器 + AI写脚本/重写/精简注入 + 三平台发布/生成文案）。这些是**"从零创作新内容"**的工具，对一条**已经发出去**的视频是噪音：不会再写脚本、不会再发布。

**目标：**
1. **换脸**：内容详情页认出 `idea_source='video_reverse'`，渲染一套"已发实况档案"的精简视图——砍掉创作台噪音，foreground 转写稿/链接/来源/发布时间 + 挖精华 + 已发记录。
2. **挖精华**：从这条真实转写稿里挖出**两桶**可复用资产——**内容素材**（进原料库）+ **口头禅/记忆点**（进人设 signature，让后面稿子越来越像用户）。AI 只给精华（粗筛掉废话），人拍板入库。
3. **修 bug + 加发布时间**：反向入库现在把 `published_at` 存成了"入库时刻"（错的）。加真实视频发布时间，尽量自动抓（yt-dlp `upload_date`），视图可手改。

**明确不做（留后，各自规划）：** 复盘（需数据，另规划）、功能B-v2「存稿↔实况匹配对照」（需正向↔反向匹配，只在本视图留空位钩子）。

---

## 二、核心决策（brainstorm 拍板）

| 决策点 | 选定 |
|---|---|
| 视图触发 | `idea_source='video_reverse'` 作开关，模板条件渲染 |
| 挖料范围 | 做（不只换脸）：从转写稿挖精华 |
| 挖料两桶分流 | **内容素材→原料库；口头禅/记忆点→人设 signature**（两个目的地，都走人拍板）|
| 挖料展示 | **AI 只给精华**——AI 按标准粗筛，废话/边缘料直接不提，只端最值钱的几条 |
| 发布时间 | 加 `media_content.published_at`；反向入库尽量从 yt-dlp `upload_date` 自动填，视图可手改 |
| 功能B/复盘 | 本次不接，只留钩子（复盘入口保留不动） |

---

## 三、为什么两桶分流、且不跟人设 7 件套打架

- **金句 vs 口头禅是两回事**：金句=你说的一句有记忆点的**话**（内容层，可当素材复用）→ 原料库；口头禅=你习惯的**说话方式/腔调**（"你要知道…""说白了…"）→ 人设 signature。**口头禅进人设才有用**：signature 条目会被注入写脚本，AI 照你的腔调写；进原料库只能躺着当素材，进不了写稿注入。
- **不打架（已核对人设框架 spec）**：人设框架规定 signature 条目"**同一张表 `media_persona_trait`、同一道人拍板闸、多个来源**"（人设访谈/L1复盘/功能B 都往里塞）。**反向转写就是又一个 signature 来源**，天然一致。这不是留后的功能B-v2（那个要正向↔反向匹配），本桶不需匹配，直接从转写稿挖口头禅→人设，独立成立。

---

## 四、挖料的判断标准（写进提示词，AI 照此粗筛）

### 桶 A — 内容素材（→ 原料库 `media_material`，类型走现有 MATERIAL_TYPES）
**✅ 留（是真料，满足"未来别的内容能复用 + 体现真实经历/独特视角"）：**
- **story 故事**：具体真实事件/案例（有人物有情节，不是泛泛"我经历过"）
- **pit 踩过的坑**：真实教训/弯路（"我当时以为…结果…"）
- **judgment 判断 / opinion 观点**：有信息量的独到看法（不是正确的废话）
- **data 数据素材**：具体数字、实证
- **quote 金句**：有记忆点、能单独拎出来用的表达

**❌ 丢（AI 直接不提）：** 开场问候/自我介绍套话、"点赞关注收藏"引导/平台套话、纯过渡句、泛泛而谈没信息量、重复啰嗦。

### 桶 B — 口头禅/记忆点（→ 人设 `media_persona_trait` dimension=`signature`）
**✅ 留：** 反复出现、标志性、一听就"这是你"的表达/句式/腔调。
**❌ 丢：** 谁都在用的口水词（"然后…""这个…""嗯"这种通用填充）、偶发一次没成习惯的。

### 通用
- **AI 只给精华**：每类只端最值钱的几条（软上限，如每类 ≤3、口头禅 ≤5），废话和边缘料不提。
- **诚实不编造**：只从真实转写稿里挖，每条 `evidence` 引原文片段。
- 每条候选带 AI 一句"**为什么值得留**"。

---

## 五、视图结构（`idea_source='video_reverse'` 时）

| 版块 | 内容 | vs 创作台 |
|---|---|---|
| **头部** | 标题/谜题(可改) + 「🎬 反向补录·已发」标签 + 原视频链接(可点开) + 📅 发布时间(可改) | 加发布时间 |
| **平台转写稿** | 转写全文，标「平台转写·可改错字」，可编辑保存（复用现有 `/script` 保存路由） | 不再是"AI写脚本"框，砍掉 AI写/重写/精简注入 |
| **🔬 从转写稿挖精华** | 按钮 → AI 挖两桶精华 → 分「内容素材」「口头禅/记忆点」两组列出，每条带类型/内容/原文依据/为什么值得留 → 逐条采纳（内容料→原料库；口头禅→人设 signature）/丢弃 | 全新 |
| **已发记录** | 该内容的 publish 记录（平台+链接）+ 手填 metrics（播放/点赞…，供复盘/决策） | 三平台发布降级：砍掉"生成文案/发布"authoring |
| **🔗 关联到我写过的内容** | 空位钩子（占位提示"（规划中）反向实况↔正向存稿对照，喂功能B学改稿"，先不接） | 全新占位 |
| **复盘入口** | 保留现有「AI 复盘」块不动（需数据，另规划） | 不变 |
| **阶段机** | 显示"已发"，允许「推进到已复盘」 | 保留（自然只剩 已发→已复盘） |

**创作台被砍的块**（reverse 时不渲染）：二期🅐创作辅助（采访补料前置子流程）、口播脚本的 AI写脚本/重写/精简注入按钮、三平台发布的"生成文案/发布"。

---

## 六、数据模型

**加 1 列（idempotent ALTER 迁移）：**
- `media_content.published_at DATETIME`（真实视频发布时间，可空）。

**修 bug：** 反向入库现在 `INSERT media_publish ... published_at=CURRENT_TIMESTAMP`（入库时刻，错）。改为写真实发布时间（拿到就用，拿不到留空/回落）。同时把 content 的 `published_at` 一并写。

**发布时间来源：**
1. `video_fetch.fetch_audio` 顺带取 yt-dlp 的 `upload_date`（YYYYMMDD），编排器 `reverse_ingest` 写进 `media_content.published_at` + `media_publish.published_at`。
2. 视图可手改（补 80 条老视频时 AI 抓不到就手填）。

**挖料落点（复用现有表，零新表）：**
- 桶 A → `media_material`（source='反向挖料'），走现有原料库入库路径。
- 桶 B → `media_persona_trait`（dimension='signature', source='反向挖料', phase_tag='' 永久），走现有人设 adopt 路径。

---

## 七、组件与接口

### 7.1 `media_ai.py` 新增 `mine_from_transcript`
```python
async def mine_from_transcript(transcript: str, persona: dict, model: str = "auto") -> dict:
    """从转写稿挖两桶精华候选。绝不写库——返回候选，人拍板 adopt 才入。
    返回 {"ok", "materials":[{type,content,brief,evidence,reason}...],
          "signatures":[{content,brief,evidence,reason}...], "error", "cost", "model"}"""
```
- `MINE_SYSTEM` 提示词编码第四节的两桶标准 + 只给精华 + 诚实引原文 + 软上限。
- 全字段走 `_txt()` 兜底；material.type 夹回 MATERIAL_TYPES；调用后 `log_injection`。
- persona 传入（one_liner/positioning）帮 AI 判断"对这个人设算不算值钱料"，但不硬绑（YAGNI，主要靠通用标准）。

### 7.2 `video_fetch.py` 取 upload_date
- `fetch_audio` 增强：yt-dlp 调用带 `--print-to-file` 或复用信息，返回 `(audio_path, upload_date)`。upload_date 取不到则 `None`。（具体 yt-dlp 参数实现时定，倾向 `--print "%(upload_date)s"` 配合下载或 `--write-info-json` 解析。）
- 签名兼容：返回值改成 dataclass 或 tuple，编排器与测试同步。

### 7.3 `media_reverse.py` 写发布时间
- `reverse_ingest` 拿到 upload_date（YYYYMMDD → DATETIME）写进 `media_content.published_at`；publish 记录 `published_at` 同步（拿不到则留空，不写成 CURRENT_TIMESTAMP）。

### 7.4 `media.py` 路由
- **改** `content_detail`：传 `is_reverse = content['idea_source']=='video_reverse'` 进模板。
- **新** `POST /media/content/{cid}/mine` → `mine_from_transcript`，返回候选 JSON（不写库），try/except 兜底。
- **新** `POST /media/content/{cid}/mine/adopt-material` → 采纳一条内容料写 `media_material`（复用现有入库逻辑，source='反向挖料'）。
- **口头禅采纳**：复用现有 `POST /media/persona/{pid}/interview/adopt`，传 `source='反向挖料'`、`dimension='signature'`。
- **新** `POST /media/content/{cid}/published-at` → 保存手改的发布时间（或并入现有 content 编辑路由）。
- 转写稿编辑复用现有 `POST /media/content/{cid}/script`。

### 7.5 `media_content.html` 视图分支
- 顶层 `{% if is_reverse %}` 渲染精简视图，`{% else %}` 保持现有创作台。
- 精简视图各块套现有样式（`.module/.mh/.btn`）。
- 挖精华 AJAX：POST `/mine` → 渲染两组候选 → 逐条采纳（分别打对应 adopt 端点）/丢弃。**不塞 SVG 图标进 JS 字符串**（已知崩坑），失败存 `orig` 还原。

---

## 八、锁死的原则

1. **人拍板**：两桶都是 AI 只提候选，采纳才入库。
2. **诚实不编造**：只从真实转写稿挖，evidence 引原文。
3. **只给精华**：AI 粗筛掉废话/边缘料，不 flood。
4. **成本可见**：`mine_from_transcript` 记 `log_injection`。
5. **不打架**：signature 条目多来源同表同闸（人设框架），反向挖料是又一来源。
6. **功能B-v2 / 复盘不接**：只留钩子/入口。
7. **前端 JS 铁律**：模板 JS 真浏览器验；SVG 图标别进 JS 字符串。

---

## 九、测试策略

- `mine_from_transcript` 取数/夹取：monkeypatch ask_ai，断言 material.type 夹回 MATERIAL_TYPES、signature 维度、_txt 兜底、log_injection 被调。
- `video_fetch` upload_date：桩 subprocess，断言解析出 upload_date；取不到返回 None。
- `reverse_ingest` 写 published_at：桩 fetch 返回 upload_date，断言 content/publish 的 published_at 落库正确；拿不到时不写成入库时刻。
- 路由：`/mine` 打桩返回候选断言 JSON；`/mine/adopt-material` 写 media_material(source='反向挖料')；signature 走 interview/adopt(source='反向挖料',dimension=signature)；`/published-at` 保存。
- 视图分支：`is_reverse=True` 渲染精简块、不渲染创作台块；`False` 保持原样（TestClient 断言关键文案）。
- 挖精华 AJAX 真浏览器验（无 SVG-in-JS 崩、两组候选渲染、采纳打对端点）。

---

## 十、代码落点

| 文件 | 改动 |
|---|---|
| `app/database.py` | +1 列 `media_content.published_at`（SCHEMA + MIGRATIONS）|
| `app/services/video_fetch.py` | `fetch_audio` 取 yt-dlp upload_date，返回 (path, upload_date) |
| `app/services/media_reverse.py` | `reverse_ingest` 写 `published_at`（content+publish），修 CURRENT_TIMESTAMP bug |
| `app/services/media_ai.py` | +`mine_from_transcript` + `MINE_SYSTEM` |
| `app/api/media.py` | `content_detail` 传 is_reverse；+`/mine`、`/mine/adopt-material`、`/published-at` 路由 |
| `app/templates/media_content.html` | `{% if is_reverse %}` 精简视图 + 挖精华 AJAX |
| tests | mine / video_fetch upload_date / reverse published_at / 路由 / 视图分支（+若干） |

---

## 十一、非目标（YAGNI）

- 复盘的数据来源与提炼逻辑（另规划）。
- 功能B-v2「反向实况↔正向存稿」匹配对照（只留钩子）。
- 挖料自动入库（违反人拍板）。
- 反向内容的 account 归属修正（沿用"挂第一个 account"，本次不做）。
- 挖料价值分（用户选"AI 只给精华"，不做打分）。
- 挖料 persona 深度定制（通用标准起步）。
