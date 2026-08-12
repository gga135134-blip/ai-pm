"""media 模块的 AI 能力。每个能力是独立调用，各拿各的上下文。

设计约束（spec §6 办法一）：分工不分身 —— 绝不让一个 AI 一次干完所有事。
选题 AI 看不到原料库和脚本细节，脚本 AI 看不到数据表和话题池。
一个 AI 一件事，注意力天然集中。
"""
import json
import logging
import re
import uuid

from app.services.ai_router import ask_ai, get_model_for_task, _load_config
from app.services.media_context import extract_json, log_injection
from app.services.media_context import (
    build_script_context, render_evidence_block, render_angle_block,
    select_materials, render_material_block,
)
from app.services.media_flow import (
    PERSONA_MODULES, module_dims, default_phase_tag,
)

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
[{"title":"选题","puzzle":"核心谜题","reason":"为什么值得做","angle":"切入角度","heat":3,"fit_score":4,"audience_ids":[],"anchor_ids":[],"dropped_drift_ids":[]}]
heat 和 fit_score 都是 1-5 的整数。
audience_ids/anchor_ids 只能从下方「资产菜单」给的 id 里选，真命中才填、不沾留空。
dropped_drift_ids 只在选题往「已放弃方向」飘时才填该 id，默认空。绝不编造 id。"""


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
    menu = await _build_asset_menu(db, persona_id)
    parts.append("资产菜单（给选题打受众/锚点标用）：\n" + menu["menu"])
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
        title = _txt(it.get("title"))
        puzzle = _txt(it.get("puzzle"))
        if not title or not puzzle:
            continue  # 铁律 2：没谜题的不入库
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score,"
            " related_trait_ids,audience_ids,anchor_ids,dropped_drift_ids,tagged) "
            "VALUES (?,?,?,?,'ai_rec',?,?,?,?,?,?,?,?,1)",
            (str(uuid.uuid4()), persona_id, title, puzzle,
             _txt(it.get("reason")), _txt(it.get("angle")),
             _clamp(it.get("heat"), 3), _clamp(it.get("fit_score"), 3),
             json.dumps(trait_ids, ensure_ascii=False),
             json.dumps(_clean_ids(it.get("audience_ids"), menu["valid_aud"]),
                        ensure_ascii=False),
             json.dumps(_clean_ids(it.get("anchor_ids"), menu["valid_anc"]),
                        ensure_ascii=False),
             json.dumps(_clean_ids(it.get("dropped_drift_ids"), menu["valid_dropped"]),
                        ensure_ascii=False)))
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


def _txt(value) -> str:
    """把 AI 返回的字段安全转成去空白的字符串。

    AI（尤其 DeepSeek）会不按约定返回类型：本该是文字的字段可能给成数字、
    列表或 None。`(x or "").strip()` 对非空非字符串会崩（int 没有 .strip()）。
    这里统一兜底：字符串照常 strip；数字转字符串；其余（list/dict/None）→ ""。
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _clean_ids(raw, valid_set) -> list:
    """把 AI 返回的 id 列表过滤成"只保留合法 id"。防 AI 编造 id / 返回错类型。

    只保留：是字符串、在 valid_set 里、不重复的 id，顺序保持。非 list → []。
    """
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for x in raw:
        if isinstance(x, str) and x in valid_set and x not in seen:
            seen.add(x)
            out.append(x)
    return out


async def _build_asset_menu(db, persona_id: str) -> dict:
    """拼给 AI 打标用的资产菜单（受众/可标锚点/已放弃方向三段），并返回合法 id 集。

    dropped 锚点单列进「已放弃方向」段，只供 dropped_drift 护栏反向查，不进可标集。
    """
    cur = await db.execute(
        "SELECT id,segment,anxiety,language,pay_willingness FROM media_audience "
        "WHERE persona_id=? AND status='active'", (persona_id,))
    auds = [dict(r) for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT id,name,value_prop,status FROM media_anchor "
        "WHERE persona_id=? AND status IN ('validating','proven')", (persona_id,))
    anchors = [dict(r) for r in await cur.fetchall()]
    cur = await db.execute(
        "SELECT id,name,value_prop FROM media_anchor "
        "WHERE persona_id=? AND status='dropped'", (persona_id,))
    dropped = [dict(r) for r in await cur.fetchall()]

    lines = []
    if auds:
        lines.append("【受众 segment】命中填进 audience_ids：")
        for a in auds:
            lines.append(f"- id={a['id']}｜{a['segment']}｜焦虑:{a['anxiety']}｜原话:{a['language']}")
    if anchors:
        lines.append("【生意锚点】服务填进 anchor_ids：")
        for a in anchors:
            lines.append(f"- id={a['id']}｜{a['name']}｜{a['value_prop']}")
    if dropped:
        lines.append("【⛔ 已放弃方向】话题若往这些方向飘才填进 dropped_drift_ids：")
        for a in dropped:
            lines.append(f"- id={a['id']}｜{a['name']}｜{a['value_prop']}")
    menu = "\n".join(lines) if lines else "（当前无受众/锚点资产可标，三个 id 列都留空）"

    return {
        "menu": menu,
        "valid_aud": {a["id"] for a in auds},
        "valid_anc": {a["id"] for a in anchors},
        "valid_dropped": {a["id"] for a in dropped},
    }


TAG_SYSTEM = """你是自媒体选题的资产标注员。给每个话题标注它命中的受众/锚点。

铁律（必须全部满足）：
1. 只能从给定的资产 id 里选，绝不编造 id。
2. 真命中才填，不沾就留空数组 —— 不硬凑、不凑数。
3. dropped_drift_ids 只在话题明显往「已放弃方向」飘时才填该 id，默认空。
4. 每个话题都要在结果里出现，用它原样的 id 对应。
5. 只输出 JSON 数组，不要任何解释文字。

输出格式：
[{"id":"话题原样id","audience_ids":[],"anchor_ids":[],"dropped_drift_ids":[]}]"""


async def tag_topics(db, persona_id: str, model: str = "auto") -> dict:
    """给选题池里未打标(tagged=0)的话题批量打受众/锚点/护栏标。人拍板前只写标，不改状态。"""
    menu = await _build_asset_menu(db, persona_id)
    cur = await db.execute(
        "SELECT id,title,puzzle FROM media_topic "
        "WHERE persona_id=? AND status='pool' AND tagged=0", (persona_id,))
    topics = [dict(r) for r in await cur.fetchall()]
    if not topics:
        return {"ok": True, "count": 0, "cost": 0, "model": "", "error": ""}

    tlist = "\n".join(
        f"- id={t['id']}｜{t['title']}｜谜题:{t['puzzle']}" for t in topics)
    prompt = (f"资产菜单：\n{menu['menu']}\n\n"
              f"待标话题（{len(topics)} 个）：\n{tlist}\n\n"
              "请为每个话题标注命中的资产 id，按输出格式返回。")

    result = await ask_ai(prompt, model=model, task_type="media_topic",
                          system_prompt=TAG_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    items = extract_json(resp, expect="array")
    if not items:
        obj = extract_json(resp, expect="object")
        items = obj.get("topics") or obj.get("data") or []

    by_id = {t["id"]: t for t in topics}
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = _txt(it.get("id"))
        if tid not in by_id:
            continue  # 按 id 匹配，防错位；瞎编的 id 丢弃
        aud = _clean_ids(it.get("audience_ids"), menu["valid_aud"])
        anc = _clean_ids(it.get("anchor_ids"), menu["valid_anc"])
        drift = _clean_ids(it.get("dropped_drift_ids"), menu["valid_dropped"])
        await db.execute(
            "UPDATE media_topic SET audience_ids=?, anchor_ids=?, "
            "dropped_drift_ids=?, tagged=1 WHERE id=?",
            (json.dumps(aud, ensure_ascii=False), json.dumps(anc, ensure_ascii=False),
             json.dumps(drift, ensure_ascii=False), tid))
        count += 1
    await db.commit()

    all_ids = list(menu["valid_aud"] | menu["valid_anc"] | menu["valid_dropped"])
    await log_injection(db, "", "tag_topics", all_ids, result.get("tokens", 0))

    return {"ok": True, "count": count, "cost": result.get("cost", 0),
            "model": result.get("model", ""), "error": ""}


# ─────────────── 二期 🅐：换脑审稿策略（纯函数）───────────────
_PROVIDER_KEY = {
    "claude": "anthropic_api_key",
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
    "qwen": "qwen_api_key",
}
_PROVIDER_ORDER = ["claude", "openai", "deepseek", "qwen"]


def available_providers(config: dict) -> list[str]:
    """已配置 API Key 的模型 provider，按固定优先级排序。"""
    return [p for p in _PROVIDER_ORDER if config.get(_PROVIDER_KEY[p])]


def resolve_reviewer_model(strategy: str, writer_model: str,
                           providers: list[str]) -> str:
    """按换脑策略决定审稿用哪个模型。spec §6.3。

    swap_model：强制换一个与写稿不同的 provider（最独立最贵）。
    same_model：同模型，仅靠独立 system_prompt 分离角色（最省最弱）。
    layered（默认）：返回 'auto'，走 task_type=media_critique 路由，角色靠独立调用分离。
    """
    if strategy == "swap_model":
        for p in providers:
            if p != writer_model:
                return p
        return writer_model  # 只有一个 provider，退化
    if strategy == "same_model":
        return writer_model
    return "auto"


_GAP_RE = re.compile(r"【缺真料：(.+?)】")


def extract_gap_markers(text: str) -> list[str]:
    """抽出草稿里 AI 标注的真料缺口。无缺口返回空列表。"""
    return [m.strip() for m in _GAP_RE.findall(text or "") if m.strip()]


INTERVIEW_SYSTEM = """你是口播内容的采访者。目标：就本条选题，向创作者提出精准问题，
挖出只有他本人有的真实素材（经历/案例/数字/判断），供后续写稿用真料。

铁律：
1. 只问「本条选题」需要、而系统现有资料里没有的（给你的"已有真料"不要重复问）。
2. 问具体的真事，不问空泛感受。要能挖出细节/数字/冲突的问题。
3. 问题控制在 5-8 个，一次性问完（创作者会一次答完）。
4. 只输出 JSON：{"questions":["问题1","问题2"]}"""

EVIDENCE_SYSTEM = """你是素材整理员。把创作者的口述回答，拆成一条条结构化真实素材。

铁律：
1. 只整理创作者真说了的，不补充、不发挥、不编造。
2. 每条标类型：experience经历 / case案例 / data数据 / opinion观点 / judgment判断。
3. 一句话说不清的可拆成多条；空泛没信息量的丢掉。
4. 判断每条是否「可长期复用」：好故事/坑/判断/金句/硬数据 = 值得存进原料库、
   以后写别的稿也能调用 → reusable=true；只对这条选题有意义的一次性琐碎细节
   → reusable=false。宁缺毋滥，别把琐碎的标成可复用。
5. reusable=true 的，给 material_type（story故事/pit坑/judgment判断/opinion观点/
   data数据/quote金句）和 brief（≤20字复用摘要，当原料库索引用）。
6. 只输出 JSON：{"items":[{"item":"素材内容","item_type":"experience",
   "reusable":true,"material_type":"pit","brief":"≤20字摘要"}]}"""


async def interview_questions(db, content_id: str, model: str = "auto") -> dict:
    """就本条选题生成 5-8 个采访问题。只读不写库；基于已有原料库只问缺口。"""
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "questions": [],
                "cost": 0, "model": ""}
    content = dict(row)

    # 已有原料库 brief —— 告诉 AI 别重复问
    cur = await db.execute(
        "SELECT brief,title FROM media_material WHERE persona_id=? AND status='active' "
        "LIMIT 30", (content["persona_id"],))
    mats = [((r["brief"] or r["title"]) or "").strip() for r in await cur.fetchall()]
    mats = [m for m in mats if m]

    parts = [f"【本条选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if mats:
        parts.append("【系统已有真料（不要重复问）】\n" + "\n".join(f"- {m}" for m in mats))
    parts.append("请就这条选题提出采访问题。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_interview",
                          system_prompt=INTERVIEW_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "questions": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    questions = [_txt(q) for q in (obj.get("questions") or []) if _txt(q)]
    await log_injection(db, content_id, "interview_questions", [],
                        result.get("tokens", 0))
    return {"ok": True, "questions": questions, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def extract_evidence(db, content_id: str, answers: str,
                           model: str = "auto") -> dict:
    """把创作者的一次性回答提炼成 media_evidence 行（source='interview'）。"""
    cur = await db.execute(
        "SELECT persona_id, title, puzzle FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "count": 0, "cost": 0, "model": ""}
    content = dict(row)
    if not (answers or "").strip():
        return {"ok": False, "error": "回答是空的", "count": 0, "cost": 0, "model": ""}

    parts = [f"【选题】{content['title']}"]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    parts.append(f"【创作者的回答】\n{answers[:8000]}")
    parts.append("请整理成结构化真实素材。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_evidence",
                          system_prompt=EVIDENCE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    items = [it for it in (obj.get("items") or []) if isinstance(it, dict)]
    valid_types = {"experience", "case", "data", "opinion", "judgment"}
    valid_mtypes = {"story", "pit", "judgment", "opinion", "data", "quote"}
    written = []
    for it in items:
        item_text = _txt(it.get("item"))
        if not item_text:
            continue
        itype = it.get("item_type") if it.get("item_type") in valid_types else "experience"
        eid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_evidence "
            "(id,content_id,persona_id,item,item_type,source) "
            "VALUES (?,?,?,?,?, 'interview')",
            (eid, content_id, content["persona_id"], item_text, itype))
        mtype = it.get("material_type") if it.get("material_type") in valid_mtypes else "story"
        # 前端据 reusable 显示「存入原料库」候选，人拍板才真入库（不自动）
        written.append({"id": eid, "item": item_text, "item_type": itype,
                        "reusable": bool(it.get("reusable")),
                        "material_type": mtype, "brief": _txt(it.get("brief"))})
    count = len(written)
    await db.commit()
    await log_injection(db, content_id, "extract_evidence", [], result.get("tokens", 0))
    return {"ok": True, "count": count, "items": written, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


SCRIPT_SYSTEM = """你是资深口播脚本撰稿人，为真人出镜的短视频写口播稿。

铁律（前 5 条是手艺，第 6 条是红线，全部必须满足）：
1. 必须以谜题开场，3 秒内抛出，禁止任何铺垫和自我介绍。
2. 必须植入给定的记忆点（如果提供了）。
3. 口语化 —— 写的是说出来的话，不是书面文章。短句，能断则断。
4. 标注时长节奏，全片控制在 60-90 秒。
5. 结尾留钩子，引导评论互动。
6. 【真实性红线】只能用给定的真实素材。若某处需要你没有的真事/数字/案例，
   用 【缺真料：具体说明缺什么】 原样标注在该处，绝不编造本人经历或数字来填。
   给了角度就按角度写；缺真料标注不影响其它部分正常写。

输出纯文本脚本，用 Markdown 分段。禁止用 ASCII 字符画（中文等宽会错位），
需要表格就用 Markdown 表格。不要输出 JSON，不要写解释。"""


# ─────────────── 二期 · 人设访谈（冷启动播种人设登记表）───────────────
# 把 ip-strategist 的判断标准翻译进提示词（走法C，不运行时读外部skill）。
PERSONA_MODULE_GUIDE = {
    "positioning": "一句话定位（帮谁解决什么）、跟同类账号最大的不同、现在处在什么阶段。",
    "audience": "目标人群是谁、他们最痛的问题分几层。提醒创作者：画像只是待验证假设，别当既定事实。",
    "topics": "能持续讲的内容主场、哪些话题方向是你的、哪些方向坚决不碰。",
    "tone": "人称视角、是自嘲还是端着、平视还是高高在上、有没有口头禅、真实说话的腔调。",
    "signature": "标志性观点/口号/固定桥段。少而硬，是别人记住你的钩子，不要贪多。",
    "taboo": "内容红线：不编造本人经历、警惕AI味/卖课味/焦虑营销、身份错位禁忌。",
    "anchor": "这个号最终为什么做、怎么变现、生意目标是什么。",
}

PERSONA_INTERVIEW_SYSTEM = """你是资深 IP 人设访谈者。就给定的人设模块，向创作者提出精准的引导问题，
帮他把脑子里的东西挖出来、说清楚。

铁律：
1. 问题要具体、可回答，避免"你的定位是什么"这种大而空的问法。
2. 一次提 5-8 个问题，围绕本模块的挖掘目标，别跑题到别的模块。
3. 只输出 JSON：{"questions":["...","..."]}，不要解释。"""


async def persona_interview_questions(db, persona_id: str, module: str,
                                      model: str = "auto") -> dict:
    """就某个人设模块生成 5-8 个引导问题。只读不写库。"""
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "人设不存在", "questions": [],
                "cost": 0, "model": ""}
    if module not in PERSONA_MODULES:
        return {"ok": False, "error": "未知模块", "questions": [],
                "cost": 0, "model": ""}
    persona = dict(row)
    mod = PERSONA_MODULES[module]
    parts = [
        f"【人设】{persona['name']}｜{persona.get('one_liner', '')}"
        f"｜当前阶段：{persona.get('current_phase', '')}",
        f"【本模块】{mod['label']}",
        f"【挖掘目标】{PERSONA_MODULE_GUIDE.get(module, '')}",
        "请就本模块向创作者提出引导问题。",
    ]
    result = await ask_ai("\n\n".join(parts), model=model,
                          task_type="media_persona_interview",
                          system_prompt=PERSONA_INTERVIEW_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "questions": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    questions = [_txt(q) for q in (obj.get("questions") or []) if _txt(q)]
    await log_injection(db, "", "persona_interview_questions", [], result.get("tokens", 0))
    return {"ok": True, "questions": questions, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


PERSONA_EXTRACT_SYSTEM = """你把创作者对某个人设模块的回答，提炼成结构化人设条目。

铁律：
1. 只提炼回答里真实说过的，绝不替他编造或脑补人设。回答里没有就少提甚至不提。
2. 每条给：dimension（限本模块允许的维度）、content（完整表述）、
   brief（≤30字精简版）、evidence（引用他的原话）、confidence（1-5，他说得越笃定越高）。
3. 只输出 JSON：{"traits":[{...}]}，不要解释。"""


async def persona_interview_extract(db, persona_id: str, module: str,
                                    answers: str, model: str = "auto") -> dict:
    """把创作者的一次性回答提炼成 candidate 人设条目。绝不写库 —— 人拍板才入库。"""
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "人设不存在", "traits": [], "cost": 0, "model": ""}
    if module not in PERSONA_MODULES:
        return {"ok": False, "error": "未知模块", "traits": [], "cost": 0, "model": ""}
    if not (answers or "").strip():
        return {"ok": False, "error": "回答是空的", "traits": [], "cost": 0, "model": ""}
    persona = dict(row)
    dims = module_dims(module)
    phase_tag = default_phase_tag(module, persona.get("current_phase", ""))

    parts = [
        f"【本模块】{PERSONA_MODULES[module]['label']}",
        f"【允许的维度】{'/'.join(dims)}",
        f"【创作者的回答】\n{answers[:8000]}",
        "请把回答提炼成结构化人设条目。",
    ]
    result = await ask_ai("\n\n".join(parts), model=model,
                          task_type="media_persona_extract",
                          system_prompt=PERSONA_EXTRACT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "traits": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("traits") or []) if isinstance(it, dict)]
    traits = []
    for it in raw:
        content = _txt(it.get("content"))
        if not content:
            continue
        dim = it.get("dimension") if it.get("dimension") in dims else dims[0]
        conf = it.get("confidence")
        conf = conf if isinstance(conf, int) and 1 <= conf <= 5 else 3
        traits.append({
            "dimension": dim,
            "content": content,
            "brief": (_txt(it.get("brief")) or content)[:30],
            "evidence": _txt(it.get("evidence")),
            "confidence": conf,
            "phase_tag": phase_tag,
        })
    await log_injection(db, "", "persona_interview_extract", [], result.get("tokens", 0))
    return {"ok": True, "traits": traits, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


LEARN_EDIT_MAX_PAIRS = 15  # 一次最多看多少条定稿对，防 token 撑爆（拍脑袋值，实测调）

LEARN_EDIT_SYSTEM = """你对比"AI 写的草稿"和"创作者真人改后的定稿"，提炼创作者反复出现的改稿习惯，作为其"语气/记忆点"人设条目。

铁律：
1. 只归纳给定改动里真实反复出现的模式，绝不编造创作者没做过的改动。看不出稳定规律就少提甚至不提（返回空）。
2. 已经给你列出的"现有条目"里已有的，别重复提。
3. 每条给：dimension（只能是 tone 或 signature；改口气/句式/节奏归 tone，招牌口头禅/固定收尾归 signature）、content（完整表述这条习惯）、brief（≤30字精简版，注入用）、evidence（引用一个真实的"草稿→定稿"改动例子）、confidence（1-5，出现越多次越高）。
4. 只输出 JSON：{"traits":[{...}]}，不要解释。"""


async def learn_edit_style(db, persona_id: str, model: str = "auto") -> dict:
    """对比最近定稿的 AI 草稿 vs 用户定稿，提炼反复出现的改稿习惯为候选风格条目。
    绝不写库 —— 返回候选，人拍板 adopt 才入。功能B / spec §6.1。"""
    cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (persona_id,))
    if not await cur.fetchone():
        return {"ok": False, "error": "人设不存在", "traits": [],
                "pair_count": 0, "cost": 0, "model": ""}

    cur = await db.execute(
        "SELECT title, ai_draft, script FROM media_content "
        "WHERE persona_id=? AND authoring_stage='finalized' "
        "AND ai_draft != '' AND script != '' AND script != ai_draft "
        "ORDER BY finalized_at DESC LIMIT ?",
        (persona_id, LEARN_EDIT_MAX_PAIRS))
    pairs = [dict(r) for r in await cur.fetchall()]
    if not pairs:
        return {"ok": True, "error": "", "traits": [], "pair_count": 0,
                "cost": 0, "model": ""}

    # 现有 tone/signature 条目，喂给 AI 让它别重复提
    cur = await db.execute(
        "SELECT brief, content FROM media_persona_trait "
        "WHERE persona_id=? AND status='active' AND dimension IN ('tone','signature')",
        (persona_id,))
    existing = [(_txt(r["brief"]) or _txt(r["content"])) for r in await cur.fetchall()]

    pair_blocks = []
    for i, p in enumerate(pairs, 1):
        pair_blocks.append(
            f"[改动 {i}]\nAI 草稿：{_txt(p['ai_draft'])[:1200]}\n"
            f"我的定稿：{_txt(p['script'])[:1200]}")

    parts = [
        "【已有的 tone/signature 条目（别重复提）】\n"
        + ("\n".join(f"- {e}" for e in existing) if existing else "（暂无）"),
        "【草稿 vs 定稿 对比】\n\n" + "\n\n".join(pair_blocks),
        "请提炼反复出现的改稿习惯为结构化条目。",
    ]
    result = await ask_ai("\n\n".join(parts), model=model,
                          task_type="media_learn_edit",
                          system_prompt=LEARN_EDIT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "traits": [],
                "pair_count": len(pairs),
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("traits") or []) if isinstance(it, dict)]
    traits = []
    for it in raw:
        content = _txt(it.get("content"))
        if not content:
            continue
        dim = it.get("dimension") if it.get("dimension") in ("tone", "signature") else "tone"
        conf = it.get("confidence")
        conf = conf if isinstance(conf, int) and 1 <= conf <= 5 else 3
        traits.append({
            "dimension": dim,
            "content": content,
            "brief": (_txt(it.get("brief")) or content)[:30],
            "evidence": _txt(it.get("evidence")),
            "confidence": conf,
            "phase_tag": "",   # tone/signature 永久，不绑阶段
        })
    await log_injection(db, "", "media_learn_edit", [], result.get("tokens", 0))
    return {"ok": True, "traits": traits, "error": "", "pair_count": len(pairs),
            "cost": result.get("cost", 0), "model": result.get("model", "")}


ANCHOR_TYPES = {"product", "service", "带货", "广告", "引流私域"}

AUDIENCE_DRAFT_SYSTEM = """你把创作者对自己受众的一段描述（或粘贴的评论/私信文本），提炼成结构化的受众画像 segment。

铁律：
1. 只基于给定文本归纳，绝不编造创作者没提到的人群或数据。看不出就少提。
2. language 字段必须是受众真实原话或创作者提供的措辞，绝不自造口吻——它会直接进文案。
3. 每个 segment 给：segment(人群名)、who(他们是谁)、anxiety(在焦虑什么)、desire(渴望)、objection(顾虑)、language(他们的原话)、pay_willingness(付费意愿1-5)、pay_scene(什么场景掏钱)、pay_ceiling(价格带)、evidence(依据)、confidence(1-5)。
4. 只输出 JSON：{"segments":[{...}]}，不要解释。"""

ANCHOR_DRAFT_SYSTEM = """你把创作者对自己变现方式的描述，提炼成结构化的生意锚点。

铁律：
1. 只基于给定文本归纳，绝不编造不存在的产品或转化数据。
2. type 只能是 product/service/带货/广告/引流私域 之一。
3. 每个锚点给：name(锚点名)、type、value_prop(解决什么问题)、price_band(价格带)、path(从内容到成交的路径)、evidence(转化数据/依据)。
4. 只输出 JSON：{"anchors":[{...}]}，不要解释。"""


async def draft_audience_segments(db, persona_id: str, answers: str,
                                  model: str = "auto") -> dict:
    """把用户对受众的一段回答/粘贴文本，提炼成 segment 画像候选。绝不写库。资产层🅑。"""
    cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (persona_id,))
    if not await cur.fetchone():
        return {"ok": False, "error": "人设不存在", "segments": [], "cost": 0, "model": ""}
    if not (answers or "").strip():
        return {"ok": False, "error": "没有内容可提炼", "segments": [], "cost": 0, "model": ""}

    result = await ask_ai(f"【创作者的描述】\n{answers[:8000]}\n\n请提炼成受众画像 segment。",
                          model=model, task_type="media_draft_audience",
                          system_prompt=AUDIENCE_DRAFT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "segments": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("segments") or []) if isinstance(it, dict)]
    segments = []
    for it in raw:
        seg = _txt(it.get("segment"))
        if not seg:
            continue
        pw = it.get("pay_willingness")
        pw = pw if isinstance(pw, int) and 1 <= pw <= 5 else 3
        cf = it.get("confidence")
        cf = cf if isinstance(cf, int) and 1 <= cf <= 5 else 3
        segments.append({
            "segment": seg, "who": _txt(it.get("who")), "anxiety": _txt(it.get("anxiety")),
            "desire": _txt(it.get("desire")), "objection": _txt(it.get("objection")),
            "language": _txt(it.get("language")), "pay_willingness": pw,
            "pay_scene": _txt(it.get("pay_scene")), "pay_ceiling": _txt(it.get("pay_ceiling")),
            "evidence": _txt(it.get("evidence")), "confidence": cf,
        })
    await log_injection(db, "", "media_draft_audience", [], result.get("tokens", 0))
    return {"ok": True, "segments": segments, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def draft_anchors(db, persona_id: str, answers: str, model: str = "auto") -> dict:
    """把用户对变现方式的描述，提炼成锚点候选。绝不写库。资产层🅑。"""
    cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (persona_id,))
    if not await cur.fetchone():
        return {"ok": False, "error": "人设不存在", "anchors": [], "cost": 0, "model": ""}
    if not (answers or "").strip():
        return {"ok": False, "error": "没有内容可提炼", "anchors": [], "cost": 0, "model": ""}

    result = await ask_ai(f"【创作者的描述】\n{answers[:8000]}\n\n请提炼成生意锚点。",
                          model=model, task_type="media_draft_anchor",
                          system_prompt=ANCHOR_DRAFT_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "anchors": [],
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    raw = [it for it in (obj.get("anchors") or []) if isinstance(it, dict)]
    anchors = []
    for it in raw:
        name = _txt(it.get("name"))
        if not name:
            continue
        atype = it.get("type") if it.get("type") in ANCHOR_TYPES else "service"
        anchors.append({
            "name": name, "type": atype, "value_prop": _txt(it.get("value_prop")),
            "price_band": _txt(it.get("price_band")), "path": _txt(it.get("path")),
            "evidence": _txt(it.get("evidence")),
        })
    await log_injection(db, "", "media_draft_anchor", [], result.get("tokens", 0))
    return {"ok": True, "anchors": anchors, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def write_script(db, content_id: str, mode: str = "full",
                       model: str = "auto", hint: str = "") -> dict:
    """AI 写口播脚本。hint 非空=带要求重写（如"开头别铺垫，更狠"/"加个案例"）。

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

    # ── 二期 🅐：加载本条证据包 + 选中角度 + 可复用原料 ──
    cur = await db.execute(
        "SELECT item,item_type FROM media_evidence WHERE content_id=?", (content_id,))
    evidence = [dict(r) for r in await cur.fetchall()]

    angle_text, angle_rationale = "", ""
    if content.get("selected_angle_id"):
        cur = await db.execute(
            "SELECT angle,rationale FROM media_angle WHERE id=?",
            (content["selected_angle_id"],))
        arow = await cur.fetchone()
        if arow:
            angle_text, angle_rationale = arow["angle"], arow["rationale"]

    material_ids = []
    material_block = ""
    if mode != "lean":
        cur = await db.execute(
            "SELECT id,brief,title,use_count FROM media_material "
            "WHERE persona_id=? AND status='active'", (content["persona_id"],))
        mats = [dict(r) for r in await cur.fetchall()]
        picked_mats = select_materials(mats)
        material_ids = [m["id"] for m in picked_mats]
        material_block = render_material_block(picked_mats)

    parts = [context_text]
    ev_block = render_evidence_block(evidence)
    if ev_block:
        parts.append(ev_block)
    if material_block:
        parts.append(material_block)
    ang_block = render_angle_block(angle_text, angle_rationale)
    if ang_block:
        parts.append(ang_block)
    parts.append(f"【本条选题】{content['title']}")
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if content["idea_reason"]:
        parts.append(f"【为什么做这条】{content['idea_reason']}")
    if hint and hint.strip():
        parts.append(f"【本次重写要求（务必满足）】{hint.strip()}")
    parts.append("请写出这条内容的口播脚本。")

    prompt = "\n\n".join(parts)
    result = await ask_ai(prompt, model=model, task_type="media_script",
                          system_prompt=SCRIPT_SYSTEM)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "script": "",
                "cost": result.get("cost", 0), "model": result.get("model", ""),
                "injected_count": 0}
    if not resp.strip():
        # 模型偶尔返回空（花了钱没吐字）。别装作"完成"骗前端，直接让用户重试。
        return {"ok": False, "error": "AI 返回了空内容，请重试（模型偶尔抽风）",
                "script": "", "cost": result.get("cost", 0),
                "model": result.get("model", ""), "injected_count": 0}

    # ── 持久化草稿：进 ai_draft（不碰 script，script 留给人定稿）──
    gaps = extract_gap_markers(resp)
    gap_text = "；".join(gaps)
    await db.execute(
        "UPDATE media_content SET ai_draft=?, evidence_gap=?, "
        "authoring_stage='drafted', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (resp, gap_text, content_id))
    await db.commit()

    all_injected = injected_ids + material_ids
    await log_injection(db, content_id, f"write_script:{mode}",
                        all_injected, result.get("tokens", 0))

    return {"ok": True, "script": resp, "error": "", "gap": gap_text,
            "cost": result.get("cost", 0), "model": result.get("model", ""),
            "injected_count": len(all_injected)}


ANGLE_SYSTEM = """你是口播选题的角度策划。基于真实素材，给出 2-3 个不同的切入角度。

铁律：
1. 每个角度是一个「怎么讲这条」的具体切入点，不是选题的复述。
2. 角度之间要真不同（换个人称/换个场景/换个冲突点），不是换壳同一套。
3. 按你认为最好的排最前（第一个会被默认选中）。
4. 只用给定真实素材能支撑的角度，别设计需要编造的角度。
5. 只输出 JSON：{"angles":[{"angle":"切入角度","rationale":"为什么这个角度打得中"}]}"""


async def propose_angles(db, content_id: str, model: str = "auto") -> dict:
    """基于证据包 + 人设，出 2-3 个候选角度，默认选中第一个。看不到：数据表、话题池。"""
    cur = await db.execute("SELECT * FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "count": 0,
                "selected_id": "", "cost": 0, "model": ""}
    content = dict(row)

    cur = await db.execute(
        "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
    persona = dict(await cur.fetchone())

    cur = await db.execute(
        "SELECT item,item_type FROM media_evidence WHERE content_id=?", (content_id,))
    evidence = [dict(r) for r in await cur.fetchall()]

    parts = [
        f"【人设】{persona['name']}｜{persona['one_liner']}",
        f"【选题】{content['title']}",
    ]
    if content["puzzle"]:
        parts.append(f"【核心谜题】{content['puzzle']}")
    if evidence:
        parts.append("【真实素材】\n" + "\n".join(
            f"- [{e['item_type']}] {e['item']}" for e in evidence))
    else:
        parts.append("【真实素材】暂无 —— 只给这条选题现有信息能支撑的角度。")
    parts.append("请给出 2-3 个切入角度。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_angle",
                          system_prompt=ANGLE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "count": 0, "selected_id": "",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    angles = [a for a in (obj.get("angles") or []) if isinstance(a, dict)
              and _txt(a.get("angle"))]
    if not angles:
        return {"ok": False, "error": "AI 没给出可用角度", "count": 0, "selected_id": "",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 重出角度时清掉旧的，避免堆积
    await db.execute("DELETE FROM media_angle WHERE content_id=?", (content_id,))
    selected_id = ""
    count = 0
    for idx, a in enumerate(angles):
        aid = str(uuid.uuid4())
        is_sel = 1 if idx == 0 else 0
        if is_sel:
            selected_id = aid
        await db.execute(
            "INSERT INTO media_angle "
            "(id,content_id,angle,rationale,is_selected,status) "
            "VALUES (?,?,?,?,?,?)",
            (aid, content_id, _txt(a.get("angle")), _txt(a.get("rationale")),
             is_sel, "selected" if is_sel else "candidate"))
        count += 1
    await db.execute(
        "UPDATE media_content SET selected_angle_id=? WHERE id=?",
        (selected_id, content_id))
    await db.commit()
    await log_injection(db, content_id, "propose_angles", [], result.get("tokens", 0))
    return {"ok": True, "count": count, "selected_id": selected_id, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


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
        raw_tags = obj.get("tags")
        # AI 可能把 tags 返回成字符串、含非字符串元素、或 None —— 统一兜底
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        elif not isinstance(raw_tags, list):
            raw_tags = []
        tag_line = " ".join(f"#{s.lstrip('#')}"
                            for s in (_txt(t) for t in raw_tags) if s)
        text = "\n\n".join(x for x in [
            _txt(obj.get("title")),
            _txt(obj.get("body")),
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
    {"dimension":"positioning|audience|tone|topics|taboo|signature|differentiator|anchor",
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
             _txt(pr.get("what_worked")),
             _txt(pr.get("what_failed")),
             _txt(pr.get("next_action"))))
        count += 1

    ov = obj.get("overall") or {}
    traits = [t for t in (obj.get("proposed_traits") or []) if isinstance(t, dict)]
    await db.execute(
        "INSERT INTO media_review "
        "(id,content_id,scope,what_worked,what_failed,next_action,proposed_traits) "
        "VALUES (?,?,'overall',?,?,?,?)",
        (str(uuid.uuid4()), content_id,
         _txt(ov.get("what_worked")),
         _txt(ov.get("what_failed")),
         _txt(ov.get("next_action")),
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
         _txt(case.get("threshold_basis")),
         _txt(case.get("topic_factor")),
         _txt(case.get("hook_factor")),
         _txt(case.get("structure_factor")),
         _txt(case.get("material_factor")),
         _txt(case.get("emotion_factor")),
         _txt(case.get("platform_factor")),
         _txt(case.get("external_factor")),
         _clamp(case.get("replicable"), 3),
         _txt(case.get("conclusion"))))

    # outcome + fingerprint 供三期查重用：以后撞到同方向的 flop 会强提示。
    # 一期就写入，否则三期开工时历史内容全是空指纹，查重形同虚设。
    fingerprint = _txt(obj.get("topic_fingerprint"))[:200]
    await db.execute(
        "UPDATE media_content SET outcome=?, topic_fingerprint=?, stage='reviewed', "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (case_type, fingerprint, content_id))
    await db.commit()

    await log_injection(db, content_id, "review_content", [],
                        result.get("tokens", 0))

    return {"ok": True, "review_count": count, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


# ─────────────── 二期 🅐：独立审稿 + 定向修订 ───────────────

CRITIQUE_SYSTEM = """你是独立的口播稿审稿人。你没参与写作，只负责挑毛病。

铁律：
1. 你只【指出】问题，绝不改写、绝不给出修改后的文本。
2. 逐维度找证据支撑的问题，找不到就留空数组，别硬凑。
3. 真实性最优先：任何看起来像编造的经历/数字/案例都要标进 fact_flags。
4. 打分诚实：score 1-5。verdict 只能是 pass(可直接用)/revise(建议改一次)/reject(建议回素材或角度)。
5. 只输出 JSON：
{"fact_flags":[],"persona_flags":[],"platform_flags":[],"gap_flags":[],
 "risk_flags":[],"score":3,"verdict":"pass","notes":"一句话总评"}

维度说明：fact_flags事实/数字存疑；persona_flags不像本人/AI味/卖课味/焦虑词；
platform_flags平台适配问题；gap_flags缺真料的地方；risk_flags红线/边界风险。"""

REVISE_SYSTEM = """你是原稿作者，现在根据审稿意见做【一次】定向修订。

铁律：
1. 只针对审稿指出的问题改，别推倒重写。
2. 仍守真实性红线：缺真料的地方继续用 【缺真料：说明】 标注，绝不编造。
3. 输出修订后的完整脚本纯文本，不要解释，不要输出 JSON。"""


async def critique_draft(db, content_id: str, strategy: str = "layered",
                         model: str = "auto") -> dict:
    """独立审稿 ai_draft。写稿器≠审稿器：这是与 write_script 完全独立的调用。

    看不到"这是刚才那个 AI 写的" —— 只给草稿全文，避免自我背书。
    """
    cur = await db.execute(
        "SELECT persona_id, ai_draft FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "review_id": "",
                "score": 0, "verdict": "", "reviewer_model": "", "cost": 0, "model": ""}
    draft = (row["ai_draft"] or "").strip()
    if not draft:
        return {"ok": False, "error": "还没有草稿可审，先让 AI 出草稿",
                "review_id": "", "score": 0, "verdict": "",
                "reviewer_model": "", "cost": 0, "model": ""}

    # 换脑：按策略决定审稿模型（与写稿模型错开）
    config = _load_config()
    writer_model = get_model_for_task("media_script", "auto")
    reviewer_model = resolve_reviewer_model(strategy, writer_model,
                                            available_providers(config))
    use_model = model if model != "auto" else reviewer_model

    prompt = f"【待审口播稿】\n{draft[:6000]}\n\n请审这份稿子。"
    result = await ask_ai(prompt, model=use_model, task_type="media_critique",
                          system_prompt=CRITIQUE_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "review_id": "", "score": 0,
                "verdict": "", "reviewer_model": use_model,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")

    def _flags(key):
        v = obj.get(key)
        if not isinstance(v, list):
            return []
        return [_txt(x) for x in v if _txt(x)]

    verdict = obj.get("verdict") if obj.get("verdict") in ("pass", "revise", "reject") \
        else "revise"
    review_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_draft_review "
        "(id,content_id,reviewed_draft,reviewer_strategy,reviewer_model,"
        " fact_flags,persona_flags,platform_flags,gap_flags,risk_flags,"
        " score,verdict,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (review_id, content_id, draft, strategy, result.get("model", use_model),
         json.dumps(_flags("fact_flags"), ensure_ascii=False),
         json.dumps(_flags("persona_flags"), ensure_ascii=False),
         json.dumps(_flags("platform_flags"), ensure_ascii=False),
         json.dumps(_flags("gap_flags"), ensure_ascii=False),
         json.dumps(_flags("risk_flags"), ensure_ascii=False),
         _clamp(obj.get("score"), 3), verdict, _txt(obj.get("notes"))))
    await db.commit()
    await log_injection(db, content_id, f"critique_draft:{strategy}", [],
                        result.get("tokens", 0))
    return {"ok": True, "review_id": review_id, "score": _clamp(obj.get("score"), 3),
            "verdict": verdict, "reviewer_model": result.get("model", use_model),
            "error": "", "cost": result.get("cost", 0), "model": result.get("model", "")}


async def revise_draft(db, content_id: str, model: str = "auto") -> dict:
    """按最近一条审稿意见改一次。至多一次：revision_count>=1 直接拒绝。"""
    cur = await db.execute(
        "SELECT ai_draft, revision_count FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在", "script": "",
                "revision_count": 0, "cost": 0, "model": ""}
    if (row["revision_count"] or 0) >= 1:
        return {"ok": False, "error": "已经定向修订过一次了，第二次请回到素材或角度重来",
                "script": "", "revision_count": row["revision_count"],
                "cost": 0, "model": ""}
    draft = (row["ai_draft"] or "").strip()
    if not draft:
        return {"ok": False, "error": "还没有草稿", "script": "",
                "revision_count": 0, "cost": 0, "model": ""}

    cur = await db.execute(
        "SELECT fact_flags,persona_flags,platform_flags,gap_flags,risk_flags,notes "
        "FROM media_draft_review WHERE content_id=? ORDER BY created_at DESC LIMIT 1",
        (content_id,))
    rev = await cur.fetchone()
    if not rev:
        return {"ok": False, "error": "没有审稿意见可依据，先审稿", "script": "",
                "revision_count": 0, "cost": 0, "model": ""}

    flag_lines = []
    for key, label in [("fact_flags", "事实存疑"), ("persona_flags", "不像本人"),
                       ("platform_flags", "平台适配"), ("gap_flags", "缺真料"),
                       ("risk_flags", "风险")]:
        try:
            arr = json.loads(rev[key] or "[]")
        except (json.JSONDecodeError, TypeError):
            arr = []
        for a in arr:
            flag_lines.append(f"- [{label}] {a}")
    notes = _txt(rev["notes"])

    parts = [f"【原稿】\n{draft[:6000]}", "【审稿意见】"]
    if flag_lines:
        parts.append("\n".join(flag_lines))
    if notes:
        parts.append(f"总评：{notes}")
    parts.append("请据此做一次定向修订，输出完整脚本。")

    result = await ask_ai("\n\n".join(parts), model=model, task_type="media_script",
                          system_prompt=REVISE_SYSTEM)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "error": resp, "script": "",
                "revision_count": row["revision_count"] or 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    if not resp.strip():
        return {"ok": False, "error": "AI 返回了空内容，请重试",
                "script": "", "revision_count": row["revision_count"] or 0,
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    new_count = (row["revision_count"] or 0) + 1
    gap_text = "；".join(extract_gap_markers(resp))
    await db.execute(
        "UPDATE media_content SET ai_draft=?, evidence_gap=?, revision_count=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (resp, gap_text, new_count, content_id))
    await db.commit()
    await log_injection(db, content_id, "revise_draft", [], result.get("tokens", 0))
    return {"ok": True, "script": resp, "revision_count": new_count, "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}
