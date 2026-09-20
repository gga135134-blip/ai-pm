"""通知配置拆成独立端点 POST /settings/notify。

重排后「通知」是独立 tab、独立表单。必须拆端点——否则在「模型与路由」tab
保存主设置时，表单里没有通知字段，会把已存的通知配置**清空**（路线图记的
「重建抹掉未列字段」同一个坑）。所以：
- POST /settings/notify 存三个通知字段
- POST /settings 不再碰通知字段（保存模型/路由时通知必须原样保留）
"""
import asyncio
import json

import pytest

from app.api import settings as st


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(st, "CONFIG_FILE", f)
    return f


def _save_main(**kw):
    args = dict(
        anthropic_api_key="", openai_api_key="", deepseek_api_key="",
        qwen_api_key="", default_ai_model="claude",
        fallback_1="claude", fallback_2="openai",
        fallback_3="deepseek", fallback_4="qwen",
        route_code="auto", route_writing="auto", route_analysis="auto",
        route_review="auto", route_vision="auto")
    args.update(kw)
    return asyncio.run(st.settings_save(**args))


def test_notify_endpoint_saves_three_fields(cfg):
    asyncio.run(st.settings_save_notify(
        serverchan_key="SCT1", pushplus_token="PP1",
        feishu_webhook="https://hook"))
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["serverchan_key"] == "SCT1"
    assert saved["pushplus_token"] == "PP1"
    assert saved["feishu_webhook"] == "https://hook"


def test_main_save_does_not_wipe_notify(cfg):
    """在「模型与路由」tab 保存主设置，不能把通知配置清掉。"""
    cfg.write_text(json.dumps({
        "serverchan_key": "SCT1", "pushplus_token": "PP1",
        "feishu_webhook": "https://hook",
    }, ensure_ascii=False), encoding="utf-8")

    _save_main(route_code="claude")

    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["serverchan_key"] == "SCT1", "保存主设置把通知配置抹掉了"
    assert saved["pushplus_token"] == "PP1"
    assert saved["feishu_webhook"] == "https://hook"
    assert saved["routes"]["code"] == "claude"
