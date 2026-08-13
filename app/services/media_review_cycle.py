"""L2 周期复盘：飞轮的动力源。

近 N 条内容对比找规律、验上轮假设、提本轮假设 + 候选资产。
候选绝不自动写库 —— AI 提炼，人拍板 adopt 才入（沿用 L1 复盘/人设访谈同款哲学）。
"""
import json
import uuid

from app.services.ai_router import ask_ai
from app.services.media_context import extract_json, log_injection

L2_MIN_CONTENTS = 5

_METRIC_KEYS = ("views", "likes", "comments", "shares", "new_fans")


def _agg(content: dict) -> dict:
    """一条内容跨平台取各指标最大值（哪个平台爆了算哪个）。"""
    out = {}
    for k in _METRIC_KEYS:
        out[k] = max((int(p.get(k) or 0) for p in content.get("platforms") or []),
                     default=0)
    return out


def _median(nums: list) -> int:
    xs = sorted(int(n or 0) for n in nums)
    if not xs:
        return 0
    n = len(xs)
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) // 2


def summarize_metrics(contents: list) -> dict:
    """纯计算汇总：条数、各指标均值/中位、爆款/flop 计数（vs 批次中位 views）。"""
    aggs = [(c["id"], _agg(c)) for c in contents]
    n = len(aggs) or 1
    avg = {k: sum(a[k] for _, a in aggs) // n for k in _METRIC_KEYS}
    median = {k: _median([a[k] for _, a in aggs]) for k in _METRIC_KEYS}
    mv = median["views"]
    hit_ids = [cid for cid, a in aggs if mv and a["views"] >= 1.5 * mv]
    flop_ids = [cid for cid, a in aggs if mv and a["views"] <= 0.5 * mv]
    return {
        "content_count": len(aggs),
        "avg": avg, "median": median,
        "hit_count": len(hit_ids), "flop_count": len(flop_ids),
        "hit_content_ids": hit_ids, "flop_content_ids": flop_ids,
    }


async def _prev_cycle(db, persona_id: str):
    cur = await db.execute(
        "SELECT * FROM media_review_cycle WHERE persona_id=? AND level='L2' "
        "ORDER BY seq DESC LIMIT 1", (persona_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def _reviewed_ids(db, persona_id: str) -> set:
    cur = await db.execute(
        "SELECT content_ids FROM media_review_cycle WHERE persona_id=? "
        "AND level='L2'", (persona_id,))
    seen = set()
    for r in await cur.fetchall():
        try:
            seen.update(json.loads(r["content_ids"] or "[]"))
        except Exception:
            pass
    return seen


async def gather_cycle_contents(db, persona_id: str):
    """纳入内容 = 该 persona 已发+有数据 且 不在任何往轮 content_ids 里。

    返回 (contents, prev_cycle)。每条 content 带 platforms(list) + 聚合指标。
    """
    prev = await _prev_cycle(db, persona_id)
    reviewed = await _reviewed_ids(db, persona_id)

    cur = await db.execute(
        "SELECT DISTINCT c.id, c.title, c.puzzle, c.script "
        "FROM media_content c "
        "WHERE c.persona_id=? AND EXISTS ("
        "  SELECT 1 FROM media_publish p JOIN media_metrics m ON m.publish_id=p.id "
        "  WHERE p.content_id=c.id AND p.status='published')", (persona_id,))
    rows = [dict(r) for r in await cur.fetchall()]

    contents = []
    for c in rows:
        if c["id"] in reviewed:
            continue
        pcur = await db.execute(
            "SELECT a.platform, m.views, m.likes, m.comments, m.shares, m.new_fans "
            "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
            "LEFT JOIN media_metrics m ON m.id=("
            "  SELECT id FROM media_metrics WHERE publish_id=p.id "
            "  ORDER BY snapshot_at DESC LIMIT 1) "
            "WHERE p.content_id=? AND p.status='published'", (c["id"],))
        c["platforms"] = [dict(r) for r in await pcur.fetchall()]
        c.update(_agg(c))
        contents.append(c)
    return contents, prev
