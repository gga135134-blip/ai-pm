# 打法库🅓 接写稿 设计 spec

**日期：** 2026-08-14
**分支：** feat/media-playbook-to-script
**触发词：** 接 AI-PM

## 1. 目标

让上一轮建的（已共享的）打法库真正影响产出：写口播脚本时，AI 从打法库挑**最贴这条选题的一条**打法，把它的结构当骨架注入写稿 AI。写完在结果上方显示"本次用了《X》· 因为…"，人一眼判断匹配准不准，能一键换一条或不用打法重写。

**本轮只接"写稿"这一个下游。** 决策引擎🅒（选题排序加 playbook 因子）是另一轮，不在本 spec。

## 2. 核心纪律（用户反复强调，最高优先级）

**写稿 AI 的注意力永远只看到一条打法。**

- 匹配是一个**独立的小 AI 调用**（`match_playbook`），跟写稿 prompt 完全隔开。整个打法库（名字+适用场景+结构）只在这个隔离的匹配调用里出现一次。
- 写稿 prompt（`write_script`）里**只出现匹配到的那一条**打法的结构，绝不整库列出来喂写稿 AI。
- 匹配不到 / 库里没打法 → 写稿 prompt 里**啥打法都不注入**，跟现在完全一样地写（不打扰、不硬塞）。

这是"档案柜不是会议桌"哲学在写稿链的落地：库是档案柜，只把当前用得上的那一页拿到桌上。

## 3. 匹配（match_playbook）

**新函数** `async match_playbook(db, content, model="auto") -> dict`（放 `app/services/media_ai.py`）：

- 读共享打法库池：`SELECT id,name,when_to_use,structure,status FROM media_playbook WHERE status IN ('proven','validating')`（打法库已是全公司共享，不按 persona 过滤，沿用上一轮设计）。
- 池空 → 直接返回 `{"ok": True, "playbook": None, "cost": 0, "model": ""}`（不调 AI，省钱）。
- 喂 AI **紧凑清单**（一条一行：`[id] 名字｜适用:when_to_use｜结构:structure`）+ 这条选题的 title/puzzle/idea_reason，问它："这条选题最该用哪个打法？给不合适就返回 none。"
- **系统提示 `MATCH_PLAYBOOK_SYSTEM`** 要求：只输出严格 JSON `{"playbook_id":"", "reason":""}`；`playbook_id` 必须是清单里的 id 或空串；`reason` 一句话说为什么这条选题适合这个打法。诚实：不合适就留空，别硬凑。
- **护栏**：AI 返回的 `playbook_id` 不在池里 → 当没匹配（`playbook: None`）。
- 命中 → 返回 `{"ok": True, "playbook": {"id","name","structure","when_to_use","status","reason"}, "cost", "model"}`。
- **成本可见**：调 AI 时 `log_injection(db, content_id, "match_playbook", [], tokens)`。
- **池子取舍**：proven 和 validating 都进池，让库还没攒出 proven 时也能用；命中后 `status` 带回，UI 标注（proven=已跑通 / validating=验证中），让人知道这条打法本身跑通没。

## 4. 注入（write_script 扩展）

`write_script` 签名加一个可选参数：

```
async def write_script(db, content_id, mode="full", model="auto", hint="", playbook_id="")
```

- `playbook_id=""`（默认）：`mode!='lean'` 时调 `match_playbook` 自动挑一条。
- `playbook_id="none"`：跳过打法（用户点"不用打法重写"）。
- `playbook_id="<id>"`：直接用指定那条（用户点"换一条"选了某条），不再调 match_playbook（省一次调用），按 id 查库拿结构。
- `mode='lean'`：不注入打法（lean 只放人设身份行，保持原语义）。

**注入位置**：在 `write_script` 组装 `parts` 时，若拿到一条打法，追加一个 `【打法骨架】` 段：

```
【打法骨架】{name}（{适用场景}）
{structure}
（按这个结构写，但别硬套；结构服务于内容，不是填空）
```

放在 `【本条选题】` 之前、原料/角度之后（骨架是"怎么讲"，选题是"讲什么"）。

**记录**：命中的打法 id 写进 `media_content.used_playbook_ids`（已有列，JSON 数组存单元素 `[id]`；none/无命中存 `[]`），供以后复盘"这条用了啥打法→效果如何"。

**返回值**：`write_script` 的结果 dict 加一个 `playbook` 字段 = `{"id","name","reason","status"}` 或 `None`，让 UI 显示。其余返回字段（script/ai_draft、cost、model、injected_count）不变。

## 5. 流程节奏（默认最省事）

- 点「AI 写脚本」→ `POST /media/content/{cid}/ai-script`（现有路由，加透传 `playbook_id` 表单参，默认空）→ 自动匹配 + 直接写出草稿。
- 结果上方显示：**"本次用了打法《X》· {reason}"**（带 proven/validating 小标签）+ 两个操作：
  - **【换一条 ▾】**：下拉列出打法库所有打法（名字+状态），选一条 → 重新 `ai-script` 带 `playbook_id=<id>`。
  - **【不用打法重写】**：重新 `ai-script` 带 `playbook_id=none`。
- 匹配不到 / 库空 → 不显示打法条（`playbook: None`），跟现在一样。

## 6. 怎么测准（用户核心顾虑："测试过才知道"）

- 每次写完都显示**用了哪条 + 匹配理由** = 完全透明，匹配准不准一眼看穿。
- **「不用打法重写」= 现成的 A/B**：同一条选题，带打法 vs 不带，人自己对比稿子有没有变好。**不单独造"两版并排对比"功能**（翻倍成本，YAGNI）。
- 真机验收：本地 DeepSeek 播几条真选题 + 几条真打法，看 ①匹配挑得对不对 ②理由靠不靠谱 ③注入后稿子结构是否真按骨架走 ④换/去掉重写是否即时生效。

## 7. 数据 / 迁移

- **零 schema 变更**：`media_content.used_playbook_ids` 列已存在（上一轮就有）。`media_playbook` 表已建。无 migration。

## 8. 代码落点

- `app/services/media_ai.py`：加 `MATCH_PLAYBOOK_SYSTEM` + `match_playbook()`；改 `write_script`（加 `playbook_id` 参 + 打法注入段 + 返回 `playbook` 字段 + 写 `used_playbook_ids`）。
- `app/api/media.py`：`content_ai_script` 路由加 `playbook_id: str = Form("")` 透传给 `write_script`。
- `app/templates/media_content.html`：AI 写稿结果区加"用了打法《X》"展示条 + 换一条下拉 + 不用打法重写按钮（复用现有 ai-script 的 AJAX 调用，带上 playbook_id）。
- 测试：`tests/test_media_playbook_match.py`（match_playbook：命中/不合适返 none/池空不调 AI/瞎编 id 当没匹配）、`tests/test_media_playbook_inject.py`（write_script：自动注入写 used_playbook_ids/playbook_id=none 跳过/指定 id 用那条/lean 不注入/返回 playbook 字段）。

## 9. 边界（本轮不做）

- 不接决策引擎🅒（选题排序 playbook 因子=0 保持不动）。
- 不改打法库本身（建/采纳/status 切换/共享逻辑一律不动）。
- 不改写稿的其他注入（人设/原料/角度/证据一律不动）。
- 不做"两版并排对比"UI（用"不用打法重写"当轻量 A/B）。
- 匹配用 AI 语义（不做关键词兜底——池小时 AI 调用便宜，且语义质量是本功能价值所在）。

## 10. 验收清单

- [ ] match_playbook：命中返 {playbook,reason}；不合适返 None；池空不调 AI；瞎编 id 当没匹配。
- [ ] write_script：默认自动匹配并注入【打法骨架】；写 used_playbook_ids=[id]；playbook_id=none 跳过且 used_playbook_ids=[]；playbook_id=<id> 用指定那条不再调 match；lean 不注入；返回 playbook 字段。
- [ ] 写稿 prompt 里最多出现一条打法（grep/断言 prompt 不含整库）。
- [ ] 路由 ai-script 透传 playbook_id。
- [ ] UI：显示"用了《X》·理由"+状态标签；换一条下拉；不用打法重写；无匹配不显示条。
- [ ] 真机 DeepSeek 端到端验通。
- [ ] 全套测试绿 + 浏览器冒烟无 Jinja/500。
