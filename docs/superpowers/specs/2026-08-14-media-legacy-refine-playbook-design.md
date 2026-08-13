# 老文案/视频炼化 + 打法库🅐 设计

**日期：** 2026-08-14
**状态：** 设计已定，待写实施计划
**触发词：** 「接 AI-PM」

---

## 1. 目标与定位

把用户整理的 **~60 条老文案 + ~20 条老视频**炼化成三类资产，并新建**打法库**（决策引擎 `playbook` 因子 / 写稿注入的落点，本轮只建资产、不接消费端）。

**核心 realization（brainstorm 收敛）：** 老文案和老视频**本质都是「反向补录已发内容」**——视频要 ASR、文案已有文字。所以**不建平行系统**：统一走「反向补录 → media_content（已发/反向）→ 挖精华」，只在挖精华上**加第三桶「结构/打法」**，并新建打法库装它。极致复用功能C（反向视图 + 挖精华），符合"不打架/别分散注意力"。

**三桶分流（各归各家）：**
| 桶 | 提什么 | 去哪 | 新/复用 |
|---|---|---|---|
| 真实经历/故事/素材 ⭐最值钱 | 经历的坑/案例/判断/金句 | 原料库 `media_material` | 复用挖精华 |
| 口头禅/像我 | 说话腔调 | 人设 `signature`（trait） | 复用挖精华 |
| 结构/打法（仅 winner） | 骨架 + 为什么成 | 打法库 `media_playbook` | **本轮新建** |

---

## 2. 用户操作流（顺序：全部入库 → 批量选 winner → 逐条挖）

1. **全部入库**：60 文案批量粘（一次建多条 media_content）；20 视频走反向入库（本轮单条、已跑通）。都落 media_content（stage=published，idea_source=`legacy_text`/`video_reverse`）。
2. **一次性批量选 winner**：管理列表页把反向补录内容列出，复选框批量勾"爆过的" → 标 `media_content.is_winner=1`。一次选完省时间（老内容没 metrics，爆款用户说了算）。
3. **逐条挖精华**（一条一条，守注意力纪律②）：两桶照旧（经历→原料库、口头禅→signature）；`is_winner=1` 的**自动多挖结构桶** → 打法库候选。

---

## 3. 注意力纪律（用户核心顾虑，贯穿全程）

1. **有限注意力**：这轮**零下游注入**——打法库只"装着"，**不碰写稿 AI、不碰决策引擎**（🅒🅓 后面单独轮）。以后消费铁律（拿选题匹配那一条、只注入一条，绝不列全库）**写进 spec 备忘，本轮不实现**。你天天用的页面/写稿流程这轮完全不变重。
2. **一次一条**：挖精华每次只喂一条内容给 AI，绝不把 60 条挤一个 prompt。
3. **档案柜不是会议桌**：库收敛——抽结构时喂**已有打法名**给 AI，先判"是不是已有打法的又一例"，是就归并（补 evidence 不新增），只有真不同才提新。20 条爆款收敛成一小撮打法。
4. **暂存薄**：不建独立暂存台——media_content（反向）当天然暂存，现成列表看进度/筛 winner；候选（原料/口头禅/打法）过手就走，采纳写目的地、没采纳丢掉，不落半成品。

---

## 4. 数据模型

**`media_content` 加一列**（idempotent ALTER 迁移）：
```python
"ALTER TABLE media_content ADD COLUMN is_winner INTEGER DEFAULT 0",
```
手动标记的"爆款"，驱动是否挖结构桶。

**`media_playbook`（打法库资产，新表，走 SCHEMA 零迁移）：**
```sql
CREATE TABLE IF NOT EXISTS media_playbook (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    name TEXT DEFAULT '',           -- 打法名（如"痛点自曝法"）
    structure TEXT DEFAULT '',      -- 骨架步骤 + 为什么成
    when_to_use TEXT DEFAULT '',    -- 什么选题适用
    evidence TEXT DEFAULT '',       -- 出自哪条爆款（可多条累积）
    source TEXT DEFAULT '',         -- 来源 slug（legacy_mine 等）
    status TEXT DEFAULT 'validating', -- validating / proven
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

---

## 5. 进料：反向补录「批量粘文本」入口

- 反向补录页加一个「粘文本批量入库」入口：一个 textarea，用户粘多条老文案，**用分隔线切分**（如空行分隔或 `---` 分隔），每段建一条 media_content：
  - `stage='published'`，`idea_source='legacy_text'`，`script=该段全文`，`title=` 该段首句截断（或"老文案 N"），`is_winner=0`，`published_at` 留空（老内容日期未知，可后编辑）。
- **不走 ASR、不走 yt-dlp**——文本已有，直接建行。一次粘多条一次建多条（"全部入库"省时间）。
- 路由 `POST /media/reverse/paste-text`（body=多条文本）→ 切分建行 → 返回建了几条。

**`is_reverse` 判定扩展**：`content_detail`（media.py:993）现在 `is_reverse = idea_source == 'video_reverse'`，**改成 `idea_source in ('video_reverse','legacy_text')`**——让 legacy_text 内容也走反向精简视图 + 挖精华（复用功能C 的反向视图）。

---

## 6. Winner 批量选择

- 新管理页 `/media/legacy`（或复用现有内容列表加筛选）：列出 `idea_source in ('video_reverse','legacy_text')` 的内容，每行复选框 + 当前 winner 标记 + 挖没挖过（可选：靠是否已有该内容来源的原料/打法粗略判断，或简单不显示）。
- 批量操作：勾若干 → 「标为爆款」→ `POST /media/legacy/mark-winner`（content_ids[], winner=1/0）→ `UPDATE media_content SET is_winner=? WHERE id IN (...)`（校验都属当前 persona）。
- 也允许单条在内容详情页切换 is_winner（小按钮）。

---

## 7. 挖精华 + 第三桶「结构/打法」

- **前两桶不动**：`mine_from_transcript`（materials→原料库 / signatures→signature）原样复用。
- **加结构桶**：新函数 `mine_structure(db, persona_id, transcript, existing_playbook_names, model)` —— 只在内容 `is_winner=1` 时调。AI 从这条爆款转写稿提炼**一个**打法候选：`{name, structure, when_to_use, evidence, similar_to}`。
  - `structure`：骨架步骤 + 为什么成（如"前3秒抛痛点问题→自曝踩坑→给3步方法→反问收尾｜靠真实踩坑建信任"），抽象可复用、不逐字模板。
  - `similar_to`：AI 判断这条是否已有打法的又一例——是则填已有打法名（人采纳时归并到那条、补 evidence），否则空（提新打法）。喂 `existing_playbook_names` 实现纪律③收敛。
  - `MINE_STRUCTURE_SYSTEM` 提示词：诚实只从这条真爆款提炼可复制结构；不空泛；一条只提一个主打法；给 similar_to 判断。
- **挖精华路由扩展**（`POST /media/content/{cid}/mine`）：查 content.is_winner，为真则额外调 `mine_structure`（传当前 persona 已有 playbook name 列表），返回里多 `playbooks` 候选。成本各自 log_injection。
- **采纳**：结构候选 → `POST /media/content/{cid}/mine/adopt-playbook`：
  - `similar_to` 命中已有打法 → 往那条 `evidence` 追加（不新增行）。
  - 否则新建 `media_playbook` 行（source='legacy_mine'，status='validating'）。
  - 前两桶采纳复用现有 `mine/adopt-material` + signature adopt（`persona_interview_adopt` source='reverse_mine'，白名单已含）。

---

## 8. 打法库浏览页

- `/media/playbook`：按 status 分组（proven → validating），卡片仿受众/锚点风：name / structure / when_to_use / evidence（出自哪些爆款）。
- 卡片操作：`validating ↔ proven` 状态切换（人手动，`POST /media/playbook/{id}/status`）、编辑、删除（软或硬，小工具）。
- 入口：自媒体导航或人设页挂个链接。
- **本轮不接决策引擎、不接写稿**（🅒🅓 留后）。

---

## 9. 复用地图（省代码、不打架）

| 件 | 复用 | 新建 |
|---|---|---|
| 反向内容视图 + 挖精华前两桶 | ✅ 功能C 全套 | — |
| media_content / media_material / signature adopt | ✅ | — |
| 反向补录 | 视频链接入口复用 | +粘文本入口 |
| is_reverse 视图 | ✅ | +认 legacy_text |
| 结构桶 | — | mine_structure + MINE_STRUCTURE_SYSTEM |
| 打法库 | — | media_playbook 表 + 浏览页 + 采纳/状态路由 |
| winner | — | is_winner 列 + 批量选页 |

---

## 10. 质量与测试

- **纯计算/切分可单测**：粘文本切分（分隔符→N 段，空段忽略）；mark-winner 归属过滤。
- **AI human-in-loop**：`mine_structure` 候选绝不自动写库；结构桶只在 is_winner 时触发（非 winner 不调、省钱）；similar_to 归并 vs 新建正确分流。
- **路由测**：paste-text 建 N 条（idea_source=legacy_text/stage=published）；mark-winner 批量改 is_winner（跨 persona 不改）；mine 对 winner 返 playbooks 候选、非 winner 不返；adopt-playbook 归并（追 evidence）vs 新建；playbook status 切换；is_reverse 认 legacy_text。
- **四条标准动作**：人拍板 / 诚实（结构只从真爆款、similar_to 收敛）/ 只给精华（一条一个主打法）/ 成本可见（结构桶单独 log_injection）。
- **回归**：不动决策引擎 / 写稿 / L1L2L3 / 前两桶挖料逻辑；只在人点击时写 is_winner/media_playbook。
- **浏览器冒烟**（controller 亲跑）：粘文本入库、批量标 winner、winner 内容挖精华出结构桶、采纳进打法库、打法库页渲染，无 Jinja/500。

---

## 11. 范围（做 / 不做）

**做：** §4-8 全部（进料粘文本 / winner 批量 / 结构桶 / 打法库表+页+采纳）。

**不做（留后，各独立）：**
- **视频批量粘链接**（20 条这轮单条已能用，批量=下一小轮便利）。
- **🅒 接决策引擎 w4**（playbook 因子从 0 接上）——单独轮。
- **🅓 接写稿注入**（选题匹配打法当骨架）——单独轮，届时守纪律①③（匹配一条、只注入一条）。
- **80 条全量 / 视频批量 ASR 排队**——这轮 ~60 文案 + 单条视频够用。

---

## 12. 落点清单（实施用）

- `app/database.py`：`MIGRATIONS` +`media_content.is_winner`；`SCHEMA` +`media_playbook` 表。
- `app/services/media_ai.py`：+`mine_structure` + `MINE_STRUCTURE_SYSTEM`。
- `app/services/media_playbook.py`（新，可选）或直接在 media.py：打法库 list/get/adopt/status/dedup 逻辑。
- `app/services/media_reverse.py` 或 media.py：粘文本切分建行 `paste_text_ingest`。
- `app/api/media.py`：+`POST /media/reverse/paste-text`、`/media/legacy`(页)、`/media/legacy/mark-winner`、`/media/content/{cid}/mine` 扩展、`/media/content/{cid}/mine/adopt-playbook`、`/media/playbook`(页)、`/media/playbook/{id}/status`；`is_reverse` 认 legacy_text。
- `app/templates/`：反向补录页+粘文本入口、legacy 批量选页、media_content.html 反向视图挖精华加结构桶展示、media_playbook.html（新浏览页）。
- 测试：`test_media_playbook*.py`（切分/结构桶校验/打法库路由/采纳归并）。

---

## 13. 开放问题 / 留后

- **粘文本切分符**：默认空行 or `---`？实施时定一个明确的、并在输入框提示。
- **打法 status 升级**（validating→proven）：本轮纯手动；以后可接真实复用数据/L2 印证自动建议。
- **winner 与 media_case hit 的关系**：is_winner 是手动老内容标记；media_case.hit 是有 metrics 的自动判定。两者独立不冲突，决策引擎/L2 各用各的。
