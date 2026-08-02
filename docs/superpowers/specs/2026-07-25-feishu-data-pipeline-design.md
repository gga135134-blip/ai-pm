# 飞书数据管道 —— 自己账号数据自动进 AI-PM 设计文档

**日期：** 2026-07-25
**所属项目：** ai-pm / 自媒体模块（media）
**模块代号：** feishu_sync
**状态：** 设计待用户审阅

---

## 1. 背景与目标

### 1.1 要解决什么

自媒体飞轮的引擎是**自己账号的真实数据**（复盘、爆款库、打法命中率全靠它转）。但一期只做了**截图识图 + 手填**两条采集路径，都要人动手抄数据。本设计让**自己账号的数据自动流进 AI-PM**，把「手动抄数据的人」从流程里解放出来。

### 1.2 范围（严格，别贪心）

**只做**：AI-PM 从**飞书多维表格**读取自己账号的数据，映射进 `media_metrics`。

**不做**（都是后续独立任务）：
- ❌ 热点抓取、对标账号研究（那是 OpenClaw 登场的地方，下一个 spec）
- ❌ 接入 OpenClaw（读一张干净的飞书表用不上它的工具集，杀鸡用牛刀）
- ❌ 公司宪法接入媒体模块、分层打通（独立小任务）
- ❌ AI-PM 往飞书写数据（保持单向：只读不写）

### 1.3 为什么用飞书、为什么不用 OpenClaw（决策依据）

- **飞书多维表格生态成熟**：飞书连接器/集成平台/Coze 已能把抖音/小红书/视频号数据抓进 Base（森陌文化、何同学工作室在用）。把「跟平台反爬搏斗」这件最脏最不稳的活外包给飞书，AI-PM 保持干净。
- **自己账号数据有「授权权」**，走「授权类/官方生态」而非「爬取类」，不拿主账号冒险。
- **OpenClaw 的价值是「去乱糟糟有反爬的地方连蒙带猜地抓」**（热点/对标），读一张结构化的飞书表用不上它，硬塞只会多一层易坏的依赖。OpenClaw 留到做热点时登场。

### 1.4 系统层通用能力（关键架构原则）

AI-PM 是「操作系统」，自媒体/学习/会议室是不同「app」。**读飞书**这个能力属于**系统层通用能力**，不埋进自媒体模块：
- `feishu_client.py` 是通用的飞书 OpenAPI 客户端（放 services 顶层，不叫 media_feishu）
- **自媒体是它的第一个使用者**；以后会议室、其它模块要读飞书直接复用
- 这兑现「底座建一次，处处复用」

---

## 2. 数据流与连接方式

### 2.1 整条链路

```
[飞书侧 · 前置条件 · 非 AI-PM 代码]
  飞书连接器 / 集成平台 / Coze  ──抓──▶  飞书多维表格(Base)
                                          每行 ≈ 某账号某条视频的一次数据
        │
        │ 飞书 OpenAPI（读多维表格记录）
        ▼
[AI-PM 代码]
  feishu_client.py（系统层：拿 token、读 Base 记录）
        │
        ▼
  media_feishu_sync.py（媒体层：飞书行 → media 数据）
        │  按 post_url 匹配到已有 media_publish
        ▼
  media_metrics（collected_by='feishu'，每次同步存一个快照）
```

### 2.2 前置条件（用户/我在飞书侧配，非本 spec 的代码）

1. 在飞书里建一个**多维表格**，用连接器/Coze 把三平台账号数据抓进去（视频链接、播放、点赞、评论、收藏、转发、发布时间等）。
2. 建一个**飞书自建应用**，拿到 `app_id` / `app_secret`，给它开「多维表格读取」权限，把应用加进那个表格的协作者。
3. 记下 Base 的 `app_token`（表格标识）和 `table_id`。

> 这些在设计文档里列为「接入前置」，具体操作用户按飞书文档做；AI-PM 代码只消费飞书 OpenAPI。

### 2.3 匹配钥匙：post_url 为主，标题为辅

**飞书行 ↔ AI-PM 内容 的匹配：先按 `post_url`（视频链接），对不上再按标题兜底。**

- AI-PM 里走「选题→写脚本→发布」流程时，标记已发会记下 `media_publish.post_url`（一期已有字段）。用户确认**会认真填链接**保证准确。
- 匹配顺序：
  1. **post_url 精确匹配**（首选，最准）
  2. post_url 匹配不到 → **标题模糊匹配兜底**（飞书行标题 ≈ content.title，归一化后比对：去空格/标点/emoji，允许一方是另一方的子串）。标题匹配到的**标记为「疑似匹配」**，让用户确认，不当成板上钉钉。
- 匹配到 → 把这行写成该 publish 的一个 metrics 快照。

**匹配不上的行 → 亮出来 + 标色 + 人工补（用户拍板决策）**：
- **不悄悄跳过、也不自动瞎建**。同步后列一个「⚠️ 未对上」清单，**不同颜色标出来**（红/黄）。
- 这些是「B 类内容」——用户直接发、或用 AI-PM 前就发的老视频，AI-PM 里没有对应记录。
- 每条给用户手动操作：**① 关联到某条已有内容**（手动指认）**② 先暂存**（回头处理）。
- 一期**不做**「AI 识别视频内容自动建内容」——那是独立的**功能 C**（见 §11），需要视频分析能力（豆包 ASR 等）。一期先把未对上的行**可见地摆出来**，让用户不漏掉、能手动补。

---

## 3. 组件设计

### 3.1 `app/services/feishu_client.py`（系统层通用）

飞书 OpenAPI 的最小客户端，纯技术、不含媒体逻辑。

```
get_tenant_access_token() -> str
    # 用 app_id/app_secret 换 tenant_access_token，带内存缓存（token 有效期约 2 小时，过期前复用）

async list_bitable_records(app_token, table_id, page_size=500) -> list[dict]
    # 分页读多维表格所有记录，返回原始记录列表（每条含 fields 字典）
    # 失败抛结构化异常，调用方兜底
```

- 配置来自 `settings.feishu_app_id` / `settings.feishu_app_secret`（加进 config.py，存 .env/settings.json，已 gitignore）。
- 通用：不认识「views/账号」这些媒体概念，只负责「把飞书表的行读回来」。

### 3.2 字段映射配置

飞书表的列名是用户/连接器定的，AI-PM 不能写死。需要一份**映射配置**告诉 AI-PM「哪列是什么」：

```
存在 settings.json["feishu_media_map"]：
{
  "app_token": "...",
  "table_id": "...",
  "fields": {
    "post_url":   "视频链接",     # 飞书列名 → 语义
    "views":      "播放量",
    "likes":      "点赞",
    "comments":   "评论",
    "shares":     "转发",
    "new_fans":   "新增粉丝",     # 可空，飞书连接器不一定有
    "snapshot_at":"数据日期"       # 可空
  }
}
```

设置页提供表单编辑这份映射（下拉选飞书实际列名，避免手打错）。

### 3.3 `app/services/media_feishu_sync.py`（媒体层）

编排：读飞书 → 匹配 → 写 metrics。

```
# 纯函数（可单元测试，不碰 DB/网络）：
map_feishu_row(row_fields, field_map) -> dict | None
    # 按映射把一行飞书 fields 提出 {post_url, views, likes, comments, shares, new_fans}
    # 数字走 media_metrics.normalize_metrics（复用一期的 1.2万→12000 等归一化）
    # 没有 post_url 的行返回 None（无法匹配，跳过）

# 异步编排（DB）：
async sync_from_feishu(db) -> dict
    # 1. feishu_client.list_bitable_records(...) 读全表
    # 2. 建 post_url → publish_id 索引（SELECT post_url,id FROM media_publish WHERE post_url<>'')
    # 3. 逐行 map_feishu_row → 按 post_url 找 publish_id
    #    - 匹配到：save_metrics(db, publish_id, data, collected_by='feishu')（复用一期）
    #    - 匹配不上：计入 skipped
    # 4. 返回报告：{ok, synced_count, skipped_count, skipped_samples, error}
```

- **复用一期的 `normalize_metrics` 和 `save_metrics`**——飞书数据走同一条归一化+快照通道，`collected_by='feishu'` 只是多一个来源标签（一期设计已支持 auto/screenshot/manual，加 feishu 天然兼容）。
- **快照语义**：每次同步给匹配到的 publish 存一条新 metrics 行（和手填一样是快照，留增长曲线）。去重策略见 §5。

### 3.4 触发方式

- **一期：手动按钮**。媒体看板/设置页一个「🔄 从飞书同步数据」按钮 → 调 `sync_from_feishu` → 弹出报告（同步 N 条、跳过 M 条）。
- **定时同步**：一期**不做**，但 `sync_from_feishu` 设计成可被定时器直接调用（以后接 cron/心跳一行接上）。理由：先让用户手动点着确认数据对了，再谈自动。

### 3.5 UI（最小）

- **设置页**新增「飞书数据源」区块：填 `app_id`/`app_secret`/`app_token`/`table_id` + 字段映射表单 + 「测试连接」按钮（拉一条记录验证通)。
- **媒体页**：「🔄 从飞书同步」按钮 + 同步结果提示条。
- 遵守一期 UI 约束：`@media (max-width:767px)` 不用 Tailwind `md:`；`TemplateResponse` 三参数；不引新框架。
  （注意：全局 UI 重设计在另一个窗口进行，本模块页面尽量简洁，跟随 base.html 现有风格，不自造视觉。）

---

## 4. 错误处理与降级

AI 输出不可信、外部 API 会挂——一期教训，全部兜底：

| 情况 | 处理 |
|---|---|
| 飞书 token 换取失败 / API 报错 | `sync_from_feishu` 返回 `ok=False` + 清晰错误，不崩，UI 提示「飞书连不上，检查凭证」 |
| 某行缺 post_url / 字段映射不上 | 跳过该行，计入 skipped，不影响其它行 |
| post_url 匹配不到 publish | 跳过 + 报告（"未在 AI-PM 登记的内容"），不建孤儿 |
| 数字字段是脏值（"暂无"/空/万单位） | 走 normalize_metrics 兜底（脏值→0，1.2万→12000） |
| 深度指标（涨粉等）飞书没有 | **绝不填 0 冒充真实**。该字段标「待手填」灰色占位，UI 上跟飞书自动数据**用不同颜色/来源区分**，用户手动补真实值。数据真实性是飞轮命根子，宁缺勿假 |
| 飞书列名改了 | 映射失配 → 跳过 + 报告，用户去设置页更新映射 |

**保底不动**：一期的截图识图 + 手填**原样保留**。飞书是多一条自动路径，不是替换——飞书拿不到的、或没接通时，人工路径永远在。

---

## 5. 去重策略

同一条 publish 会被多次同步（每天点一次），语义是「增长快照」，本该多行。但要防「同一天同一 publish 重复存」：

**一期策略（简单可靠）**：`sync_from_feishu` 里，若某 publish **当天已有 `collected_by='feishu'` 的快照**，则**更新那条**而非插新行；跨天则插新行。这样：
- 一天内多次点同步 → 只保留当天最新，不堆重复
- 跨天 → 每天一条，留增长曲线

（`save_metrics` 目前只插不更，需为 feishu 路径加一个「当天已存则更新」的小分支，或在 sync 层先查后决定 insert/update。）

---

## 6. 配置与密钥

- 新增 `settings`：`feishu_app_id`、`feishu_app_secret`（config.py + .env）
- `settings.json["feishu_media_map"]`：表标识 + 字段映射
- ⚠️ 密钥只进 `.env` / `settings.json`（均已 gitignore），**绝不进代码、不进 Git**
- 设置页「测试连接」帮用户确认凭证和映射对了再用

---

## 7. 测试策略

沿用一期做法（项目无 pytest-asyncio）：

- **纯函数单测**：`map_feishu_row`（各种飞书行 → 正确提取/归一化/None）。覆盖：正常行、缺 post_url、脏数字、飞书万单位、缺失可空列。
- **编排逻辑**：`sync_from_feishu` 是异步+DB，用控制器 live 测（TestClient + 伪造 session，仿一期）：塞几条 media_publish + 喂假的飞书行（mock `list_bitable_records`）→ 断言匹配的写了 metrics、不匹配的跳过、报告数对、去重生效。
- **feishu_client**：token 缓存和记录解析可单测（mock httpx 响应）；真实飞书调用由用户「测试连接」按钮验证。

---

## 8. 技术约束（沿用项目规范）

- 无新前端框架，vanilla JS，本地裁剪版 tailwind（用色前 grep 确认）
- `@media (max-width:767px)`，不用 Tailwind `md:`
- `TemplateResponse` 三参数；Jinja2 无 tojson，用 json.dumps + |safe；模板 dict 键别用 items/keys/values/get
- 不用 PowerShell -replace 改 UTF-8 文件
- 新增 config 字段走 pydantic Settings；密钥进 .env
- 数字归一化、快照写入**复用一期** `media_metrics.normalize_metrics` / `save_metrics`

---

## 9. 交付后要用户做的事

1. 飞书侧：建多维表格 + 连接器抓数据 + 建自建应用给权限（§2.2）
2. AI-PM 设置页：填飞书凭证 + 配字段映射 + 测试连接
3. 确保 AI-PM 里发布内容时填了 `post_url`（否则匹配不上）
4. 点「从飞书同步」，核对数据对不对

---

## 10. 已确认的决策（用户拍板）

1. **匹配钥匙**：post_url 为主 + 标题模糊匹配兜底；标题匹配的标「疑似」让用户确认。用户确认会认真填链接。
2. **未对上的行**：亮出来 + 标色 + 用户手动补（关联/暂存），不跳过不自动建。AI 识别视频入库是独立功能 C（§11）。
3. **触发方式**：一期只做手动同步按钮，定时留后。
4. **深度指标**：以真实为准，飞书拿不到的**绝不填 0 冒充**，标「待手填」+ 来源颜色区分，用户补真实值。

---

## 11. 后续独立功能（不在本 spec，已记入项目记忆）

- **功能 B — AI 学用户改稿（飞轮升级）**：AI 出脚本 → 用户改（更口语化/更像自己）→ 系统对比 AI 原稿与终稿，提炼「用户风格特征」沉淀进人设 → 下次写得更像用户。这是「写作做轻、体系做重」的极致：每一笔修改都在教 AI。属「写脚本」侧，跟飞书拉数据无关，独立 spec。
- **功能 C — 给链接让 AI 识别视频内容、反向入库**：把没走 AI-PM 流程的已发视频，通过链接让 AI 分析（转文字/提选题/拆结构）补建成内容记录，纳入飞轮。需视频分析能力——**豆包语音识别（ASR）**（用户有现成 API，OpenClaw 也装了；之前会话查过豆包 ASR 接入规格）。是 §2.3「未对上的行」的自动化升级。
- 定时自动同步（接 cron/心跳）
- OpenClaw 接入做热点/对标（它真正的主场，自带工具多）
- 封面生成（用户有现成的其它模型 API）
- 公司宪法接入媒体模块 / 分层打通
