"""设置页保存时，routes 必须合并、不能重建。

真机踩到：用户配好 media_script→claude，后来去设置页填了个 API Key，
一保存 media_script 就被抹掉，写稿悄悄退回 deepseek——稿子质量掉了还查不出原因。
这是「静默丢配置」，人不会收到任何提示，所以必须有测试兜着。
"""
import json

import pytest

from app.api import settings as st


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(st, "CONFIG_FILE", f)
    return f


def _save(**kw):
    """按表单默认值调保存，只覆盖传进来的字段。

    必须把每个参数都显式传全——直接调端点函数时，没传的参数拿到的是
    FastAPI 的 Form(...) 对象本身，会一路写进 json.dump 然后炸。
    """
    import asyncio
    args = dict(
        anthropic_api_key="", openai_api_key="", deepseek_api_key="",
        qwen_api_key="", default_ai_model="claude",
        fallback_1="claude", fallback_2="openai",
        fallback_3="deepseek", fallback_4="qwen",
        serverchan_key="", pushplus_token="", feishu_webhook="",
        route_code="auto", route_writing="auto", route_analysis="auto",
        route_review="auto", route_vision="auto", route_media_script="auto")
    args.update(kw)
    return asyncio.run(st.settings_save(**args))


def test_unlisted_route_survives_a_save(cfg):
    """表单上没有的路由键，保存后必须还在——这就是那个 bug。"""
    cfg.write_text(json.dumps({
        "routes": {"media_script": "claude", "media_topic": "qwen"}
    }, ensure_ascii=False), encoding="utf-8")

    _save()

    routes = json.loads(cfg.read_text(encoding="utf-8"))["routes"]
    assert routes["media_topic"] == "qwen", "表单没列的路由被保存抹掉了"


def test_form_fields_overwrite(cfg):
    cfg.write_text(json.dumps({"routes": {"code": "deepseek"}},
                              ensure_ascii=False), encoding="utf-8")
    _save(route_code="claude")
    routes = json.loads(cfg.read_text(encoding="utf-8"))["routes"]
    assert routes["code"] == "claude"


def test_media_script_is_on_the_form_now(cfg):
    """写稿路由以前不在表单里，只能改文件、改完还会被下次保存抹掉。"""
    _save(route_media_script="claude")
    routes = json.loads(cfg.read_text(encoding="utf-8"))["routes"]
    assert routes["media_script"] == "claude"


def test_save_keeps_unrelated_top_level_keys(cfg):
    """users / session_secret 这些不属于本表单的顶层字段也不能丢。"""
    cfg.write_text(json.dumps({
        "users": {"gaga": "hash"}, "session_secret": "s3cret",
        # media_topic 故意选一个**没上表单**的键——上了表单的（如 media_script）
        # 被表单值覆盖是正确行为，不该拿来验这条。
        "routes": {"media_topic": "qwen"},
    }, ensure_ascii=False), encoding="utf-8")

    _save()

    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["users"] == {"gaga": "hash"}
    assert saved["session_secret"] == "s3cret"
    assert saved["routes"]["media_topic"] == "qwen"
