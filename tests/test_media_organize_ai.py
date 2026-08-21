"""organize_content：一次调用出摘要+排版。"""
import asyncio
from app.services import media_ai


def test_organize_returns_summary_and_formatted(monkeypatch):
    async def fake_ai(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": '{"summary":"讲员工用AI泄密的防范","formatted":"清理后的正文"}',
                "model": "x", "tokens": 5, "cost": 0.0}
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai)

    async def go():
        r = await media_ai.organize_content("很长的原始正文……")
        assert r["ok"] and r["summary"].startswith("讲员工") and r["formatted"] == "清理后的正文"
    asyncio.run(go())


def test_organize_empty_script():
    async def go():
        r = await media_ai.organize_content("   ")
        assert r["ok"] is False
    asyncio.run(go())
