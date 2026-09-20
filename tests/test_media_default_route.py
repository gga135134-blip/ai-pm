"""自媒体默认模型（routes.media_default）：管所有 media_* 任务的兜底。

自媒体是独立项目，它的模型在自媒体页里设。除了写稿/审稿单独指定，其余十几个
media_* 任务（选题/文案/复盘…）走一个「自媒体默认模型」总开关；没设或选「跟随全局」
就回落到 ai-pm 全局默认（向后兼容，行为不变）。非 media_ 任务不受它影响。
"""
import json

import pytest

from app.services import ai_router


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(ai_router, "CONFIG_FILE", f)
    return f


def _write(cfg, data):
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_media_task_uses_media_default_when_set(cfg):
    _write(cfg, {"default_ai_model": "deepseek", "routes": {"media_default": "claude"}})
    assert ai_router.get_model_for_task("media_topic") == "claude"


def test_media_specific_route_beats_media_default(cfg):
    _write(cfg, {"default_ai_model": "deepseek",
                 "routes": {"media_default": "claude", "media_script": "qwen"}})
    assert ai_router.get_model_for_task("media_script") == "qwen"


def test_media_default_auto_falls_back_to_global(cfg):
    _write(cfg, {"default_ai_model": "deepseek", "routes": {"media_default": "auto"}})
    assert ai_router.get_model_for_task("media_topic") == "deepseek"


def test_media_default_absent_falls_back_to_global(cfg):
    _write(cfg, {"default_ai_model": "deepseek", "routes": {}})
    assert ai_router.get_model_for_task("media_topic") == "deepseek"


def test_non_media_task_ignores_media_default(cfg):
    _write(cfg, {"default_ai_model": "deepseek", "routes": {"media_default": "claude"}})
    assert ai_router.get_model_for_task("writing") == "deepseek"
