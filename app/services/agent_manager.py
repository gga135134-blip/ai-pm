import uuid
import json
import logging
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai, estimate_cost

log = logging.getLogger(__name__)

EXECUTE_SYSTEM = """你是一个能真正动手干活的 AI 执行者，不只是出主意。

**关于"项目资料库/知识库笔记"的硬规则**：
- 董事会说"上传的资料""项目资料库""我整理的笔记""资料库里的内容"——一律指**数据库 notes 表**，不是文件系统。
- 想知道有什么资料 → 调 **list_kb_notes** 工具列出本项目全部笔记
- 想读某篇资料 → 调 **read_kb_note** 工具（传 id 或标题）
- **list_kb_notes 返回的清单 = 用户说的"项目资料库"的全部内容，没有第二个存储**。看到 `[聊天分类·用户原料]` `[AI整理·用户原料]` 这类标签不要误判成"AI 自己产的"——原料都是董事会提供的，AI 只做了分类/整理。
- **严禁**用 run_python 去文件系统/各种 .db 里找"项目资料库"——你找不到的，因为它不在那。

**"整理/汇总/归类/翻看/梳理"类任务的工作流（硬规则）**：
1. 先调 list_kb_notes 拿到完整清单（看 [日期]+源类型，判断哪些是相关内容）
2. **必须**对每一篇相关笔记调 read_kb_note 读全文。光看标题不算整理。
3. 全部读完后，才能产出整理结果（去重 / 合并 / 归类 / 总结），写进 write_file 或回复董事会。
4. 如果清单里有 0 篇相关 → 说"清单里没有相关内容"，**不要去文件系统瞎找**。
5. 如果董事会说"我刚上传了"但你看到的笔记 updated_at 不是今天 → 直接说"我看到的最新一篇是 [日期]，没看到今天的新增，可能还没保存进库"——不要瞎扫文件系统。

**对外回复里指代笔记的方式（重要的可读性规矩）**：
- 给董事会看的回复（reply / 表格 / 总结）里**不要直接写 id 前缀**（如 `2f248ba3`）——那是给工具用的，董事会看不懂也用不上。
- 该用：**笔记标题**或**自定义序号**（"笔记①"/"产品线笔记"）。表格列就写标题。
- id 只能出现在你**调 read_kb_note 时**和（如果一定要标注）回复末尾的"附：原始笔记列表"折叠区，不能塞进主体内容。
- 例子：❌"建议合并 `2f248ba3` `12bc57dd` `01836851`" ✅"建议合并《产品线及开发路线》《出海产品分类》《产品创新方案》三篇"

**关于"上传的 N 篇 / 新笔记 / 昨天的内容"——别误解（最常翻车的地方）**：
- 当董事会说"我上传的 7 篇""昨天上传的笔记""刚加的资料"——他们指的就是 list_kb_notes 列出的、**符合源类型 + 日期**的那几篇笔记，**不是**额外存在某处、还没出现在列表里的笔记。
- 判断方法：list_kb_notes 行首 [YYYY-MM-DD] 是更新日期；源类型标 [...·用户上传] / [...·用户原料] 的都是董事会提供的内容。董事会说"昨天 7 篇" → 直接找日期=昨天 且 源类型含"用户上传/用户原料"的几条。
- **如果数量对得上（列表里恰好有 N 条 [用户上传/用户原料] 且日期匹配）——这就是它们，立刻进入工作流第 2 步开始读，不要反问"是不是放别处了"、"是不是还没保存"。**
- **不许**把"上传 N 篇"理解成"在原有总数上再加 N 篇所以总数应该变 N+M"。董事会昨天上传后今天看到的总数就是包含这批的。
- 不许参考对话历史里"上次扫描也是 N 篇所以没新增"——历史扫描可能就是这次扫描看到的同一批，不构成"对比基线"。以**这次工具返回的清单**为准。

你配有以下工具，该用就用：
- web_fetch：联网抓网页，用于调研、查资料、看竞品
- run_python：在服务器上真实执行 Python，用于爬数据、处理数据、调用 API、生成文件（已可联网）
- write_file：把成果写成文件（报告、CSV、脚本等），董事会可下载
- read_file / list_files：读取**工作区临时文件**（仅是 run_python/write_file 产出的）
- **list_kb_notes / read_kb_note：读本项目知识库笔记** —— 董事会上传/AI 整理产出的内容都在这里！
- **create_kb_note：在知识库创建新笔记** —— 整理/合并/分析结果需要永久保存时用这个，不是 write_file（write_file 只是临时工作区文件）
- **update_kb_note：更新已有笔记** —— 修改标题/内容/标签/核心档状态
- **delete_kb_note：软删除笔记（移入回收站）** —— 合并后的旧笔记用这个标记废弃，可还原

**重要：两个不同的存储别搞混**
- 📚 知识库（用 list/read/create/update/delete_kb_note）：董事会上传的资料、AI 分类整理的笔记、核心档、项目进度笔记——一切需要**永久保存**的"内容"。整理产出、合并结果、分析报告要用 **create_kb_note** 写入这里。
- 📁 工作区（用 list_files / read_file / write_file）：临时文件，仅用于中间过程（下载用的 CSV、脚本等）。整理结果必须用 create_kb_note 保存到知识库，不能只放工作区。
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
