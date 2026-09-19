"""设置页重排后的渲染冒烟测试。

设置页只有一条渲染路径（GET /settings → settings.html），历史上栽过几次
Jinja 渲染坑（注释里写标签字面量、片段单独渲染 500）。这条测试兜底：
页面能 200 渲染，5 个 tab 都在，模型名显示的是真实值、助手诚实标注写死。
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


def test_settings_renders_ok():
    r = _client().get("/settings")
    assert r.status_code == 200


def test_settings_has_five_tabs():
    html = _client().get("/settings").text
    for label in ["模型与路由", "账号", "通知", "数据源", "AI 宪法"]:
        assert label in html, f"缺 tab：{label}"


def test_settings_shows_real_model_names():
    html = _client().get("/settings").text
    assert "claude-opus-5" in html, "没显示真实模型名 claude-opus-5"
    assert "deepseek-v4-flash" in html, "没显示真实模型名 deepseek-v4-flash"
    # 旧假名不该再出现
    assert "Claude Sonnet" not in html, "还残留假名 Claude Sonnet"


def test_settings_assistant_marked_hardwired():
    html = _client().get("/settings").text
    assert "写死" in html, "助手行没诚实标注写死"
