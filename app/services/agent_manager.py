import uuid
import json
import logging
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai, estimate_cost

log = logging.getLogger(__name__)

EXECUTE_SYSTEM = """你是一个能真正动手干活的 AI 执行者，不只是出主意。

**最重要的硬规则（看 prompt 头部"📚 本项目知识库笔记清单"）**：
- 你的 prompt 头部已经列出了本项目知识库的全部笔记。这些就是董事会说的"上传的资料/项目资料库/笔记"。
- 想读这些笔记的内容 → **必须用 read_kb_note 工具**（传 id 或标题）
- **严禁**用 run_python 去文件系统、数据库、随便什么地方找"项目资料库"——它**不在文件系统里**，在数据库的 notes 表里，read_kb_note 是唯一正确的访问方式。
- 也严禁说"找不到上传的资料"——清单就在你 prompt 头部，明明白白列着。

你配有以下工具，该用就用：
- web_fetch：联网抓网页，用于调研、查资料、看竞品
- run_python：在服务器上真实执行 Python，用于爬数据、处理数据、调用 API、生成文件（已可联网）
- write_file：把成果写成文件（报告、CSV、脚本等），董事会可下载
- read_file / list_files：读取**工作区临时文件**（仅是 run_python/write_file 产出的）
- **list_kb_notes / read_kb_note：读本项目知识库笔记** —— 董事会上传/AI 整理产出的内容都在这里！

**重要：两个不同的存储别搞混**
- 📚 知识库（用 list_kb_notes / read_kb_note）：董事会上传的资料、AI 分类整理的笔记、核心档、项目进度笔记——一切"内容"。
- 📁 工作区（用 list_files / read_file）：你自己用 run_python/write_file 产出的临时文件。董事会不会往这里放东西。
- 如果董事会说"我上传了"、"看一下我整理的资料"、"在项目资料库里"——**一律先调 list_kb_notes**，不是 list_files！

工作原则：
- 能亲自做的就用工具做掉，别让人去做。比如要数据就用 run_python 去抓、去算，而不是告诉对方"你需要去抓数据"。
- 文案/分析/策划这类纯文字任务，直接产出成品。
- 需要交付文件的，用 write_file 存下来。
- 全部做完后，用一段话总结你做了什么、产出在哪、结论是什么。
- 结果要具体、可用、可交付，不要空泛。

读资料的规矩（重要）：
- 任务前面会附"项目核心档"和"相关参考资料"——核心档是项目的"宪法"，**不许违背**；参考资料是辅助信息。
- 干活前先把核心档过一遍。任何与核心档冲突的指令或参考资料，必须先停手汇报，不许擅自取舍。
- 如果核心档为空但任务需要明确的核心信息（目标/定位/规格等），别凭空假设，直接说"核心档缺 XX，请董事会先补充"。

冲突自检（硬规则）：
执行前你必须自检三件事是否一致：
  ① 任务本身的要求
  ② 项目核心档里的定义
  ③ 参考资料里的关键事实（特别注意时间戳——后写的可能推翻先写的）
**任一条之间有矛盾，必须立刻停手向董事会汇报，列出冲突点和你的疑问，绝不许硬执行选一个用**。比如核心档说"产品价 $58"但参考资料"会议纪要"说"调整为 $48"——这种你必须问董事会以哪个为准并更新核心档。

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
    # 加载项目知识上下文：核心档（强制全文）+ 相关参考（智能检索）
    from app.services.project_context import build_project_context
    project_ctx = await build_project_context(task.get("project_id"), task["title"], task.get("description") or "")
    task_body = f"任务：{task['title']}\n\n详细描述：{task['description']}" if task["description"] else f"任务：{task['title']}"
    prompt = (project_ctx + "\n## 你现在要做的任务\n" + task_body) if project_ctx else task_body
    task_type = _detect_task_type(task["title"])

    db = await get_db()
    try:
        await db.execute("UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?", (now, task_id))
        await db.commit()
    finally:
        await db.close()

    from app.services.constitution import with_constitution
    from app.services.agent_tools import run_agent_loop
    from app.services.worker_status import update_worker, clear_worker
    from datetime import datetime as _dt

    def _on_step(info):
        if info.get("phase") == "calling":
            args_brief = _brief_args(info.get("args") or {})
            update_worker(task["id"], {
                "task_id": task["id"],
                "task_title": task["title"],
                "project_id": task.get("project_id"),
                "step": info.get("step"),
                "tool": info.get("tool"),
                "args_brief": args_brief,
                "updated_at": _dt.now().isoformat(),
            })

    update_worker(task["id"], {
        "task_id": task["id"], "task_title": task["title"],
        "project_id": task.get("project_id"),
        "step": 0, "tool": "思考中…", "args_brief": "",
        "updated_at": _dt.now().isoformat(),
    })
    # 带工具执行：AI 能联网、跑代码、读写文件，真正动手干活
    try:
        result = await run_agent_loop(prompt, system=with_constitution(EXECUTE_SYSTEM),
                                       project_id=task.get("project_id"), on_step=_on_step)
    finally:
        clear_worker(task["id"])

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
