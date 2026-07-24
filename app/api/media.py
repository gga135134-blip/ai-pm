import json
import logging
import uuid
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from app.database import get_db
from app.services.media_flow import (
    PLATFORMS, STAGES, STAGE_LABELS, can_transition, next_stage,
)
from app.services.media_ai import recommend_topics
from app.services.media_ai import write_script

log = logging.getLogger(__name__)

router = APIRouter()

TRAIT_DIMENSIONS = {
    "positioning": "定位",
    "audience": "受众",
    "tone": "语气",
    "topics": "选题方向",
    "taboo": "内容禁区",
    "signature": "记忆点",
    "differentiator": "差异化",
}


def _tpl(request, name, ctx):
    ctx["request"] = request
    return request.app.state.templates.TemplateResponse(request, name, ctx)


async def _first_persona_id(db) -> str | None:
    """一期只有一个人设；架构上支持多人设，这里取第一个 active 的。"""
    cur = await db.execute(
        "SELECT id FROM media_persona WHERE status='active' ORDER BY created_at LIMIT 1")
    row = await cur.fetchone()
    return row["id"] if row else None


# ─────────────── 人设 ───────────────

@router.get("/media/persona", response_class=HTMLResponse)
async def persona_home(request: Request):
    """没有人设时引导创建，有则跳到第一个人设档案。"""
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
    finally:
        await db.close()
    if pid:
        return RedirectResponse(f"/media/persona/{pid}", status_code=302)
    return _tpl(request, "media_persona.html",
                {"persona": None, "traits_by_dim": {}, "accounts": [],
                 "dimensions": TRAIT_DIMENSIONS, "platforms": PLATFORMS,
                 "archived": []})


@router.post("/media/persona")
async def persona_create(name: str = Form(...), one_liner: str = Form(""),
                         current_phase: str = Form("冷启动")):
    pid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_persona (id,name,one_liner,current_phase) VALUES (?,?,?,?)",
            (pid, name.strip(), one_liner.strip(), current_phase.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.get("/media/persona/{pid}", response_class=HTMLResponse)
async def persona_detail(request: Request, pid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        persona = dict(row) if row else None

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active' "
            "ORDER BY confidence DESC, created_at DESC", (pid,))
        traits = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='archived' "
            "ORDER BY created_at DESC", (pid,))
        archived = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_account WHERE persona_id=? ORDER BY created_at", (pid,))
        accounts = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

    traits_by_dim = {}
    for dim in TRAIT_DIMENSIONS:
        hit = [t for t in traits if t["dimension"] == dim]
        if hit:
            traits_by_dim[dim] = hit

    return _tpl(request, "media_persona.html",
                {"persona": persona, "traits_by_dim": traits_by_dim,
                 "accounts": accounts, "dimensions": TRAIT_DIMENSIONS,
                 "platforms": PLATFORMS, "archived": archived})


@router.post("/media/persona/{pid}/trait")
async def trait_create(pid: str, dimension: str = Form(...),
                       content: str = Form(...), brief: str = Form(""),
                       confidence: int = Form(3), evidence: str = Form("")):
    db = await get_db()
    try:
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        phase = row["current_phase"] if row else ""
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?,?,?,?,'manual',?,?,?)",
            (str(uuid.uuid4()), pid, dimension, content.strip(),
             brief.strip()[:30], evidence.strip(), confidence, phase))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.post("/media/trait/{tid}/archive")
async def trait_archive(tid: str):
    """归档而非删除 —— 人设演化史要完整留痕。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id FROM media_persona_trait WHERE id=?", (tid,))
        row = await cur.fetchone()
        pid = row["persona_id"] if row else ""
        await db.execute(
            "UPDATE media_persona_trait SET status='archived' WHERE id=?", (tid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.post("/media/persona/{pid}/account")
async def account_create(pid: str, platform: str = Form(...),
                         account_name: str = Form(""), account_url: str = Form(""),
                         platform_note: str = Form("")):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_account "
            "(id,persona_id,platform,account_name,account_url,platform_note) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), pid, platform, account_name.strip(),
             account_url.strip(), platform_note.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


# ─────────────── 话题库 ───────────────

TOPIC_SOURCES = {
    "manual": "人工", "ai_rec": "AI推荐", "hot": "热点",
    "comment": "评论区", "competitor": "对标", "review": "复盘衍生",
}


@router.get("/media/topics", response_class=HTMLResponse)
async def topics_home(request: Request, source: str = ""):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        sql = ("SELECT * FROM media_topic WHERE persona_id=? AND status='pool'")
        args = [pid]
        if source:
            sql += " AND source=?"
            args.append(source)
        sql += " ORDER BY decision_score DESC, fit_score DESC, heat DESC, created_at DESC"
        cur = await db.execute(sql, tuple(args))
        topics = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_topic WHERE persona_id=? AND status='rejected' "
            "ORDER BY created_at DESC LIMIT 20", (pid,))
        rejected = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_topics.html",
                {"topics": topics, "rejected": rejected, "persona_id": pid,
                 "sources": TOPIC_SOURCES, "cur_source": source})


@router.post("/media/topics")
async def topic_create(persona_id: str = Form(...), title: str = Form(...),
                       puzzle: str = Form(""), reason: str = Form(""),
                       angle: str = Form(""), heat: int = Form(3),
                       fit_score: int = Form(3)):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score) "
            "VALUES (?,?,?,?,'manual',?,?,?,?)",
            (str(uuid.uuid4()), persona_id, title.strip(), puzzle.strip(),
             reason.strip(), angle.strip(), heat, fit_score))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)


async def _adopt_topic(db, topic_id: str) -> str | None:
    """话题 → 内容。把谜题和理由一起带过去，开工时不用重新想。"""
    cur = await db.execute("SELECT * FROM media_topic WHERE id=?", (topic_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "pool":
        return None
    t = dict(row)
    cid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_content "
        "(id,persona_id,title,puzzle,stage,idea_source,idea_reason) "
        "VALUES (?,?,?,?,'idea',?,?)",
        (cid, t["persona_id"], t["title"], t["puzzle"], t["source"], t["reason"]))
    await db.execute(
        "UPDATE media_topic SET status='adopted', adopted_content_id=? WHERE id=?",
        (cid, topic_id))
    await db.commit()
    return cid


@router.post("/media/topic/{tid}/adopt")
async def topic_adopt(tid: str):
    db = await get_db()
    try:
        cid = await _adopt_topic(db, tid)
    finally:
        await db.close()
    if not cid:
        return RedirectResponse("/media/topics", status_code=302)
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/topic/{tid}/reject")
async def topic_reject(tid: str, rejected_reason: str = Form("")):
    """弃单必须留原因 —— 下次 AI 推荐时带上，防止重复推同类垃圾。"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_topic SET status='rejected', rejected_reason=? WHERE id=?",
            (rejected_reason.strip(), tid))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)


# ─────────────── 内容看板 ───────────────

@router.get("/media", response_class=HTMLResponse)
async def board(request: Request):
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        persona = dict(await cur.fetchone())

        cur = await db.execute(
            "SELECT * FROM media_content WHERE persona_id=? "
            "ORDER BY updated_at DESC, created_at DESC", (pid,))
        contents = [dict(r) for r in await cur.fetchall()]

        # 每条内容的三平台发布状态 + 最新播放量，看板卡片上直接显示
        cur = await db.execute(
            "SELECT p.content_id, p.id AS publish_id, a.platform, p.status, "
            "  (SELECT views FROM media_metrics m WHERE m.publish_id=p.id "
            "   ORDER BY snapshot_at DESC LIMIT 1) AS views "
            "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
            "JOIN media_content c ON c.id=p.content_id WHERE c.persona_id=?", (pid,))
        pubs = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_topic WHERE persona_id=? AND status='pool'",
            (pid,))
        pool_count = (await cur.fetchone())["c"]
    finally:
        await db.close()

    by_content = {}
    for p in pubs:
        by_content.setdefault(p["content_id"], []).append(p)
    for c in contents:
        c["publishes"] = by_content.get(c["id"], [])

    columns = [{"stage": s, "label": STAGE_LABELS[s],
                "cards": [c for c in contents if c["stage"] == s]} for s in STAGES]

    return _tpl(request, "media_board.html",
                {"persona": persona, "columns": columns, "platforms": PLATFORMS,
                 "pool_count": pool_count, "total": len(contents)})


@router.post("/media/content")
async def content_create(persona_id: str = Form(...), title: str = Form(...),
                         puzzle: str = Form("")):
    cid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source) "
            "VALUES (?,?,?,?,'idea','manual')",
            (cid, persona_id, title.strip(), puzzle.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/content/{cid}/stage")
async def content_stage(cid: str, to: str = Form(...), back: str = Form("")):
    """推进或退回阶段。非法流转静默忽略，不报错打断用户。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if row and can_transition(row["stage"], to):
            await db.execute(
                "UPDATE media_content SET stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (to, cid))
            await db.commit()
    finally:
        await db.close()
    target = "/media" if back == "board" else f"/media/content/{cid}"
    return RedirectResponse(target, status_code=302)


@router.post("/media/topics/ai-recommend")
async def topics_ai_recommend():
    db = await get_db()
    try:
        pid = await _first_persona_id(db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        try:
            result = await recommend_topics(db, pid)
        except Exception as e:
            log.exception("AI 推选题失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


# ─────────────── 内容详情 ───────────────

@router.get("/media/content/{cid}", response_class=HTMLResponse)
async def content_detail(request: Request, cid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/media", status_code=302)
        content = dict(row)

        cur = await db.execute(
            "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
        persona = dict(await cur.fetchone())

        cur = await db.execute(
            "SELECT * FROM media_account WHERE persona_id=? AND status='active' "
            "ORDER BY created_at", (content["persona_id"],))
        accounts = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_publish WHERE content_id=?", (cid,))
        pubs = {r["account_id"]: dict(r) for r in await cur.fetchall()}

        # 每个发布记录的最新数据
        metrics = {}
        for aid, p in pubs.items():
            cur = await db.execute(
                "SELECT * FROM media_metrics WHERE publish_id=? "
                "ORDER BY snapshot_at DESC LIMIT 1", (p["id"],))
            m = await cur.fetchone()
            if m:
                metrics[p["id"]] = dict(m)

        cur = await db.execute(
            "SELECT * FROM media_review WHERE content_id=? ORDER BY created_at", (cid,))
        reviews = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

    for r in reviews:
        try:
            r["proposed_traits"] = json.loads(r["proposed_traits"] or "[]")
        except (json.JSONDecodeError, TypeError):
            r["proposed_traits"] = []

    return _tpl(request, "media_content.html",
                {"content": content, "persona": persona, "accounts": accounts,
                 "pubs": pubs, "metrics": metrics, "reviews": reviews,
                 "platforms": PLATFORMS, "stages": STAGES,
                 "stage_labels": STAGE_LABELS,
                 "next_stage": next_stage(content["stage"])})


@router.post("/media/content/{cid}/script")
async def content_save_script(cid: str, script: str = Form(""),
                              edit_note: str = Form(""), cover_idea: str = Form("")):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_content SET script=?, edit_note=?, cover_idea=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (script, edit_note, cover_idea, cid))
        # 脚本从空变有 → 自动推进到 scripted，省一次手动点击
        cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if script.strip() and row and row["stage"] == "idea":
            await db.execute(
                "UPDATE media_content SET stage='scripted' WHERE id=?", (cid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/content/{cid}/ai-script")
async def content_ai_script(cid: str, mode: str = Form("full")):
    db = await get_db()
    try:
        try:
            result = await write_script(db, cid, mode=mode)
        except Exception as e:
            log.exception("AI 写脚本失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)
