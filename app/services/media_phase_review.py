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


def count_topics_serving(anchor_id: str, topics: list) -> int:
    """近期有几条选题在往这个锚点靠（media_topic.anchor_ids 含 anchor_id）。

    anchor_ids 可能是 DB 原样的 JSON 字符串，也可能已解析成 list。
    """
    n = 0
    for t in topics:
        raw = t.get("anchor_ids")
        try:
            ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            ids = []
        if anchor_id in ids:
            n += 1
    return n


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


L3_SYSTEM = """你站在阶段的高度，看一个人设阶段积累的规律与真实数据趋势，
判断人设是否该进化。

铁律（违反即失败）：
1. 你只提建议和动作候选，绝不假装能改人设；一切靠人点应用按钮。
2. 诚实——阶段建议必须以「退出信号」的实际数据为主要依据，trait 动作必须引
   具体 L2 规律做 evidence；数据够不着就别硬推。
3. 只给精华：trait 归档/晋升每类 ≤3 条，别把整个注册表翻底朝天。
4. 别把偶然当趋势——一轮好不代表阶段到了。

阶段建议：
- 结合退出信号的实际数据 + L2 规律，给 phase_reco：advance（进下一阶段）或
  stay（原地）。多数信号未达参考线就 stay——人设进化要真实数据达到程度才发生。
- 只前进或原地：phase_to 只能是当前阶段的下一个；绝不建议倒退或跳级。

trait 策展（只对给定的现有注册表，不造新——造新是 L2 的活）：
- archive：陈旧（近几轮 L2 规律不再印证）或被新规律矛盾。
- promote：被近几轮 L2 规律反复印证，值得提置信。
- 每条给 trait_id（必须来自给定清单）+ action(archive/promote) + evidence + reason。

只输出严格 JSON：
{"phase_reco":"advance|stay","phase_to":"","phase_reason":"",
 "trait_actions":[{"trait_id":"","action":"archive|promote","evidence":"","reason":""}]}"""


def _build_l3_prompt(phase_from, next_phase, signals, l2, traits):
    parts = [f"【当前阶段】{phase_from}（下一阶段：{next_phase or '已是终点，只可 stay'}）"]
    parts.append("【阶段退出信号·实际数据】")
    for s in signals:
        parts.append(f"- {s['signal']}：实际 {s['value']}，参考线 {s['ref']}，"
                     f"{'达标' if s['met'] else '未达'}")
    if not signals:
        parts.append("（终点阶段，无退出信号，只做 trait 策展）")
    parts.append(f"【纳入 {len(l2)} 轮 L2 的规律与验证】")
    for c in l2:
        pats = "；".join(p.get("pattern", "") for p in (c.get("patterns") or []))
        conf = sum(1 for h in (c.get("hypotheses_tested") or [])
                   if h.get("verdict") == "confirmed")
        parts.append(f"- 第{c.get('seq')}轮：规律[{pats}]，已验证假设 {conf} 条")
    parts.append("【当前人设条目（只在这些里做 archive/promote，别造新）】")
    for t in traits:
        parts.append(f"- trait_id={t['id']}｜[{t['dimension']}] {t['content']}"
                     f"（置信 {t['confidence']}）")
    parts.append("请判断阶段是否该进化，并对现有条目给策展动作。")
    return "\n".join(parts)


async def run_l3_review(db, persona_id: str, model: str = "auto",
                        force: bool = False) -> dict:
    l2, prev = await gather_l2_since(db, persona_id)
    count = len(l2)
    if count < L3_MIN_L2_CYCLES and not force:
        return {"ok": False, "count": count,
                "warn": f"才 {count} 轮 L2，还看不出阶段级趋势，"
                        f"建议攒到 ~{L3_MIN_L2_CYCLES} 轮再跑"}

    cur = await db.execute(
        "SELECT current_phase FROM media_persona WHERE id=?", (persona_id,))
    prow = await cur.fetchone()
    phase_from = prow["current_phase"] if prow else ""
    next_phase = _next_phase(phase_from)

    trend = summarize_trend(l2)
    signals = phase_exit_signals(phase_from, l2)

    cur = await db.execute(
        "SELECT id, dimension, content, confidence FROM media_persona_trait "
        "WHERE persona_id=? AND status='active' ORDER BY confidence DESC",
        (persona_id,))
    traits = [dict(r) for r in await cur.fetchall()]
    active_ids = {t["id"] for t in traits}

    prompt = _build_l3_prompt(phase_from, next_phase, signals, l2, traits)
    result = await ask_ai(prompt, model=model, task_type="media_phase_review",
                          system_prompt=L3_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "count": count, "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "count": count, "error": "阶段复盘结果无法解析",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 校验阶段建议：phase_to 只能是当前阶段的下一个，否则回落 stay
    reco = obj.get("phase_reco")
    phase_to = obj.get("phase_to") or ""
    if reco == "advance" and phase_to and phase_to == next_phase:
        phase_reco, phase_to_final = "advance", phase_to
    else:
        phase_reco, phase_to_final = "stay", ""

    # 校验 trait 动作：trait_id 必须在 active 集合、action 白名单
    trait_actions = []
    for a in (obj.get("trait_actions") or []):
        if not isinstance(a, dict):
            continue
        tid = a.get("trait_id")
        if tid in active_ids and a.get("action") in ("archive", "promote"):
            # 补 dimension/content 便于报告页展示
            t = next((x for x in traits if x["id"] == tid), {})
            trait_actions.append({
                "trait_id": tid, "dimension": t.get("dimension", ""),
                "content": t.get("content", ""), "action": a.get("action"),
                "evidence": a.get("evidence", ""), "reason": a.get("reason", "")})

    seq = (prev["seq"] + 1) if prev else 1
    rid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_phase_review "
        "(id,persona_id,seq,phase_from,l2_cycle_ids,metrics_trend,phase_signals,"
        " phase_reco,phase_to,phase_reason,trait_actions,cost,model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, persona_id, seq, phase_from,
         json.dumps([c["id"] for c in l2], ensure_ascii=False),
         json.dumps(trend, ensure_ascii=False),
         json.dumps(signals, ensure_ascii=False),
         phase_reco, phase_to_final, obj.get("phase_reason", ""),
         json.dumps(trait_actions, ensure_ascii=False),
         result.get("cost", 0), result.get("model", "")))
    await db.commit()
    await log_injection(db, "", "media_phase_review",
                        [c["id"] for c in l2], result.get("tokens", 0))
    return {"ok": True, "review_id": rid, "seq": seq, "count": count,
            "cost": result.get("cost", 0), "model": result.get("model", "")}


_JSON_FIELDS = ("l2_cycle_ids", "metrics_trend", "phase_signals", "trait_actions")


async def list_phase_reviews(db, persona_id: str) -> list:
    cur = await db.execute(
        "SELECT id, seq, phase_from, phase_reco, phase_to, l2_cycle_ids, "
        "cost, model, created_at FROM media_phase_review "
        "WHERE persona_id=? ORDER BY seq DESC", (persona_id,))
    out = []
    for r in await cur.fetchall():
        d = dict(r)
        try:
            d["l2_count"] = len(json.loads(d.get("l2_cycle_ids") or "[]"))
        except Exception:
            d["l2_count"] = 0
        out.append(d)
    return out


async def get_phase_review(db, review_id: str):
    cur = await db.execute(
        "SELECT * FROM media_phase_review WHERE id=?", (review_id,))
    row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    for f in _JSON_FIELDS:
        default = "{}" if f == "metrics_trend" else "[]"
        try:
            d[f] = json.loads(d.get(f) or default)
        except Exception:
            d[f] = {} if f == "metrics_trend" else []
    return d
