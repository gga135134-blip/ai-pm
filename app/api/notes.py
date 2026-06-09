import uuid
from datetime import datetime
from fastapi import APIRouter, Request, Form, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.importer import import_file, import_folder, import_upload
from app.services.note_ai import classify_content, summarize_notes, generate_weekly_report
from app.services.backup import create_backup, list_backups, cleanup_old_backups

router = APIRouter()


@router.get("/notes", response_class=HTMLResponse)
async def note_list(request: Request, tag: str = "", q: str = "", project_id: str = ""):
    db = await get_db()
    try:
        # 构建查询
        where_parts = []
        params = []
        if tag:
            where_parts.append("(',' || n.tags || ',' LIKE ?)")
            params.append(f"%,{tag},%")
        if q:
            where_parts.append("(n.title LIKE ? OR n.content LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if project_id:
            where_parts.append("n.project_id = ?")
            params.append(project_id)

        where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        cursor = await db.execute(f"""
            SELECT n.*, p.name as project_name
            FROM notes n
            LEFT JOIN projects p ON p.id = n.project_id
            {where_clause}
            ORDER BY n.is_pinned DESC, n.updated_at DESC
        """, params)
        notes = [dict(row) for row in await cursor.fetchall()]

        # 获取所有标签用于侧边栏
        cursor = await db.execute("SELECT tags FROM notes WHERE tags != ''")
        all_tags_raw = [row["tags"] for row in await cursor.fetchall()]
        tag_counts = {}
        for raw in all_tags_raw:
            for t in raw.split(","):
                t = t.strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1

        # 获取项目列表用于筛选
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    return request.app.state.templates.TemplateResponse(
        request, "notes.html",
        {
            "request": request,
            "notes": notes,
            "tag_counts": tag_counts,
            "projects": projects,
            "current_tag": tag,
            "current_q": q,
            "current_project": project_id,
        },
    )


@router.get("/notes/new", response_class=HTMLResponse)
async def note_new_form(request: Request, project_id: str = "", task_id: str = ""):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_form.html",
        {"request": request, "note": None, "projects": projects, "pre_project": project_id, "pre_task": task_id},
    )


@router.post("/notes/new")
async def note_create(
    title: str = Form(...),
    content: str = Form(""),
    author: str = Form(""),
    project_id: str = Form(""),
    task_id: str = Form(""),
    tags: str = Form(""),
):
    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    # 清理标签格式
    clean_tags = ",".join(t.strip() for t in tags.split(",") if t.strip())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, author, project_id, task_id, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (note_id, title, content, author, project_id or None, task_id or None, clean_tags, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


# ── 文件导入（必须在 {note_id} 之前注册）──────────────

@router.get("/notes/import", response_class=HTMLResponse)
async def import_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_import.html", {"request": request, "projects": projects, "result": None}
    )


@router.post("/notes/import/upload")
async def import_upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    project_id: str = Form(""),
    author: str = Form(""),
    tags: str = Form(""),
):
    results = []
    for f in files:
        content = await f.read()
        r = await import_upload(f.filename, content, project_id, author, tags)
        results.append(r)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_import.html", {"request": request, "projects": projects, "result": {"type": "upload", "items": results}}
    )


@router.post("/notes/import/path")
async def import_from_path(
    request: Request,
    path: str = Form(...),
    project_id: str = Form(""),
    author: str = Form(""),
    tags: str = Form(""),
):
    from pathlib import Path as P
    p = P(path)
    if p.is_file():
        r = await import_file(path, project_id, author, tags)
        items = [r]
    elif p.is_dir():
        r = await import_folder(path, project_id, author, tags)
        items = r.get("imported", [])
        if r.get("skipped"):
            items.append({"skipped": r["skipped"]})
    else:
        items = [{"error": f"路径不存在: {path}"}]
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_import.html", {"request": request, "projects": projects, "result": {"type": "path", "items": items}}
    )


# ── AI 智能功能（必须在 {note_id} 之前注册）──────────

@router.get("/notes/classify", response_class=HTMLResponse)
async def classify_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_classify.html", {"request": request, "projects": projects, "result": None}
    )


@router.post("/notes/classify")
async def classify_submit(
    request: Request,
    text: str = Form(...),
    project_id: str = Form(""),
    model: str = Form("auto"),
):
    created = await classify_content(text, project_id, model)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_classify.html", {"request": request, "projects": projects, "result": created}
    )


@router.post("/notes/summarize")
async def summarize_submit(tag: str = Form("")):
    result = await summarize_notes(tag=tag)
    now = datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'ai_summary', ?, ?)""",
            (note_id, f"AI 整理: {tag or '全部笔记'}", result["summary"], f"AI整理,{tag}" if tag else "AI整理", now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.get("/notes/weekly", response_class=HTMLResponse)
async def weekly_report_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "weekly_report.html", {"request": request, "report": None}
    )


@router.post("/notes/weekly")
async def weekly_report_generate(request: Request, model: str = Form("auto")):
    result = await generate_weekly_report(model)
    return request.app.state.templates.TemplateResponse(
        request, "weekly_report.html", {"request": request, "report": result}
    )


# ── 笔记详情（{note_id} 路由放最后）──────────────────

@router.get("/notes/{note_id}", response_class=HTMLResponse)
async def note_detail(request: Request, note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT n.*, p.name as project_name
            FROM notes n
            LEFT JOIN projects p ON p.id = n.project_id
            WHERE n.id = ?
        """, (note_id,))
        note = dict(await cursor.fetchone())

        # 找到同标签的相关笔记
        related = []
        if note["tags"]:
            tag_list = [t.strip() for t in note["tags"].split(",") if t.strip()]
            if tag_list:
                like_parts = " OR ".join(["(',' || tags || ',' LIKE ?)" for _ in tag_list])
                params = [f"%,{t},%" for t in tag_list]
                params.append(note_id)
                cursor = await db.execute(
                    f"SELECT id, title, tags, updated_at FROM notes WHERE ({like_parts}) AND id != ? ORDER BY updated_at DESC LIMIT 10",
                    params,
                )
                related = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    tag_list = [t.strip() for t in note["tags"].split(",") if t.strip()] if note["tags"] else []

    return request.app.state.templates.TemplateResponse(
        request, "note_detail.html",
        {"request": request, "note": note, "tag_list": tag_list, "related": related},
    )


@router.get("/notes/{note_id}/edit", response_class=HTMLResponse)
async def note_edit_form(request: Request, note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        note = dict(await cursor.fetchone())
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_form.html",
        {"request": request, "note": note, "projects": projects, "pre_project": "", "pre_task": ""},
    )


@router.post("/notes/{note_id}/edit")
async def note_update(
    note_id: str,
    title: str = Form(...),
    content: str = Form(""),
    author: str = Form(""),
    project_id: str = Form(""),
    task_id: str = Form(""),
    tags: str = Form(""),
):
    now = datetime.now().isoformat()
    clean_tags = ",".join(t.strip() for t in tags.split(",") if t.strip())
    db = await get_db()
    try:
        await db.execute(
            "UPDATE notes SET title=?, content=?, author=?, project_id=?, task_id=?, tags=?, updated_at=? WHERE id=?",
            (title, content, author, project_id or None, task_id or None, clean_tags, now, note_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.post("/notes/{note_id}/pin")
async def note_toggle_pin(note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT is_pinned FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()
        new_val = 0 if row["is_pinned"] else 1
        await db.execute("UPDATE notes SET is_pinned = ? WHERE id = ?", (new_val, note_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


@router.post("/notes/{note_id}/delete")
async def note_delete(note_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


# ── 决策日志 ──────────────────────────────────────────

@router.get("/decisions", response_class=HTMLResponse)
async def decisions_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT d.*, p.name as project_name
            FROM decisions d
            LEFT JOIN projects p ON p.id = d.project_id
            ORDER BY d.created_at DESC
        """)
        decisions = [dict(row) for row in await cursor.fetchall()]
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "decisions.html", {"request": request, "decisions": decisions, "projects": projects}
    )


@router.post("/decisions/new")
async def decision_create(
    title: str = Form(...),
    context: str = Form(""),
    decision: str = Form(""),
    reason: str = Form(""),
    made_by: str = Form(""),
    project_id: str = Form(""),
):
    dec_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO decisions (id, project_id, title, context, decision, reason, made_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dec_id, project_id or None, title, context, decision, reason, made_by, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/decisions", status_code=303)


# ── 备份 ──────────────────────────────────────────────

@router.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request):
    backups = await list_backups()
    return request.app.state.templates.TemplateResponse(
        request, "backups.html", {"request": request, "backups": backups}
    )


@router.post("/backups/create")
async def backup_create():
    await create_backup()
    await cleanup_old_backups(keep=10)
    return RedirectResponse("/backups", status_code=303)
