import datetime, json
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.study_engine import get_or_build_today_plan, record_action

router = APIRouter()


def _tpl(request, name, ctx):
    ctx["request"] = request
    return request.app.state.templates.TemplateResponse(request, name, ctx)


@router.get("/study", response_class=HTMLResponse)
async def study_home(request: Request):
    db = await get_db()
    try:
        items = await get_or_build_today_plan(db)
        ids = [i["item_id"] for i in items]
        titles = {}
        if ids:
            q = ",".join("?" for _ in ids)
            cur = await db.execute(f"SELECT id,title FROM study_points WHERE id IN ({q})", tuple(ids))
            titles = {r["id"]: r["title"] for r in await cur.fetchall()}
        # 倒计时 + 掌握度
        cur = await db.execute("SELECT exam_date FROM study_settings WHERE id=1")
        exam = datetime.date.fromisoformat((await cur.fetchone())["exam_date"])
        days_left = (exam - datetime.date.today()).days
        cur = await db.execute("SELECT subject,COUNT(*) c FROM study_review WHERE stage>=3 GROUP BY subject")
        mastered = {r["subject"]: r["c"] for r in await cur.fetchall()}
        reviews = [i for i in items if i["kind"] == "review"]
        news = [i for i in items if i["kind"] == "new"]
    finally:
        await db.close()
    return _tpl(request, "study_today.html",
                {"reviews": reviews, "news": news, "titles": titles,
                 "days_left": days_left, "mastered": mastered})


@router.get("/study/point/{pid}", response_class=HTMLResponse)
async def study_point(request: Request, pid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM study_points WHERE id=?", (pid,))
        row = await cur.fetchone()
        point = dict(row) if row else None
        if point:
            point["type"] = json.loads(point["type"])
            point["related"] = json.loads(point["related"])
    finally:
        await db.close()
    return _tpl(request, "study_point.html", {"point": point})


@router.get("/study/archetype/{aid}", response_class=HTMLResponse)
async def study_archetype(request: Request, aid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM study_archetypes WHERE id=?", (aid,))
        row = await cur.fetchone()
        arch = dict(row) if row else None
        if arch:
            arch["variants"] = json.loads(arch["variants"])
            arch["knowledge_points"] = json.loads(arch["knowledge_points"])
    finally:
        await db.close()
    return _tpl(request, "study_archetype.html", {"arch": arch})


@router.get("/study/exam/{subject}", response_class=HTMLResponse)
async def study_exam(request: Request, subject: str):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM study_questions WHERE subject=? AND source='2026预测密卷'", (subject,))
        qs = []
        for r in await cur.fetchall():
            d = dict(r); d["options"] = json.loads(d["options"]); qs.append(d)
    finally:
        await db.close()
    return _tpl(request, "study_exam.html", {"subject": subject, "questions": qs})


@router.get("/study/review", response_class=HTMLResponse)
async def study_review_page(request: Request):
    db = await get_db()
    try:
        today = datetime.date.today().isoformat()
        cur = await db.execute(
            "SELECT * FROM study_review WHERE due_date<=? ORDER BY due_date", (today,))
        due = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "study_review.html", {"due": due})


@router.post("/study/record")
async def study_record(request: Request, item_type: str = Form(...),
                       item_id: str = Form(...), action: str = Form(...),
                       subject: str = Form(""), next_url: str = Form("/study")):
    db = await get_db()
    try:
        await record_action(db, item_type, item_id, action, subject)
    finally:
        await db.close()
    return RedirectResponse(next_url, status_code=303)
