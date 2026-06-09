from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.database import init_db
from app.api import dashboard, projects, tasks, settings, agents, notes, chat

app = FastAPI(title="AI 项目管理平台")

APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
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
