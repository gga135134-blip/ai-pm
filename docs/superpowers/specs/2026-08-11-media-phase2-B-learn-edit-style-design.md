# 自媒体二期 · 功能B「AI 学用户改稿」设计 spec

**日期：** 2026-08-11
**所属：** ai-pm / 自媒体模块（media）二期
**性质：** 正式设计 spec（已过用户逐节口头评审，待写 writing-plans 实现计划）
**前置文档：**
- 一期设计 `docs/superpowers/specs/2026-07-24-media-ops-system-design.md`
- 🅐 单条生产线 spec `docs/superpowers/specs/2026-08-04-media-phase2-A-single-content-pipeline-design.md`
- 人设框架地基 spec `docs/superpowers/specs/2026-08-08-media-phase2-persona-framework-design.md`

---

## 一、这块是什么，为什么值得做

**目标：** 让 AI **对比"AI 出的草稿"和"用户真人改后的定稿"**，提炼出用户反复出现的改稿习惯（风格特征），沉淀进人设，下次写脚本时自动用上——**每一笔修改都在教 AI 写得更像用户**。

**为什么用户很看重：** 这是「写作做轻、体系做重」的极致。用户真人出镜会把 AI 草稿改得更口语化、更像自己；如果这些修改只停在单条内容里就浪费了。功能 B 把它回流成人设资产，让飞轮那半"越用越省心"真正转起来。

**原料已经在攒（关键前提，无需额外开发）：** 🅐 生产线的 `finalize` 早已把两版都持久化——`media_content.ai_draft`（AI 草稿）保留不动，`media_content.script`（用户定稿）存真人改后的版本。所以功能 B **只需做"提炼"这半**，"存差异"那半已经免费实现。

---

## 二、核心原则：学改稿产出的东西 = 人设条目，不另起炉灶

功能 B 提炼出的风格特征，和人设访谈（`persona_interview_extract`）、L1 复盘（`review_content`）产出的东西，是**同一种资产、进同一张表、过同一道人拍板闸、走同一套注入预算**。第三个入口，同一张注册表。

| | 人设访谈（已有） | L1 复盘（已有） | AI 学改稿（本轮） |
|---|---|---|---|
| 触发 | 冷启动/定位变了 | 内容发布有数据了 | 攒够定稿，主动复盘 |
| 来源 | 用户脑子里引导挖 | 真实数据反推 | AI 草稿 vs 用户定稿**对比** |
| 产物 | candidate traits | candidate traits | candidate traits |
| 落点 | `media_persona_trait` | 同一张表 | 同一张表 |
| 维度 | 8 个 dimension | 那几个 | **tone / signature** |
| 生效 | 人拍板 adopt | 同一道闸 | 同一道闸 |

**收益：不发明新 schema、不发明新注入逻辑、不发明新审核闸。**

---

## 三、锁死的原则（用户明确要求不变）

1. **人拍板闸** —— AI 只提候选，用户采纳才入库，绝不自动写库（呼应人设"人拍板不跟风"）。
2. **创作器 ≠ 审稿器** —— 用户就是审稿器，提炼 AI 只负责提候选。
3. **注入预算不撑爆** —— 学到的 tone/signature 条目跟现有条目**公平竞争固定槽位**（`INJECTION_BUDGET`：signature 3 必注、tone 走 trait 8 按 confidence 竞争），不是无限堆。
4. **诚实不编造** —— 学的是用户真实改过的稿，依据栏（`evidence`）存真实改动例子。
5. **AI 成本可见** —— `learn_edit_style` 必须记 `log_injection`。
6. **存的永远是用户定稿的真实版** —— `ai_draft` 只读用于对比，从不覆盖 `script`。

---

## 四、决策记录（brainstorm 拍板）

| 决策点 | 选定 | 理由 |
|---|---|---|
| 学习粒度 | **即时存差异 + 攒够提炼** | 与原 spec 一致；"存差异"已由 finalize 免费实现，只做"提炼" |
| 特征落点 | **A：复用 persona_trait 的 tone/signature** | 零新管道、自动走注入预算、与"L1 精修同一批条目"哲学一致。**不行再升级为专门的改写规则库（B），但先 A** |
| 触发入口 | **人设档案页加一块** | 风格资产归人设，位置正 |
| diff 方式 | **AI 直接读两版文字找规律，不算结构化 diff** | LLM 比手写文本 diff 更强更省事 |

---

## 五、数据流

```
人设档案页「🪞 AI 学我改稿」块
  显示：已有 N 条定稿可供学习
        │
        ▼ 点「让 AI 复盘」
  POST /media/persona/{pid}/learn-edits
        │
        ▼ learn_edit_style(db, persona_id, model)
  读最近 ≤N 条定稿（ai_draft 非空 且 script != ai_draft）
  + 读现有 tone/signature 条目（"已有的别重复提"）
        │
        ▼ 喂 AI：成对的「AI 草稿 → 用户定稿」+ 现有风格条目
  AI 找反复出现的改动模式，返回候选（带真实例子做 evidence）
        │
        ▼ 返回 JSON：candidates（绝不写库）
  页面逐条渲染，用户「采纳 / 丢弃」
        │
        ▼ 采纳：POST /media/persona/{pid}/interview/adopt
  （复用现有 adopt，加 source 参数）→ source='learned_edit'
        │
        ▼ 写入 media_persona_trait（tone/signature，phase_tag 空=永久）
  下次 write_script 自动注入 → AI 写得更像用户
```

---

## 六、组件与接口

### 6.1 `media_ai.py` 新增 `learn_edit_style`

```python
async def learn_edit_style(db, persona_id: str, model: str = "auto") -> dict:
    """对比最近定稿的 AI 草稿 vs 用户定稿，提炼反复出现的改稿习惯为候选风格条目。
    绝不写库——返回候选，人拍板 adopt 才入。"""
    # 返回：{"ok": bool, "traits": [ {dimension, content, brief, evidence, confidence, phase_tag}, ... ],
    #        "pair_count": int, "error": str, "cost": float, "model": str}
```

- **取数：** `SELECT title, ai_draft, script FROM media_content WHERE persona_id=? AND authoring_stage='finalized' AND ai_draft != '' AND script != '' AND script != ai_draft ORDER BY finalized_at DESC LIMIT N`（N 常量，默认 15，防 token 撑爆）。
- **注入现有条目：** 同时读该人设 active 的 tone/signature 条目，塞进提示词让 AI **不重复提**已有的。
- **提示词红线：** 只许基于给定的真实改动归纳，**不许编造**用户没做过的改动模式；每条候选的 `evidence` 必须引用真实改动例子。
- **候选夹取：** `dimension` 夹回 `{"tone", "signature"}`（越界丢弃或归 tone）；`confidence` 夹 1–5；`phase_tag` 恒为 `""`（tone/signature 永久，phase_bound=False，与人设框架一致）；`content`/`brief` 走 `_txt()` 兜底防 AI 返回非字符串。
- **成本可见：** 调用后 `log_injection`（记注入了几对草稿+token+cost），符合原则 5。
- **返回结构与 `persona_interview_extract` 对齐**，这样 adopt 端点可直接复用。

### 6.2 `media.py` 新增/改动路由

**新增** `POST /media/persona/{pid}/learn-edits`：
```python
@router.post("/media/persona/{pid}/learn-edits")
async def persona_learn_edits(pid: str):
    # try/except 包 learn_edit_style，失败返回 {"ok": False, "error": ...}
    # 返回 JSONResponse(result)
```

**改动** `POST /media/persona/{pid}/interview/adopt`：加一个可选 `source: str = Form("interview")` 参数，写入 `media_persona_trait.source`。功能 B 前端传 `source="learned_edit"`；不传时默认 `interview`，**向后兼容现有访谈流程**。

**改动** `persona_detail`（GET）：查一个"可供学习的定稿数" `learnable_count`（同 6.1 取数的 COUNT），塞进模板上下文，用于显示提示和阈值。

### 6.3 `media_persona.html` 新增一块 UI

- 在人设页加一个 `.module`「🪞 AI 学我改稿」，套现有访谈页/人设页样式（`.module/.mh/.btn ai`）。
- 显示 `已有 {{ learnable_count }} 条定稿可供学习`。
- 「让 AI 复盘」按钮 → AJAX POST `/learn-edits` → 渲染候选列表（每条：维度标签 + content + 例子依据 + 星级）。
- 每条候选「采纳 / 丢弃」：采纳 AJAX POST `interview/adopt`（带 `source=learned_edit`），成功后条目从候选区消失、提示已入库。
- **阈值提示（不硬拦）：** `learnable_count < 3` 时按钮旁提示"再攒几条改稿，AI 学得更准"；`== 0` 时按钮禁用。
- **前端 JS 铁律（踩过的坑）：** 模板 JS 改动必须真浏览器验（TestClient 测不出 JS 语法崩）；SVG 图标别塞进 JS 单引号字符串。

---

## 七、边界与错误处理

- **定稿数为 0：** 页面显示空态，按钮禁用，不调 AI。
- **AI 返回空/无规律：** `traits` 为空，页面显示"这批改稿没提炼出稳定习惯，再攒几条"，不装作学到东西（呼应 🅐"空返回不装完成"）。
- **AI 返回类型不可信：** 全部字段走 `_txt()`/`_clamp()` 兜底（DeepSeek 曾把该文字的字段返回成 int）。
- **重复提议：** 靠"喂现有 tone/signature 条目让 AI 别重复"+ 人拍板时肉眼去重双保险；**不加 learned 标记列**（YAGNI：每次读最近 N 条重算，成本可控，规律需要 volume 反而应重复看）。
- **adopt 越界维度：** 单租户、前端只发受控值；沿用现有 adopt 的宽松处理（记为已知 Minor，同人设框架 spec §未做/留后）。

---

## 八、测试策略（照项目 TDD 习惯）

无 pytest-asyncio，控制器用 TestClient + 伪造签名 session cookie；AI 能力 monkeypatch 打桩。

1. **`learn_edit_style` 取数正确**：只取 finalized 且 ai_draft≠script 的，按 finalized_at DESC 限 N 条（打桩 ask_ai，断言喂进去的对数/内容）。
2. **候选路由返回结构**：`POST /learn-edits` 打桩返回候选，断言 JSON 结构 `ok/traits`。
3. **adopt 写 source='learned_edit'**：`POST /interview/adopt` 带 `source=learned_edit`，断言 `media_persona_trait.source == 'learned_edit'`、维度落 tone/signature、`status='active'`。
4. **adopt 向后兼容**：不传 source 时仍写 `interview`（保护现有访谈流程）。
5. **注入仍认这些条目**：`is_injectable` / `build_script_context` 对 `source='learned_edit'` 的 tone/signature 条目照常注入（走现有路径，加断言即可）。
6. **阈值提示渲染**：人设页 `learnable_count` 为 0 / <3 / ≥3 时页面文案与按钮状态正确。

live 真调模型的端到端验收（配 AI key，真定几条稿→复盘→看候选像不像用户）留给用户手测。

---

## 九、代码落点（轻）

| 文件 | 改动 |
|---|---|
| `app/services/media_ai.py` | +1 函数 `learn_edit_style` + 1 常量 `LEARN_EDIT_MAX_PAIRS=15` |
| `app/api/media.py` | +1 路由 `/learn-edits`；`adopt` 加 `source` 参数；`persona_detail` 加 `learnable_count` |
| `app/templates/media_persona.html` | +1 块「🪞 AI 学我改稿」UI + AJAX JS |
| `tests/test_media_routes.py` / `test_media_context.py` | +6 测试 |

**不动数据库（零 migration）、不动注入预算逻辑、不动 finalize。**

---

## 十、明确不做（YAGNI）

- **专门的改写规则库（选项 B）** —— A 起步，实测不够再升级。
- **自动学习（定稿即自动提炼入库）** —— 违反人拍板闸，坚决不做。
- **结构化文本 diff 计算** —— 交给 LLM。
- **learned 标记列 / 增量学习游标** —— 每次读最近 N 条重算即可。
- **跨人设风格迁移、风格版本历史** —— 一期只有一个人设，YAGNI。

---

## 十一、未决 / 留后的 Minor（非阻塞）

1. `LEARN_EDIT_MAX_PAIRS` 的 15 是拍脑袋值，靠实测调（token vs 规律稳定性）。
2. 阈值 3 同样待实测。
3. adopt 的 dimension 未做服务端校验（沿用现状，单租户可接受）。
4. tone vs signature 的归类由 AI 判断，可能偏差；人拍板时可改维度（复用现有 adopt 的 dimension 参数）。
