import datetime as dt
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


async def send_daily_study_reminder(db) -> bool:
    """构建今日学习提醒消息并通过已配置渠道推送；成功后记录 reminder_last_sent。"""
    from app.services.study_engine import _settings, get_or_build_today_plan

    s = await _settings(db)
    exam = dt.date.fromisoformat(s["exam_date"])
    days_left = (exam - dt.date.today()).days

    items = await get_or_build_today_plan(db)
    reviews = [i for i in items if i["kind"] == "review"]
    news = [i for i in items if i["kind"] == "new"]

    cur = await db.execute(
        "SELECT subject, COUNT(*) c FROM study_review WHERE stage>=3 GROUP BY subject")
    mastered = {r["subject"]: r["c"] for r in await cur.fetchall()}

    title = f"📚 今日学习提醒 | 距考试 {days_left} 天"
    lines = [
        f"**距考试还有 {days_left} 天**",
        "",
        f"📋 今日任务：复习 **{len(reviews)}** 项 · 新学 **{len(news)}** 项",
    ]
    if mastered:
        lines += ["", "**已掌握（stage≥3）：**"]
        for subj, cnt in mastered.items():
            lines.append(f"- {subj}：{cnt} 个考点")
    if days_left <= 30:
        lines += ["", "⚡ **冲刺阶段，全力以赴！**"]
    elif days_left <= 60:
        lines += ["", "💪 **稳步推进，坚持就是胜利！**"]
    else:
        lines += ["", "🌱 **积少成多，每天进步一点！**"]

    result = await notify_wechat(title, "\n".join(lines))
    if result["sent"]:
        await db.execute(
            "UPDATE study_settings SET reminder_last_sent=? WHERE id=1",
            (dt.date.today().isoformat(),))
        await db.commit()
        log.info("Study reminder sent via %s", result["channel"])
    else:
        log.warning("Study reminder failed: %s", result["error"])
    return result["sent"]


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
