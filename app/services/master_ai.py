"""
总 AI（Master AI）—— 调度员

职责：
1. 接收人的自然语言指令
2. 理解意图，自动规划
3. 拆解任务，分配给子 AI
4. 监督执行，检查质量
5. 汇报结果，沉淀知识

总 AI 不执行具体任务，只做调度和管理。
"""

import json
import uuid
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai
from app.services.task_engine import decompose_project
from app.services.agent_manager import execute_task
from app.services.note_ai import classify_content

MASTER_SYSTEM = """你是一个 AI 项目管理总监（Master AI）。你管理一个二人公司的所有项目和 AI 工作者。

你的职责：
1. 理解用户的意图和指令
2. 决定下一步行动并执行
3. 调度子 AI 去做具体工作
4. 监督质量，汇报结果

你可以执行以下动作（返回 JSON）：
{
  "action": "动作类型",
  "params": { ... },
  "reply": "对用户说的话"
}

可用动作：
- "chat": 纯对话，不需要操作。params: {}
- "create_project": 创建新项目。params: {"name": "项目名", "description": "描述", "owner": "负责人"}
- "decompose": 拆解项目任务。params: {"project_id": "项目ID", "goal": "目标描述"}
- "execute_tasks": 批量执行待办AI任务。params: {"project_id": "项目ID"}
- "classify_text": 智能分类一段文字。params: {"text": "内容", "project_id": "可选项目ID"}
- "status_report": 生成状态报告。params: {"project_id": "可选，不填则全部"}
- "decision": 记录一个决策。params: {"title": "标题", "decision": "决定", "reason": "原因", "project_id": "可选"}
- "create_note": 创建笔记。params: {"title": "标题", "content": "内容", "tags": "标签", "project_id": "可选"}

规则：
- reply 用中文，简洁直接
- 如果用户意图不明确，reply 里追问，action 用 "chat"
- 如果需要多步操作，先做第一步，reply 里说明后续计划
- 只返回 JSON，不要其他内容"""


async def get_context_for_master() -> str:
    """收集当前系统状态给总 AI 做决策参考"""
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT p.id, p.name, p.status, p.description,
                COUNT(t.id) as task_count,
                SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) as done_count,
                SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) as running_count,
                SUM(CASE WHEN t.status = 'blocked' THEN 1 ELSE 0 END) as blocked_count
            FROM projects p LEFT JOIN tasks t ON t.project_id = p.id
            WHERE p.status = 'active'
            GROUP BY p.id
        """)
        projects = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM notes")
        note_count = (await cursor.fetchone())["cnt"]
    finally:
        await db.close()

    if not projects:
        return "当前没有活跃项目。"

    lines = ["当前系统状态:"]
    for p in projects:
        lines.append(f"- 项目「{p['name']}」(ID:{p['id'][:8]}...) 状态:{p['status']} 任务:{p['task_count']}个(完成{p['done_count'] or 0}/待办{p['pending_count'] or 0}/执行中{p['running_count'] or 0}/阻塞{p['blocked_count'] or 0})")
    lines.append(f"- 知识库共 {note_count} 条笔记")
    return "\n".join(lines)


async def master_chat(message: str, sender: str, model: str = "auto") -> dict:
    """总 AI 处理一条消息"""

    # 1. 收集上下文
    context = await get_context_for_master()

    # 2. 保存用户消息到对话记录
    now = datetime.now().isoformat()
    msg_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO messages (id, project_id, task_id, channel, content, direction, created_at) VALUES (?, NULL, NULL, 'master_ai', ?, 'in', ?)",
            (msg_id, f"[{sender}] {message}", now),
        )
        await db.commit()
    finally:
        await db.close()

    # 3. 问总 AI
    prompt = f"""系统状态：
{context}

用户 [{sender}] 说：{message}

请分析意图并决定行动。"""

    result = await ask_ai(prompt=prompt, model=model, task_type="analysis", system_prompt=MASTER_SYSTEM)

    # 4. 解析响应
    try:
        resp_text = result["response"]
        if "```json" in resp_text:
            resp_text = resp_text.split("```json")[1].split("```")[0]
        elif "```" in resp_text:
            resp_text = resp_text.split("```")[1].split("```")[0]
        parsed = json.loads(resp_text.strip())
    except (json.JSONDecodeError, IndexError):
        parsed = {"action": "chat", "params": {}, "reply": result["response"]}

    action = parsed.get("action", "chat")
    params = parsed.get("params", {})
    reply = parsed.get("reply", "我理解了，正在处理...")

    # 5. 执行动作
    action_result = None
    if action == "create_project":
        action_result = await _action_create_project(params)
    elif action == "decompose":
        action_result = await _action_decompose(params)
    elif action == "execute_tasks":
        action_result = await _action_execute_tasks(params)
    elif action == "classify_text":
        action_result = await _action_classify(params)
    elif action == "status_report":
        action_result = await _action_status_report(params)
    elif action == "decision":
        action_result = await _action_decision(params)
    elif action == "create_note":
        action_result = await _action_create_note(params)

    # 6. 保存 AI 回复
    ai_msg_id = str(uuid.uuid4())
    now2 = datetime.now().isoformat()
    full_reply = reply
    if action_result:
        full_reply += f"\n\n[执行结果] {action_result}"

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO messages (id, project_id, task_id, channel, content, direction, created_at) VALUES (?, NULL, NULL, 'master_ai', ?, 'out', ?)",
            (ai_msg_id, full_reply, now2),
        )
        # 记录 AI 调用
        run_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, 'success', ?)""",
            (run_id, result["model"] + " [总AI]", f"[{sender}] {message}", result["response"], result["tokens"], result["cost"], now2),
        )
        await db.commit()
    finally:
        await db.close()

    return {
        "reply": full_reply,
        "action": action,
        "action_result": action_result,
        "model": result["model"],
        "cost": result["cost"],
    }


# ── 动作执行器 ──────────────────────────────────────

async def _action_create_project(params: dict) -> str:
    from app.api.projects import project_create
    name = params.get("name", "新项目")
    desc = params.get("description", "")
    owner = params.get("owner", "")
    pid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO projects (id, name, description, owner, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (pid, name, desc, owner, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"已创建项目「{name}」"


async def _action_decompose(params: dict) -> str:
    project_id = params.get("project_id", "")
    goal = params.get("goal", "")
    if not project_id:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT id FROM projects WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1")
            row = await cursor.fetchone()
            if row:
                project_id = row["id"]
        finally:
            await db.close()
    if not project_id:
        return "没有找到活跃项目"
    tasks = await decompose_project(project_id, goal)
    return f"已拆解出 {len(tasks)} 个子任务"


async def _action_execute_tasks(params: dict) -> str:
    project_id = params.get("project_id", "")
    db = await get_db()
    try:
        if project_id:
            cursor = await db.execute(
                "SELECT id FROM tasks WHERE project_id = ? AND status = 'pending' AND assignee = 'ai' ORDER BY priority ASC LIMIT 5",
                (project_id,),
            )
        else:
            cursor = await db.execute(
                "SELECT id FROM tasks WHERE status = 'pending' AND assignee = 'ai' ORDER BY priority ASC LIMIT 5"
            )
        task_ids = [row["id"] for row in await cursor.fetchall()]
    finally:
        await db.close()

    if not task_ids:
        return "没有待执行的 AI 任务"

    results = []
    for tid in task_ids:
        try:
            r = await execute_task(tid)
            review = r.get("auto_review", {}).get("review", {})
            status = "通过" if review.get("passed") else "需人工审核"
            results.append(f"✓ 任务已执行({status})")
        except Exception as e:
            results.append(f"✗ 执行失败: {e}")

    return f"执行了 {len(task_ids)} 个任务:\n" + "\n".join(results)


async def _action_classify(params: dict) -> str:
    text = params.get("text", "")
    project_id = params.get("project_id", "")
    if not text:
        return "没有内容可分类"
    created = await classify_content(text, project_id)
    return f"已提取 {len(created)} 条信息并存入知识库"


async def _action_status_report(params: dict) -> str:
    context = await get_context_for_master()
    return context


async def _action_decision(params: dict) -> str:
    dec_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO decisions (id, project_id, title, decision, reason, made_by, created_at) VALUES (?, ?, ?, ?, ?, 'AI', ?)",
            (dec_id, params.get("project_id") or None, params.get("title", ""), params.get("decision", ""), params.get("reason", ""), now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"已记录决策「{params.get('title', '')}」"


async def _action_create_note(params: dict) -> str:
    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    tags = params.get("tags", "")
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, project_id, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'master_ai', ?, ?)""",
            (note_id, params.get("title", ""), params.get("content", ""), params.get("project_id") or None, tags, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"已创建笔记「{params.get('title', '')}」"
