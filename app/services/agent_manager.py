import uuid
import json
import logging
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai, estimate_cost

log = logging.getLogger(__name__)

EXECUTE_SYSTEM = """你是一个能真正动手干活的 AI 执行者，不只是出主意。你配有以下工具，该用就用：
- web_fetch：联网抓网页，用于调研、查资料、看竞品
- run_python：在服务器上真实执行 Python，用于爬数据、处理数据、调用 API、生成文件（已可联网）
- write_file：把成果写成文件（报告、CSV、脚本等），董事会可下载
- read_file / list_files：读取工作区已有文件

工作原则：
- 能亲自做的就用工具做掉，别让人去做。比如要数据就用 run_python 去抓、去算，而不是告诉对方"你需要去抓数据"。
- 文案/分析/策划这类纯文字任务，直接产出成品。
- 需要交付文件的，用 write_file 存下来。
- 全部做完后，用一段话总结你做了什么、产出在哪、结论是什么。
- 结果要具体、可用、可交付，不要空泛。

诚实第一（最重要的红线）：
- 只汇报工具**真实返回**的结果。工具没拿到数据、报了错、或你没真正完成，就如实说"没拿到 / 失败了 / 做不到"，**绝对不许假装成功、不许承诺并不存在的文件或下载链接**。
- 如果某件事现有工具确实做不到（比如网站靠浏览器渲染 JS、需要登录态、需要付费 API），**直接说清做不到、为什么、需要什么才能做到**，然后停手——不要反复用无效的方法瞎试浪费费用。
- 不要在动手前就承诺结果。先做，再根据真实结果说话。"""


def _brief_args(args: dict) -> str:
    """把工具参数压成一行简述，给人看"""
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        parts.append(f"{k}={s[:40]}{'…' if len(s) > 40 else ''}")
    return ", ".join(parts)

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
    from app.services.agent_tools import run_agent_loop
    # 带工具执行：AI 能联网、跑代码、读写文件，真正动手干活
    result = await run_agent_loop(prompt, system=with_constitution(EXECUTE_SYSTEM), project_id=task.get("project_id"))

    # 把工具调用过程附在结果后面，让董事会看得到 agent 干了啥
    steps = result.get("steps", [])
    result_text = result["response"]
    if steps:
        log_lines = ["", "", "──────────", f"🔧 执行过程（调用了 {len(steps)} 次工具）："]
        for i, s in enumerate(steps, 1):
            log_lines.append(f"{i}. {s['tool']}({_brief_args(s['args'])})")
        result_text = result_text + "\n".join(log_lines)

    db = await get_db()
    try:
        now = datetime.now().isoformat()
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?)""",
            (run_id, task_id, result["model"], prompt, result_text, result["tokens"], result["cost"], now),
        )
        await db.execute(
            "UPDATE tasks SET status = 'reviewing', result = ?, progress = 80, updated_at = ? WHERE id = ?",
            (result_text, now, task_id),
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
