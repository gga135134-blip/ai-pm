"""项目 AI —— 每个项目的专属 AI 对话。

与总 AI 的区别：
- 总 AI 看全局（所有项目+知识库摘要），管立项、跨项目调度
- 项目 AI 只看本项目（核心档+所有任务+笔记），管这个项目内的讨论、追加任务、临时干活

通过 channel='project_ai' + project_id 隔离对话历史。
"""
import json
import uuid
import logging
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai
from app.services.master_ai import _extract_action_json

log = logging.getLogger(__name__)


PROJECT_AI_SYSTEM = """你是 **项目 {code} 「{name}」 的专属 AI**，只为这一个项目服务。

你的职责：
- 跟董事会讨论本项目的方向、计划、产出
- 接受董事会指令：当场动手干活、追加新任务、启停自动执行
- 提示董事会关注核心档、任务进度、潜在冲突

你**只**了解本项目的信息，不要谈论其他项目。

可用动作（返回 JSON）：
{{
  "action": "动作类型",
  "params": {{...}},
  "reply": "对董事会说的话"
}}

可用 action：
- "chat": 纯对话/汇报/追问，不做任何改动。params: {{}}
- "do_now": 当场用工具做一件具体的事（联网抓资料、跑代码、生成文件、**读知识库笔记**）。本项目工作区会自动归属。params: {{"task": "具体描述"}}
- "create_task": 在本项目新建一个任务。params: {{"title": "任务标题", "description": "详细描述", "assignee": "ai 或留空表示人工", "priority": 1-5, "ai_model": "auto/claude/openai/deepseek/qwen"}}
- "start_auto": 启动本项目的自动执行（worker 并行干所有待办 AI 任务）。params: {{}}
- "stop_auto": 停止本项目自动执行。params: {{}}

**两个不同的存储（必须区分清楚，否则会答错）**：
- **📚 知识库笔记**：董事会上传/AI 整理产出的内容，存数据库。上面"项目当前状态"里已经列出了本项目所有笔记的标题。
  - 想看笔记**内容**：用 do_now，task 描述里说"读知识库笔记《xxx》并 yyy"，do_now 调用的执行 agent 会自动加载本项目的核心档+相关笔记全文。
  - 董事会说"上传了"、"整理出了笔记"、"在知识库放了 xxx" → 一律指**知识库**，不是工作区！
- **📁 工作区文件**：只有执行 agent 用 run_python/write_file 产出的临时文件才在这里。**普通情况下董事会不会往工作区放东西**。
- 如果你看到上面的"📚 知识库笔记清单"里有标题，那就**真的存在**，别说"没看到"。

诚信红线：
- 历史对话里 [执行结果] 是真实回执，看到 ❌ 就承认失败，别假装成功。
- reply 文字不会真触发动作，只有 action 字段会。说"我会建任务"必须当轮就 action=create_task，否则别说。
- 看到核心档与任务/参考资料冲突，必须先停手汇报，不许硬执行。

规则：
- 中文，简洁直接。
- 只返回 JSON，不要其他文字。
- reply 文字里引用词语用「」，**不要用 ASCII 双引号 ""**（会破坏 JSON 格式）。"""


async def get_project_snapshot(project_id: str) -> str:
    """构造项目快照给项目 AI 做决策参考"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT code, name, description, status, ai_budget, automation_level FROM projects WHERE id = ?", (project_id,))
        proj = dict(await cursor.fetchone())

        # 任务状态分布
        cursor = await db.execute(
            """SELECT id, title, status, assignee, priority FROM tasks
               WHERE project_id = ? ORDER BY priority ASC, created_at ASC""",
            (project_id,),
        )
        tasks = [dict(r) for r in await cursor.fetchall()]

        # 知识库笔记清单（按 is_core 优先 + 最近更新）
        cursor = await db.execute(
            """SELECT title, is_core, source_type, updated_at FROM notes
               WHERE project_id = ? AND deleted_at IS NULL
               ORDER BY is_core DESC, updated_at DESC LIMIT 50""",
            (project_id,),
        )
        all_notes = [dict(r) for r in await cursor.fetchall()]
        core_notes = [n["title"] for n in all_notes if n["is_core"]]
        note_count = len(all_notes)

        # AI 费用
        cursor = await db.execute(
            "SELECT SUM(cost) as total FROM agent_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ?)",
            (project_id,),
        )
        spent = round((await cursor.fetchone())["total"] or 0, 4)
    finally:
        await db.close()

    lines = []
    lines.append(f"项目 {proj['code']}「{proj['name']}」当前状态:")
    lines.append(f"- 描述: {proj['description'] or '(无)'}")
    lines.append(f"- 模式: {'全自动' if proj.get('automation_level') == 'auto' else '每步确认'}, 状态: {proj['status']}")
    lines.append(f"- AI 预算: ${proj.get('ai_budget') or 0}, 已花: ${spent}")

    status_counts = {}
    for t in tasks:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
    status_str = " / ".join([f"{k}: {v}" for k, v in status_counts.items()]) if status_counts else "无任务"
    lines.append(f"- 任务({len(tasks)}个): {status_str}")

    if tasks:
        lines.append("- 任务清单:")
        for t in tasks[:20]:
            lines.append(f"  · [{t['status']}] P{t['priority']} {t['title']} ({t['assignee'] or '未分配'})")
        if len(tasks) > 20:
            lines.append(f"  · …还有 {len(tasks) - 20} 个")

    if core_notes:
        lines.append(f"- ⭐ 核心档({len(core_notes)}篇): " + "、".join(core_notes))
    else:
        lines.append("- ⭐ 核心档: 暂无（如果董事会确认了项目宪法、目标定位等，建议提醒他们标为核心档）")

    # 列出全部笔记标题（带 ⭐ 标 + 源类型），让 AI 知道知识库里有什么
    if all_notes:
        lines.append(f"- 📚 知识库笔记清单（共 {note_count} 篇，本项目所有笔记标题如下）:")
        for n in all_notes[:50]:
            star = "⭐" if n["is_core"] else "  "
            src_label = {"ai_classified": "[AI分类]", "ai_summary": "[AI整理]",
                         "auto_progress": "[进度]", "file_import": "[文件]",
                         "upload": "[上传]", "url_import": "[网页]",
                         "image": "[图片]", "image_article": "[图片整理]",
                         "ai_chat": "[AI问答]", "ai_weekly": "[周报]",
                         "master_ai": "[总AI]"}.get(n.get("source_type"), "")
            lines.append(f"    {star} {src_label}{n['title']}")
        if note_count > 50:
            lines.append(f"    …还有 {note_count - 50} 篇（用 do_now 可让执行 agent 智能检索全部）")
    else:
        lines.append("- 📚 知识库: 本项目暂无任何笔记")
    return "\n".join(lines)


async def _get_recent_history(project_id: str, limit: int = 12) -> str:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT content, direction FROM messages WHERE channel = 'project_ai' AND project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    rows.reverse()
    lines = []
    for r in rows:
        who = "董事会" if r["direction"] == "in" else "你(项目AI)"
        lines.append(f"{who}: {r['content']}")
    return "\n".join(lines)


async def project_chat(project_id: str, message: str, sender: str, model: str = "auto") -> dict:
    """项目 AI 处理一条消息"""
    # 项目元信息（生成 system prompt 时要用）
    db = await get_db()
    try:
        cursor = await db.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            return {"reply": "❌ 找不到该项目", "action": "chat", "cost": 0, "model": "none"}
        code, name = row["code"] or "(无编号)", row["name"]
    finally:
        await db.close()

    snapshot = await get_project_snapshot(project_id)
    history = await _get_recent_history(project_id)

    # 保存用户消息
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO messages (id, project_id, task_id, channel, content, direction, created_at) VALUES (?, ?, NULL, 'project_ai', ?, 'in', ?)",
            (str(uuid.uuid4()), project_id, f"[{sender}] {message}", now),
        )
        await db.commit()
    finally:
        await db.close()

    history_block = f"最近的对话记录：\n{history}\n\n" if history else ""
    prompt = f"""项目当前状态：
{snapshot}

{history_block}董事会成员 [{sender}] 最新说：{message}

请结合上下文分析意图并决定行动。"""

    system_prompt = PROJECT_AI_SYSTEM.format(code=code, name=name)
    result = await ask_ai(prompt=prompt, model=model, task_type="analysis", system_prompt=system_prompt)

    from app.services.agent_tools import _sanitize_output
    parsed = _extract_action_json(_sanitize_output(result["response"]))
    action = parsed.get("action", "chat")
    params = parsed.get("params", {}) or {}
    reply = parsed.get("reply") or "..."

    # 执行动作
    action_result = None
    if action != "chat":
        try:
            if action == "do_now":
                action_result = await _action_do_now_in_project(project_id, params, history)
            elif action == "create_task":
                action_result = await _action_create_task(project_id, params)
            elif action == "start_auto":
                action_result = await _action_start_auto(project_id)
            elif action == "stop_auto":
                action_result = await _action_stop_auto(project_id)
            else:
                action_result = f"❌ 未识别的动作: {action}"
        except Exception as e:
            log.exception("Project AI action %s failed", action)
            action_result = f"❌ 动作 {action} 执行失败: {type(e).__name__}: {e}"

    # 拼最终回复
    if action == "do_now" and action_result:
        full_reply = action_result
    else:
        full_reply = reply
        if action_result:
            full_reply += f"\n\n[执行结果] {action_result}"

    # 保存 AI 回复 + 记账
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO messages (id, project_id, task_id, channel, content, direction, created_at) VALUES (?, ?, NULL, 'project_ai', ?, 'out', ?)",
            (str(uuid.uuid4()), project_id, full_reply, datetime.now().isoformat()),
        )
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, 'success', ?)""",
            (str(uuid.uuid4()), result["model"] + f" [P{code}-AI]", f"[{sender}@{code}] {message}",
             result["response"], result["tokens"], result["cost"], datetime.now().isoformat()),
        )
        await db.commit()
    finally:
        await db.close()

    return {"reply": full_reply, "action": action, "model": result["model"], "cost": result["cost"]}


async def _action_do_now_in_project(project_id: str, params: dict, history: str) -> str:
    from app.services.agent_tools import run_agent_loop
    from app.services.constitution import with_constitution
    from app.services.agent_manager import EXECUTE_SYSTEM, _brief_args
    from app.services.project_context import build_project_context

    task = params.get("task", "")
    if not task:
        return "❌ 没有具体任务内容"
    # 注入本项目的核心档+参考资料
    ctx = await build_project_context(project_id, task, task)
    full_task = ctx + f"\n## 当前要做的事\n{task}" if ctx else task
    if history:
        full_task = f"对话背景：\n{history}\n\n──────\n" + full_task

    result = await run_agent_loop(full_task, system=with_constitution(EXECUTE_SYSTEM), project_id=project_id)
    text = result["response"]
    steps = result.get("steps", [])
    if steps:
        lines = ["", "──────────", f"🔧 执行过程（调用了 {len(steps)} 次工具）："]
        for i, s in enumerate(steps, 1):
            lines.append(f"{i}. {s['tool']}({_brief_args(s['args'])})")
        text += "\n".join(lines)
    text += f"\n\n（本次执行花费 ${result['cost']}）"
    return text


async def _action_create_task(project_id: str, params: dict) -> str:
    title = params.get("title", "").strip()
    if not title:
        return "❌ 任务标题为空"
    task_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO tasks (id, project_id, title, description, assignee, ai_model, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (task_id, project_id, title, params.get("description", ""),
             params.get("assignee", "ai"), params.get("ai_model", "auto"),
             int(params.get("priority", 3)), now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"✅ 已新建任务「{title}」"


async def _action_start_auto(project_id: str) -> str:
    from app.services.auto_runner import start_auto, is_running
    if is_running(project_id):
        return "项目已在自动执行中"
    if start_auto(project_id):
        return "✅ 已启动自动执行，worker 开始并行干活"
    return "❌ 启动失败"


async def _action_stop_auto(project_id: str) -> str:
    from app.services.auto_runner import stop_auto
    stop_auto(project_id)
    return "✅ 已发送停止信号"
