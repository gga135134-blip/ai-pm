"""OpenClaw Agent 客户端 —— 把任务发给本机 OpenClaw 执行，拿回结果。"""
import httpx
from app.config import settings

_TIMEOUT = httpx.Timeout(300.0, connect=10.0)  # OpenClaw 执行任务可能需要几分钟


async def ask_openclaw(prompt: str, system: str = "") -> dict:
    """
    调用 OpenClaw /v1/chat/completions 接口，让 OpenClaw 用它的工具集执行任务。
    返回与 ai_router.ask_ai 同结构的 dict：response / model / tokens / cost / steps。
    """
    base = settings.openclaw_base_url.rstrip("/")
    token = settings.openclaw_token
    if not token:
        raise RuntimeError("OPENCLAW_TOKEN 未配置，请在 .env 里加上 OPENCLAW_TOKEN=...")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"model": "openclaw/default", "messages": messages},
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return {
        "response": content,
        "model": "openclaw/default",
        "tokens": usage.get("total_tokens", 0),
        "cost": 0.0,   # 本机自托管，无 API 费用
        "steps": [],
    }


def is_configured() -> bool:
    return bool(settings.openclaw_token and settings.openclaw_base_url)
