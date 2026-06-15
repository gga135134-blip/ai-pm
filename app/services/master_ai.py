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

MASTER_SYSTEM = """你现在的角色是**总 AI（项目总监）**。董事会（Gaga / An）通过这个对话框跟你沟通，你是他们唯一直接对话的 AI。

你可以执行以下动作（返回 JSON）：
{
  "action": "动作类型",
  "params": { ... },
  "reply": "对用户说的话"
}

可用动作：
- "chat": 纯对话/追问/汇报，不做任何改动。params: {}
- "do_now": 当场动手做一件具体的事（联网抓网页、跑代码算数据、查资料、生成文件等）。你有真实工具，能联网能跑 Python。董事会让你"抓取/查一下/算一下/整理"这类即时小活时用这个，直接做完返回结果，不必为这点小事建项目。params: {"task": "要做的事的完整描述", "project": "可选，归属哪个项目"}
- "create_project": 正式创建项目（必须先聊清目标/预算/时间/授权等级，并得到董事会确认后才用）。创建时会自动按 goal 拆解出任务，不要再单独 decompose。params: {"name": "项目名", "description": "完整的项目框架描述", "goal": "用于拆解任务的核心目标", "owner": "负责人", "ai_budget": AI费用预算美元数字, "automation_level": "manual 或 auto"}
- "decompose": 给项目拆解任务。params: {"project": "项目编号如P003或项目名", "goal": "目标描述"}
- "execute_tasks": 批量执行某项目的待办AI任务。params: {"project": "项目编号或名称"}
- "classify_text": 智能分类一段文字入知识库。params: {"text": "内容", "project": "可选项目编号/名称"}
- "status_report": 汇报项目状态。params: {"project": "可选，不填则汇报全部"}
- "decision": 记录一个决策（涉及外部花钱的事项必须走这个提请董事会）。params: {"title": "标题", "decision": "决定", "reason": "原因", "project": "可选项目编号/名称"}
- "create_note": 创建笔记。params: {"title": "标题", "content": "内容", "tags": "标签", "project": "可选项目编号/名称"}

立项流程（重要）：
- 当董事会提出一个新想法时，**不要急着创建项目**。先用 "chat" 追问核心框架：目标、AI 预算、外部预算、大概执行时间、授权等级（每步确认 还是 预算内全自动）。
- 把框架聊清楚后，用 "chat" 给出一份"项目框架草案 + 初步任务清单"，请董事会确认。
- **董事会一旦说"确认/可以/建吧/开工/是的"等同意的话，你这一轮就必须返回 action="create_project"，绝对不能再用 chat 拖延或继续追问。** 这是硬性要求。create_project 会自动按 goal 拆出任务。

关于能力边界（重要）：
- 你和执行 agent 只能产出"文字成果"：方案、文案、分析、策划、报告、调研。
- 你**不能**真正写代码跑爬虫、不能调用外部接口、不能联网抓数据。涉及这类需要技术落地或花钱买服务（API、代理IP、第三方工具）的事，要作为 "decision" 提请董事会拍板，**不要向董事会索要账号密码、代理IP、密钥等凭据**。

规则：
- 项目一律用编号称呼（如 P003），方便董事会指代。
- reply 用中文，简洁直接，不说客套话。
- params 里引用项目用 "project" 字段，填编号（P003）或项目名都行。
- 只返回 JSON，不要返回任何 JSON 以外的文字。"""


async def _resolve_project(ref: str) -> str | None:
    """把项目引用（编号 P003 / 纯数字 3 / 项目名）解析成 project_id"""
    if not ref:
        return None
    ref = str(ref).strip()
    db = await get_db()
    try:
        # 直接是 UUID
        cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (ref,))
        row = await cursor.fetchone()
        if row:
            return row["id"]
        # 编号：P003 / p3 / 3
        code = ref.upper()
        if code.isdigit():
            code = f"P{int(code):03d}"
        elif code.startswith("P") and code[1:].isdigit():
            code = f"P{int(code[1:]):03d}"
        cursor = await db.execute("SELECT id FROM projects WHERE code = ?", (code,))
        row = await cursor.fetchone()
        if row:
            return row["id"]
        # 按名称模糊匹配
        cursor = await db.execute("SELECT id FROM projects WHERE name LIKE ? ORDER BY updated_at DESC LIMIT 1", (f"%{ref}%",))
        row = await cursor.fetchone()
        return row["id"] if row else None
    finally:
        await db.close()


async def get_context_for_master() -> str:
    """收集当前系统状态给总 AI 做决策参考"""
    from app.api.projects import ensure_project_codes
    await ensure_project_codes()
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT p.id, p.code, p.name, p.status, p.description, p.ai_budget, p.automation_level,
                COUNT(t.id) as task_count,
                SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) as done_count,
                SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) as running_count,
                SUM(CASE WHEN t.status = 'blocked' THEN 1 ELSE 0 END) as blocked_count
            FROM projects p LEFT JOIN tasks t ON t.project_id = p.id
            WHERE p.status = 'active'
            GROUP BY p.id
            ORDER BY p.code
        """)
        projects = [dict(row) for row in await cursor.fetchall()]

        # 各项目 AI 费用
        cursor = await db.execute("""
            SELECT t.project_id, SUM(r.cost) as cost
            FROM agent_runs r JOIN tasks t ON t.id = r.task_id
            GROUP BY t.project_id
        """)
        costs = {row["project_id"]: row["cost"] or 0 for row in await cursor.fetchall()}

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM notes WHERE deleted_at IS NULL")
        note_count = (await cursor.fetchone())["cnt"]
    finally:
        await db.close()

    if not projects:
        return "当前没有活跃项目。董事会如果提出新想法，先聊清框架再立项。"

    lines = ["当前活跃项目:"]
    for p in projects:
        spent = round(costs.get(p["id"], 0), 4)
        budget = f"，AI预算${p['ai_budget']}" if p.get("ai_budget") else ""
        mode = "全自动" if p.get("automation_level") == "auto" else "每步确认"
        lines.append(
            f"- [{p['code']}]「{p['name']}」状态:{p['status']} 模式:{mode} "
            f"任务{p['task_count']}个(完成{p['done_count'] or 0}/待办{p['pending_count'] or 0}/执行中{p['running_count'] or 0}/阻塞{p['blocked_count'] or 0}) "
            f"已花AI费${spent}{budget}"
        )
    lines.append(f"知识库共 {note_count} 条笔记。")
    return "\n".join(lines)


async def _get_recent_history(limit: int = 12) -> str:
    """读最近的对话历史，让总 AI 记得上下文（关键！否则它是金鱼）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT content, direction FROM messages WHERE channel = 'master_ai' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    rows.reverse()  # 时间正序
    lines = []
    for r in rows:
        who = "董事会" if r["direction"] == "in" else "你(总AI)"
        # 去掉 AI 回复里的 [执行结果] 尾巴，保持历史干净
        content = r["content"].split("\n\n[执行结果]")[0]
        lines.append(f"{who}: {content}")
    return "\n".join(lines)


def _extract_action_json(text: str) -> dict:
    """从 AI 回复里稳健地提取 {action, params, reply} JSON。
    解析失败时，把整段文字当作 reply 返回（action=chat），但去掉裸 JSON 的观感。"""
    raw = (text or "").strip()

    # 去掉 ```json ... ``` 围栏
    candidate = raw
    if "```" in candidate:
        import re as _re
        m = _re.search(r"```(?:json)?\s*(.+?)```", candidate, _re.DOTALL)
        if m:
            candidate = m.group(1).strip()

    # 直接尝试（strict=False 允许字符串里有真实换行符，AI 常这么返回）
    try:
        obj = json.loads(candidate, strict=False)
        if isinstance(obj, dict) and ("action" in obj or "reply" in obj):
            return obj
    except json.JSONDecodeError:
        pass

    # 从第一个 { 到最后一个 } 截取再试
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(candidate[start:end + 1], strict=False)
            if isinstance(obj, dict) and ("action" in obj or "reply" in obj):
                return obj
        except json.JSONDecodeError:
            pass

    # 实在解析不了：当纯聊天，原样返回（绝不做 unicode_escape，会把中文搅成乱码）
    return {"action": "chat", "params": {}, "reply": raw}


async def master_chat(message: str, sender: str, model: str = "auto") -> dict:
    """总 AI 处理一条消息"""

    # 1. 收集上下文 + 对话历史
    context = await get_context_for_master()
    history = await _get_recent_history()  # 存入本条消息前先读历史

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
    history_block = f"最近的对话记录（请结合上下文理解，不要重复发问已问过的内容）：\n{history}\n\n" if history else ""
    prompt = f"""系统状态：
{context}

{history_block}董事会成员 [{sender}] 最新说：{message}

请结合上面的对话记录分析意图并决定行动。"""

    from app.services.constitution import with_constitution
    result = await ask_ai(prompt=prompt, model=model, task_type="analysis", system_prompt=with_constitution(MASTER_SYSTEM))

    # 4. 解析响应（稳健提取 JSON，解析不出来才退化为纯聊天）
    parsed = _extract_action_json(result["response"])
    action = parsed.get("action", "chat")
    params = parsed.get("params", {}) or {}
    reply = parsed.get("reply") or parsed.get("response") or "我理解了，正在处理..."

    # 5. 执行动作
    action_result = None
    if action == "do_now":
        action_result = await _action_do_now(params)
    elif action == "create_project":
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

async def _action_do_now(params: dict) -> str:
    """总 AI 当场用工具干一件具体的事"""
    from app.services.agent_tools import run_agent_loop
    from app.services.constitution import with_constitution
    from app.services.agent_manager import EXECUTE_SYSTEM, _brief_args
    task = params.get("task", "")
    if not task:
        return "没有具体任务内容"
    project_id = await _resolve_project(params.get("project") or "")
    result = await run_agent_loop(task, system=with_constitution(EXECUTE_SYSTEM), project_id=project_id)
    text = result["response"]
    steps = result.get("steps", [])
    if steps:
        lines = ["", "──────────", f"🔧 执行过程（调用了 {len(steps)} 次工具）："]
        for i, s in enumerate(steps, 1):
            lines.append(f"{i}. {s['tool']}({_brief_args(s['args'])})")
        text += "\n".join(lines)
    text += f"\n\n（本次执行花费 ${result['cost']}）"
    return text


async def _action_create_project(params: dict) -> str:
    from app.api.projects import ensure_project_codes
    name = params.get("name", "新项目")
    desc = params.get("description", "")
    owner = params.get("owner", "")
    ai_budget = float(params.get("ai_budget") or 0)
    automation_level = params.get("automation_level", "manual")
    goal = params.get("goal") or desc  # 用于自动拆解任务的目标
    pid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO projects (id, name, description, owner, status, ai_budget, automation_level, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (pid, name, desc, owner, ai_budget, automation_level, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    await ensure_project_codes()
    db = await get_db()
    try:
        cursor = await db.execute("SELECT code FROM projects WHERE id = ?", (pid,))
        code = (await cursor.fetchone())["code"]
    finally:
        await db.close()

    # 建完直接拆任务，避免建出空项目
    task_note = ""
    if goal:
        try:
            tasks = await decompose_project(pid, goal)
            if tasks:
                task_note = f"，并自动拆解出 {len(tasks)} 个任务"
        except Exception as e:
            task_note = f"（任务拆解失败: {e}，可稍后手动拆解）"

    return f"已创建项目 [{code}]「{name}」{task_note}，可在项目页查看"


async def _action_decompose(params: dict) -> str:
    project_id = await _resolve_project(params.get("project") or params.get("project_id", ""))
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
        return "没有找到对应项目"
    tasks = await decompose_project(project_id, goal)
    return f"已拆解出 {len(tasks)} 个子任务"


async def _action_execute_tasks(params: dict) -> str:
    project_id = await _resolve_project(params.get("project") or params.get("project_id", ""))
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
    project_id = await _resolve_project(params.get("project") or params.get("project_id", ""))
    if not text:
        return "没有内容可分类"
    created = await classify_content(text, project_id or "")
    return f"已提取 {len(created)} 条信息并存入知识库"


async def _action_status_report(params: dict) -> str:
    context = await get_context_for_master()
    return context


async def _action_decision(params: dict) -> str:
    dec_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    project_id = await _resolve_project(params.get("project") or params.get("project_id", ""))
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO decisions (id, project_id, title, decision, reason, made_by, created_at) VALUES (?, ?, ?, ?, ?, 'AI', ?)",
            (dec_id, project_id, params.get("title", ""), params.get("decision", ""), params.get("reason", ""), now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"已记录决策「{params.get('title', '')}」"


async def _action_create_note(params: dict) -> str:
    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    tags = params.get("tags", "")
    project_id = await _resolve_project(params.get("project") or params.get("project_id", ""))
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, project_id, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'master_ai', ?, ?)""",
            (note_id, params.get("title", ""), params.get("content", ""), project_id, tags, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"已创建笔记「{params.get('title', '')}」"
