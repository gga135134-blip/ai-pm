import uuid
from datetime import datetime
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.importer import import_upload
from app.services.note_ai import classify_content

router = APIRouter()


def _build_task_tree(tasks: list[dict]) -> list[dict]:
    by_id = {t["id"]: {**t, "children": []} for t in tasks}
    roots = []
    for t in tasks:
        node = by_id[t["id"]]
        parent_id = t.get("parent_task_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


@router.get("/projects", response_class=HTMLResponse)
async def project_list(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT p.*,
                COUNT(t.id) as task_count,
                SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) as done_count
            FROM projects p
            LEFT JOIN tasks t ON t.project_id = p.id
            GROUP BY p.id
            ORDER BY p.updated_at DESC
        """)
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "project_list.html", {"request": request, "projects": projects}
    )


@router.get("/projects/new", response_class=HTMLResponse)
async def project_new_form(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "project_form.html", {"request": request, "project": None}
    )


@router.post("/projects/new")
async def project_create(name: str = Form(...), description: str = Form(""), owner: str = Form(""), budget: float = Form(0), revenue: float = Form(0)):
    project_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO projects (id, name, description, owner, status, budget, revenue, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (project_id, name, description, owner, budget, revenue, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        project = dict(await cursor.fetchone())

        cursor = await db.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY priority ASC, created_at DESC",
            (project_id,),
        )
        tasks = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT SUM(cost) as total FROM agent_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ?)",
            (project_id,),
        )
        row = await cursor.fetchone()
        project_cost = round(row["total"] or 0, 4)

        # 项目关联的笔记（资料）
        cursor = await db.execute(
            "SELECT id, title, tags, source_type, updated_at FROM notes WHERE project_id = ? ORDER BY updated_at DESC",
            (project_id,),
        )
        project_notes = [dict(row) for row in await cursor.fetchall()]

        # 项目决策
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        project_decisions = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    statuses = ["pending", "running", "reviewing", "blocked", "done", "failed"]
    task_board = {s: [t for t in tasks if t["status"] == s] for s in statuses}
    task_tree = _build_task_tree(tasks)

    return request.app.state.templates.TemplateResponse(
        request, "project_detail.html",
        {
            "request": request, "project": project, "task_board": task_board,
            "tasks": tasks, "task_tree": task_tree, "project_cost": project_cost,
            "project_notes": project_notes, "project_decisions": project_decisions,
        },
    )


@router.get("/projects/{project_id}/edit", response_class=HTMLResponse)
async def project_edit_form(request: Request, project_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        project = dict(await cursor.fetchone())
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "project_form.html", {"request": request, "project": project}
    )


@router.post("/projects/{project_id}/edit")
async def project_update(project_id: str, name: str = Form(...), description: str = Form(""), owner: str = Form(""), status: str = Form("active"), budget: float = Form(0), revenue: float = Form(0)):
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "UPDATE projects SET name=?, description=?, owner=?, status=?, budget=?, revenue=?, updated_at=? WHERE id=?",
            (name, description, owner, status, budget, revenue, now, project_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/delete")
async def project_delete(project_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM agent_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ?)", (project_id,))
        await db.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/projects", status_code=303)


@router.post("/projects/{project_id}/clone")
async def project_clone(project_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        src = dict(await cursor.fetchone())

        new_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        await db.execute(
            "INSERT INTO projects (id, name, description, owner, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?)",
            (new_id, src["name"] + " (副本)", src["description"], src["owner"], now, now),
        )

        cursor = await db.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        )
        src_tasks = [dict(row) for row in await cursor.fetchall()]

        id_map = {}
        for t in src_tasks:
            new_task_id = str(uuid.uuid4())
            id_map[t["id"]] = new_task_id
            new_parent = id_map.get(t["parent_task_id"]) if t["parent_task_id"] else None
            await db.execute(
                """INSERT INTO tasks (id, project_id, parent_task_id, title, description, assignee, ai_model, priority, needs_human, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (new_task_id, new_id, new_parent, t["title"], t["description"], t["assignee"], t["ai_model"], t["priority"], t["needs_human"], now, now),
            )

        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{new_id}", status_code=303)


@router.post("/projects/{project_id}/upload")
async def project_upload_material(
    project_id: str,
    files: list[UploadFile] = File(...),
    author: str = Form(""),
    tags: str = Form(""),
):
    """上传文件到项目资料库"""
    for f in files:
        content = await f.read()
        await import_upload(f.filename, content, project_id, author, tags)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/classify")
async def project_classify_text(
    project_id: str,
    text: str = Form(...),
    model: str = Form("auto"),
):
    """粘贴文字 → AI 自动分类后归入项目"""
    await classify_content(text, project_id, model)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)
