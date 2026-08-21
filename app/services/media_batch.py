"""媒体批量后台跑：per-content 核心（路由与后台跑器共用）+ 后台跑器。"""
import asyncio
import threading
from app.database import get_db
from app.services.media_ai import organize_content, mine_from_transcript, mine_structure
from app.services.media_mine_queue import enqueue_candidates
from app.services.media_assistant import log_action


async def run_organize_one(db, cid) -> dict:
    """整理一条：摘要另存 + 格式改写(留痕可撤)。传入 db，由调用方管连接。"""
    cur = await db.execute("SELECT persona_id,script FROM media_content WHERE id=?", (cid,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在"}
    pid, script = row["persona_id"], row["script"] or ""
    if not script.strip():
        return {"ok": False, "error": "无正文"}
    res = await organize_content(script)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "整理失败")}
    formatted = res.get("formatted") or script
    await log_action(db, pid, "organize_format", "media_content", cid,
                     before={"script": script}, after={"script": formatted})
    await db.execute("UPDATE media_content SET summary=?, script=? WHERE id=?",
                     (res.get("summary", ""), formatted, cid))
    await db.commit()
    return {"ok": True, "summary": res.get("summary", "")}


async def run_mine_one(db, cid, kind, force=0) -> dict:
    """挖一条：kind=signature 从任意内容挖口头禅；essence 仅爆款挖素材+打法。"""
    cur = await db.execute(
        "SELECT persona_id,script,is_winner,mined_signature_at,mined_essence_at "
        "FROM media_content WHERE id=?", (cid,))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "内容不存在"}
    pid, script = row["persona_id"], row["script"] or ""
    if kind == "signature":
        if row["mined_signature_at"] and not force:
            return {"ok": True, "added": 0, "skipped": "already"}
        res = await mine_from_transcript(db, pid, script)
        added = await enqueue_candidates(db, pid, cid, "signature", res.get("signatures") or [])
        await db.execute("UPDATE media_content SET mined_signature_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
        await db.commit()
        return {"ok": True, "added": added, "skipped": ""}
    elif kind == "essence":
        if not row["is_winner"]:
            return {"ok": True, "added": 0, "skipped": "not_winner"}
        if row["mined_essence_at"] and not force:
            return {"ok": True, "added": 0, "skipped": "already"}
        res = await mine_from_transcript(db, pid, script)
        added = await enqueue_candidates(db, pid, cid, "material", res.get("materials") or [])
        st = await mine_structure(db, pid, script)
        if st.get("ok") and st.get("playbook"):
            added += await enqueue_candidates(db, pid, cid, "playbook", [st["playbook"]])
        await db.execute("UPDATE media_content SET mined_essence_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
        await db.commit()
        return {"ok": True, "added": added, "skipped": ""}
    return {"ok": False, "error": "kind 非法"}


# ─────────────── 后台跑器（每人设一个活跃任务·内存进度） ───────────────
_jobs = {}
_lock = threading.Lock()
_OP_LABEL = {"organize": "整理", "mine_signature": "挖记忆点", "mine_essence": "挖精华"}


def get_status(persona_id):
    with _lock:
        j = _jobs.get(persona_id)
        return dict(j) if j else None


def start_batch(persona_id, op, content_ids) -> bool:
    if op not in ("organize", "mine_signature", "mine_essence"):
        return False
    ids = [str(c) for c in (content_ids or [])]
    if not ids:
        return False
    with _lock:
        j = _jobs.get(persona_id)
        if j and j.get("running"):
            return False
        _jobs[persona_id] = {"op": op, "op_label": _OP_LABEL.get(op, op),
                             "done": 0, "total": len(ids), "running": True, "ok_count": 0}
    asyncio.create_task(_run_batch(persona_id, op, ids))
    return True


async def _run_batch(persona_id, op, content_ids):
    try:
        for cid in content_ids:
            db = await get_db()
            try:
                if op == "organize":
                    r = await run_organize_one(db, cid)
                elif op == "mine_signature":
                    r = await run_mine_one(db, cid, "signature")
                elif op == "mine_essence":
                    r = await run_mine_one(db, cid, "essence")
                else:
                    r = {"ok": False}
            except Exception:
                r = {"ok": False}
            finally:
                await db.close()
            with _lock:
                j = _jobs.get(persona_id)
                if j:
                    j["done"] += 1
                    if r.get("ok"):
                        j["ok_count"] += 1
    finally:
        with _lock:
            j = _jobs.get(persona_id)
            if j:
                j["running"] = False
