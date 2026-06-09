from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.database import get_db

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM projects")
        project_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tasks")
        task_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE status = 'done'")
        done_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE status = 'running'")
        running_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tasks WHERE needs_human = 1 AND status != 'done'")
        needs_human_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT SUM(cost) as total FROM agent_runs"
        )
        row = await cursor.fetchone()
        total_cost = row["total"] or 0.0

        cursor = await db.execute("""
            SELECT p.*, COUNT(t.id) as task_count,
                SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) as done_count
            FROM projects p
            LEFT JOIN tasks t ON t.project_id = p.id
            WHERE p.status = 'active'
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            LIMIT 5
        """)
        recent_projects = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute("""
            SELECT t.*, p.name as project_name
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.status IN ('running', 'reviewing', 'blocked')
            ORDER BY t.updated_at DESC
            LIMIT 10
        """)
        active_tasks = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    stats = {
        "project_count": project_count,
        "task_count": task_count,
        "done_count": done_count,
        "running_count": running_count,
        "needs_human_count": needs_human_count,
        "total_cost": round(total_cost, 4),
    }

    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "recent_projects": recent_projects,
            "active_tasks": active_tasks,
        },
    )
