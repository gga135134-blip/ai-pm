import datetime, json, random
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
        ids = [i["item_id"] for i in items if i["item_type"] == "point"]
        titles = {}
        if ids:
            q = ",".join("?" for _ in ids)
            cur = await db.execute(f"SELECT id,title FROM study_points WHERE id IN ({q})", tuple(ids))
            titles = {r["id"]: r["title"] for r in await cur.fetchall()}
        # archetype titles
        arch_ids = [i["item_id"] for i in items if i["item_type"] == "archetype"]
        if arch_ids:
            q = ",".join("?" for _ in arch_ids)
            cur = await db.execute(f"SELECT id,stem FROM study_archetypes WHERE id IN ({q})", tuple(arch_ids))
            for r in await cur.fetchall():
                titles[r["id"]] = r["stem"][:40] if r["stem"] else r["id"]
        # 倒计时 + 掌握度
        cur = await db.execute("SELECT exam_date FROM study_settings WHERE id=1")
        exam = datetime.date.fromisoformat((await cur.fetchone())["exam_date"])
        days_left = (exam - datetime.date.today()).days
        cur = await db.execute("SELECT subject,COUNT(*) c FROM study_review WHERE stage>=3 GROUP BY subject")
        mastered = {r["subject"]: r["c"] for r in await cur.fetchall()}
        reviews = [i for i in items if i["kind"] == "review"]
        news = [i for i in items if i["kind"] == "new"]
        # Fix 4: 今日进度条 — count done items from study_records
        today_iso = datetime.date.today().isoformat()
        cur = await db.execute(
            "SELECT DISTINCT item_type,item_id FROM study_records WHERE plan_date=?",
            (today_iso,))
        done_rows = await cur.fetchall()
        done_set = {(r["item_type"], r["item_id"]) for r in done_rows}
        done_keys = {f"{r['item_type']}:{r['item_id']}" for r in done_rows}
        progress_done = sum(1 for it in items if (it["item_type"], it["item_id"]) in done_set)
        progress_total = len(items)
        # 今日已学科目，供练习入口判断
        studied_subjects = list({it["subject"] for it in items
                                  if (it["item_type"], it["item_id"]) in done_set})
    finally:
        await db.close()
    return _tpl(request, "study_today.html",
                {"reviews": reviews, "news": news, "titles": titles,
                 "days_left": days_left, "mastered": mastered,
                 "progress_done": progress_done, "progress_total": progress_total,
                 "done_keys": done_keys, "studied_subjects": studied_subjects})


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


@router.get("/study/practice", response_class=HTMLResponse)
async def study_practice(request: Request):
    db = await get_db()
    try:
        today_iso = datetime.date.today().isoformat()
        # 今日已学考点 ID
        cur = await db.execute(
            "SELECT DISTINCT item_id FROM study_records WHERE plan_date=? AND item_type='point'",
            (today_iso,))
        studied_ids = [r["item_id"] for r in await cur.fetchall()]
        # 今日已学科目
        studied_subjects = []
        if studied_ids:
            q = ",".join("?" for _ in studied_ids)
            cur = await db.execute(
                f"SELECT DISTINCT subject FROM study_points WHERE id IN ({q})", tuple(studied_ids))
            studied_subjects = [r["subject"] for r in await cur.fetchall()]
        # 优先取与今日考点关联的题目
        practice_qs, seen = [], set()
        if studied_ids:
            cur = await db.execute(
                "SELECT * FROM study_questions WHERE qtype IN ('单选','多选')")
            for r in await cur.fetchall():
                d = dict(r)
                try:
                    maps = json.loads(d["maps_to"])
                except Exception:
                    maps = []
                if any(m in studied_ids for m in maps) and d["id"] not in seen:
                    d["options"] = json.loads(d["options"])
                    practice_qs.append(d)
                    seen.add(d["id"])
        # 不足 5 题则从今日科目的 2026 密卷补充
        if len(practice_qs) < 5 and studied_subjects:
            need = 10 - len(practice_qs)
            for subj in studied_subjects:
                cur = await db.execute(
                    "SELECT * FROM study_questions WHERE subject=? AND source='2026预测密卷'"
                    " AND qtype IN ('单选','多选') ORDER BY RANDOM() LIMIT ?",
                    (subj, need))
                for r in await cur.fetchall():
                    d = dict(r)
                    if d["id"] not in seen:
                        d["options"] = json.loads(d["options"])
                        practice_qs.append(d)
                        seen.add(d["id"])
                        need -= 1
                if need <= 0:
                    break
        random.shuffle(practice_qs)
        practice_qs = practice_qs[:10]
    finally:
        await db.close()
    return _tpl(request, "study_practice.html", {"questions": practice_qs})


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


@router.get("/study/settings", response_class=HTMLResponse)
async def study_settings_page(request: Request):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM study_settings WHERE id=1")
        s = dict(await cur.fetchone())
    finally:
        await db.close()
    from app.api.settings import load_settings
    cfg = load_settings()
    msg = request.query_params.get("msg", "")
    return _tpl(request, "study_settings.html",
                {"s": s, "has_key": bool(cfg.get("serverchan_key")), "msg": msg})


@router.post("/study/settings")
async def study_settings_save(request: Request,
                               reminder_hour: int = Form(8),
                               daily_new_target: int = Form(150)):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE study_settings SET reminder_hour=?, daily_new_target=? WHERE id=1",
            (reminder_hour, max(1, daily_new_target)))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/study/settings?msg=saved", status_code=303)


@router.post("/study/settings/test")
async def study_settings_test(request: Request):
    from app.services.notifier import notify_wechat
    result = await notify_wechat("✅ 推送测试", "ai-pm 学习模块推送测试成功！距考试还有 N 天，加油！")
    msg = "test_ok" if result["sent"] else f"test_fail"
    return RedirectResponse(f"/study/settings?msg={msg}", status_code=303)


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
