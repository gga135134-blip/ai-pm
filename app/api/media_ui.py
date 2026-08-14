"""自媒体 UI 专用只读取数：四步状态 + 体系库存量。

单独成文件是为了不与 media.py 抢改动（功能开发在另一条线）。
本文件只读、不写、不改业务逻辑。
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.database import get_db

router = APIRouter()


async def _current_persona_id(request, db):
    """与 media.py 同款语义：cookie 优先，回落第一个 active 人设。"""
    pid = request.cookies.get("media_persona")
    if pid:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        if await cur.fetchone():
            return pid
    cur = await db.execute(
        "SELECT id FROM media_persona WHERE status='active' ORDER BY created_at LIMIT 1")
    row = await cur.fetchone()
    return row["id"] if row else None


async def _scalar(db, sql, args):
    cur = await db.execute(sql, args)
    row = await cur.fetchone()
    return (row[0] if row and row[0] is not None else 0)


@router.get("/media/ui/steps")
async def media_ui_steps(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False})

        # 人设名/期数：有些页面的路由没查人设，模板传不了，由前端兜底填上
        cur = await db.execute(
            "SELECT name, current_phase FROM media_persona WHERE id=?", (pid,))
        prow = await cur.fetchone()
        persona = {"name": prow["name"], "phase": prow["current_phase"]} if prow else None

        adopted = await _scalar(
            db, "SELECT COUNT(*) FROM media_topic WHERE persona_id=? AND status='adopted'", (pid,))
        making = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? "
                "AND stage IN ('scripted','recording','editing')", (pid,))
        ready = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? AND stage='ready'", (pid,))
        published = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? "
                "AND stage IN ('published','reviewed')", (pid,))
        reviewed = await _scalar(
            db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? AND stage='reviewed'", (pid,))

        # 各体系库存量。lib 表都有 status 列（archived 为归档）；
        # 老文案没有独立表，是 idea_source='legacy_text' 的内容（见 services/media_legacy.py）。
        libs = {
            "persona": await _scalar(
                db, "SELECT COUNT(*) FROM media_persona_trait WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "audience": await _scalar(
                db, "SELECT COUNT(*) FROM media_audience WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "anchor": await _scalar(
                db, "SELECT COUNT(*) FROM media_anchor WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            "material": await _scalar(
                db, "SELECT COUNT(*) FROM media_material WHERE persona_id=? "
                    "AND COALESCE(status,'active')<>'archived'", (pid,)),
            # 打法库是全公司共享一池（见 commit 0b6efd7），不按 persona 过滤
            "playbook": await _scalar(
                db, "SELECT COUNT(*) FROM media_playbook WHERE COALESCE(status,'active')<>'archived'", ()),
            "legacy": await _scalar(
                db, "SELECT COUNT(*) FROM media_content WHERE persona_id=? "
                    "AND idea_source='legacy_text'", (pid,)),
        }

        steps = {
            "topic": {"done": adopted > 0, "count": adopted,
                      "label": f"采用 {adopted} 条" if adopted else "还没采用选题"},
            "content": {"done": making == 0 and published > 0, "count": making,
                        "label": f"{making} 条在做" if making else "没有在做的内容"},
            # 发布步只管「待发」：没有积压且发过东西才算做完
            "publish": {"done": ready == 0 and published > 0, "count": ready,
                        "label": f"待发 {ready}" if ready else (f"已发 {published}" if published else "还没有待发")},
            "review": {"done": reviewed > 0, "count": reviewed,
                       "label": f"已复盘 {reviewed}" if reviewed else "发布后解锁"},
        }
        if published == 0:
            steps["review"]["locked"] = True
            steps["review"]["reason"] = "要先发布内容，才能复盘"

        return JSONResponse({"ok": True, "persona_id": pid, "persona": persona,
                             "steps": steps, "libs": libs,
                             "libs_empty": [k for k, v in libs.items() if not v]})
    finally:
        await db.close()


@router.get("/media/review")
async def media_review_home(request: Request):
    """复盘落地页：原本只有 /media/review-cycle/{id} 与 /media/phase-review/{id}
    两个详情路由，列表入口藏在人设档案页里。这里补一个只读落地页。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona, cycles, phases, published = None, [], [], []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_review_cycle WHERE persona_id=? ORDER BY created_at DESC", (pid,))
            cycles = [dict(r) for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT * FROM media_phase_review WHERE persona_id=? ORDER BY created_at DESC", (pid,))
            phases = [dict(r) for r in await cur.fetchall()]
            # 已发/已复盘内容：从「发布」步移过来，它们是复盘的输入
            cur = await db.execute(
                "SELECT id,title,stage,is_winner,published_at,created_at FROM media_content "
                "WHERE persona_id=? AND stage IN ('published','reviewed') "
                "ORDER BY COALESCE(published_at, created_at) DESC", (pid,))
            published = [dict(r) for r in await cur.fetchall()]
        ctx = {"request": request, "persona": persona, "cycles": cycles,
               "phases": phases, "published": published}
        return request.app.state.templates.TemplateResponse(request, "media_review_home.html", ctx)
    finally:
        await db.close()
