"""飞书 → media_metrics 同步（媒体层）。

读飞书表 → 按 post_url(标题兜底) 匹配 media_publish → 写 metrics 快照。
匹配不上的持久化进 media_feishu_unmatched 让用户手动补。
"""
import json
import re
import uuid
from datetime import date

from app.services.feishu_client import list_bitable_records
from app.services.media_metrics import normalize_metrics, METRIC_FIELDS

_PUNCT = re.compile(r"[\s　\W_]+", re.UNICODE)


def norm_title(s: str) -> str:
    """归一化标题用于模糊匹配：去空白/标点/emoji，转小写。"""
    if not s:
        return ""
    return _PUNCT.sub("", str(s)).lower()


def _cell(fields: dict, col_name: str):
    """从飞书 fields 取一列的值。飞书文本列可能是 [{'text':..}] 结构，做个兼容。"""
    v = fields.get(col_name)
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v[0].get("text", "")
    return v


def map_feishu_row(fields: dict, field_map: dict) -> dict | None:
    """把一行飞书 fields 按映射提取。无 post_url 且无 title 返回 None。"""
    fm = (field_map or {}).get("fields") or {}
    post_url = str(_cell(fields, fm.get("post_url", "")) or "").strip()
    title = str(_cell(fields, fm.get("title", "")) or "").strip()
    if not post_url and not title:
        return None
    raw = {}
    missing = []
    for f in METRIC_FIELDS:
        col = fm.get(f)
        val = _cell(fields, col) if col else None
        if col and val not in (None, ""):
            raw[f] = val
        else:
            missing.append(f)  # 飞书没映射或没给值 → 标待手填
    return {"post_url": post_url, "title": title,
            "metrics": normalize_metrics(raw), "missing_fields": missing}
