"""设置页「当前实际生效」列的模型解析器。

设置页要显示每个任务**实际会跑哪个模型的真实名字**（不再写 "Claude Sonnet"
这种对不上的假名）。这些解析器复用现有路由逻辑，只负责把结果映射成真实模型名，
供 GET /settings 渲染。三类任务：
- 文本任务（写稿/文案/…）走 ai_router.get_model_for_task
- 识图走 ai_router 的识图链（默认千问）
- 工具类（助手/执行）走 agent_tools._get_tool_client 的 key 顺序（写死，Claude 进不来）
"""
import json

import pytest

from app.services import ai_router
from app.services import agent_tools


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(ai_router, "CONFIG_FILE", f)
    return f


def _write(cfg, data):
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── real_model_name：内部 key → 真实模型名 ──

def test_real_model_name_maps_claude_to_opus5():
    assert ai_router.real_model_name("claude") == "claude-opus-5"


def test_real_model_name_maps_deepseek_to_v4_flash():
    assert ai_router.real_model_name("deepseek") == "deepseek-v4-flash"


def test_real_model_name_passes_unknown_through():
    assert ai_router.real_model_name("something") == "something"


# ── effective_text_model：文本任务当前实际生效 ──

def test_effective_text_model_honors_route(cfg):
    _write(cfg, {"routes": {"media_script": "claude"}})
    got = ai_router.effective_text_model("media_script")
    assert got == {"key": "claude", "name": "claude-opus-5"}


def test_effective_text_model_falls_back_to_default(cfg):
    _write(cfg, {"default_ai_model": "deepseek", "routes": {"code": "auto"}})
    got = ai_router.effective_text_model("code")
    assert got == {"key": "deepseek", "name": "deepseek-v4-flash"}


# ── effective_vision_model：识图当前实际生效 ──

def test_effective_vision_model_auto_prefers_qwen(cfg):
    _write(cfg, {"qwen_api_key": "k", "routes": {"vision": "auto"}})
    got = ai_router.effective_vision_model()
    assert got == {"key": "qwen", "name": "qwen-vl-plus"}


def test_effective_vision_model_route_override(cfg):
    _write(cfg, {"qwen_api_key": "k", "anthropic_api_key": "k",
                 "routes": {"vision": "claude"}})
    got = ai_router.effective_vision_model()
    assert got == {"key": "claude", "name": "claude-opus-5"}


# ── effective_tool_model：工具类（助手/执行）当前实际生效（写死顺序）──

def test_effective_tool_model_prefers_deepseek(cfg):
    _write(cfg, {"deepseek_api_key": "k", "qwen_api_key": "k"})
    got = agent_tools.effective_tool_model()
    assert got == {"key": "deepseek", "name": "deepseek-chat"}


def test_effective_tool_model_none_when_no_key(cfg):
    _write(cfg, {})
    got = agent_tools.effective_tool_model()
    assert got == {"key": None, "name": None}
