# app/services/media_agent_tools.py
"""自媒体助手工具集：查类 + 改草稿类。ctx=persona_id。每个工具返回给 agent 的文本。"""
import json, uuid
from app.database import get_db
from app.services.media_assistant import log_action
from app.services.ai_router import ask_ai


async def _tool_list_contents(args, pid):
    stage = (args or {}).get("stage")
    db = await get_db()
    try:
        if stage:
            cur = await db.execute(
                "SELECT id,title,stage FROM media_content WHERE persona_id=? AND stage=? "
                "ORDER BY updated_at DESC LIMIT 50", (pid, stage))
        else:
            cur = await db.execute(
                "SELECT id,title,stage FROM media_content WHERE persona_id=? "
                "ORDER BY updated_at DESC LIMIT 50", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    if not rows:
        return "（该人设暂无内容）"
    return "\n".join(f"[{r['id']}] {r['title']}（{r['stage']}）" for r in rows)


async def _tool_read_content(args, pid):
    cid = (args or {}).get("id", "")
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT title,puzzle,script,ai_draft,stage FROM media_content "
            "WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        return "（找不到这条内容，或不属于当前人设）"
    r = dict(row)
    body = r["script"] or r["ai_draft"] or "（暂无正文/脚本）"
    return f"标题：{r['title']}\n谜题：{r['puzzle']}\n阶段：{r['stage']}\n正文：\n{body}"


async def _tool_list_topics(args, pid):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id,title FROM media_topic WHERE persona_id=? AND status='pool' "
            "ORDER BY created_at DESC LIMIT 50", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"[{r['id']}] {r['title']}" for r in rows) or "（选题池为空）"


async def _tool_list_playbooks(args, pid):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT name,when_to_use,status FROM media_playbook "
            "ORDER BY CASE status WHEN 'proven' THEN 0 ELSE 1 END, created_at DESC")
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    if not rows:
        return "（打法库为空）"
    return "\n".join(f"{r['name']}｜适用:{r['when_to_use']}｜{r['status']}" for r in rows)


async def _tool_list_materials(args, pid):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT type,title,brief FROM media_material "
            "WHERE (persona_id=? OR scope='shared') AND status='active' LIMIT 60", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"[{r['type']}] {r['title']}：{r['brief'] or ''}" for r in rows) or "（原料库为空）"


async def _tool_list_audiences(args, pid):
    db = await get_db()
    try:
        cur = await db.execute("SELECT segment,anxiety FROM media_audience WHERE persona_id=? LIMIT 40", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"{r['segment']}：{r['anxiety'] or ''}" for r in rows) or "（暂无受众）"


async def _tool_list_anchors(args, pid):
    db = await get_db()
    try:
        cur = await db.execute("SELECT name,type,status FROM media_anchor WHERE persona_id=? LIMIT 40", (pid,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return "\n".join(f"{r['name']}（{r['type']}·{r['status']}）" for r in rows) or "（暂无锚点）"


_READ = {
    "list_contents": _tool_list_contents, "read_content": _tool_read_content,
    "list_topics": _tool_list_topics, "list_playbooks": _tool_list_playbooks,
    "list_materials": _tool_list_materials, "list_audiences": _tool_list_audiences,
    "list_anchors": _tool_list_anchors,
}


async def _tool_create_topic(args, pid):
    a = args or {}
    title = (a.get("title") or "").strip()
    if not title:
        return "（建选题需要 title）"
    tid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_topic (id,persona_id,title,puzzle,source,reason,status) "
            "VALUES (?,?,?,?, 'assistant',?, 'pool')",
            (tid, pid, title, (a.get("puzzle") or "").strip(), (a.get("reason") or "").strip()))
        await log_action(db, pid, "create_topic", "media_topic", tid, after={"title": title})
    finally:
        await db.close()
    return f"已把选题「{title}」加进选题池（可在改动记录里撤销）。"


_NEXT_SYSTEM = """基于给的上一条内容（转写稿+结尾预告），拟这个人设的下一条选题。
只输出严格 JSON：{"title":"","puzzle":"","reason":"承接上期…"}"""


async def _tool_write_next(args, pid):
    cid = (args or {}).get("from_content_id", "")
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT title,script FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        src = await cur.fetchone()
        if not src:
            await db.close()
            return "（找不到源内容）"
        src = dict(src)
        result = await ask_ai(f"上一条标题：{src['title']}\n转写稿：\n{(src['script'] or '')[:4000]}",
                              model="auto", task_type="media_topic",
                              system_prompt=_NEXT_SYSTEM, json_mode=True)
        obj = {}
        try:
            obj = json.loads(result.get("response", "{}"))
        except Exception:
            obj = {}
        title = (obj.get("title") or f"{src['title']}（下一集）").strip()
        ncid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source,idea_reason,parent_content_id) "
            "VALUES (?,?,?,?, 'idea','assistant',?,?)",
            (ncid, pid, title, (obj.get("puzzle") or "").strip(), (obj.get("reason") or "").strip(), cid))
        await log_action(db, pid, "write_next", "media_content", ncid,
                         after={"title": title, "parent": cid})
    finally:
        await db.close()
    return f"已开出续集「{title}」（idea 阶段，承接《{src['title']}》；可在改动记录里撤销）。"


async def _tool_draft_script(args, pid):
    from app.services.media_ai import write_script
    cid = (args or {}).get("content_id", "")
    hint = (args or {}).get("hint", "")
    db = await get_db()
    try:
        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
        if not row:
            await db.close()
            return "（找不到这条内容）"
        before_draft = row["ai_draft"] or ""
        res = await write_script(db, cid, mode="full", hint=hint)
        if not res.get("ok"):
            await db.close()
            return f"（写稿失败：{res.get('error', '')}）"
        await log_action(db, pid, "draft_script", "media_content", cid,
                         before={"ai_draft": before_draft}, after={"ai_draft": res.get("script", "")})
    finally:
        await db.close()
    return "已写好脚本草稿（在内容的口播脚本区，未定稿；可撤销还原）。"


async def _tool_match_playbook(args, pid):
    from app.services.media_ai import match_playbook
    cid = (args or {}).get("content_id", "")
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_content WHERE id=? AND persona_id=?", (cid, pid))
        row = await cur.fetchone()
        if not row:
            await db.close()
            return "（找不到这条内容）"
        res = await match_playbook(db, dict(row))
    finally:
        await db.close()
    pb = res.get("playbook")
    return (f"最贴的打法：《{pb['name']}》——{pb.get('reason', '')}" if pb
            else "（没有匹配到合适的打法）")


_WRITE = {
    "create_topic": _tool_create_topic, "write_next": _tool_write_next,
    "draft_script": _tool_draft_script, "match_playbook": _tool_match_playbook,
}


def _schema(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


MEDIA_TOOL_SCHEMAS = [
    _schema("list_contents", "列出当前人设的内容（标题+阶段，不含正文）。可选 stage 筛选。",
            {"stage": {"type": "string", "description": "可选：idea/scripted/.../published"}}),
    _schema("read_content", "读某条内容的标题/谜题/正文（点名某条时用）。", {"id": {"type": "string"}}, ["id"]),
    _schema("list_topics", "列出当前人设选题池里的待选选题。"),
    _schema("list_playbooks", "列出打法库（全公司共享）。"),
    _schema("list_materials", "列出原料库（本人设 + 公司共享料）。"),
    _schema("list_audiences", "列出当前人设的受众。"),
    _schema("list_anchors", "列出当前人设的锚点。"),
]

MEDIA_TOOL_SCHEMAS += [
    _schema("create_topic", "往选题池加一条选题（草稿·可撤）。",
            {"title": {"type": "string"}, "puzzle": {"type": "string"}, "reason": {"type": "string"}}, ["title"]),
    _schema("write_next", "针对某条内容写下一条续集（建 idea 内容+记血缘·可撤）。",
            {"from_content_id": {"type": "string"}}, ["from_content_id"]),
    _schema("draft_script", "给某条内容写口播脚本草稿（不定稿·可撤）。",
            {"content_id": {"type": "string"}, "hint": {"type": "string"}}, ["content_id"]),
    _schema("match_playbook", "给某条内容匹配最贴的一条打法（读）。",
            {"content_id": {"type": "string"}}, ["content_id"]),
]


async def dispatch_media_tool(name, args, persona_id):
    fn = _READ.get(name) or _WRITE.get(name)
    if fn:
        return await fn(args, persona_id)
    return f"（未知工具 {name}）"
