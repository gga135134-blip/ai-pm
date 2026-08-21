"""自媒体助手：动作留痕 + 撤销 + 系统提示。核心/入库动作留作 Phase 2。"""
import json
import uuid

MEDIA_ASSISTANT_SYSTEM = """你是这个自媒体人设的 AI 助手。你能查内容/选题/打法库/原料库，能建选题、写下一条续集、写脚本草稿、匹配打法。
纪律：只在需要时调工具；查东西回清单/摘要，别一次性铺开全部。写稿/续集只用最贴的一条打法当骨架，别堆。
你只做"草稿/可逆"的事——建选题、续集、脚本草稿都是草稿，人还会定稿；采纳进库/删除这类核心动作你现在不能做（让用户去对应页面点）。
做完把你做了什么、建了哪条、简明告诉用户。"""


async def log_action(db, persona_id, action_type, target_table, target_id,
                     before=None, after=None, conversation_ref="") -> str:
    aid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_assistant_action "
        "(id,persona_id,conversation_ref,action_type,target_table,target_id,before_json,after_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (aid, persona_id, conversation_ref, action_type, target_table, target_id,
         json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False)))
    await db.commit()
    return aid


async def list_actions(db, persona_id) -> list:
    cur = await db.execute(
        "SELECT * FROM media_assistant_action WHERE persona_id=? "
        "ORDER BY CASE status WHEN 'applied' THEN 0 ELSE 1 END, created_at DESC",
        (persona_id,))
    return [dict(r) for r in await cur.fetchall()]


async def revert_action(db, action_id) -> bool:
    cur = await db.execute("SELECT * FROM media_assistant_action WHERE id=?", (action_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "applied":
        return False
    a = dict(row)
    before = json.loads(a["before_json"] or "{}")
    if a["action_type"] in ("create_topic", "write_next"):
        # 建类 → 删掉建的记录
        await db.execute(f"DELETE FROM {a['target_table']} WHERE id=?", (a["target_id"],))
    elif a["action_type"] == "draft_script":
        # 草稿类 → 还原 ai_draft
        await db.execute("UPDATE media_content SET ai_draft=? WHERE id=?",
                         (before.get("ai_draft", ""), a["target_id"]))
    else:
        return False
    await db.execute("UPDATE media_assistant_action SET status='reverted' WHERE id=?", (action_id,))
    await db.commit()
    return True
