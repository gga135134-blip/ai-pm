# 复盘页助手（复盘能力 + 现有能力，对话框嵌复盘页）设计 spec

**日期：** 2026-08-14
**分支：** feat/media-assistant-review
**触发词：** 接 AI-PM

## 1. 目标

给 AI 助手加**复盘能力**（解读 + 跑 L2/L3），并把助手对话框**嵌进复盘页** `/media/review`。助手仍保有全部现有能力（查/建草稿/核心动作 Phase1+2）。顺带补上"复盘页没法触发跑复盘"的旧缺口。

## 2. 已拍板

- 复盘助手 = 现有全部能力 + 复盘能力。
- **跑 L2/L3 走待确认卡**（耗时+花钱）：聊天回合只秒回 stage，确认后 apply 里同步跑，不超时。
- 对话框抽**共享片段**两处嵌（/media/assistant + /media/review），DRY。
- 执行：inline（用户明确不双审）。

## 3. 复盘读工具（加进 _READ，无需确认）

`media_agent_tools.py` 加 4 个读工具（复用 media_review_cycle/media_phase_review 的读函数，各开 db）：
- `list_cycles()` → 列历轮周期复盘（seq + 时间 + 规律条数摘要）。
- `read_cycle(id)` → 某轮 L2 的规律/假设/建议摘要。
- `list_phase_reviews()` → 列阶段复盘。
- `read_phase_review(id)` → 某轮 L3 的阶段建议/信号摘要。
- 返回 scoped 文本（清单/摘要），让助手解读"上轮复盘发现了啥"。

## 4. 复盘跑工具（加进 _CORE，stage→待确认卡）

- `run_cycle_review()` → stage pending(action_type='run_l2', summary="跑一轮周期复盘（会花 AI 费用）")。
- `run_phase_review()` → stage pending(action_type='run_l3', summary="跑一轮阶段复盘（会花 AI 费用）")。
- 无 target（作用于当前人设整体）；不验单条 target。

**apply_action 加两分支**（复用现有 apply 框架）：
- `run_l2`：`await run_l2_cycle(db, pid, force=1)`（确认即越过门槛）；reversible=0。
- `run_l3`：`await run_l3_review(db, pid, force=1)`；reversible=0。
- 生成的复盘报告有自己的删除（复盘页），故 run 类不可撤（revert_action 对 run_l2/run_l3 靠 reversible=0 守卫自动拒绝，无需新分支）。

## 5. 对话历史端点（让嵌入框自包含）

现在助手页 msgs 由服务端渲染；嵌到复盘页需要自包含。加 `GET /media/assistant/history` → 返当前人设最近 N 条消息 JSON `[{role,content}]`。嵌入框 JS init 时 fetch history + pending 渲染。

## 6. 共享片段 + 两处嵌

- 新建 `app/templates/_media_assistant_box.html`：对话容器(#chat)+输入(#ast-input/#ast-btn)+待确认区(#ast-pending)+状态(#ast-status)+`<script>`(init 载 history+pending、astSend、loadPending、confirmAction)。**自包含**（JS 载 history，不依赖服务端 msgs）。
- `media_assistant.html`：改为 `{% include "_media_assistant_box.html" %}`（移除原内联对话/脚本 + 服务端 msgs 渲染；assistant_page 路由可不再传 msgs）。顶部标题/改动记录入口保留。
- `media_review_home.html`：底部 `{{ shell.step_nav(...) }}` 之前加一块"🤖 助手"区 + `{% include "_media_assistant_box.html" %}`。
- 片段用固定 ID（两页同页不会同时出现两个框，安全）。

## 7. 代码落点

- `app/services/media_agent_tools.py`：+4 读工具(_READ)+2 跑工具(_CORE stage)+schemas。
- `app/services/media_assistant.py`：`apply_action` 加 run_l2/run_l3 分支（import run_l2_cycle/run_l3_review）。
- `app/api/media.py`：+ `GET /media/assistant/history`；assistant_page 简化（不再必须传 msgs，模板改 include）。
- `app/templates/_media_assistant_box.html`（新）、`media_assistant.html`（改 include）、`media_review_home.html`（嵌入）。
- 测试：读工具（list/read cycle+phase 返 scoped）；跑工具 stage（落 pending action_type=run_l2/run_l3 不执行）；apply_action run_l2/run_l3（monkeypatch run_l2_cycle/run_l3_review 断言被调 + reversible=0）；history 端点；两模板 include 渲染。

## 8. 边界（本轮不做）

- 不改 L2/L3 现有分析逻辑/门槛常量。
- 不做复盘结果的 trait/phase/anchor 应用进助手（那是复盘报告页的 apply，重且已存在）——助手只跑+解读+引导去报告页应用。
- run 类不做后台异步（apply 同步跑，用户点确认时等；一次 AI 调用可接受）。

## 9. 验收清单

- [ ] 读工具：list/read cycle+phase 返 scoped 摘要，助手能解读。
- [ ] 跑工具：run_cycle_review/run_phase_review 只 stage pending(run_l2/run_l3)，不执行。
- [ ] apply_action：run_l2/run_l3 真跑 run_l2_cycle/run_l3_review(force=1)，reversible=0；revert 拒绝。
- [ ] history 端点返最近消息；嵌入框 init 载 history+pending。
- [ ] _media_assistant_box.html 抽出；助手页 + 复盘页都 include，两处对话框可用（发消息/待确认卡/确认取消）。
- [ ] 全套测试绿 + 浏览器冒烟（复盘页出现助手框→发消息→让它"跑一轮周期复盘"出待确认卡→确认→L2 生成；助手页照常）。
- [ ] 真机 DeepSeek：复盘页对话触发跑 L2 → 确认 → 复盘报告生成。
