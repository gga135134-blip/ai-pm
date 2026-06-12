"""自动执行引擎：项目启动自动模式后，AI 任务排队自动执行+审核，
预算护栏拦截超支，进度自动写入知识库，关键事件微信/飞书通知。"""
import asyncio
import logging
import uuid
from datetime import datetime
from app.database import get_db
from app.services.agent_manager import execute_task
from app.services.notifier import notify_wechat

log = logging.getLogger(__name__)

# project_id -> True 表示正在自动运行（协作式停止标志）
_running: dict[str, bool] = {}


def is_running(project_id: str) -> bool:
    return _running.get(project_id, False)


async def get_ai_spent(project_id: str) -> float:
    """项目累计 AI 费用（含拆解和执行）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT SUM(cost) as total FROM agent_runs WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ?)",
            (project_id,),
        )
        row = await cursor.fetchone()
        return round(row["total"] or 0, 6)
    finally:
        await db.close()


async def _write_progress_note(project: dict, task: dict, status_label: str, cost: float):
    """任务执行后自动往知识库写进度笔记（项目名/进度 文件夹）"""
    now = datetime.now().isoformat()
    result_snippet = (task.get("result") or "")[:1500]
    content = (
        f"任务：{task['title']}\n"
        f"状态：{status_label}\n"
        f"本次费用：${cost:.4f}\n\n"
        f"## 执行结果\n{result_snippet}"
    )
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, project_id, task_id, tags, source_type, folder, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '进度,自动执行', 'auto_progress', ?, ?, ?)""",
            (str(uuid.uuid4()), f"[{status_label}] {task['title']}", content,
             project["id"], task["id"], f"{project['name']}/进度", now, now),
        )
        await db.commit()
    finally:
        await db.close()


async def _get_next_task(project_id: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM tasks WHERE project_id = ? AND status = 'pending'
            AND assignee = 'ai' AND needs_human = 0
            ORDER BY priority ASC, created_at ASC LIMIT 1""",
            (project_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def _get_task(task_id: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return dict(await cursor.fetchone())
    finally:
        await db.close()


async def run_project_auto(project_id: str):
    """自动执行循环：逐个跑完项目里所有可自动执行的 AI 任务"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            return
        project = dict(row)
    finally:
        await db.close()

    _running[project_id] = True
    budget = project.get("ai_budget") or 0
    warned_80 = False
    done_count, blocked_count = 0, 0

    try:
        while _running.get(project_id):
            # ── 预算护栏 ──
            spent = await get_ai_spent(project_id)
            if budget > 0:
                if spent >= budget:
                    await notify_wechat(
                        f"⛔ 项目已暂停：{project['name']}",
                        f"AI 费用 ${spent:.4f} 已达预算上限 ${budget:.2f}。\n请检查后调整预算或手动继续。",
                    )
                    break
                if not warned_80 and spent >= budget * 0.8:
                    warned_80 = True
                    await notify_wechat(
                        f"⚠️ 预算预警：{project['name']}",
                        f"AI 费用已用 ${spent:.4f}（{spent / budget * 100:.0f}%），预算 ${budget:.2f}。",
                    )

            # ── 取下一个任务 ──
            task = await _get_next_task(project_id)
            if not task:
                break

            # ── 执行（含自动 AI 审核）──
            try:
                await execute_task(task["id"])
            except Exception as e:
                log.error("Auto execute task %s failed: %s", task["id"], e)
                now = datetime.now().isoformat()
                db = await get_db()
                try:
                    await db.execute(
                        "UPDATE tasks SET status = 'failed', updated_at = ? WHERE id = ?", (now, task["id"])
                    )
                    await db.commit()
                finally:
                    await db.close()
                await notify_wechat(f"❌ 任务执行出错：{task['title']}", f"项目：{project['name']}\n错误：{e}")
                continue

            # ── 记录进度到知识库 ──
            after = await _get_task(task["id"])
            spent_now = await get_ai_spent(project_id)
            task_cost = round(spent_now - spent, 6)
            if after["status"] == "done":
                done_count += 1
                await _write_progress_note(project, after, "完成", task_cost)
            elif after["status"] == "blocked":
                blocked_count += 1
                await _write_progress_note(project, after, "未过审-待人工", task_cost)
                await notify_wechat(
                    f"🔍 任务需人工审核：{after['title']}",
                    f"项目：{project['name']}\nAI 审核未通过，已标记等你处理。",
                )

            await asyncio.sleep(1)

        # ── 收尾汇总 ──
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT
                    SUM(CASE WHEN status = 'pending' AND needs_human = 1 THEN 1 ELSE 0 END) as need_human,
                    SUM(CASE WHEN status = 'pending' AND (assignee != 'ai' OR assignee IS NULL OR assignee = '') THEN 1 ELSE 0 END) as human_tasks
                FROM tasks WHERE project_id = ?""",
                (project_id,),
            )
            counts = dict(await cursor.fetchone())
        finally:
            await db.close()

        final_spent = await get_ai_spent(project_id)
        summary = (
            f"本轮完成 {done_count} 个任务，{blocked_count} 个待人工审核。\n"
            f"还有 {counts.get('need_human') or 0} 个任务等你拍板，"
            f"{counts.get('human_tasks') or 0} 个人工任务。\n"
            f"项目累计 AI 费用：${final_spent:.4f}"
        )
        if done_count or blocked_count:
            await notify_wechat(f"✅ 自动执行结束：{project['name']}", summary)
    finally:
        _running.pop(project_id, None)


def start_auto(project_id: str) -> bool:
    """启动自动执行（已在跑则忽略），返回是否新启动"""
    if _running.get(project_id):
        return False
    asyncio.create_task(run_project_auto(project_id))
    return True


def stop_auto(project_id: str):
    _running[project_id] = False
