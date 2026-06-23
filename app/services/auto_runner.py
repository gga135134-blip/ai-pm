"""自动执行引擎：项目启动自动模式后，AI 任务排队自动执行+审核，
预算护栏拦截超支，进度自动写入知识库，关键事件微信/飞书通知。"""
import asyncio
import hashlib
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


async def board_signature(project_id: str) -> str:
    """看板指纹：所有任务的 id:status 摘要。状态一变指纹就变，前端据此判断看板是否过期。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, status FROM tasks WHERE project_id = ? ORDER BY id", (project_id,)
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()
    raw = ";".join(f"{r['id']}:{r['status']}" for r in rows)
    return hashlib.md5(raw.encode()).hexdigest()


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


async def _claim_next_task(project_id: str) -> dict | None:
    """原子地领取一个待执行任务并立刻标记为 running，防止并发重复领取"""
    from datetime import datetime as _dt
    now = _dt.now().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM tasks WHERE project_id = ? AND status = 'pending'
            AND assignee = 'ai' AND needs_human = 0
            ORDER BY priority ASC, created_at ASC LIMIT 1""",
            (project_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        task = dict(row)
        # 立刻改状态防止下一次循环重复领取
        await db.execute(
            "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ? AND status = 'pending'",
            (now, task["id"]),
        )
        await db.commit()
        return task
    finally:
        await db.close()


async def _get_task(task_id: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return dict(await cursor.fetchone())
    finally:
        await db.close()


MAX_PARALLEL_WORKERS = 3  # 一个项目最多同时跑几个 worker agent


async def _run_one_task(task: dict, project: dict, spent_before: float) -> dict:
    """跑一个任务的完整闭环：execute_task → 写进度笔记 → 返回结果。
    出错也要捕获返回，避免拖垮 gather。"""
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
        try:
            await notify_wechat(f"❌ 任务执行出错：{task['title']}", f"项目：{project['name']}\n错误：{e}")
        except Exception:
            pass
        return {"task": task, "status": "failed", "cost": 0}

    after = await _get_task(task["id"])
    spent_after = await get_ai_spent(project["id"])
    task_cost = round(spent_after - spent_before, 6)

    if after["status"] == "done":
        await _write_progress_note(project, after, "完成", task_cost)
    elif after["status"] == "blocked":
        await _write_progress_note(project, after, "未过审-待人工", task_cost)
        try:
            await notify_wechat(
                f"🔍 任务需人工审核：{after['title']}",
                f"项目：{project['name']}\nAI 审核未通过，已标记等你处理。",
            )
        except Exception:
            pass
    return {"task": after, "status": after["status"], "cost": task_cost}


async def _heal_stuck_running(project_id: str):
    """启动前自愈：把卡在 running 但内存里没活跃 worker 的任务重置回 pending。
    这通常是进程被中断/重启留下的孤儿任务。"""
    from app.services.worker_status import get_project_workers
    active_task_ids = {w.get("task_id") for w in get_project_workers(project_id)}
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, title FROM tasks WHERE project_id = ? AND status = 'running'",
            (project_id,),
        )
        stuck = [(r["id"], r["title"]) for r in await cursor.fetchall() if r["id"] not in active_task_ids]
        for tid, title in stuck:
            await db.execute(
                "UPDATE tasks SET status = 'pending', updated_at = ? WHERE id = ?",
                (now, tid),
            )
            log.info("Healed stuck-running task %s (%s) -> pending", tid, title)
        if stuck:
            await db.commit()
        return len(stuck)
    finally:
        await db.close()


async def heal_stuck_running_all(threshold_minutes: int = 30):
    """定期自愈：把全部项目中卡在 running 超过 threshold_minutes 分钟且无活跃 worker 的任务重置回 pending。"""
    from app.services.worker_status import get_project_workers
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(minutes=threshold_minutes)).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM projects")
        project_ids = [r["id"] for r in await cursor.fetchall()]
    finally:
        await db.close()

    total = 0
    for project_id in project_ids:
        active_task_ids = {w.get("task_id") for w in get_project_workers(project_id)}
        now = datetime.now().isoformat()
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT id, title FROM tasks WHERE project_id = ? AND status = 'running' AND updated_at < ?",
                (project_id, cutoff),
            )
            stuck = [(r["id"], r["title"]) for r in await cursor.fetchall() if r["id"] not in active_task_ids]
            for tid, title in stuck:
                await db.execute(
                    "UPDATE tasks SET status = 'pending', updated_at = ? WHERE id = ?",
                    (now, tid),
                )
                log.info("Periodic heal: task %s (%s) stuck-running→pending", tid, title)
            if stuck:
                await db.commit()
                total += len(stuck)
        finally:
            await db.close()
    return total


async def run_project_auto(project_id: str):
    """并行编排：最多 N 个 worker agent 同时干活，完成一个补一个。
    总 AI 角色由 auto_runner 扮演——派工、限预算、通知董事会。"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            return
        project = dict(row)
    finally:
        await db.close()

    # 启动前自愈：清理上次崩溃/重启留下的卡死 running 任务
    healed = await _heal_stuck_running(project_id)
    if healed:
        log.info("Project %s: healed %d stuck-running tasks before launch", project_id, healed)

    _running[project_id] = True
    budget = project.get("ai_budget") or 0
    warned_80 = False
    done_count, blocked_count, failed_count = 0, 0, 0
    workers: set[asyncio.Task] = set()

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

            # ── 派工：补满到 MAX_PARALLEL_WORKERS ──
            while _running.get(project_id) and len(workers) < MAX_PARALLEL_WORKERS:
                task = await _claim_next_task(project_id)
                if not task:
                    break
                w = asyncio.create_task(_run_one_task(task, project, spent))
                workers.add(w)
                log.info("Project %s: dispatched worker for task %s (running=%d)", project_id, task["id"], len(workers))

            if not workers:
                # 没活了，结束
                break

            # ── 等任一 worker 完成，统计、继续派活 ──
            done, workers = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for w in done:
                try:
                    r = w.result()
                    s = r.get("status")
                    if s == "done":
                        done_count += 1
                    elif s == "blocked":
                        blocked_count += 1
                    elif s == "failed":
                        failed_count += 1
                except Exception as e:
                    log.error("Worker task crashed: %s", e)
                    failed_count += 1

            await asyncio.sleep(0.5)

        # 收尾：等剩下的 worker 跑完（如果是被 stop 中断的也要等当前任务结束）
        if workers:
            done, _ = await asyncio.wait(workers)
            for w in done:
                try:
                    r = w.result()
                    s = r.get("status")
                    if s == "done":
                        done_count += 1
                    elif s == "blocked":
                        blocked_count += 1
                    elif s == "failed":
                        failed_count += 1
                except Exception:
                    failed_count += 1

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
            f"完成 {done_count} 个、待人工审核 {blocked_count} 个、失败 {failed_count} 个。\n"
            f"还有 {counts.get('need_human') or 0} 个任务等你拍板，"
            f"{counts.get('human_tasks') or 0} 个人工任务。\n"
            f"项目累计 AI 费用：${final_spent:.4f}"
        )
        if done_count or blocked_count or failed_count:
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
