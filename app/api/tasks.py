import uuid
from datetime import datetime
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db

router = APIRouter()


@router.post("/projects/{project_id}/tasks/new")
async def task_create(
    project_id: str,
    title: str = Form(...),
    description: str = Form(""),
    assignee: str = Form(""),
    ai_model: str = Form("auto"),
    priority: int = Form(3),
    parent_task_id: str = Form(""),
):
    task_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO tasks (id, project_id, parent_task_id, title, description, assignee, ai_model, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (task_id, project_id, parent_task_id or None, title, description, assignee, ai_model, priority, now, now),
        )
        await db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = dict(await cursor.fetchone())

        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (task["project_id"],))
        project = dict(await cursor.fetchone())

        cursor = await db.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY priority ASC",
            (task_id,),
        )
        subtasks = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        runs = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    return request.app.state.templates.TemplateResponse(
        request, "task_detail.html",
        {"request": request, "task": task, "project": project, "subtasks": subtasks, "runs": runs},
    )


@router.post("/tasks/{task_id}/status")
async def task_update_status(task_id: str, status: str = Form(...)):
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        progress = 100 if status == "done" else None
        if progress is not None:
            await db.execute(
                "UPDATE tasks SET status=?, progress=?, updated_at=? WHERE id=?",
                (status, progress, now, task_id),
            )
        else:
            await db.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, now, task_id),
            )
        cursor = await db.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        project_id = row["project_id"]
        await db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/tasks/{task_id}/delete")
async def task_delete(task_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT project_id FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        project_id = row["project_id"]
        await db.execute("DELETE FROM agent_runs WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM tasks WHERE parent_task_id = ?", (task_id,))
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)
