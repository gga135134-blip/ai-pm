# app/services/media_agent_tools.py
"""自媒体助手工具集：查类 + 改草稿类。ctx=persona_id。每个工具返回给 agent 的文本。"""
from app.database import get_db


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


async def dispatch_media_tool(name, args, persona_id):
    fn = _READ.get(name)
    if fn:
        return await fn(args, persona_id)
    return f"（未知工具 {name}）"
