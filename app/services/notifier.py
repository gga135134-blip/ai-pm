import logging
import httpx
from app.api.settings import load_settings

log = logging.getLogger(__name__)


async def notify_wechat(title: str, content: str = "") -> dict:
    """推送通知：按已配置的渠道依次尝试（Server酱 → PushPlus → 飞书）。
    返回 {"sent": bool, "channel": str, "error": str}"""
    config = load_settings()

    channels = []
    if config.get("serverchan_key"):
        channels.append(("serverchan", _send_serverchan, config["serverchan_key"]))
    if config.get("pushplus_token"):
        channels.append(("pushplus", _send_pushplus, config["pushplus_token"]))
    if config.get("feishu_webhook"):
        channels.append(("feishu", _send_feishu, config["feishu_webhook"]))

    if not channels:
        return {"sent": False, "channel": "", "error": "未配置任何通知渠道"}

    last_error = ""
    for name, sender, key in channels:
        try:
            await sender(key, title, content)
            return {"sent": True, "channel": name, "error": ""}
        except Exception as e:
            last_error = f"{name}: {e}"
            log.warning("Notify via %s failed: %s", name, e)
            continue

    return {"sent": False, "channel": "", "error": last_error}


async def _send_serverchan(key: str, title: str, content: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=10,
        )
        resp.raise_for_status()


async def _send_pushplus(token: str, title: str, content: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content},
            timeout=10,
        )
        resp.raise_for_status()


async def _send_feishu(webhook: str, title: str, content: str):
    """飞书自定义机器人 webhook"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            webhook,
            json={"msg_type": "text", "content": {"text": f"【{title}】\n{content}"}},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, None):
            raise RuntimeError(f"飞书返回错误: {data}")
