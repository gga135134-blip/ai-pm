# 自媒体 AI 助手 Phase 2（核心动作进对话 + 确认机制）设计 spec

**日期：** 2026-08-14
**分支：** feat/media-assistant-phase2
**触发词：** 接 AI-PM

## 1. 目标

给 Phase 1 的助手补上**核心动作**（标爆款/删除/采纳入库），但核心动作**要人确认才执行**——AI 只"拟"，在对话里出**待确认卡**，你点确认才真做。复用 Phase 1 的动作日志（留痕可撤）。

## 2. 已拍板（brainstorm 确认）

- **确认方式**：待确认卡 + 按钮（AI 拟好→卡片"要把《X》标爆款 [确认][取消]"→点确认才执行）。
- **四个核心工具**（都走待确认卡）：标爆款 `mark_winner`、删除内容 `delete_content`、采纳素材/口头禅 `adopt_signature`/`adopt_material`、采纳打法 `adopt_playbook`。
- **撤销**：标爆款/采纳类 apply 后可撤（延续 Phase 1）；**删除 apply 后不可撤**（内容真删了）——确认卡就是安全闸，改动记录标"不可撤"。

## 3. 确认机制（复用 media_assistant_action）

- 核心工具**只 stage 不执行**：调用时 `log_action(..., status='pending')` 落一条 pending 行（action_type + target + `after_json` 存人读摘要 summary + 执行所需参数），返给 AI "已拟好待确认"。
- AI 回复告知用户；**助手页显示该人设所有 pending 行为待确认卡**，每张：summary +「确认」「取消」。
- **确认** → `POST /media/assistant/action/{id}/apply` → `apply_action` 执行真动作 + status='applied'（可撤类 apply 后仍可在改动记录撤销）。
- **取消** → `POST /media/assistant/action/{id}/cancel` → status='cancelled'。
- `media_assistant_action.status` 现为 TEXT（值 applied/reverted），新增取值 pending/cancelled——**零 schema 变更**。

## 4. media_assistant 服务扩展

- **`log_action` 加 `status='applied'` 参**（现在硬编码不写 status；加参，核心工具 stage 传 'pending'）。
- **`apply_action(db, action_id) -> bool`**（新）：读 pending 行，按 action_type 执行，回填 before/after（供撤销），置 status='applied'、必要时 reversible=0：
  - `mark_winner`：before={is_winner:当前值}，UPDATE is_winner=1。可撤。
  - `delete_content`：复用现有 6 子表清理 + 删内容（media_metrics/publish/review/case/evidence/angle/draft_review）。**reversible=0**（不可撤）。
  - `adopt_signature`：INSERT media_persona_trait(dimension='signature', source='assistant')，after['created_id']=新id。可撤。
  - `adopt_material`：INSERT media_material(source='反向挖料' 或 'assistant')，after['created_id']=新id。可撤。
  - `adopt_playbook`：similar_to 命中→追加 evidence 归并(**reversible=0**，难净撤)；否则 INSERT(source='assistant')，after['created_id']=新id，可撤。
- **`cancel_action(db, action_id) -> bool`**（新）：pending→cancelled。
- **`list_pending(db, persona_id) -> list`**（新）：给助手页待确认卡。
- **`revert_action` 加分支**（apply 后撤销）：`mark_winner`→UPDATE is_winner=before；`adopt_signature`/`adopt_material`/`adopt_playbook`(新建的)→DELETE from 对应表 WHERE id=after['created_id']；reversible=0 的（delete_content/归并 playbook）→拒绝（返 False）。

## 5. 核心工具（media_agent_tools 加 _CORE 组，只 stage）

在 `media_agent_tools.py` 加 `_CORE` dict + stage 函数（各自开 db、验证 target 属当前人设、log_action status='pending'、返"已拟好《…》待确认"）。dispatch 里 `_READ or _WRITE or _CORE`。

- `mark_winner(content_id)`：验 content 属人设→pending(after={summary,content_id})。
- `delete_content(content_id)`：验属人设→pending。
- `adopt_signature(content, evidence?)`：AI 拟口头禅文本→pending(after={summary,content,evidence})。
- `adopt_material(type, content, brief?, evidence?)`：AI 拟素材→pending。
- `adopt_playbook(name, structure, when_to_use?, evidence?, similar_to?)`：AI 拟打法→pending。

schemas 加这 5 个（标注"需人确认"）。系统提示 `MEDIA_ASSISTANT_SYSTEM` 更新：**告诉 AI 现在能做核心动作，但它只是"拟"，会等用户在卡片上确认；删除不可撤要谨慎**（去掉 Phase 1 里"核心动作你现在不能做"那句）。

## 6. 端点 + UI

- `POST /media/assistant/action/{id}/apply`、`POST .../cancel`、`GET /media/assistant/pending`（返当前人设 pending 列表）。
- `media_assistant.html`：待确认区（页面加载 + 每次发消息后 fetch pending 渲染卡片），每张 summary +「确认」(post apply) +「取消」(post cancel)，操作后重刷 pending。卡片醒目（区别于普通消息）。
- assistant_ask 返回里带 `pending_count`（AI 刚 stage 了就提示"有 N 条待确认"）。

## 7. 代码落点

- `app/services/media_assistant.py`：log_action 加 status 参；+ apply_action / cancel_action / list_pending；revert_action 加 mark_winner/adopt_* 分支；更新 MEDIA_ASSISTANT_SYSTEM。
- `app/services/media_agent_tools.py`：+ _CORE 5 工具（stage）+ schemas；dispatch 加 _CORE。
- `app/api/media.py`：+ apply/cancel/pending 路由；assistant_ask 返回加 pending_count。
- `app/templates/media_assistant.html`：待确认卡区 + fetch/确认/取消 JS。
- 测试：apply_action（每种 action_type 执行正确 + before/created_id 回填 + delete 置 reversible=0）；cancel_action；list_pending；revert（mark_winner 还原/adopt 删记录/delete 拒绝）；core stage 工具（只 pending 不执行、验 target 属人设）；apply/cancel/pending 路由；assistant_ask pending_count。

## 8. 边界（本轮不做）

- 不动 Phase 1 查/改草稿工具与其它逻辑。
- 改人设阶段/trait（人设进化）仍不进助手（走复盘页）。
- 待确认卡不做批量确认（一张张点；量少）。
- adopt_playbook 归并的撤销不做（reversible=0）。

## 9. 验收清单

- [ ] log_action 支持 status；stage 工具落 pending 不执行、验 target 属当前人设。
- [ ] apply_action：mark_winner 改 is_winner+可撤；delete_content 删+清子表+reversible=0；adopt_* 写库+回填 created_id+可撤（归并 playbook reversible=0）。
- [ ] cancel_action：pending→cancelled。
- [ ] revert_action：mark_winner 还原、adopt_* 删记录、delete/归并 拒绝。
- [ ] 路由 apply/cancel/pending；助手页待确认卡（确认/取消/重刷）；assistant_ask 返 pending_count。
- [ ] 系统提示更新（AI 知道能做核心动作但需确认、删除谨慎）。
- [ ] 全套测试绿 + 浏览器冒烟（对话让它"把《X》标爆款"→出待确认卡→确认→is_winner 改；"删掉《Y》"→确认卡→确认→删且改动记录标不可撤）。
- [ ] 真机 DeepSeek：对话触发一个核心动作→出卡→确认→生效。
