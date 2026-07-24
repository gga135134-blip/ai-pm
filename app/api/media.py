import json
import uuid
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.media_flow import PLATFORMS, STAGES, STAGE_LABELS

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
