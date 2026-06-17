import asyncio
import logging
import logging.handlers
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.config import BASE_DIR
from app.database import init_db
from app.api import dashboard, projects, tasks, settings, agents, notes, chat

app = FastAPI(title="AI 项目管理平台")

APP_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR = BASE_DIR / "data" / "workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/workspace", StaticFiles(directory=str(WORKSPACE_DIR)), name="workspace")
app.state.templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(settings.router)
app.include_router(agents.router)
app.include_router(notes.router)


@app.on_event("startup")
async def startup():
    await init_db()
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    asyncio.create_task(_periodic_heal_loop())


async def _periodic_heal_loop():
    """每 10 分钟检查一次全项目，把卡在 running 超过 30 分钟且无活跃 worker 的任务重置为 pending。"""
    from app.services.auto_runner import heal_stuck_running_all
    _log = logging.getLogger(__name__)
    await asyncio.sleep(60)  # 等应用完全启动后再开始
    while True:
        try:
            healed = await heal_stuck_running_all(threshold_minutes=30)
            if healed:
                _log.info("Periodic heal: reset %d stuck-running task(s) to pending", healed)
        except Exception:
            _log.exception("Periodic heal loop error")
        await asyncio.sleep(600)  # 10 分钟
