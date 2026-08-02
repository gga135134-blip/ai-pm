import json
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from app.config import BASE_DIR
from app.api.auth import get_users, hash_password, verify_password
from app.services import feishu_client

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
        "feishu_webhook": "",
        "routes": {"code": "auto", "writing": "auto", "analysis": "auto", "review": "auto"},
        "feishu_media_map": {"app_token": "", "table_id": "", "fields": {}},
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            defaults.update(saved)
    return defaults


def save_settings(data: dict):
    # 合并写入，保留 users / session_secret 等不属于本表单的字段
    existing = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing.update(data)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from app.services.constitution import get_constitution
    config = load_settings()
    config["company_manual"] = get_constitution()
    users = list(get_users().keys())
    msg = request.query_params.get("msg", "")
    return request.app.state.templates.TemplateResponse(
        request, "settings.html", {"request": request, "config": config, "users": users, "msg": msg}
    )


@router.post("/settings/manual")
async def settings_save_manual(company_manual: str = Form("")):
    from app.services.constitution import save_constitution
    save_constitution(company_manual)
    return RedirectResponse("/settings", status_code=303)


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
    feishu_webhook: str = Form(""),
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
        "feishu_webhook": feishu_webhook,
        "routes": {
            "code": route_code,
            "writing": route_writing,
            "analysis": route_analysis,
            "review": route_review,
        },
    }
    save_settings(data)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/users/add")
async def user_add(request: Request, username: str = Form(...), password: str = Form(...), password2: str = Form(...)):
    if password != password2:
        return RedirectResponse("/settings?msg=两次密码不一致", status_code=303)
    if not username.strip():
        return RedirectResponse("/settings?msg=用户名不能为空", status_code=303)
    cfg = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    users = cfg.get("users", {})
    if username in users:
        return RedirectResponse(f"/settings?msg=用户 {username} 已存在", status_code=303)
    users[username] = hash_password(password)
    cfg["users"] = users
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return RedirectResponse(f"/settings?msg=已添加用户 {username}", status_code=303)


@router.post("/settings/users/change-password")
async def user_change_password(request: Request, username: str = Form(...),
                               old_password: str = Form(...), new_password: str = Form(...), new_password2: str = Form(...)):
    if new_password != new_password2:
        return RedirectResponse("/settings?msg=两次新密码不一致", status_code=303)
    if not verify_password(old_password, get_users().get(username, "")):
        return RedirectResponse("/settings?msg=原密码错误", status_code=303)
    cfg = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.setdefault("users", {})[username] = hash_password(new_password)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return RedirectResponse(f"/settings?msg={username} 密码已更新", status_code=303)


@router.post("/settings/users/delete")
async def user_delete(request: Request, username: str = Form(...)):
    current_user = request.session.get("user")
    if username == current_user:
        return RedirectResponse("/settings?msg=不能删除自己", status_code=303)
    cfg = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.get("users", {}).pop(username, None)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return RedirectResponse(f"/settings?msg=已删除用户 {username}", status_code=303)


@router.post("/settings/test-notify")
async def settings_test_notify():
    from app.services.notifier import notify_wechat
    result = await notify_wechat("AI-PM 测试通知", "如果你看到这条消息，说明通知渠道配置成功！")
    status = "ok" if result["sent"] else "fail"
    detail = result["channel"] if result["sent"] else result["error"]
    return RedirectResponse(f"/settings?notify_test={status}&detail={detail}", status_code=303)


@router.post("/settings/feishu")
async def save_feishu_config(request: Request,
                             app_token: str = Form(""),
                             table_id: str = Form(""),
                             f_post_url: str = Form(""),
                             f_title: str = Form(""),
                             f_views: str = Form(""),
                             f_likes: str = Form(""),
                             f_comments: str = Form(""),
                             f_shares: str = Form(""),
                             f_new_fans: str = Form(""),
                             f_snapshot_at: str = Form("")):
    cfg = load_settings()
    cfg["feishu_media_map"] = {
        "app_token": app_token.strip(),
        "table_id": table_id.strip(),
        "fields": {k: v.strip() for k, v in {
            "post_url": f_post_url, "title": f_title, "views": f_views,
            "likes": f_likes, "comments": f_comments, "shares": f_shares,
            "new_fans": f_new_fans, "snapshot_at": f_snapshot_at,
        }.items() if v.strip()},
    }
    save_settings(cfg)
    return RedirectResponse("/settings", status_code=302)


@router.post("/settings/feishu/test")
async def test_feishu(request: Request):
    cfg = load_settings().get("feishu_media_map") or {}
    app_token, table_id = cfg.get("app_token"), cfg.get("table_id")
    if not app_token or not table_id:
        return JSONResponse({"ok": False, "error": "请先填 app_token 和 table_id 并保存"})
    try:
        records = await feishu_client.list_bitable_records(app_token, table_id, page_size=1)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"连接失败: {e}"})
    columns = list(records[0]["fields"].keys()) if records else []
    return JSONResponse({"ok": True, "count": len(records), "columns": columns})
