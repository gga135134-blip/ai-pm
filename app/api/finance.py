import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.database import get_db
from datetime import datetime

router = APIRouter()


@router.get("/finance", response_class=HTMLResponse)
async def finance_page(request: Request):
    import traceback
    try:
        return await _finance_page(request)
    except Exception:
        err = traceback.format_exc()
        return HTMLResponse(f"<pre style='color:red;padding:20px'>{err}</pre>", status_code=500)

async def _finance_page(request: Request):
    db = await get_db()

    # ── 汇总 ──────────────────────────────────────────────
    cursor = await db.execute("SELECT COALESCE(SUM(cost),0) as v FROM agent_runs")
    total_ai = (await cursor.fetchone())["v"]

    cursor = await db.execute("SELECT COALESCE(SUM(amount),0) as v FROM expenses")
    total_manual = (await cursor.fetchone())["v"]

    now = datetime.now()
    month_start = f"{now.year}-{now.month:02d}-01"

    cursor = await db.execute(
        "SELECT COALESCE(SUM(cost),0) as v FROM agent_runs WHERE created_at >= ?",
        (month_start,)
    )
    month_ai = (await cursor.fetchone())["v"]

    cursor = await db.execute(
        "SELECT COALESCE(SUM(amount),0) as v FROM expenses WHERE created_at >= ?",
        (month_start,)
    )
    month_manual = (await cursor.fetchone())["v"]

    # ── 按项目汇总 ─────────────────────────────────────────
    cursor = await db.execute("""
        SELECT p.id, p.name, p.code,
               COALESCE(SUM(ar.cost), 0) as ai_cost
        FROM projects p
        LEFT JOIN tasks t ON t.project_id = p.id
        LEFT JOIN agent_runs ar ON ar.task_id = t.id
        GROUP BY p.id
    """)
    ai_by_proj = {r["id"]: r["ai_cost"] or 0 for r in await cursor.fetchall()}

    cursor = await db.execute("""
        SELECT p.id, p.name, p.code,
               COALESCE(SUM(e.amount), 0) as manual_cost
        FROM projects p
        LEFT JOIN expenses e ON e.project_id = p.id
        GROUP BY p.id
    """)
    project_rows = await cursor.fetchall()

    project_stats = []
    for row in project_rows:
        pid = row["id"]
        ai = round(ai_by_proj.get(pid, 0), 4)
        manual = round(row["manual_cost"] or 0, 2)
        if ai > 0 or manual > 0:
            project_stats.append({
                "id": pid,
                "name": row["name"],
                "code": row["code"] or "",
                "ai_cost": ai,
                "manual_cost": manual,
                "total": round(ai + manual, 4),
            })
    project_stats.sort(key=lambda x: x["total"], reverse=True)

    # ── 近 6 个月月度数据 ──────────────────────────────────
    months = []
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")

    cursor = await db.execute("""
        SELECT strftime('%Y-%m', created_at) as mo, COALESCE(SUM(cost),0) as v
        FROM agent_runs
        WHERE created_at >= date('now','-6 months')
        GROUP BY mo
    """)
    ai_mo = {r["mo"]: round(r["v"], 4) for r in await cursor.fetchall()}

    cursor = await db.execute("""
        SELECT strftime('%Y-%m', created_at) as mo, COALESCE(SUM(amount),0) as v
        FROM expenses
        WHERE created_at >= date('now','-6 months')
        GROUP BY mo
    """)
    manual_mo = {r["mo"]: round(r["v"], 2) for r in await cursor.fetchall()}

    monthly_data = [
        {"month": m, "ai": ai_mo.get(m, 0), "manual": manual_mo.get(m, 0)}
        for m in months
    ]

    # ── 支出明细 ──────────────────────────────────────────
    cursor = await db.execute("""
        SELECT e.*, p.name as project_name, p.code as project_code
        FROM expenses e
        LEFT JOIN projects p ON p.id = e.project_id
        ORDER BY e.created_at DESC LIMIT 200
    """)
    expenses = [dict(r) for r in await cursor.fetchall()]

    # ── AI 调用明细 ──────────────────────────────────────
    cursor = await db.execute("""
        SELECT ar.id, ar.cost, ar.tokens_used, ar.model, ar.created_at,
               t.title as task_title,
               p.name as project_name, p.code as project_code
        FROM agent_runs ar
        LEFT JOIN tasks t ON t.id = ar.task_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE ar.cost > 0
        ORDER BY ar.created_at DESC LIMIT 200
    """)
    ai_runs = [dict(r) for r in await cursor.fetchall()]

    templates = request.app.state.templates
    return templates.TemplateResponse("finance.html", {
        "request": request,
        "total_ai": total_ai,
        "total_manual": total_manual,
        "month_ai": month_ai,
        "month_manual": month_manual,
        "total_all": round(total_ai + total_manual, 4),
        "month_all": round(month_ai + month_manual, 4),
        "now_month": f"{now.year}-{now.month:02d}",
        "project_stats": project_stats,
        "monthly_data": monthly_data,
        "monthly_data_json": json.dumps(monthly_data),
        "expenses": expenses,
        "ai_runs": ai_runs,
    })
