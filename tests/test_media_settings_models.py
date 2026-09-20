"""自媒体模型设置端点 POST /media/settings/models。

自媒体独立管自己的模型：写稿(media_script) + 自媒体默认(media_default) + 审稿策略。
routes 必须合并不能重建（宪法：保存抹掉未列键的老坑）。
"""
import asyncio
import json

import pytest

from app.api import settings as st
import app.api.media as media_api


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(st, "CONFIG_FILE", f)
    return f


def test_saves_media_routes_and_strategy(cfg):
    asyncio.run(media_api.save_media_models(
        route_media_script="claude", route_media_default="qwen", strategy="swap_model"))
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["routes"]["media_script"] == "claude"
    assert saved["routes"]["media_default"] == "qwen"
    assert saved["media_review_strategy"] == "swap_model"


def test_merge_keeps_other_routes(cfg):
    cfg.write_text(json.dumps({"routes": {"code": "claude", "media_topic": "qwen"}},
                              ensure_ascii=False), encoding="utf-8")
    asyncio.run(media_api.save_media_models(
        route_media_script="auto", route_media_default="auto", strategy="layered"))
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["routes"]["code"] == "claude", "合并没保住别的路由"
    assert saved["routes"]["media_topic"] == "qwen"


def test_bad_strategy_defaults_layered(cfg):
    asyncio.run(media_api.save_media_models(
        route_media_script="auto", route_media_default="auto", strategy="garbage"))
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["media_review_strategy"] == "layered"
