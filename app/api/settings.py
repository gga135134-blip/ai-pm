import json
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.config import BASE_DIR

router = APIRouter()
CONFIG_FILE = BASE_DIR / "data" / "settings.json"


def load_settings() -> dict:
    defaults = {
        "anthropic_api_key": "",
        "openai_api_key": "",
        "deepseek_api_key": "",
        "qwen_api_key": "",
        "default_ai_model": "claude",
        "fallback_order": ["claude", "openai", "deepseek", "qwen"],
        "serverchan_key": "",
        "pushplus_token": "",
        "routes": {"code": "auto", "writing": "auto", "analysis": "auto", "review": "auto"},
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            defaults.update(saved)
    return defaults


def save_settings(data: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = load_settings()
    return request.app.state.templates.TemplateResponse(
        request, "settings.html", {"request": request, "config": config}
    )


@router.post("/settings")
async def settings_save(
    anthropic_api_key: str = Form(""),
    openai_api_key: str = Form(""),
    deepseek_api_key: str = Form(""),
    qwen_api_key: str = Form(""),
    default_ai_model: str = Form("claude"),
    fallback_1: str = Form("claude"),
    fallback_2: str = Form("openai"),
    fallback_3: str = Form("deepseek"),
    fallback_4: str = Form("qwen"),
    serverchan_key: str = Form(""),
    pushplus_token: str = Form(""),
    route_code: str = Form("auto"),
    route_writing: str = Form("auto"),
    route_analysis: str = Form("auto"),
    route_review: str = Form("auto"),
):
    fallback_order = []
    for m in [fallback_1, fallback_2, fallback_3, fallback_4]:
        if m and m not in fallback_order:
            fallback_order.append(m)

    data = {
        "anthropic_api_key": anthropic_api_key,
        "openai_api_key": openai_api_key,
        "deepseek_api_key": deepseek_api_key,
        "qwen_api_key": qwen_api_key,
        "default_ai_model": default_ai_model,
        "fallback_order": fallback_order,
        "serverchan_key": serverchan_key,
        "pushplus_token": pushplus_token,
        "routes": {
            "code": route_code,
            "writing": route_writing,
            "analysis": route_analysis,
            "review": route_review,
        },
    }
    save_settings(data)
    return RedirectResponse("/settings", status_code=303)
