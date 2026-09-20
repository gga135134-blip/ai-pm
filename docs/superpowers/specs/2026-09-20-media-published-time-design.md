# 自媒体·已发内容线补完整：发布时间准确化 + 复盘助手答准 + 格式统一

> 2026-09-20 · 复盘线子项 · 设计文档

## 背景与真实现状（已核实，勿被"缺字段"误导）

Schema **已有** `media_content.published_at` 和 `media_publish.published_at`（[app/database.py:282,292,618](../../../app/database.py)），不是缺字段。逐条核实后，用户原始四诉求大半已建好，真正要动的是"助手答不准 + 列表格式乱 + 上传时间要抓准"：

| 项 | 真实现状 |
|---|---|
| 已发内容单条录/改发布时间 | ✅ **已存在**：端点 `POST /media/content/{cid}/published-at`（[media.py:1512](../../../app/api/media.py)）+ 详情页日期框 `savePublishedAt()`（[media_content.html:69](../../../app/templates/media_content.html)）。`is_reverse = idea_source in ("video_reverse","legacy_text")`（[media.py:1319](../../../app/api/media.py)），故 90 条 legacy 详情页**也有**该框。 |
| reverse_ingest 写 published_at | ✅ **已写**：[media_reverse.py:58-61,71-73](../../../app/services/media_reverse.py) 把 yt-dlp `upload_date` 写进 content+publish。 |
| 摘要 + 正文清理（格式统一 A/B） | ✅ **能力已有**：`organize_content` 出 `summary`+`formatted`（[media_ai.py:1730](../../../app/services/media_ai.py)），`run_organize_one` 落库（[media_batch.py:10](../../../app/services/media_batch.py)）。只是没跑到那些【文案 NN】上。 |
| 复盘助手按发布时间答题 | ❌ **真洞**：`_tool_list_contents` 按 `updated_at` 排、不返 published_at（[media_agent_tools.py:11](../../../app/services/media_agent_tools.py)）。 |
| 短标题（格式统一 C） | ❌ **真缺**：乱标题是老文案导入取"正文首 40 字"（[media_legacy.py:32](../../../app/services/media_legacy.py)），有的大长条、有的【文案 NN】；`organize_content` 不产标题。 |

## 设计原则

1. **读取时兜底，不写死**：复盘页排序已用 `COALESCE(published_at, created_at)`（[media_ui.py:130](../../../app/api/media_ui.py)）。让助手工具跟上同一套，空发布时间按录入时间排/显示，并**标注为估算**。**绝不把 created_at 写进 published_at**——否则日后"重定真日期"时分不清真假。
2. **能复用别新建**：格式统一的摘要/正文清理复用现成 `organize_content`/`run_organize_one`；标题作为它的第三个输出加上去，一次整理三样全清。
3. **改写留痕可撤**：标题改写走 `log_action`（宪法第 7 条），和现在改 `script` 同一套路，改坏能还原。

## 改动一：③ 复盘助手读工具（必做，~20 行，TDD）

**文件**：`app/services/media_agent_tools.py`
- `_tool_list_contents`：`ORDER BY updated_at DESC` → `ORDER BY COALESCE(published_at, created_at) DESC`；SELECT 增补 `published_at, created_at`；输出每行带发布时间——`published_at` 非空用 `published_at[:10]`，空则用 `created_at[:10]` 并标 `（按录入时间估）`。
- `_tool_read_content`：SELECT 增补 `published_at, created_at`；返回加"发布时间：..."行（同样兜底+估算标注）。
- 系统提示词（`MEDIA_ASSISTANT_SYSTEM`，[app/services/media_assistant.py](../../../app/services/media_assistant.py)）补一句：回答"最后一条/这周/这月已发"按 `list_contents` 的发布时间为准，标"（按录入时间估）"的是占位非真实发布时间。

**TDD** 断言：混排按发布时间倒序正确；空 published_at 带估算标注、非空不带；`read_content` 返回含发布时间行。

## 改动二：② 复盘页就地补录（~35 行，纯前端）

**文件**：`app/templates/media_review_home.html`（已发列表）
- 每行加 `<input type="date" value="{{ (c.published_at or '')[:10] }}">`，失焦/按钮即存，POST 现成 `/media/content/{cid}/published-at`。**零后端改动**。
- `value` 只填真实 `published_at`，**不预填 created_at**（避免把估算当真日期存回）；空值提交=清空该条、回落兜底。
- 存成功轻提示。

**验收**：本地起服（端口 8012+）——列表出现日期框、填一条能存、刷新显示、助手随即按新日期答准。

## 改动三：⑤ 上传时按可获取的准确时间记录（小，主为复验）

- **视频反向入库**：已写 published_at（改动前状态）。**本轮任务=起服实测确认真生效**（贴一条链接，验 content/publish 的 published_at 落了 yt-dlp 日期）；若发现失效再修。
- **老文案粘贴**：纯文本无日期源，不抓、不造假，交由手动补录（改动二）。

## 改动四：⑥ 格式统一 A+B+C（核心新增，~25 行，TDD）

**C 短标题（新）**——文件 `app/services/media_ai.py` + `app/services/media_batch.py`：
- `ORGANIZE_SYSTEM`（[media_ai.py:1730](../../../app/services/media_ai.py)）加第 3 项输出 `title`：≤20 字短标题，点出主题，替代"正文首句"式乱标题。JSON 变 `{"title":"","summary":"","formatted":""}`。
- `organize_content` 解析并返回 `title`（缺失容错为空）。
- `run_organize_one`（[media_batch.py:10](../../../app/services/media_batch.py)）：`title` 非空时一并 `UPDATE media_content SET title=?`，`log_action` 的 before/after 增补 `title`（可撤连标题一起还原）。title 为空则不动原标题（不清空）。

**A+B（复用）**：摘要、正文清理随 C 同一次调用产出，无需新逻辑。

**存量清洗**：跑现成「老文案批量整理」（老文案页多选→批量整理，走后台跑器）把存量【文案 NN】/长首句洗成短标题+摘要+清理正文。

**A 列表显示**：`media_review_home.html` 已发列表核对渲染"短标题 + 一句摘要 + 日期"一致；若有不齐（如缺摘要行占位）顺手微调。

**TDD** 断言：`organize_content` 从含标题的 JSON 正确取出 title；`run_organize_one` 改写 title 且 log_action 记了 title 前后值；title 为空时保留原 title 不清空。

## 不做（记进路线图待办）

- **B · 未来内容排期**：给待发内容加"计划发布日"`planned_at`。用户不定期发，**做轻量清单版**（按计划日期排的待发清单，非日历格子），单开一轮。语义与 `published_at`（已发·实际）不同。
- 批量智能补日期：抖音 cookie 已过期、legacy 无日期源，无料可挖，YAGNI。

## 风险

- 助手 `_tool_list_contents` 输出格式变更（加发布时间）——核对仅助手循环用它，向后兼容。
- title 改写覆盖原标题：仅经"批量整理"触发（老文案页限 `legacy_text`），video_reverse 内容不受影响；`log_action` 保证可撤。
- 日期字符串格式与 `<input type=date>` 的 `YYYY-MM-DD` 一致，端点原样存，与详情页一致。
