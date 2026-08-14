# 老文案批量挖矿（两条流）设计 spec

**日期：** 2026-08-14
**分支：** feat/media-batch-mine
**触发词：** 接 AI-PM

## 1. 目标

把"一条条点挖精华"升级成**两条批量流**，解决 70 条老文案没法逐条手挖的痛，且守住"AI 一次只喂一条"的纪律：

- **流A · 挖记忆点**（口头禅 → 人设 signature）：从**所有**老文案批量挖。
- **流B · 挖精华**（素材 → 原料库 + 打法 → 打法库）：**只从爆款**批量挖。

挖出的候选去重后**复选批量采纳**；挖过的老文案有**标识**，重挖默认跳过（省钱）。

**本轮不做**：下游"内容/写稿怎么引用挖出来的库"（写稿已在用这三个库，那块不动，另议）。单条内容页的"挖精华"保留不变。

## 2. 方法论依据（用户拍板，与项目哲学一致）

「**打法/素材只从真爆款抽**（不爆的是噪音）；**口头禅从所有老文案抽**（腔调就是腔调，不看爆没爆）」。所以两条流的取材范围不同：记忆点=全部，精华=仅爆款。

## 3. 纪律（最高优先级）

**AI 一次只喂一条老文案**——批量=前端**逐条**调 AI，绝不把多条挤进一个 prompt。批量是"自动排队 + 进度"，不是"合并喂"。

## 4. 架构总览

```
老文案页(/media/legacy)                        复核页(/media/mine-review)
 ├ 复选老文案                                    ├ 待采纳候选(去重分组)
 ├ [批量挖记忆点]─┐                              ├ 复选 → [采纳选中]
 └ [批量挖精华]──┤  前端逐条循环                  └ 采纳→写库(人设/原料库/打法库)+标candidate已采纳
                 ↓  POST .../mine-to-queue?kind=…(每条一次AI调用)
          写候选→media_mine_candidate暂存表 + 给content打已挖标识
```

- **暂存表**让候选跨刷新不丢：中途关页面，已挖的候选和标识都在，不用重挖（不白花钱）。
- **前端编排**（浏览器逐条 fetch），不建后台任务基础设施——70 条几分钟，开着页面即可。每条挖完即持久化，进度可断点续。

## 5. 数据

**5.1 `media_content` 加两个标识列**（MIGRATIONS，idempotent ALTER）：
- `mined_signature_at DATETIME`（挖过记忆点的时间，NULL=没挖过）
- `mined_essence_at DATETIME`（挖过精华的时间）

**5.2 新建暂存表 `media_mine_candidate`**（SCHEMA，零迁移）：
```sql
CREATE TABLE IF NOT EXISTS media_mine_candidate (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'signature' | 'material' | 'playbook'
    payload TEXT DEFAULT '{}',       -- JSON：该候选的字段（见下）
    source_content_id TEXT DEFAULT '',
    dedup_key TEXT DEFAULT '',       -- 归一化文本，用于去重分组
    status TEXT DEFAULT 'pending',   -- 'pending' | 'adopted' | 'discarded'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```
payload 按 kind：
- signature：`{content, brief, evidence, reason}`
- material：`{type, content, brief, evidence, reason}`
- playbook：`{name, structure, when_to_use, evidence, similar_to}`

dedup_key：signature/material 用 `content` 去空白后前 60 字；playbook 用 `name`。

## 6. AI 拆分（复用现有函数，不新写 prompt）

- **流A 记忆点**：调现有 `mine_from_transcript`，**只取 signatures 桶**写候选（materials 桶丢弃——素材归流B）。
- **流B 精华**：调 `mine_from_transcript` 取 **materials 桶** + 调 `mine_structure` 取 **playbook** 写候选（signatures 桶丢弃——记忆点归流A）。

> 权衡：流A 复用 mine_from_transcript 会白算 materials 的输出 token（略废但省一个新 prompt 的开发+调优；量级可接受）。若日后成本敏感再拆 signature-only 函数。

## 7. 挖矿端点（前端逐条调）

**`POST /media/content/{cid}/mine-to-queue`**，Form `kind`（`signature`|`essence`）、`force`（默认 0，1=已挖也重挖）：
- 查 content。`kind=signature`：若 `mined_signature_at` 非空且非 force → 返 `{ok:True, skipped:'already'}`；否则 `mine_from_transcript` → 写 signature 候选 → set `mined_signature_at=now`。
- `kind=essence`：**要求 `is_winner=1`**（非爆款返 `{ok:True, skipped:'not_winner'}`）；已挖且非 force → skip；否则 `mine_from_transcript`(materials) + `mine_structure`(playbook) → 写候选 → set `mined_essence_at=now`。
- 返回 `{ok, added:<写入候选数>, skipped:<原因或"">}`。
- 成本各自 `log_injection`（复用两个 mine 函数内已有的记账）。
- **去重**：写候选前查同 persona 同 kind 同 dedup_key 的 pending 候选，已存在则不重复插（同一句在同一条里被 AI 返回多次时幂等）。

## 8. 前端批量编排（老文案页）

- 列表每行复选框（已有），底部两按钮：
  - **「批量挖记忆点」**：对**所有**勾选的 content，逐条 `fetch mine-to-queue?kind=signature`，进度条"挖到 12/40"。
  - **「批量挖精华」**：对勾选的 content 逐条 `kind=essence`，非爆款自动 skip（进度显示"跳过 N 条非爆款"）。
- 挖完提示"共挖出候选 X 条 → [去复核采纳]"，链到 `/media/mine-review`。
- 每行显示已挖标识：`记忆点✓`（mined_signature_at 非空）/ `精华✓`（mined_essence_at 非空）。已挖行的复选框旁标灰"已挖·重挖需勾"，默认批量时跳过（force 关）。

## 9. 复核采纳页 `/media/mine-review`

- **`GET /media/mine-review`**：查当前人设所有 `status='pending'` 候选，**按 (kind, dedup_key) 去重分组**：同一句合并成一条，显示 `内容 + "出现 N 次·来自《标题…》等" + evidence`。三段分组展示（记忆点 / 素材 / 打法）。每组一个复选框。
- **`POST /media/mine-review/adopt`**：Form `candidate_ids[]`（每个去重组的代表 id）→ 逐个采纳：
  - signature → INSERT `media_persona_trait`(dimension='signature', source='reverse_mine', 复用 persona_interview_adopt 落库逻辑)。
  - material → INSERT `media_material`(source='反向挖料')。
  - playbook → 复用 adopt-playbook 的 similar_to 归并/新建逻辑。
  - 采纳后把**该去重组所有** pending 候选（同 dedup_key）标 `status='adopted'`（一次采纳消掉整组）。
- **`POST /media/mine-review/discard`**：Form `candidate_ids[]` → 整组标 `status='discarded'`（不想要的清掉，不再显示）。
- 复选批量：页面顶部"全选/反选"+ 「采纳选中」「丢弃选中」。

## 10. 代码落点

- `app/database.py`：+2 MIGRATIONS 列 + media_mine_candidate 表（SCHEMA）。
- `app/services/media_mine_queue.py`（新）：`enqueue_candidates(db, persona_id, source_content_id, kind, items)`（去重写候选）、`list_pending_grouped(db, persona_id)`（去重分组）、`adopt_candidates(db, ids)` / `discard_candidates(db, ids)`。纯 DB 逻辑，不调 AI。
- `app/api/media.py`：`mine-to-queue` 端点（调 mine 函数 + enqueue + 打标）；`/media/mine-review` 页 + adopt/discard 路由；`/media/legacy` 页 context 加两个标识 + 列表渲染标识；采纳复用现有落库 SQL。
- `app/templates/media_legacy.html`：复选 + 两批量按钮 + 进度 + 标识（JS 逐条 fetch 编排，不塞 SVG 进 JS）。
- `app/templates/media_mine_review.html`（新）：去重分组 + 复选批量采纳/丢弃。
- 测试：queue 服务（enqueue 去重 / 分组 / adopt 落对库 + 标组 / discard）、mine-to-queue 路由（signature 写候选打标 / essence 非爆款 skip / 已挖 skip / force 重挖）、review 路由（分组去重 / 批量 adopt 落库 + 标 adopted / discard）。

## 11. 边界（本轮不做）

- 下游引用（写稿/内容怎么用这三个库）——另议，写稿现有注入不动。
- 单条内容页 `/mine` 保留原样（即时挖+即时采纳，不进暂存表）。
- 视频反向内容也可批量（idea_source in legacy_text/video_reverse 都在老文案页）——本轮一起支持（同一列表）。
- 不建后台任务（前端编排够用）；候选暂存不设自动过期（采纳/丢弃即消，量可控）。

## 12. 验收清单

- [ ] media_content +2 标识列迁移；media_mine_candidate 表建。
- [ ] mine-to-queue：signature 写候选+打 mined_signature_at；essence 要求 winner（非爆款 skip）+打 mined_essence_at；已挖 skip，force 重挖；候选去重幂等。
- [ ] 流A 复用 mine_from_transcript 取 signatures；流B 取 materials + mine_structure 打法。
- [ ] 老文案页：两批量按钮逐条编排+进度+跳过非爆款/已挖；每行标识 记忆点✓/精华✓。
- [ ] 复核页：pending 候选去重分组（同句合并显 N 次+来源）；复选批量采纳落对库（人设/原料库/打法库·打法归并）；采纳消整组；丢弃整组。
- [ ] 采纳 source 复用现有（signature=reverse_mine / material=反向挖料 / playbook 归并）。
- [ ] 全套测试绿 + 浏览器冒烟（播多条→批量挖→复核去重→采纳，无 Jinja/500）。
- [ ] 真机 DeepSeek 批量挖几条端到端验通。
