"""数据采集：截图识图 / 手填 / 归一化。

采集降级链（spec §3.4）：自动抓取 → 失败 → 截图识图 → 仍失败 → 手动填表单。
一期实现后两条；自动抓取因平台反爬不稳定，二期再评估。
"""
import base64
import logging
import re
import uuid

from app.services.ai_router import ask_ai_vision
from app.services.media_context import extract_json

log = logging.getLogger(__name__)

METRIC_FIELDS = ["views", "likes", "comments", "shares", "new_fans"]

# AI 有时用中文键名返回，做个映射
_ALIASES = {
    "播放": "views", "播放量": "views", "观看": "views",
    "点赞": "likes", "赞": "likes",
    "评论": "comments", "评论数": "comments",
    "转发": "shares", "分享": "shares",
    "涨粉": "new_fans", "新增粉丝": "new_fans", "粉丝": "new_fans",
}

_UNITS = {"万": 10_000, "w": 10_000, "W": 10_000,
          "k": 1_000, "K": 1_000,
          "亿": 100_000_000}


def _to_int(value) -> int:
    """把平台后台的各种数字写法转成整数。转不了返回 0。

    平台普遍显示"1.2万"而不是 12000，识图 AI 会原样返回，必须在这里统一。
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))

    s = str(value).strip().replace(",", "").replace("+", "")
    if not s:
        return 0

    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([万wWkK亿])?$", s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2)
    if unit:
        num *= _UNITS[unit]
    return max(0, int(num))


def normalize_metrics(raw: dict) -> dict:
    """把 AI 或表单给的原始字典，归一化成 5 个非负整数字段。

    纯函数：AI 输出脏数据是常态，归一化必须可测试、可预期。
    """
    src = dict(raw or {})
    for cn, en in _ALIASES.items():
        if cn in src and en not in src:
            src[en] = src[cn]
    return {f: _to_int(src.get(f)) for f in METRIC_FIELDS}


VISION_PROMPT = """这是自媒体平台后台的数据截图。请读出这条内容的数据。

只输出 JSON，不要任何解释：
{"views":播放量,"likes":点赞,"comments":评论,"shares":转发,"new_fans":涨粉}

规则：
- 数字保留截图上的原始写法（如"1.2万"就写"1.2万"），不要自己换算。
- 截图上没有的项填 0。
- 看不清就填 0，不要猜。"""


async def recognize_screenshot(image_bytes: bytes, media_type: str) -> dict:
    """识别后台截图里的数据。DeepSeek 不支持识图，ask_ai_vision 会自动选模型。"""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    result = await ask_ai_vision(
        VISION_PROMPT, [{"media_type": media_type, "data": b64}])
    resp = result.get("response", "")
    if resp.startswith("[错误]"):
        return {"ok": False, "error": resp, "data": {},
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "error": "识别结果无法解析，请手动填写", "data": {},
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    return {"ok": True, "data": normalize_metrics(obj), "error": "",
            "cost": result.get("cost", 0), "model": result.get("model", "")}


async def save_metrics(db, publish_id: str, data: dict, collected_by: str) -> str:
    """写一条数据快照。每次采集都是新行，保留增长曲线。"""
    m = normalize_metrics(data)
    mid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_metrics "
        "(id,publish_id,views,likes,comments,shares,new_fans,collected_by) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (mid, publish_id, m["views"], m["likes"], m["comments"],
         m["shares"], m["new_fans"], collected_by))
    await db.commit()
    return mid
