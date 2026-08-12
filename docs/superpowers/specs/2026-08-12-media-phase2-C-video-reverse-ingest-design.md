# 自媒体二期 · 功能C 视频反向入库 — 设计

**日期：** 2026-08-12
**性质：** 自媒体飞轮的一个补口——把**没走 AI-PM 流程的已发视频**，通过链接让系统转写+AI提选题，补建成 content 记录纳入飞轮。是飞书数据管道 spec §11 一直预留的功能C。
**依赖：** 自媒体一期闭环（media_content/media_publish/复盘）已上线；决策引擎（dup_penalty 读 media_content.topic_fingerprint/outcome）已上线。

---

## 1. 背景与目标

飞轮现在只认"从系统里长出来的内容"。但用户很多已发视频是早年或临时发的，没走过 AI-PM——这些真实战绩（尤其爆款/扑街）对复盘、查重、决策都是宝贵样本，却在系统外。

**目标：** 贴一条已发视频链接 → 系统自动转写 → AI 提「标题+核心谜题」 → 补建成 `media_content`（已发阶段）+ 一条 publish 记录 → 纳入飞轮（能被 L1 复盘、被决策引擎 dup_penalty 查重、被 performance 统计）。

**核心决策（brainstorm 拍板）：**
- 输入路径 = **贴链接全自动 ASR**（不走"人贴文字稿"）。
- ASR = **豆包/火山「录音文件识别」（投 URL 异步轮询）**——用户手上就是这个。
- 提取深度 = **选题(标题+谜题)+存转写全文，落 `stage='published'`**。**不拆结构/打法**（打法库🅑还没建，等它）。
- 入库方式 = **直接建 + 可后编辑**（不做预览确认闸）。内容不是人设资产，adopt 现有做法也是直接建、可逆。

---

## 2. 管线（一条链）

```
抖音/小红书/视频号链接
  ① video_fetch: yt-dlp 抽音频（服务器 subprocess）→ 本地临时音频文件
  ② 存成带随机 token 的临时音频，公开免登录可访问（豆包服务器要能 GET 到）
  ③ asr_client: 提交该公网 URL 给豆包「录音文件识别」→ 异步轮询 → 转写全文
  ④ media_ai.extract_from_transcript: DeepSeek 从稿里提「标题 + 核心谜题 + 话题指纹」
  ⑤ media_reverse: 建 media_content(stage=published, idea_source=video_reverse) + media_publish(存链接)
  ⑥ 删临时音频（finally，成败都删）
```

**为什么用公网临时音频 URL 当桥：** 「录音文件识别」是"投 URL 异步"——豆包服务器回来抓音频。ai-pm 生产服是公网裸跑 `159.75.200.213:8000`，所以把音频托管在本机、给豆包一个本机公网 URL 即可，不需额外对象存储。临时音频走随机 token + 免登录 + ASR 完即删，暴露面最小。

---

## 3. 组件（各司其职、可独立测）

### 3.1 `app/services/video_fetch.py`
- `async fetch_audio(url: str, out_dir: Path) -> Path`：调 yt-dlp（`-x --audio-format mp3` 或 m4a）把链接音频抽到 `out_dir/<uuid>.mp3`，返回路径。
- subprocess 走 `asyncio.create_subprocess_exec`，超时上限（如 120s）。
- 失败（yt-dlp 非零退出/超时/无输出文件）抛 `VideoFetchError("拿不到视频音频（平台可能防爬或链接失效）")`。
- 一期**不带 cookie**，尽力而为；抖音有时需登录态才拿得到，拿不到就报错（cookie 支持留后）。

### 3.2 `app/services/asr_client.py`
- `async transcribe_url(audio_url: str, cfg: dict) -> str`：豆包「录音文件识别」适配器。
  - submit：POST 提交任务（audio url + 格式），带鉴权头（从 cfg 取 app_id/access_key/resource_id）。拿到 task/request id。
  - poll：轮询 query 直到完成或超时（上限如 180s，间隔如 3s）。
  - 成功返回拼好的全文；失败/超时抛 `ASRError("转写失败或超时")`。
- 用 httpx（项目已依赖）。凭证与 base_url 全从入参 cfg，**不硬编码**，便于换代/换供应商。
- 适配器接口稳定（`transcribe_url(audio_url, cfg)->str`），换 ASR 只换此文件。

### 3.3 `app/services/media_ai.py`（新增能力）
- `async extract_from_transcript(transcript: str, model="auto") -> dict`：DeepSeek json_mode，`EXTRACT_SYSTEM` 让 AI 从口播稿提 `{"title","puzzle","topic_fingerprint"}`。
  - 走现有 `ask_ai` + `extract_json` + `_txt` 兜底（DeepSeek 返回错类型防御）。
  - 记 `log_injection`（成本可见，沿袭规矩）。
  - 失败（错误前缀/解析不出）返回 `{}`——由编排器兜底建带 fallback 标题的内容，不丢转写稿。

### 3.4 `app/services/media_reverse.py`（编排器）
- `async reverse_ingest(db, persona_id: str, video_url: str, model="auto") -> dict`：串 ①→⑥。
  - 建临时工作目录（如 `data/asr_tmp/`）。
  - `fetch_audio` → 移/软链到公开音频目录（带随机 token 文件名）。
  - `transcribe_url(public_url, cfg)` 取稿。
  - `extract_from_transcript` 取选题（失败则 fallback：title=链接、puzzle=""）。
  - 建 `media_content` + `media_publish`（见 §4）。
  - `finally` 删临时+公开音频文件。
  - 返回 `{ok, content_id, title, error}`。任一硬失败（fetch/asr）返回 `{ok:False, error}` 且**不建行**。

### 3.5 `app/api/media.py`（路由）
- `POST /media/reverse-ingest`（form: `video_url`）：取 `_first_persona_id` → 读豆包 cfg（settings）→ `reverse_ingest` → JSON `{ok, content_id, title, error}`。try/except 兜底防前端崩。
- `GET /media/asr-audio/{token}`：公开免登录返回临时音频文件（`FileResponse`），token 非法/文件不存在 → 404。仅服务 `data/asr_public/` 下的音频，防目录穿越（token 白名单为随机 uuid，不接受路径分隔符）。

### 3.6 `app/main.py`（白名单）
- `_AuthMiddleware` 放行 `path.startswith("/media/asr-audio/")`（仿现有 `/s/` 分享页），让豆包免登录抓音频。

### 3.7 设置页 + config
- `app/config.py` + 设置页加豆包 ASR 凭证字段：`douyin_asr_app_id` / `douyin_asr_access_key` / `douyin_asr_resource_id`（命名随实际接口定）。存 settings.json（与现有 API keys 一致）。
- `reverse_ingest` 读不到凭证 → 返回 `{ok:False, error:"未配置豆包 ASR 凭证，去设置页填"}`。

### 3.8 模板入口
- 内容列表页（或话题页附近）加「🎬 视频反向入库」入口：贴链接的输入框 + 按钮 + AJAX（POST /media/reverse-ingest，处理中态，成功跳到新内容详情/刷新）。**不塞 SVG 图标进 JS 字符串**（已知崩坑），失败存 `orig` 还原。

---

## 4. 数据落库（零 schema 变更）

复用现有表，不加新表新列：

**`media_content`：**
- `stage='published'`（本来就是已发）
- `idea_source='video_reverse'`（新取值，字段已存，可追溯）
- `title`=AI 提取（失败 fallback=链接）
- `puzzle`=AI 提取（失败留空）
- `script`=转写全文（宝贵资产，务必存住）
- `topic_fingerprint`=AI 给（供决策引擎 dup_penalty 查重）
- `idea_reason`=原视频链接

**`media_publish`：**
- `content_id`=新内容、`post_url`=视频链接、`published_at`=当下、`status='published'`、`account_id`=该人设第一个 media_account 的 id。

> ⚠️ `media_publish.account_id` 是 NOT NULL 外键→media_account。反向入库时未必知道发在哪个号，**规则定死**：取该人设第一个 account 的 id；**若该人设一个 account 都没有，则只建 content、不建 publish 记录**（performance 统计本就需要 account+手填 metrics，此时降级为"内容已入库但未挂号"，用户可后续在内容详情补挂）。编排器返回值不因是否建 publish 而变（content_id 都在）。

**metrics**：保持现有手填流程——视频真实播放/点赞用户后面在数据页手补。补了之后这条反向内容就跟原生内容一样能进 performance/复盘。

---

## 5. 失败处理（每段独立兜底，绝不留垃圾行）

| 失败点 | 处理 |
|---|---|
| 未配豆包凭证 | 早返回 `ok:False`，提示去设置页填，不建行 |
| yt-dlp 拿不到音频 | `ok:False`，报"拿不到视频音频（平台防爬/链接失效）"，**不建任何行** |
| 豆包 ASR 失败/超时 | `ok:False`，报"转写失败/超时"，不建行（轮询设上限 180s） |
| AI 提取失败 | **仍建内容**：title=链接兜底、puzzle 空、script=转写稿。你后编辑。转写稿不丢 |
| 全程 | 临时+公开音频 `finally` 里必删（成败都删），防磁盘堆积/暴露 |

---

## 6. 部署/凭证依赖（需用户在服务器备好）

1. 服务器 `pip install yt-dlp`（在 venv 里）。
2. 设置页填豆包「录音文件识别」凭证（app_id / access_key / resource_id）。
3. 抖音有时需 cookie 才拿得到音频——一期不带，拿不到就报错；需要再加 cookie 文件支持。
4. 确认生产服 `159.75.200.213:8000` 对外可达（豆包要回抓音频）——现状已可达。

---

## 7. 测试

- `asr_client`：stub httpx 单测——请求头/参数构造正确、轮询状态机（pending→success/failed）、响应解析、超时路径。不真调豆包。
- `media_reverse`：stub 掉 fetch/asr/extract 三段，单测——成功建 content+publish、`stage='published'`、`idea_source='video_reverse'`、script=稿、topic_fingerprint 落库、临时文件清理、三种失败各自行为（fetch失败不建行/asr失败不建行/extract失败建 fallback 行）。
- `video_fetch`：subprocess+网络难单测——测命令构造 + 失败路径（假 yt-dlp 返回非零 / 无输出文件 → 抛 VideoFetchError）。
- `extract_from_transcript`：AI 能力，import 冒烟 + 真机验。
- 路由 `POST /media/reverse-ingest`：TestClient + stub `reverse_ingest`（不真跑网络/AI），验 JSON 形状 + 无凭证/无人设分支。
- `GET /media/asr-audio/{token}`：白名单免登录可达（TestClient 不带 cookie 拿到文件）、非法 token 404、目录穿越防护。
- **真机 e2e**：真链接→真豆包→真 DeepSeek，需用户服务器配好凭证后跑（本地无豆包 key 就 stub asr_client 验全流程 + 真调 DeepSeek 验 extract）。

---

## 8. 代码落点小结

| 文件 | 改动 |
|---|---|
| `app/services/video_fetch.py` | 新建：yt-dlp 抽音频 + VideoFetchError |
| `app/services/asr_client.py` | 新建：豆包录音文件识别适配器 transcribe_url + ASRError |
| `app/services/media_ai.py` | 加 `extract_from_transcript` + `EXTRACT_SYSTEM` |
| `app/services/media_reverse.py` | 新建：`reverse_ingest` 编排器 |
| `app/api/media.py` | 加 `POST /media/reverse-ingest` + `GET /media/asr-audio/{token}` |
| `app/main.py` | 白名单放行 `/media/asr-audio/` |
| `app/config.py` + 设置页模板 | 加豆包 ASR 凭证字段 |
| 内容列表模板 | 加「🎬 视频反向入库」入口 + AJAX |
| tests | asr_client / media_reverse / video_fetch / 路由 / asr-audio 各测 |

---

## 9. 非目标（本次不做）

- 拆结构/打法提取（等打法库🅑建了再做）。
- 批量导入多链接（先单条跑通）。
- 抖音 cookie 登录态（尽力而为，需要再加）。
- 自动补 metrics（保持手填）。
- 异步后台任务队列（单用户同步 AJAX + "处理中"态够用；裸跑 uvicorn 无 nginx 60s 卡）。
- 视频画面/OCR 分析（只做音频 ASR）。
