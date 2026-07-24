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
