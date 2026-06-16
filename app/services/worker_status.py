"""活跃 Worker 状态（内存）—— 作战室视图用，记录当前正在干活的 agent 在做什么。

不持久化，进程重启后清空。前端轮询 /projects/<id>/workers 获取实时状态。
"""
from threading import Lock

_workers: dict[str, dict] = {}  # task_id -> {task_id, task_title, project_id, step, tool, args_brief, updated_at}
_lock = Lock()


def update_worker(task_id: str, info: dict):
    with _lock:
        _workers[task_id] = info


def clear_worker(task_id: str):
    with _lock:
        _workers.pop(task_id, None)


def get_project_workers(project_id: str) -> list[dict]:
    with _lock:
        return [w for w in _workers.values() if w.get("project_id") == project_id]


def get_all_workers() -> list[dict]:
    with _lock:
        return list(_workers.values())
