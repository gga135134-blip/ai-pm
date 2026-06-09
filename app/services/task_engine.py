import json
import uuid
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai

DECOMPOSE_SYSTEM = """你是一个项目管理专家。用户会给你一个项目目标或任务描述，你需要将其拆解为具体的、可执行的子任务。

请返回 JSON 格式，结构如下：
{
  "tasks": [
    {
      "title": "任务标题",
      "description": "详细描述",
      "assignee": "ai 或留空表示人工",
      "priority": 1-5的数字,
      "ai_model": "auto/claude/openai",
      "needs_human": true/false
    }
  ]
}

规则：
- 每个任务要具体、可执行，不要太笼统
- 优先级 1 最高，5 最低
- 能用 AI 做的标 assignee 为 "ai"，必须人做的留空
- 需要人拍板决策的标 needs_human 为 true
- 只返回 JSON，不要其他内容"""


async def decompose_project(project_id: str, goal: str, model: str = "auto") -> list[dict]:
    result = await ask_ai(
        prompt=f"请将以下项目目标拆解为具体任务：\n\n{goal}",
        model=model,
        task_type="analysis",
        system_prompt=DECOMPOSE_SYSTEM,
    )

    try:
        text = result["response"]
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        parsed = json.loads(text.strip())
        task_list = parsed.get("tasks", [])
    except (json.JSONDecodeError, IndexError, KeyError):
        task_list = [{"title": "AI 拆解失败，请手动创建任务", "description": result["response"], "assignee": "", "priority": 1, "needs_human": True, "ai_model": "auto"}]

    db = await get_db()
    created = []
    try:
        now = datetime.now().isoformat()
        for t in task_list:
            task_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO tasks (id, project_id, title, description, assignee, ai_model, priority, needs_human, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    task_id, project_id,
                    t.get("title", "未命名任务"),
                    t.get("description", ""),
                    t.get("assignee", ""),
                    t.get("ai_model", "auto"),
                    t.get("priority", 3),
                    1 if t.get("needs_human") else 0,
                    now, now,
                ),
            )
            created.append({"id": task_id, **t})

        # 记录这次 AI 调用
        run_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?)""",
            (run_id, None, result["model"], f"拆解项目: {goal}", result["response"], result["tokens"], result["cost"], now),
        )

        await db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        await db.commit()
    finally:
        await db.close()

    return created
