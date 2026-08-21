"""run_agent_loop 支持自定义工具集/分发/ctx（向后兼容）。"""
import asyncio
import app.services.agent_tools as at


class _FakeMsg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeTC:
    def __init__(self, name):
        self.id = "tc1"
        self.function = type("F", (), {"name": name, "arguments": "{}"})()
    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": "{}"}}


class _FakeResp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]
        self.usage = type("U", (), {"total_tokens": 1, "prompt_tokens": 1, "completion_tokens": 1})()


def test_custom_dispatch_receives_ctx(monkeypatch):
    calls = []

    class _FakeCompletions:
        def __init__(self):
            self.n = 0
        async def create(self, **kw):
            self.n += 1
            if self.n == 1:
                return _FakeResp(_FakeMsg("", [_FakeTC("my_tool")]))
            return _FakeResp(_FakeMsg("完成"))

    class _FakeClient:
        def __init__(self):
            self.chat = type("Ch", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(at, "_get_tool_client", lambda: (_FakeClient(), "fake", "deepseek"))

    async def my_dispatch(name, args, ctx):
        calls.append((name, ctx))
        return "ok"

    async def go():
        r = await at.run_agent_loop("hi", system="s",
                                    tool_schemas=[{"type": "function", "function": {"name": "my_tool", "parameters": {"type": "object", "properties": {}}}}],
                                    dispatch=my_dispatch, ctx="PERSONA_A")
        return r
    r = asyncio.run(go())
    assert calls == [("my_tool", "PERSONA_A")] and "完成" in r["response"]
