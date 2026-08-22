"""自媒体助手：动作留痕 + 撤销 + 系统提示。核心/入库动作留作 Phase 2。"""
import json
import uuid

MEDIA_ASSISTANT_SYSTEM = """你是这个自媒体人设的 AI 助手。你能查内容/选题/打法库/原料库，能建选题、写下一条续集、写脚本草稿、匹配打法。
纪律：只在需要时调工具；查东西回清单/摘要，别一次性铺开全部。写稿/续集只用最贴的一条打法当骨架，别堆。
你只做"草稿/可逆"的事——建选题、续集、脚本草稿都是草稿，人还会定稿；
你也能做核心动作：标爆款、删除内容、把口头禅/素材/打法采纳进库。但这些你只是"拟"——系统会生成待确认卡，用户点确认后才真执行，你不用等结果，告诉用户"已拟好，去确认卡点确认"即可。删除内容确认后不可撤，涉及删除务必先说清楚是哪条。
做完把你做了什么、建了哪条、简明告诉用户。"""


async def log_action(db, persona_id, action_type, target_table, target_id,
                     before=None, after=None, conversation_ref="", status="applied") -> str:
    aid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_assistant_action "
        "(id,persona_id,conversation_ref,action_type,target_table,target_id,before_json,after_json,status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (aid, persona_id, conversation_ref, action_type, target_table, target_id,
         json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False), status))
    await db.commit()
    return aid


async def list_actions(db, persona_id) -> list:
    cur = await db.execute(
        "SELECT * FROM media_assistant_action WHERE persona_id=? "
        "ORDER BY CASE status WHEN 'applied' THEN 0 ELSE 1 END, created_at DESC",
        (persona_id,))
    return [dict(r) for r in await cur.fetchall()]


async def apply_action(db, action_id) -> bool:
    cur = await db.execute("SELECT * FROM media_assistant_action WHERE id=?", (action_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "pending":
        return False
    a = dict(row)
    at, pid, tid = a["action_type"], a["persona_id"], a["target_id"]
    after = json.loads(a["after_json"] or "{}")
    before, reversible = {}, 1
    if at == "mark_winner":
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id=?", (tid,))
        r = await cur.fetchone()
        before = {"is_winner": (r["is_winner"] if r else 0)}
        await db.execute("UPDATE media_content SET is_winner=1 WHERE id=?", (tid,))
    elif at == "delete_content":
        await db.execute("DELETE FROM media_metrics WHERE publish_id IN "
                         "(SELECT id FROM media_publish WHERE content_id=?)", (tid,))
        for tbl in ("media_publish", "media_review", "media_case",
                    "media_evidence", "media_angle", "media_draft_review"):
            await db.execute(f"DELETE FROM {tbl} WHERE content_id=?", (tid,))
        await db.execute("DELETE FROM media_content WHERE id=?", (tid,))
        reversible = 0
    elif at == "adopt_signature":
        content = (after.get("content") or "").strip()
        nid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?, 'signature',?,?, 'assistant',?,3,'')",
            (nid, pid, content, (after.get("brief") or content)[:30], (after.get("evidence") or "").strip()))
        after["created_id"], after["created_table"] = nid, "media_persona_trait"
    elif at == "adopt_material":
        detail = (after.get("content") or "").strip()
        nid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_material (id,persona_id,type,title,detail,brief,source) "
            "VALUES (?,?,?,?,?,?, 'assistant')",
            (nid, pid, (after.get("type") or "story"), detail[:40], detail,
             (after.get("brief") or detail)[:30]))
        after["created_id"], after["created_table"] = nid, "media_material"
    elif at == "adopt_playbook":
        name = (after.get("name") or "").strip()
        sim = (after.get("similar_to") or "").strip()
        merged = False
        if sim:
            cur = await db.execute("SELECT id,evidence FROM media_playbook WHERE persona_id=? AND name=?", (pid, sim))
            ex = await cur.fetchone()
            if ex:
                new_ev = ((ex["evidence"] or "") + "\n---\n" + (after.get("evidence") or "")).strip()
                await db.execute("UPDATE media_playbook SET evidence=? WHERE id=?", (new_ev, ex["id"]))
                merged, reversible = True, 0
        if not merged:
            nid = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO media_playbook "
                "(id,persona_id,name,structure,when_to_use,evidence,source,status) "
                "VALUES (?,?,?,?,?,?, 'assistant','validating')",
                (nid, pid, name, (after.get("structure") or "").strip(),
                 (after.get("when_to_use") or "").strip(), (after.get("evidence") or "").strip()))
            after["created_id"], after["created_table"] = nid, "media_playbook"
    elif at == "run_l2":
        from app.services.media_review_cycle import run_l2_cycle
        await run_l2_cycle(db, pid, force=True)
        reversible = 0
    elif at == "run_l3":
        from app.services.media_phase_review import run_l3_review
        await run_l3_review(db, pid, force=True)
        reversible = 0
    else:
        return False
    await db.execute(
        "UPDATE media_assistant_action SET status='applied', reversible=?, before_json=?, after_json=? WHERE id=?",
        (reversible, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), action_id))
    await db.commit()
    return True


async def revert_action(db, action_id) -> bool:
    cur = await db.execute("SELECT * FROM media_assistant_action WHERE id=?", (action_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "applied":
        return False
    if not row["reversible"]:
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
    elif a["action_type"] == "organize_format":
        # 整理格式 → 还原 script
        await db.execute("UPDATE media_content SET script=? WHERE id=?",
                         (before.get("script", ""), a["target_id"]))
    elif a["action_type"] == "mark_winner":
        await db.execute("UPDATE media_content SET is_winner=? WHERE id=?",
                         (before.get("is_winner", 0), a["target_id"]))
    elif a["action_type"] in ("adopt_signature", "adopt_material", "adopt_playbook"):
        after = json.loads(a["after_json"] or "{}")
        tbl, nid = after.get("created_table"), after.get("created_id")
        if not tbl or not nid:
            return False
        await db.execute(f"DELETE FROM {tbl} WHERE id=?", (nid,))
    else:
        return False
    await db.execute("UPDATE media_assistant_action SET status='reverted' WHERE id=?", (action_id,))
    await db.commit()
    return True


async def cancel_action(db, action_id) -> bool:
    cur = await db.execute("SELECT status FROM media_assistant_action WHERE id=?", (action_id,))
    r = await cur.fetchone()
    if not r or r["status"] != "pending":
        return False
    await db.execute("UPDATE media_assistant_action SET status='cancelled' WHERE id=?", (action_id,))
    await db.commit()
    return True


async def list_pending(db, persona_id) -> list:
    cur = await db.execute(
        "SELECT * FROM media_assistant_action WHERE persona_id=? AND status='pending' "
        "ORDER BY created_at", (persona_id,))
    return [dict(r) for r in await cur.fetchall()]
