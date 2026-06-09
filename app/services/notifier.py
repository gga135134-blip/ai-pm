import httpx
from app.api.settings import load_settings


async def notify_wechat(title: str, content: str = ""):
    config = load_settings()

    if config.get("serverchan_key"):
        await _send_serverchan(config["serverchan_key"], title, content)
    elif config.get("pushplus_token"):
        await _send_pushplus(config["pushplus_token"], title, content)


async def _send_serverchan(key: str, title: str, content: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=10,
        )


async def _send_pushplus(token: str, title: str, content: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content},
            timeout=10,
        )
