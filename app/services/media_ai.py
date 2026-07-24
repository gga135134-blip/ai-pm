"""media 模块的 AI 能力。每个能力是独立调用，各拿各的上下文。

设计约束（spec §6 办法一）：分工不分身 —— 绝不让一个 AI 一次干完所有事。
选题 AI 看不到原料库和脚本细节，脚本 AI 看不到数据表和话题池。
一个 AI 一件事，注意力天然集中。
"""
import json
import logging
import uuid

from app.services.ai_router import ask_ai
from app.services.media_context import extract_json, log_injection
from app.services.media_context import build_script_context

log = logging.getLogger(__name__)

RECOMMEND_SYSTEM = """你是资深自媒体选题策划。你的任务是基于人设推荐选题。

铁律（必须全部满足）：
1. 每个选题必须写出「核心谜题」—— 一个受众想解开的具体疑问，带悬念。
   反面例子："聊聊育儿焦虑"（平铺主题，没有钩子）
   正面例子："为什么越努力的妈妈，孩子越叛逆？"（有悬念，想看答案）
2. 给不出谜题的选题说明还没想透，不要输出。
3. 不得推荐与「已弃选题」同类的方向。
4. 只输出 JSON 数组，不要任何解释文字。

输出格式：
[{"title":"选题","puzzle":"核心谜题","reason":"为什么值得做","angle":"切入角度","heat":3,"fit_score":4}]
heat 和 fit_score 都是 1-5 的整数。"""


async def recommend_topics(db, persona_id: str, model: str = "auto") -> dict:
    """AI 推选题。基于人设条目 + 已发内容表现 + 弃单原因。

    看不到：原料库、脚本细节、剪辑信息 —— 与选题决策无关。
    """
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "人设不存在", "count": 0, "cost": 0, "model": ""}
    persona = dict(row)

    cur = await db.execute(
        "SELECT id,dimension,content,brief,confidence FROM media_persona_trait "
        "WHERE persona_id=? AND status='active' ORDER BY confidence DESC LIMIT 12",
        (persona_id,))
    traits = [dict(r) for r in await cur.fetchall()]

    # 已发内容的表现，让 AI 知道什么方向有效
    cur = await db.execute(
        "SELECT c.title, MAX(m.views) AS views FROM media_content c "
        "JOIN media_publish p ON p.content_id=c.id "
        "JOIN media_metrics m ON m.publish_id=p.id "
        "WHERE c.persona_id=? GROUP BY c.id ORDER BY views DESC LIMIT 10",
        (persona_id,))
    performance = [dict(r) for r in await cur.fetchall()]

    # 池子里已有的，避免重复推
    cur = await db.execute(
        "SELECT title FROM media_topic WHERE persona_id=? AND status='pool' LIMIT 30",
        (persona_id,))
    existing = [r["title"] for r in await cur.fetchall()]

    # 弃单原因 —— 防止 AI 重复推垃圾
    cur = await db.execute(
        "SELECT title, rejected_reason FROM media_topic "
        "WHERE persona_id=? AND status='rejected' ORDER BY created_at DESC LIMIT 15",
        (persona_id,))
    rejected = [dict(r) for r in await cur.fetchall()]

    parts = [
        f"人设：{persona['name']}｜{persona['one_liner']}｜当前阶段：{persona['current_phase']}",
    ]
    if traits:
        parts.append("人设条目：\n" + "\n".join(
            f"- [{t['dimension']}] {t['brief'] or t['content'][:40]}" for t in traits))
    if performance:
        parts.append("已发内容表现（播放量）：\n" + "\n".join(
            f"- {p['title']}：{p['views'] or 0}" for p in performance))
    if existing:
        parts.append("话题池已有（不要重复）：\n" + "\n".join(f"- {t}" for t in existing))
    if rejected:
        parts.append("已弃选题及原因（不要推同类）：\n" + "\n".join(
            f"- {r['title']}：{r['rejected_reason'] or '未说明'}" for r in rejected))
    parts.append("请推荐 5 个新选题。")

    prompt = "\n\n".join(parts)
    result = await ask_ai(prompt, model=model, task_type="media_topic",
                          system_prompt=RECOMMEND_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    items = extract_json(resp, expect="array")
    if not items:
        # AI 可能把数组包在 {"topics": [...]} 里
        obj = extract_json(resp, expect="object")
        items = obj.get("topics") or obj.get("data") or []
    if not items:
        return {"ok": False, "error": "AI 输出无法解析为选题列表", "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    trait_ids = [t["id"] for t in traits]
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        puzzle = (it.get("puzzle") or "").strip()
        if not title or not puzzle:
            continue  # 铁律 2：没谜题的不入库
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score,"
            " related_trait_ids) VALUES (?,?,?,?,'ai_rec',?,?,?,?,?)",
            (str(uuid.uuid4()), persona_id, title, puzzle,
             (it.get("reason") or "").strip(), (it.get("angle") or "").strip(),
             _clamp(it.get("heat"), 3), _clamp(it.get("fit_score"), 3),
             json.dumps(trait_ids, ensure_ascii=False)))
        count += 1
    await db.commit()

    await log_injection(db, "", "recommend_topics", trait_ids,
                        result.get("tokens", 0))

    return {"ok": True, "count": count, "cost": result.get("cost", 0),
            "model": result.get("model", ""), "error": ""}


def _clamp(value, default: int) -> int:
    """把 AI 给的评分夹到 1-5。AI 偶尔会返回 0、10 或字符串。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(5, v))


SCRIPT_SYSTEM = """你是资深口播脚本撰稿人，为真人出镜的短视频写口播稿。

铁律（必须全部满足，不超过 5 条 —— 规则多了每条都做不好）：
1. 必须以谜题开场，3 秒内抛出，禁止任何铺垫和自我介绍。
2. 必须植入给定的记忆点（如果提供了）。
3. 口语化 —— 写的是说出来的话，不是书面文章。短句，能断则断。
4. 标注时长节奏，全片控制在 60-90 秒。
5. 结尾留钩子，引导评论互动。

输出纯文本脚本，用 Markdown 分段。禁止用 ASCII 字符画（中文等宽会错位），
需要表格就用 Markdown 表格。不要输出 JSON，不要写解释。"""


async def write_script(db, content_id: str, mode: str = "full",
                       model: str = "auto") -> dict:
    """AI 写口播脚本。

    mode="full"：注入完整预算内的人设资产（默认）
    mode="lean"：只注入人设身份行 —— 用于与 full 对比，判断注入是否真的有效
                （spec §6 兜底：人的判断本身就是最好的评估器）

    看不到：数据表、话题池、财务 —— 与写脚本无关。
    """
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "script": "",
                "cost": 0, "model": "", "injected_count": 0}
    content = dict(row)

    cur = await db.execute(
        "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
    persona = dict(await cur.fetchone())

    if mode == "lean":
        context_text = (f"【人设】{persona['name']}｜{persona['one_liner']}"
                        f"｜当前阶段：{persona['current_phase']}")
        injected_ids = []
    else:
        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active'",
            (content["persona_id"],))
        traits = [dict(r) for r in await cur.fetchall()]
        context_text, injected_ids = build_script_context(persona, traits)

    parts = [context_text, f"【本条选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if content["idea_reason"]:
        parts.append(f"【为什么做这条】{content['idea_reason']}")
    parts.append("请写出这条内容的口播脚本。")

    prompt = "\n\n".join(parts)
    result = await ask_ai(prompt, model=model, task_type="media_script",
                          system_prompt=SCRIPT_SYSTEM)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "script": "",
                "cost": result.get("cost", 0), "model": result.get("model", ""),
                "injected_count": 0}

    await log_injection(db, content_id, f"write_script:{mode}",
                        injected_ids, result.get("tokens", 0))

    return {"ok": True, "script": resp, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", ""),
            "injected_count": len(injected_ids)}


PLATFORM_STYLE = {
    "douyin": "抖音：标题要短要炸，前 10 个字决定点开率。3-5 个话题标签。",
    "xhs": "小红书：标题带 emoji，正文分点排版，口语化像跟朋友说话。"
           "正文控制在 600 字内（超过阅读完成率骤降）。5-8 个话题标签。",
    "shipinhao": "视频号：受众年龄偏大，标题直白讲清价值，少用网络黑话。"
                 "2-3 个话题标签。",
}

COPY_SYSTEM = """你是自媒体平台文案专家。根据口播脚本，为指定平台写发布文案。

铁律：
1. 标题必须承接脚本的核心谜题，保留悬念。
2. 严格遵守该平台的字数和风格要求。
3. 话题标签用该平台真实存在的通用标签，不要造词。
4. 只输出 JSON，不要解释。

输出格式：{"title":"标题","body":"正文","tags":["标签1","标签2"]}"""


async def generate_platform_copy(db, content_id: str, account_id: str,
                                 model: str = "auto") -> dict:
    """为单个平台生成发布文案。

    看不到：人设全档、原料库、历史数据 —— 有脚本和平台特性就够了。
    """
    cur = await db.execute(
        "SELECT title, puzzle, script FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "publish_text": "",
                "cost": 0, "model": ""}
    content = dict(row)
    if not (content["script"] or "").strip():
        return {"ok": False, "error": "请先写脚本，文案是从脚本来的",
                "publish_text": "", "cost": 0, "model": ""}

    cur = await db.execute(
        "SELECT platform, platform_note FROM media_account WHERE id=?", (account_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "账号不存在", "publish_text": "",
                "cost": 0, "model": ""}
    account = dict(row)

    parts = [
        f"【平台要求】{PLATFORM_STYLE.get(account['platform'], account['platform'])}",
    ]
    if account["platform_note"]:
        parts.append(f"【本账号在该平台的策略】{account['platform_note']}")
    parts.append(f"【选题】{content['title']}")
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    parts.append(f"【口播脚本】\n{content['script'][:4000]}")
    parts.append("请生成该平台的发布文案。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_copy",
                          system_prompt=COPY_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "publish_text": "",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    if not obj:
        # 解析不了就把原文给用户，总比丢掉强
        text = resp.strip()
    else:
        tags = obj.get("tags") or []
        tag_line = " ".join(f"#{t.lstrip('#')}" for t in tags if t)
        text = "\n\n".join(x for x in [
            (obj.get("title") or "").strip(),
            (obj.get("body") or "").strip(),
            tag_line,
        ] if x)

    await log_injection(db, content_id, f"platform_copy:{account['platform']}",
                        [], result.get("tokens", 0))

    return {"ok": True, "publish_text": text, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


REVIEW_SYSTEM = """你是自媒体数据复盘专家。基于真实数据分析一条内容的表现。

铁律：
1. 结论必须基于给定数据，不许编造没给你的数字。
2. 区分「可复制的方法论」和「运气/热点」—— replicable 打分要诚实，
   蹭上热点的爆款打 1-2 分，方法论过硬的打 4-5 分。把运气当能力会让人学错。
3. 提炼人设条目时必须给证据，证据不足就少提甚至不提。
4. 只输出 JSON，不要解释。

输出格式：
{
  "platform_reviews": [
    {"platform":"douyin","what_worked":"","what_failed":"","next_action":""}
  ],
  "overall": {"what_worked":"","what_failed":"","next_action":""},
  "case": {
    "case_type":"hit|flop|normal",
    "threshold_basis":"判定依据",
    "topic_factor":"选题层归因","hook_factor":"开场钩子归因",
    "structure_factor":"结构归因","material_factor":"原料归因",
    "emotion_factor":"情绪曲线归因","platform_factor":"平台适配归因",
    "external_factor":"外部因素与运气成分",
    "replicable":3,
    "conclusion":"一句话结论"
  },
  "proposed_traits": [
    {"dimension":"positioning|audience|tone|topics|taboo|signature|differentiator",
     "content":"条目内容","brief":"≤30字精简版","evidence":"证据","confidence":3}
  ],
  "topic_fingerprint": "3-6个核心语义标签，逗号分隔，用于以后查重"
}

关于 topic_fingerprint：写这条内容"讲的是什么"的语义标签，不是标题复述。
例："职场妈妈,时间管理,愧疚感,边界感"。以后做同方向选题时靠它查重。"""


async def review_content(db, content_id: str, model: str = "auto") -> dict:
    """L1 单条复盘：N 份平台复盘 + 1 份总复盘 + 1 份归因 + 候选人设条目。

    候选条目绝不自动写入 trait 表 —— AI 提炼，人拍板。
    防止 AI 把偶然当规律污染人设（spec §5.2 关键设计）。

    看不到：原料库、话题池 —— 与复盘无关。
    """
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "review_count": 0,
                "cost": 0, "model": ""}
    content = dict(row)

    cur = await db.execute(
        "SELECT p.id AS publish_id, p.account_id, p.publish_text, a.platform, "
        "  m.views, m.likes, m.comments, m.shares, m.new_fans "
        "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
        "LEFT JOIN media_metrics m ON m.id = ("
        "  SELECT id FROM media_metrics WHERE publish_id=p.id "
        "  ORDER BY snapshot_at DESC LIMIT 1) "
        "WHERE p.content_id=? AND p.status='published'", (content_id,))
    pubs = [dict(r) for r in await cur.fetchall()]
    if not pubs:
        return {"ok": False, "error": "还没有已发布的平台，无法复盘",
                "review_count": 0, "cost": 0, "model": ""}
    if not any(p["views"] for p in pubs):
        return {"ok": False, "error": "还没有采集到数据，先录入播放量再复盘",
                "review_count": 0, "cost": 0, "model": ""}

    # 账号历史中位播放量 —— 判定爆款/失败的基准
    cur = await db.execute(
        "SELECT MAX(m.views) v FROM media_content c "
        "JOIN media_publish p ON p.content_id=c.id "
        "JOIN media_metrics m ON m.publish_id=p.id "
        "WHERE c.persona_id=? AND c.id != ? GROUP BY c.id",
        (content["persona_id"], content_id))
    history = sorted(r["v"] or 0 for r in await cur.fetchall())
    median = history[len(history) // 2] if history else 0

    parts = [f"【选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if content["script"]:
        parts.append(f"【口播脚本】\n{content['script'][:3000]}")
    parts.append("【各平台数据】\n" + "\n".join(
        f"- {p['platform']}：播放 {p['views'] or 0}，赞 {p['likes'] or 0}，"
        f"评 {p['comments'] or 0}，转 {p['shares'] or 0}，粉 +{p['new_fans'] or 0}"
        for p in pubs))
    parts.append(f"【账号历史中位播放量】{median}"
                 f"（用于判定 case_type：显著高于为 hit，显著低于为 flop）")
    parts.append("请复盘这条内容。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_review",
                          system_prompt=REVIEW_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "review_count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "error": "复盘结果无法解析", "review_count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 重跑复盘时清掉旧的，避免堆积
    await db.execute("DELETE FROM media_review WHERE content_id=?", (content_id,))
    await db.execute("DELETE FROM media_case WHERE content_id=?", (content_id,))

    by_platform = {p["platform"]: p["account_id"] for p in pubs}
    count = 0
    for pr in obj.get("platform_reviews") or []:
        if not isinstance(pr, dict):
            continue
        aid = by_platform.get(pr.get("platform", ""), "")
        await db.execute(
            "INSERT INTO media_review "
            "(id,content_id,scope,account_id,what_worked,what_failed,next_action) "
            "VALUES (?,?,'platform',?,?,?,?)",
            (str(uuid.uuid4()), content_id, aid,
             (pr.get("what_worked") or "").strip(),
             (pr.get("what_failed") or "").strip(),
             (pr.get("next_action") or "").strip()))
        count += 1

    ov = obj.get("overall") or {}
    traits = [t for t in (obj.get("proposed_traits") or []) if isinstance(t, dict)]
    await db.execute(
        "INSERT INTO media_review "
        "(id,content_id,scope,what_worked,what_failed,next_action,proposed_traits) "
        "VALUES (?,?,'overall',?,?,?,?)",
        (str(uuid.uuid4()), content_id,
         (ov.get("what_worked") or "").strip(),
         (ov.get("what_failed") or "").strip(),
         (ov.get("next_action") or "").strip(),
         json.dumps(traits, ensure_ascii=False)))
    count += 1

    case = obj.get("case") or {}
    case_type = case.get("case_type") if case.get("case_type") in \
        ("hit", "flop", "normal") else "normal"
    await db.execute(
        "INSERT INTO media_case "
        "(id,persona_id,content_id,case_type,threshold_basis,topic_factor,"
        " hook_factor,structure_factor,material_factor,emotion_factor,"
        " platform_factor,external_factor,replicable,conclusion) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), content["persona_id"], content_id, case_type,
         (case.get("threshold_basis") or "").strip(),
         (case.get("topic_factor") or "").strip(),
         (case.get("hook_factor") or "").strip(),
         (case.get("structure_factor") or "").strip(),
         (case.get("material_factor") or "").strip(),
         (case.get("emotion_factor") or "").strip(),
         (case.get("platform_factor") or "").strip(),
         (case.get("external_factor") or "").strip(),
         _clamp(case.get("replicable"), 3),
         (case.get("conclusion") or "").strip()))

    # outcome + fingerprint 供三期查重用：以后撞到同方向的 flop 会强提示。
    # 一期就写入，否则三期开工时历史内容全是空指纹，查重形同虚设。
    fingerprint = (obj.get("topic_fingerprint") or "").strip()[:200]
    await db.execute(
        "UPDATE media_content SET outcome=?, topic_fingerprint=?, stage='reviewed', "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (case_type, fingerprint, content_id))
    await db.commit()

    await log_injection(db, content_id, "review_content", [],
                        result.get("tokens", 0))

    return {"ok": True, "review_count": count, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
