import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "aipm.db"


class Settings(BaseSettings):
    app_name: str = "AI 项目管理平台"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    default_ai_model: str = "claude"

    # 通知配置
    serverchan_key: str = ""
    pushplus_token: str = ""

    # OpenClaw Agent
    openclaw_base_url: str = "http://127.0.0.1:18791"
    openclaw_token: str = ""

    # Feishu (飞书)
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"


settings = Settings()
