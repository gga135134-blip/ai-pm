# 自媒体二期 · 受众画像 + 生意锚点 设计 spec

**日期：** 2026-08-11
**所属：** ai-pm / 自媒体模块（media）二期 · 资产层🅑
**性质：** 正式设计 spec（已过用户逐节口头评审，待写 writing-plans 实现计划）
**前置文档：**
- 一期设计 `docs/superpowers/specs/2026-07-24-media-ops-system-design.md`（§3.2 受众与生意层原始表设计）
- 人设框架地基 spec `docs/superpowers/specs/2026-08-08-media-phase2-persona-framework-design.md`（audience/anchor 已是 trait 维度）
- 原料库/功能B 同批：`2026-08-11-media-phase2-B-learn-edit-style-design.md`

---

## 一、这块是什么，为什么做

**目标：** 把「受众画像」和「生意锚点」从人设里的一句话，升级成**结构化资产富表**——受众拆成带焦虑/原话/付费意愿的 segment 卡片，锚点拆成带价值主张/成交路径的变现方式。**为后面的选题筛选和决策引擎准备"算得动"的数据底座。**

**为什么值得做（一期设计 §3.2 原话）：**
- 受众画像是**内容和生意的双重筛子**：选题命不中某个 segment 的 anxiety → 再热也是自嗨；`pay_willingness` 高的 segment 才值得倾斜内容。
- `language`（受众原话）是隐藏高价值项：受众原话直接进脚本，比任何文案技巧都有效。
- 锚点必须显性化：没有锚点的内容播放量再高也只是热闹，有锚点后决策引擎才能判断「这选题虽热但离变现太远」。

---

## 二、核心决策：两层分工，不动写稿

人设框架已经把 `audience`（受众）和 `anchor`（生意锚点）做成了 `media_persona_trait` 的**维度**（访谈时一句话式记录，写脚本时注入）。本块新建的富表**与之两层分工，不是取代**：

| | 人设 trait 的 audience/anchor 维度（已有） | media_audience / media_anchor 富表（本块） |
|---|---|---|
| 粒度 | 一句话概括 | 完整结构化画像 |
| 消费方 | **写脚本注入**（AI 只需知道"跟谁说话"，一句话够） | **选题筛选 / 决策引擎**（需要 anxiety/pay_willingness 精算） |
| 类比 | 原料库的 evidence（本条用的） | 原料库的 material（跨内容资产） |

**锁死原则（用户拍板）：**
1. **写稿注入逻辑一行不改** —— `write_script` / `build_script_context` 继续从人设 trait 取 audience/anchor 一句话。富表**不接写稿**。风险归零。
2. **不动 finalize、不动人设 trait、不动注入预算。**
3. **人拍板闸** —— AI 起草只返回候选，人采纳才写库（复用人设访谈那套）。
4. **AI 成本可见** —— 每次 AI 起草调用后 `log_injection`。
5. **诚实不编造** —— AI 只基于用户回答/粘贴文本归纳，evidence 记来源。

---

## 三、决策记录（brainstorm 拍板）

| 决策点 | 选定 | 理由 |
|---|---|---|
| 富表 vs trait 维度 | **两层分工（不取代、不改写稿注入）** | 各喂各的场景，风险最小，同原料库 evidence/material 图式 |
| 录入方式 | **AI 访谈起草 + 人拍板 + 手动表单兜底** | 复用 interview 模式，填得快又准；language 原话字段靠 AI 从回答/评论区抽 |
| 页面位置 | **各自独立页** `/media/audience` + `/media/anchor` | 与刚上线的原料库页一致，卡片式宽敞 |
| 锚点↔segment 关联 | **砍掉（连 `target_audience_ids` 列都不建）** | 消费方（决策引擎）未建，现在连线没人用；建列+多选UI是纯负担。决策引擎立项时再补 |

---

## 四、数据（2 张新表，零 ALTER 迁移）

新表直接进 `app/database.py` 的 `SCHEMA` 字符串（`CREATE TABLE IF NOT EXISTS`），`init_db()` 的 `executescript(SCHEMA)` 重启自动建。**不写 MIGRATIONS 的 ALTER**（那是给既有表加列用的）。

### 4.1 `media_audience`（受众画像）

```sql
CREATE TABLE IF NOT EXISTS media_audience (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    segment TEXT DEFAULT '',           -- 细分人群名
    who TEXT DEFAULT '',               -- 他们是谁：年龄/职业/生活状态
    anxiety TEXT DEFAULT '',           -- 在焦虑什么（内容钩子来源）
    desire TEXT DEFAULT '',            -- 渴望变成什么样
    objection TEXT DEFAULT '',         -- 不行动的阻力/顾虑
    language TEXT DEFAULT '',          -- 他们自己怎么说这事（原话，进文案）
    pay_willingness INTEGER DEFAULT 3, -- 付费意愿 1-5
    pay_scene TEXT DEFAULT '',         -- 什么场景下会掏钱
    pay_ceiling TEXT DEFAULT '',       -- 能接受的价格带
    evidence TEXT DEFAULT '',          -- 从哪来
    confidence INTEGER DEFAULT 3,      -- 1-5
    source TEXT DEFAULT '',            -- interview/manual/comment
    status TEXT DEFAULT 'active',      -- active/archived
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

### 4.2 `media_anchor`（生意锚点）

```sql
CREATE TABLE IF NOT EXISTS media_anchor (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    name TEXT DEFAULT '',              -- 锚点名
    type TEXT DEFAULT 'service',       -- product自有产品/service服务/带货/广告/引流私域
    value_prop TEXT DEFAULT '',        -- 解决什么问题
    price_band TEXT DEFAULT '',        -- 价格带
    path TEXT DEFAULT '',              -- 从内容到成交的路径
    evidence TEXT DEFAULT '',          -- 转化数据
    source TEXT DEFAULT '',            -- interview/manual
    status TEXT DEFAULT 'validating',  -- validating验证中/proven已跑通/dropped已放弃
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);
```

> **砍掉** spec 原设计里的 `target_audience_ids`（锚点服务哪些 segment）——决策引擎未建，一期不做。

---

## 五、组件与接口

### 5.1 `media_ai.py` 新增 2 个起草函数

模仿 `persona_interview_extract`（读回答→提炼候选→绝不写库→log_injection）。

```python
async def draft_audience_segments(db, persona_id: str, answers: str, model="auto") -> dict:
    """把用户对受众的一段回答/粘贴的评论文本，提炼成 segment 画像候选。绝不写库。
    返回：{"ok", "segments":[{segment,who,anxiety,desire,objection,language,
           pay_willingness(1-5),pay_scene,pay_ceiling,evidence,confidence(1-5)}],
           "error","cost","model"}"""

async def draft_anchors(db, persona_id: str, answers: str, model="auto") -> dict:
    """把用户对变现方式的一段回答，提炼成锚点候选。绝不写库。
    返回：{"ok","anchors":[{name,type,value_prop,price_band,path,evidence}],
           "error","cost","model"}"""
```

- **红线（SYSTEM 提示）**：只基于用户给的文本归纳，不编造 segment / 锚点；`language` 字段必须是受众真实原话或用户提供的措辞，不许 AI 自造口吻。
- **夹取**：`pay_willingness`/`confidence` 夹 1-5 非法默认 3；`type` 夹回 `{product,service,带货,广告,引流私域}` 越界默认 service；全部字段走 `_txt()` 兜底。
- **成本可见**：调用后 `log_injection(db, "", "media_draft_audience"/"media_draft_anchor", [], tokens)`。

### 5.2 `media.py` 新增两组路由

**受众：**
- `GET /media/audience` → 列表页（第一个 active 人设的 segment 卡片，按 `pay_willingness DESC, confidence DESC` 排=值钱的靠前）
- `POST /media/audience` → 手动新增一条 segment（source='manual'）
- `POST /media/audience/draft` → 调 `draft_audience_segments` 返候选 JSON（不写库）
- `POST /media/audience/adopt` → 人拍板写一条（source='interview'）
- `POST /media/audience/{aid}/archive` → 软删 status='archived'

**锚点：** 对称的 5 个（`/media/anchor` 列表按 status 分组 proven→validating→dropped；draft/adopt/manual/archive）。

所有路由套现有 `_tpl` / `_first_persona_id` / try-except+JSONResponse 既有写法。

### 5.3 模板（2 新，仿原料库 media_materials.html 卡片风）

- `media_audience.html`：segment 卡片列表 + 「🎯 AI 帮我梳理受众」块（问答 textarea → draft → 候选逐张采纳/丢弃，AJAX）+ 「随手加一条」手动表单（details 折叠）。卡片显示 who/anxiety/language(高亮，标"可直接进文案")/付费意愿星级/来源，可归档。
- `media_anchor.html`：锚点卡片按 status 分组 + AI 起草 + 手动。卡片显示 type 标签/value_prop/price_band/path/status。
- **`<script>` 铁律**：AJAX 里绝不把 SVG/emoji 塞进 JS 单引号字符串；escapeHtml 防 XSS；采纳 POST 用 x-www-form-urlencoded。（沿用功能B 的验证过的写法。）

### 5.4 入口

看板头 + 人设页头加「受众」「锚点」按钮（`ic.icon` 现有图标）。

---

## 六、边界与错误处理

- **无人设**：页面空态引导先建人设（同原料库页）。
- **AI 起草空返回**：候选为空 → 页面提示"没提炼出画像，多说点或换个角度"，不装作成功（沿用🅐"空返回不装完成"）。
- **AI 返回类型不可信**：全部字段 `_txt()`/夹取兜底（DeepSeek 曾返回 int）。
- **本地 DB 无真数据**：本地只有测试人设，真 GAGA 画像在生产；浏览器验证用测试 cookie 登录 + 直接插演示行验渲染（同原料库做法）。

---

## 七、测试策略（照项目 TDD）

无 pytest-asyncio；纯函数/AI 打桩 + TestClient + 伪造签名 cookie。

1. **schema**：新表建成、字段齐（`tests/test_media_schema.py` 或新建，仿既有 schema 测）。
2. **draft_audience_segments 夹取**：AI 打桩返回越界 pay_willingness/type → 夹回合法；空回答不调 AI。
3. **draft_anchors 夹取**：同上。
4. **受众 adopt 写库**：`POST /media/audience/adopt` → 行入 media_audience，source='interview'，status='active'。
5. **手动新增**：`POST /media/audience` source='manual'。
6. **归档软删**：`POST /media/audience/{id}/archive` → status='archived'，列表不再显示。
7. **锚点对称测**：adopt/manual/archive 各一。
8. **列表页渲染**：受众页按 pay_willingness 降序、锚点页按 status 分组渲染正确（TestClient 取 HTML 断言）。

live 真调模型的端到端 + AJAX JS 崩溃检查（preview_start + 签名 cookie + typeof 函数名）由 controller/用户浏览器实测。

---

## 八、代码落点

| 文件 | 改动 |
|---|---|
| `app/database.py` | SCHEMA +2 表（media_audience / media_anchor） |
| `app/services/media_ai.py` | +2 起草函数 + 2 SYSTEM 提示常量 |
| `app/api/media.py` | +2 组路由（各 5 个）+ 常量（ANCHOR_TYPES 等） |
| `app/templates/media_audience.html` `media_anchor.html` | 2 新模板 |
| `app/templates/media_board.html` `media_persona.html` | 各 +2 入口 |
| `tests/test_media_*.py` | +约 10 测试 |

**不动写稿注入、不动 finalize、不动人设 trait、不动注入预算。**

---

## 九、明确不做（YAGNI）

- **锚点↔segment 关联（target_audience_ids）** —— 决策引擎立项时补。
- **富表接入写稿注入** —— 两层分工，写稿继续用 trait 一句话。
- **从评论区自动采集 language** —— 一期靠 AI 从用户粘贴的文本抽；自动爬评论区是以后的事。
- **决策引擎消费这些表（audience_hit / anchor_distance 打分）** —— 本块只建资产，消费方是后面独立的决策引擎 spec。
- **受众/锚点版本历史** —— 单人设，YAGNI。

---

## 十、未决 / 留后的 Minor（非阻塞）

1. AI 起草的 segment 可能与已有 segment 重复；靠人拍板肉眼去重（不加自动查重）。
2. `pay_willingness` 排序遇到并列不稳定，可接受。
3. 锚点 `type` 枚举中英混用（product/service vs 带货/广告/引流私域）沿用 spec 原文，够用。
