"""自媒体模型设置页 GET /media/settings 渲染冒烟。

自媒体独立管模型的页面：写稿模型 / 自媒体默认模型 / 审稿策略 + 真实模型名。
兜 Jinja 渲染坑，确认能 200、三块都在、显示真名、说明 API Key 在全局配。
"""
import base64
import json

from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_or_create_session_secret


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


def test_media_settings_renders_ok():
    r = _client().get("/media/settings")
    assert r.status_code == 200


def test_media_settings_has_three_blocks():
    html = _client().get("/media/settings").text
    for label in ["写稿模型", "自媒体默认模型", "审稿"]:
        assert label in html, f"缺块：{label}"


def test_media_settings_shows_real_model_names_and_apikey_note():
    html = _client().get("/media/settings").text
    assert "claude-opus-5" in html
    assert "deepseek-v4-flash" in html
    assert "API Key" in html and "全局" in html, "没提示 API Key 在全局配"
    assert "Claude Sonnet" not in html
