import uuid
import json
import logging
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai, estimate_cost

log = logging.getLogger(__name__)

EXECUTE_SYSTEM = """你是一个高效的 AI 工作者。用户会给你一个具体任务，请认真完成并返回结果。
- 如果是代码任务，直接给出代码
- 如果是文案任务，直接给出文案
- 如果是分析任务，给出结构化的分析结果
- 结果要具体、可用，不要空泛"""

REVIEW_SYSTEM = """你是一个质量审核专家。用户会给你一个任务描述和 AI 的执行结果，请评估质量。

请返回 JSON：
{
  "passed": true/false,
  "score": 1-10,
  "feedback": "具体反馈",
  "suggestion": "改进建议（如不通过）"
}

只返回 JSON。"""


def _detect_task_type(title: str) -> str:
    title_lower = title.lower()
    if any(kw in title_lower for kw in ["代码", "编程", "开发", "code", "api", "接口", "脚本", "数据库"]):
        return "code"
    if any(kw in title_lower for kw in ["审核", "检查", "review", "质检"]):
        return "review"
    if any(kw in title_lower for kw in ["分析", "调研", "研究", "analysis"]):
        return "analysis"
    return "writing"


async def estimate_task_cost(task_id: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = dict(await cursor.fetchone())
    finally:
        await db.close()

    prompt = f"任务：{task['title']}\n\n详细描述：{task['description']}" if task["description"] else f"任务：{task['title']}"
    task_type = _detect_task_type(task["title"])
    return estimate_cost(prompt, task["ai_model"], task_type)


async def execute_task(task_id: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = dict(await cursor.fetchone())
    finally:
        await db.close()

    now = datetime.now().isoformat()
    run_id = str(uuid.uuid4())
    prompt = f"任务：{task['title']}\n\n详细描述：{task['description']}" if task["description"] else f"任务：{task['title']}"
    task_type = _detect_task_type(task["title"])

    db = await get_db()
    try:
        await db.execute("UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?", (now, task_id))
        await db.commit()
    finally:
        await db.close()

    from app.services.constitution import with_constitution
    result = await ask_ai(prompt=prompt, model=task["ai_model"], task_type=task_type, system_prompt=with_constitution(EXECUTE_SYSTEM))

    db = await get_db()
    try:
        now = datetime.now().isoformat()
        fallback_note = f" (备用模型，原模型: {result['fallback_from']})" if result.get("fallback_from") else ""
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?)""",
            (run_id, task_id, result["model"] + fallback_note, prompt, result["response"], result["tokens"], result["cost"], now),
        )
        await db.execute(
            "UPDATE tasks SET status = 'reviewing', result = ?, progress = 80, updated_at = ? WHERE id = ?",
            (result["response"], now, task_id),
        )
        await db.commit()
    finally:
        await db.close()

    # 自动触发 AI 审核
    review_result = await review_task(task_id)

    return {"run_id": run_id, "result": result, "auto_review": review_result}


async def review_task(task_id: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = dict(await cursor.fetchone())
    finally:
        await db.close()

    prompt = f"任务：{task['title']}\n描述：{task['description']}\n\nAI 执行结果：\n{task['result']}"

    from app.services.constitution import with_constitution
    result = await ask_ai(prompt=prompt, model="auto", task_type="review", system_prompt=with_constitution(REVIEW_SYSTEM))

    try:
        text = result["response"]
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        review = json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        review = {"passed": False, "score": 5, "feedback": result["response"], "suggestion": ""}

    now = datetime.now().isoformat()
    run_id = str(uuid.uuid4())

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?)""",
            (run_id, task_id, result["model"] + " [审核]", f"[审核] {prompt[:200]}", result["response"], result["tokens"], result["cost"], now),
        )

        if review.get("passed"):
            await db.execute(
                "UPDATE tasks SET status = 'done', progress = 100, updated_at = ? WHERE id = ?",
                (now, task_id),
            )
        else:
            new_result = task["result"] + f"\n\n---\n[AI 审核未通过] 评分: {review.get('score')}/10\n反馈: {review.get('feedback')}\n建议: {review.get('suggestion')}"
            await db.execute(
                "UPDATE tasks SET status = 'blocked', progress = 50, result = ?, needs_human = 1, updated_at = ? WHERE id = ?",
                (new_result, now, task_id),
            )

        await db.commit()
    finally:
        await db.close()

    return {"review": review, "run_id": run_id}
