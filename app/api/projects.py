import uuid
from datetime import datetime
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.importer import import_upload
from app.services.note_ai import classify_content

router = APIRouter()


async def ensure_project_codes():
    """给所有还没有编号的项目按创建顺序分配 P001、P002…"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT code FROM projects WHERE code != '' AND code IS NOT NULL")
        existing = [row["code"] for row in await cursor.fetchall()]
        max_num = 0
        for c in existing:
            if c and c[1:].isdigit():
                max_num = max(max_num, int(c[1:]))

        cursor = await db.execute("SELECT id FROM projects WHERE code = '' OR code IS NULL ORDER BY created_at ASC")
        missing = [row["id"] for row in await cursor.fetchall()]
        for pid in missing:
            max_num += 1
            await db.execute("UPDATE projects SET code = ? WHERE id = ?", (f"P{max_num:03d}", pid))
        if missing:
            await db.commit()
    finally:
        await db.close()


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
    await ensure_project_codes()
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
async def project_create(
    name: str = Form(...), description: str = Form(""), owner: str = Form(""),
    budget: float = Form(0), revenue: float = Form(0),
    automation_level: str = Form("manual"), ai_budget: float = Form(0),
):
    project_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO projects (id, name, description, owner, status, budget, revenue, automation_level, ai_budget, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
            (project_id, name, description, owner, budget, revenue, automation_level, ai_budget, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    await ensure_project_codes()
    # 建好标准知识库文件夹：配置/资料/执行/文档
    from app.services.folder_template import ensure_project_folders
    await ensure_project_folders(name)
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
            "SELECT id, title, tags, source_type, is_core, updated_at FROM notes WHERE project_id = ? AND deleted_at IS NULL ORDER BY is_core DESC, updated_at DESC",
            (project_id,),
        )
        project_notes = [dict(row) for row in await cursor.fetchall()]

        # 项目决策
        cursor = await db.execute(
            "SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        project_decisions = [dict(row) for row in await cursor.fetchall()]

        # 外部支出台账
        cursor = await db.execute(
            "SELECT * FROM expenses WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        expenses = [dict(row) for row in await cursor.fetchall()]
        expense_total = round(sum(e["amount"] or 0 for e in expenses), 2)

        # 核心档计数（项目"宪法"）
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM notes WHERE project_id = ? AND is_core = 1 AND deleted_at IS NULL",
            (project_id,),
        )
        core_count = (await cursor.fetchone())["cnt"]

        # 作战室：最近本项目的 AI 执行记录（含工具步骤）
        cursor = await db.execute(
            """SELECT r.*, t.title as task_title FROM agent_runs r
               LEFT JOIN tasks t ON t.id = r.task_id
               WHERE t.project_id = ? OR r.prompt LIKE ?
               ORDER BY r.created_at DESC LIMIT 15""",
            (project_id, f"%{project_id[:8]}%"),
        )
        runs_with_tools = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    statuses = ["pending", "running", "reviewing", "blocked", "done", "failed"]
    task_board = {s: [t for t in tasks if t["status"] == s] for s in statuses}
    task_tree = _build_task_tree(tasks)

    from app.services.auto_runner import is_running, board_signature
    return request.app.state.templates.TemplateResponse(
        request, "project_detail.html",
        {
            "request": request, "project": project, "task_board": task_board,
            "tasks": tasks, "task_tree": task_tree, "project_cost": project_cost,
            "project_notes": project_notes, "project_decisions": project_decisions,
            "expenses": expenses, "expense_total": expense_total,
            "auto_running": is_running(project_id),
            "runs_with_tools": runs_with_tools,
            "core_count": core_count,
            "board_sig": await board_signature(project_id),
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
async def project_update(
    project_id: str, name: str = Form(...), description: str = Form(""), owner: str = Form(""),
    status: str = Form("active"), budget: float = Form(0), revenue: float = Form(0),
    automation_level: str = Form("manual"), ai_budget: float = Form(0),
):
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "UPDATE projects SET name=?, description=?, owner=?, status=?, budget=?, revenue=?, automation_level=?, ai_budget=?, updated_at=? WHERE id=?",
            (name, description, owner, status, budget, revenue, automation_level, ai_budget, now, project_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/expenses/new")
async def expense_create(
    project_id: str,
    title: str = Form(...),
    amount: float = Form(0),
    category: str = Form("其他"),
    note: str = Form(""),
):
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO expenses (id, project_id, title, amount, category, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, title, amount, category, note, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/expenses/{expense_id}/delete")
async def expense_delete(project_id: str, expense_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM expenses WHERE id = ? AND project_id = ?", (expense_id, project_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}/ai-messages")
async def project_ai_messages(project_id: str):
    """项目 AI 对话历史 JSON"""
    from fastapi.responses import JSONResponse
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT content, direction, created_at FROM messages WHERE channel = 'project_ai' AND project_id = ? ORDER BY created_at ASC LIMIT 100",
            (project_id,),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    return JSONResponse({"messages": rows})


@router.post("/projects/{project_id}/ai-ask")
async def project_ai_ask(project_id: str, message: str = Form(...), sender: str = Form("Gaga"), model: str = Form("auto")):
    """项目 AI 接收消息并回复"""
    from fastapi.responses import JSONResponse
    from app.services.project_ai import project_chat
    try:
        result = await project_chat(project_id, message, sender, model)
        return JSONResponse(result)
    except Exception as e:
        # 兜底：任何异常都返回合法 JSON，避免前端 r.json() 失败
        import logging, traceback
        logging.getLogger(__name__).exception("project_ai_ask failed")
        return JSONResponse({
            "reply": f"❌ 项目 AI 内部出错：{type(e).__name__}: {e}\n（已自动记录，可重试或换个说法）",
            "action": "chat", "cost": 0, "model": "error",
        })


@router.post("/projects/{project_id}/ai-clear")
async def project_ai_clear(project_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM messages WHERE channel = 'project_ai' AND project_id = ?", (project_id,))
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
            "INSERT INTO projects (id, name, description, owner, status, budget, revenue, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
            (new_id, src["name"] + " (副本)", src["description"], src["owner"], src.get("budget", 0), 0, now, now),
        )

        # 复制项目笔记（资料），不复制决策记录（决策应该是新项目重新做的）
        cursor = await db.execute("SELECT * FROM notes WHERE project_id = ? AND deleted_at IS NULL", (project_id,))
        src_notes = [dict(row) for row in await cursor.fetchall()]
        for n in src_notes:
            await db.execute(
                """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), n["title"], n["content"], n.get("author", ""), new_id,
                 n.get("tags", ""), n.get("source_type", "manual"), now, now),
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
