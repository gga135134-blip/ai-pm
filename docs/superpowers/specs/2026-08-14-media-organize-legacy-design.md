# 老文案批量整理（摘要 + 统一格式）设计 spec

**日期：** 2026-08-14
**分支：** feat/media-organize-legacy
**触发词：** 接 AI-PM

## 1. 目标

老文案又多又长（88 条已发，正文各占一大段），一眼看不出讲什么。加一个**批量整理**：AI 逐条给每条 ①**一句话摘要**（另存，列表里标题下显示，方便扫）②**统一格式**（清理正文排版，改写但可撤）。

## 2. 设计（复用现成，守纪律）

- **两件事**：摘要（安全·另存字段）+ 格式清理（改写正文·留痕可撤）。
- **纪律**：批量 = 前端**逐条**喂 AI（每条一次调用，绝不多条挤一个 prompt），带进度。
- **可撤**：格式改写会覆盖已发原始稿，所以**走已建的助手留痕机制**（`media_assistant_action`）——改前正文存 before，「🤖 改动记录」页一键还原。摘要是新字段、additive，不需撤。
- **一次 AI 调用出两样**：`organize_content(script)` 一次返回 `{summary, formatted}`（省钱）。

## 3. 数据

`media_content` 加 `summary TEXT DEFAULT ''`（MIGRATIONS，idempotent ALTER）。无新表。

## 4. AI

`app/services/media_ai.py` 加 `async organize_content(script, model="auto") -> dict`：
- 空 script → `{ok: False}`。
- 系统提示 `ORGANIZE_SYSTEM`：给一条口播/文案正文，输出严格 JSON `{"summary":"一句话说这条讲了啥(≤30字)", "formatted":"清理排版后的正文(合并碎行/去序号残留/统一分段，别改内容别扩写别删信息)"}`。
- 返回 `{ok, summary, formatted, cost, model}`；调用记 `log_injection`。

## 5. 端点（逐条，前端编排）

`POST /media/content/{cid}/organize`（无参）：
- 查 content（script）。空 → `{ok:False,error:'无正文'}`。
- 调 `organize_content(script)`。失败 → `{ok:False,error}`。
- **格式改写留痕**：`log_action(db, persona_id, 'organize_format', 'media_content', cid, before={script}, after={script:formatted})`（复用 Task 助手的 log_action）。
- `UPDATE media_content SET summary=?, script=? WHERE id=?`（summary + formatted）。
- 返回 `{ok:True, summary}`。

## 6. 撤销扩展

`app/services/media_assistant.py::revert_action` 加分支：`action_type == 'organize_format'` → `UPDATE media_content SET script=? WHERE id=?`（还原 before 的 script）。（摘要不还原，无害。）「改动记录」页动作名映射加 `organize_format:'整理格式'`。

## 7. 前端

- **老文案页** `/media/legacy`：复选（已有）+ 加「批量整理（摘要+排版）」按钮 → 前端逐条 `fetch /media/content/{id}/organize`，进度"整理中 12/40"。完成提示"已整理 N 条 → 刷新看摘要"。
- **列表显示摘要**：老文案页每条标题下显示 `summary`（有则显示，灰色小字）。legacy_home 的 SELECT 加 `summary`。
- **复盘页** `media_review_home.html`：已发内容列表每条标题下也显示 `summary`（用户就是在这页发现长文案难扫的）；对应查询加 `summary`。

## 8. 边界（本轮不做）

- 不做"归类/分主题"、不做"找规律"（那是 L2 复盘）——本轮只摘要 + 格式。
- 不接对话助手（助手加"整理"工具可留作后续；本轮是老文案页的批量按钮）。
- 格式清理只重排不改写内容（提示词约束"别改内容别扩写别删信息"）。

## 9. 代码落点

- `app/database.py`：+`summary` 列（MIGRATIONS）。
- `app/services/media_ai.py`：+`organize_content` + `ORGANIZE_SYSTEM`。
- `app/services/media_assistant.py`：`revert_action` 加 `organize_format` 分支。
- `app/api/media.py`：+`POST /media/content/{cid}/organize`；`legacy_home` SELECT 加 summary；`media_review_home` 查询加 summary。
- `app/templates/media_legacy.html`：批量整理按钮 + 逐条编排 JS + 进度 + 每条摘要显示。
- `app/templates/media_review_home.html`：已发列表每条显示 summary。
- `app/templates/media_assistant_actions.html`：动作名映射加 organize_format。
- 测试：organize_content（mock ask_ai 返 JSON → 出 summary+formatted）；organize 路由（更新 summary+script + 记 organize_format 日志）；revert_action organize_format 还原 script；legacy 列表渲染 summary。

## 10. 验收清单

- [ ] media_content +summary 列。
- [ ] organize_content：一次调用返 {summary, formatted}；空 script 挡住。
- [ ] /organize：更新 summary+script、记 organize_format 动作（before=原 script）。
- [ ] revert_action：organize_format → 还原 script。
- [ ] 老文案页：批量整理按钮逐条编排+进度；每条标题下显示摘要。
- [ ] 复盘页已发列表显示摘要。
- [ ] 全套测试绿 + 浏览器冒烟（播一条→整理→摘要出现→改动记录有 organize_format→撤销→正文还原）。
- [ ] 真机 DeepSeek 整理几条验摘要准/格式没改坏内容。
