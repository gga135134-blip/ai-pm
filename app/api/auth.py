import hashlib
import json
import os
import secrets
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.config import BASE_DIR

router = APIRouter()
_CFG = BASE_DIR / "data" / "settings.json"


def _load() -> dict:
    if _CFG.exists():
        with open(_CFG, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    _CFG.parent.mkdir(parents=True, exist_ok=True)
    with open(_CFG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def get_users() -> dict:
    return _load().get("users", {})


def ensure_default_users():
    cfg = _load()
    if not cfg.get("users"):
        cfg["users"] = {
            "admin": hash_password("admin123"),
            "colleague": hash_password("colleague123"),
        }
        _save(cfg)
        return True
    return False


def get_or_create_session_secret() -> str:
    cfg = _load()
    if not cfg.get("session_secret"):
        cfg["session_secret"] = secrets.token_hex(32)
        _save(cfg)
    return cfg["session_secret"]


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return request.app.state.templates.TemplateResponse(
        request, "login.html", {"request": request, "error": error}
    )


@router.post("/login")
async def do_login(request: Request, username: str = Form(...), password: str = Form(...)):
    users = get_users()
    stored = users.get(username.strip())
    if stored and verify_password(password, stored):
        request.session["user"] = username.strip()
        next_url = request.query_params.get("next", "/")
        return RedirectResponse(url=next_url, status_code=302)
    return request.app.state.templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "用户名或密码错误"}, status_code=401
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
