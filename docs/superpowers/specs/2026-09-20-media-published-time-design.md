# 自媒体·发布时间线补完整 + 复盘助手按真实发布时间答题

> 2026-09-20 · 复盘线子项 · 设计文档

## 背景与真实现状（已核实，勿被"缺字段"误导）

Schema **已有** `media_content.published_at` 和 `media_publish.published_at`（[app/database.py:282,292,618](../../../app/database.py)），不是缺字段。逐条核实四个诉求后，大半已经建好：

| 诉求 | 真实现状 |
|---|---|
| ① 已发内容单条录/改发布时间 | ✅ **已存在**：端点 `POST /media/content/{cid}/published-at`（[media.py:1512](../../../app/api/media.py)）+ 详情页日期输入框 `savePublishedAt()`（[media_content.html:69](../../../app/templates/media_content.html)）。门控 `is_reverse = idea_source in ("video_reverse","legacy_text")`（[media.py:1319](../../../app/api/media.py)），所以存量 90 条 legacy 内容详情页**也已有**该输入框。 |
| ② 存量空的能补 | ⚠️ 能力已有（逐条进详情页改），但缺一个"就地快速补"的顺手入口。 |
| ④ reverse_ingest 写 published_at | ✅ **已写**：[media_reverse.py:58-61,71-73](../../../app/services/media_reverse.py) 把 yt-dlp 抓的 `upload_date` 写进 content + publish 两处。拿不到日期时为 None，属数据源问题非代码洞。 |
| ③ 复盘助手按发布时间准确答题 | ❌ **真洞**：`_tool_list_contents`（[media_agent_tools.py:11](../../../app/services/media_agent_tools.py)）按 `updated_at DESC` 排、只返 id/title/stage，不返也不按 `published_at`；`_tool_read_content` 也不返发布时间。故问"最后一条已发"只能靠列表顺序猜。 |

**结论**：本轮只补 ③（真洞）+ ②（便利性）。①④已完成不动。

## 设计原则：读取时兜底，不写死

复盘页排序/显示**已经**用 `COALESCE(published_at, created_at)`（[media_ui.py:130](../../../app/api/media_ui.py)、[media_review_home.html:55](../../../app/templates/media_review_home.html)）—— 空发布时间的当前已按"录入时间"兜底。

- **不**把 `created_at` 写进 `published_at`。写死会污染真实字段，日后"重新定真日期"时分不清哪条是真、哪条是占位。
- 让助手工具**也**用 `COALESCE(published_at, created_at)`，与复盘页一致：现在立刻能按时间答题，填了真日期自动接管，零迁移零风险。
- 助手输出**标注**发布时间是否为估算（无真实 `published_at` 时打"（按录入时间估）"），用户一眼看出哪些还欠真日期。

## 改动一：③ 复盘助手读工具（必做，~20 行，走 TDD）

**文件**：`app/services/media_agent_tools.py`

1. `_tool_list_contents`：
   - `ORDER BY updated_at DESC` → `ORDER BY COALESCE(published_at, created_at) DESC`。
   - SELECT 增补 `published_at, created_at`。
   - 输出每行带发布时间；`published_at` 为空时用 `created_at[:10]` 并标 `（按录入时间估）`，非空用 `published_at[:10]`。
2. `_tool_read_content`：
   - SELECT 增补 `published_at, created_at`；返回文本加"发布时间：..."一行（同样的兜底+估算标注）。
3. 系统提示词（`MEDIA_ASSISTANT_SYSTEM`，[app/services/media_assistant.py](../../../app/services/media_assistant.py)）补一句：回答"最后一条/这周/这月已发"等按 `list_contents` 返回的发布时间为准，标"（按录入时间估）"的是占位、非真实发布时间。

**TDD**：先写测试断言
- `_tool_list_contents` 返回文本里已发内容按发布时间倒序（真日期 > 兜底日期混排正确）；
- 空 `published_at` 的条目带"（按录入时间估）"标注、非空的不带；
- `_tool_read_content` 返回含发布时间行。

## 改动二：② 复盘页就地补录（可选便利，~30-40 行，纯前端）

**文件**：`app/templates/media_review_home.html`（已发列表处）

- 每行标题旁加 `<input type="date" value="{{ (c.published_at or '')[:10] }}">` + 一个失焦/按钮即存的小 JS，POST 现成的 `/media/content/{cid}/published-at`。
- **零后端改动**（端点已存在）。
- 存成功给个轻提示；空值提交 = 清空该条 published_at（回落兜底）。
- 只对 `published_at` 为空或需修正的补录用；`value` 只填真实 `published_at`，不预填 created_at（避免误把估算当真日期存回去）。

**验收**：本地起服（端口 8012+）用眼睛验——列表出现日期框、填一条能存、刷新后显示、复盘助手随即能按新日期答题。

## 不做（记进路线图待办）

- **B · 未来内容排期/日历**：给待发内容（idea/scripted/ready）加"计划发布时间"`planned_at` + 排期页 + 日历 UI。语义与 `published_at`（已发·实际）完全不同，属新功能，几百行，单开一轮设计。
- 批量智能补日期：抖音 cookie 已过期、legacy 是纯文本无日期源，无料可挖，YAGNI。

## 风险

- 助手工具排序改动可能影响其它依赖 `_tool_list_contents` 输出格式的地方——核对仅助手循环用它，格式变更（加发布时间）向后兼容。
- 就地补录的日期格式需与 `<input type=date>` 的 `YYYY-MM-DD` 一致，端点已按字符串原样存，与详情页一致。
