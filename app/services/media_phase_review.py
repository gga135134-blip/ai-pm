"""L3 阶段复盘：人设进化层。

回看攒够的 L2 轮次 + 真实数据趋势，判断人设是否该进下一阶段、哪些 trait 该
归档/晋升。候选绝不自动应用 —— AI 提议，人点应用按钮才改人设。

阶段建议以退出信号的实际数据（账号真实已发数据算出）为主依据；数据没到参考线
就倾向 stay。只前进或原地，绝不倒退。
"""
import json
import uuid

from app.services.ai_router import ask_ai
from app.services.media_context import extract_json, log_injection

PHASE_ORDER = ["冷启动", "涨粉", "转化"]
L3_MIN_L2_CYCLES = 3
COLD_VIEWS_BASELINE = 3000
COLD_HIT_MIN = 1
GROWTH_CONFIRMED_MIN = 2


def _next_phase(phase_from: str):
    try:
        i = PHASE_ORDER.index(phase_from)
    except ValueError:
        return None
    return PHASE_ORDER[i + 1] if i + 1 < len(PHASE_ORDER) else None


def summarize_trend(l2: list) -> dict:
    series = []
    for c in l2:
        ms = c.get("metrics_summary") or {}
        avg = ms.get("avg") or {}
        series.append({
            "seq": c.get("seq"),
            "avg_views": avg.get("views", 0),
            "avg_new_fans": avg.get("new_fans", 0),
            "hit_count": ms.get("hit_count", 0),
        })
    return {"series": series}


def phase_exit_signals(phase_from: str, l2: list) -> list:
    if phase_from == "冷启动":
        cum_hit = sum((c.get("metrics_summary") or {}).get("hit_count", 0)
                      for c in l2)
        latest = (((l2[-1].get("metrics_summary") or {}).get("avg") or {})
                  .get("views", 0)) if l2 else 0
        return [
            {"signal": "累计爆款数", "value": cum_hit, "ref": COLD_HIT_MIN,
             "met": cum_hit >= COLD_HIT_MIN},
            {"signal": "最近一轮均值播放", "value": latest,
             "ref": COLD_VIEWS_BASELINE, "met": latest >= COLD_VIEWS_BASELINE},
        ]
    if phase_from == "涨粉":
        fans_pos = bool(l2) and all(
            ((c.get("metrics_summary") or {}).get("avg") or {}).get("new_fans", 0) > 0
            for c in l2)
        cum_conf = sum(
            sum(1 for h in (c.get("hypotheses_tested") or [])
                if h.get("verdict") == "confirmed")
            for c in l2)
        return [
            {"signal": "新增粉丝持续为正", "value": ("是" if fans_pos else "否"),
             "ref": "全为正", "met": fans_pos},
            {"signal": "累计已验证假设", "value": cum_conf,
             "ref": GROWTH_CONFIRMED_MIN, "met": cum_conf >= GROWTH_CONFIRMED_MIN},
        ]
    return []


async def _prev_l3(db, persona_id: str):
    cur = await db.execute(
        "SELECT * FROM media_phase_review WHERE persona_id=? "
        "ORDER BY seq DESC LIMIT 1", (persona_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def gather_l2_since(db, persona_id: str):
    """纳入上一轮 L3 之后新建的 L2 轮（无上轮 L3 → 全部 L2 轮）。"""
    prev = await _prev_l3(db, persona_id)
    if prev:
        cur = await db.execute(
            "SELECT * FROM media_review_cycle WHERE persona_id=? AND level='L2' "
            "AND created_at > ? ORDER BY seq", (persona_id, prev["created_at"]))
    else:
        cur = await db.execute(
            "SELECT * FROM media_review_cycle WHERE persona_id=? AND level='L2' "
            "ORDER BY seq", (persona_id,))
    l2 = []
    for r in await cur.fetchall():
        d = dict(r)
        for f in ("metrics_summary", "hypotheses_tested", "patterns"):
            default = "{}" if f == "metrics_summary" else "[]"
            try:
                d[f] = json.loads(d.get(f) or default)
            except Exception:
                d[f] = {} if f == "metrics_summary" else []
        l2.append(d)
    return l2, prev
