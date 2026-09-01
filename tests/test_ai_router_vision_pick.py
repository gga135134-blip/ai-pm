"""识图选哪家：设置页显式指定 > 默认顺序（千问最便宜排第一），失败往后退。"""
import asyncio

from app.services import ai_router


def _patch(monkeypatch, config, fail=()):
    """把三家识图调用换成 stub，返回记录调用顺序的列表。"""
    called = []

    def mk(name):
        async def _stub(prompt, images, system_prompt, cfg):
            called.append(name)
            if name in fail:
                raise RuntimeError(name + " 挂了")
            return {"response": "看到了", "model": name, "tokens": 1, "cost": 0.0}
        return _stub

    monkeypatch.setattr(ai_router, "_load_config", lambda: config)
    monkeypatch.setattr(ai_router, "_call_qwen_vision", mk("qwen"))
    monkeypatch.setattr(ai_router, "_call_claude_vision", mk("claude"))
    monkeypatch.setattr(ai_router, "_call_openai_vision", mk("openai"))
    return called


def _ask():
    return asyncio.run(ai_router.ask_ai_vision("看图", [{"media_type": "image/png", "data": "x"}]))


ALL_KEYS = {"qwen_api_key": "q", "anthropic_api_key": "a", "openai_api_key": "o"}


def test_auto_prefers_qwen(monkeypatch):
    """三家都配了 Key 时，auto 走千问——最便宜，跟 get_model_for_task 同一个原则。"""
    called = _patch(monkeypatch, dict(ALL_KEYS))
    assert _ask()["model"] == "qwen"
    assert called == ["qwen"]


def test_explicit_route_wins(monkeypatch):
    """设置页指定了就听它的，哪怕千问也配了 Key。"""
    called = _patch(monkeypatch, dict(ALL_KEYS, routes={"vision": "claude"}))
    assert _ask()["model"] == "claude"
    assert called == ["claude"]


def test_explicit_route_without_key_falls_back(monkeypatch):
    """指定了 openai 但没配它的 Key —— 别报死，退回默认顺序。"""
    cfg = {"qwen_api_key": "q", "routes": {"vision": "openai"}}
    called = _patch(monkeypatch, cfg)
    assert _ask()["model"] == "qwen"
    assert called == ["qwen"]


def test_falls_through_on_failure(monkeypatch):
    """千问调用挂了就往后退到 Claude，不把整次识图判死。"""
    called = _patch(monkeypatch, dict(ALL_KEYS), fail=("qwen",))
    assert _ask()["model"] == "claude"
    assert called == ["qwen", "claude"]


def test_all_fail_reports_last_error(monkeypatch):
    called = _patch(monkeypatch, dict(ALL_KEYS), fail=("qwen", "claude", "openai"))
    out = _ask()
    assert out["response"].startswith("[错误]")
    assert "OpenAI" in out["response"]
    assert called == ["qwen", "claude", "openai"]
    # 三家都配了 key，就没有「去配谁」这句提示
    assert "还没配" not in out["response"]


def test_failure_hints_at_unconfigured_providers(monkeypatch):
    """真机踩过的场景：千问没配 key → 只剩 Claude → Claude 被中转 403 挡下。
    光报「Claude 识图失败 403」看不懂，得直说还有哪家没配。"""
    called = _patch(monkeypatch, {"anthropic_api_key": "a"}, fail=("claude",))
    out = _ask()
    assert called == ["claude"]
    assert "Claude 识图调用失败" in out["response"]
    assert "还没配" in out["response"]
    assert "通义千问" in out["response"]
    assert "OpenAI" in out["response"]


def test_no_key_at_all(monkeypatch):
    """一家都没配（只有不支持识图的 deepseek）——给人话，别抛异常。"""
    called = _patch(monkeypatch, {"deepseek_api_key": "d"})
    out = _ask()
    assert out["model"] == "none"
    assert "API Key" in out["response"]
    assert called == []


def test_only_claude_configured(monkeypatch):
    called = _patch(monkeypatch, {"anthropic_api_key": "a"})
    assert _ask()["model"] == "claude"
    assert called == ["claude"]
