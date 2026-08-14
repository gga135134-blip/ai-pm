"""主题库路由。

主题 = 人投喂的粗方向，AI 据此展开成多条选题。
与选题（media_topic）的分工：主题长期存在、不消耗、可反复展开；
选题是一次性的，采用或弃掉就出池。

单独成文件，便于审阅，也避免与 media.py 的功能开发抢改动。
"""
import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.database import get_db
from app.services import media_ai

router = APIRouter()


async def _current_persona_id(request, db):
    pid = request.cookies.get("media_persona")
    if pid:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        if await cur.fetchone():
            return pid
    cur = await db.execute(
        "SELECT id FROM media_persona WHERE status='active' ORDER BY created_at LIMIT 1")
    row = await cur.fetchone()
    return row["id"] if row else None


@router.post("/media/themes")
async def create_theme(request: Request, title: str = Form(...), note: str = Form("")):
    """投喂一个主题。一句话即可。"""
    title = (title or "").strip()
    if not title:
        return RedirectResponse("/media/topics", status_code=302)
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        await db.execute(
            "INSERT INTO media_theme (id,persona_id,title,note) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), pid, title, (note or "").strip()))
        await db.commit()
        return RedirectResponse("/media/topics", status_code=302)
    finally:
        await db.close()


@router.post("/media/theme/{tid}/expand")
async def expand_theme(tid: str):
    """让 AI 围绕这个主题展开成 N 条选题。主题不消耗，可反复展开。"""
    db = await get_db()
    try:
        r = await media_ai.expand_theme(db, tid)
        return JSONResponse(r)
    finally:
        await db.close()


@router.post("/media/theme/{tid}/archive")
async def archive_theme(tid: str):
    """归档主题。不删除——已展开的选题要留着溯源。"""
    db = await get_db()
    try:
        await db.execute("UPDATE media_theme SET status='archived' WHERE id=?", (tid,))
        await db.commit()
        return RedirectResponse("/media/topics", status_code=302)
    finally:
        await db.close()
