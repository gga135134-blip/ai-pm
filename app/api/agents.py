from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.services.task_engine import decompose_project
from app.services.agent_manager import execute_task, review_task, estimate_task_cost
from app.services.notifier import notify_wechat

router = APIRouter()


@router.post("/projects/{project_id}/decompose")
async def api_decompose(project_id: str, goal: str = Form(...), model: str = Form("auto")):
    await decompose_project(project_id, goal, model)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/tasks/{task_id}/estimate", response_class=HTMLResponse)
async def api_estimate(request: Request, task_id: str):
    est = await estimate_task_cost(task_id)
    return request.app.state.templates.TemplateResponse(
        "cost_estimate.html", {"request": request, "estimate": est, "task_id": task_id}
    )


@router.post("/tasks/{task_id}/execute")
async def api_execute(task_id: str):
    result = await execute_task(task_id)
    review = result.get("auto_review", {}).get("review", {})
    try:
        status = "通过" if review.get("passed") else "需人工审核"
        await notify_wechat(
            f"任务执行完成 - {status}",
            f"评分: {review.get('score', '-')}/10\n反馈: {review.get('feedback', '')}",
        )
    except Exception:
        pass
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/review")
async def api_review(task_id: str):
    result = await review_task(task_id)
    review = result["review"]
    try:
        status = "通过" if review.get("passed") else "未通过"
        await notify_wechat(f"AI 审核{status}", f"评分: {review.get('score', '-')}\n反馈: {review.get('feedback', '')}")
    except Exception:
        pass
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)
