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


L2_SYSTEM = """你是资深自媒体操盘手，看一批已发内容的对比，找可复制的规律。

铁律（违反即失败）：
1. 你只提炼规律和候选，绝不假装能改数据库；一切靠人拍板。
2. 诚实——每条规律必须有这批数据支撑，evidence 引具体内容标题或数字；
   样本太少不足以下结论时标 confidence:"low"，不硬凑规律。
3. 只给精华：规律 ≤5 条，新假设 ≤3 条，候选人设条目/受众修正各 ≤3 条。
4. 别把偶然当规律——一条爆了是运气，多条同向才是规律。

假设-验证：
- 若给了"上轮假设"，逐条判定 verdict：confirmed / refuted / inconclusive，
  并给这批数据里的证据；数据不足以判就 inconclusive（诚实，别硬判）。
- 提出本轮新假设：statement（结论）+ how_to_test（下轮怎么验）+ basis（本轮依据）。

落点分流：
- 人设特征候选 → proposed_traits（dimension 用 tone/signature/positioning 等）。
- 受众修正 → proposed_audience。
- 打法/教训/红线/权重调整建议 → advisory（这些暂无自动落点，先当文字建议）。

只输出严格 JSON，结构：
{"patterns":[{"pattern":"","evidence":"","confidence":"high|medium|low"}],
 "hypotheses":[{"statement":"","how_to_test":"","basis":""}],
 "hypotheses_tested":[{"ref_id":"","verdict":"confirmed|refuted|inconclusive","evidence":""}],
 "proposed_traits":[{"dimension":"","content":"","brief":"","evidence":"","confidence":3}],
 "proposed_audience":[{"segment":"","field":"","new_value":"","evidence":""}],
 "advisory":{"playbooks":[],"lessons":[],"redlines":[],"weight_suggestion":""}}"""


def _build_l2_prompt(contents, summary, prev):
    parts = [f"【本轮纳入 {summary['content_count']} 条已发内容】"]
    for c in contents:
        parts.append(
            f"- 《{c['title']}》谜题：{c.get('puzzle') or '—'}；"
            f"播放 {c['views']}，赞 {c['likes']}，评 {c['comments']}，"
            f"转 {c['shares']}，粉 +{c['new_fans']}")
    parts.append(
        f"【汇总】均值播放 {summary['avg']['views']}，中位 {summary['median']['views']}；"
        f"爆款 {summary['hit_count']} 条，flop {summary['flop_count']} 条。")
    if prev:
        try:
            prev_hyp = json.loads(prev.get("hypotheses") or "[]")
        except Exception:
            prev_hyp = []
        if prev_hyp:
            parts.append("【上轮假设（请逐条用 ref_id 判定）】")
            for h in prev_hyp:
                parts.append(f"- ref_id={h.get('id')}：{h.get('statement')}")
    parts.append("请复盘这批内容，找规律、验上轮假设、提本轮假设与候选资产。")
    return "\n".join(parts)


async def run_l2_cycle(db, persona_id: str, model: str = "auto",
                       force: bool = False) -> dict:
    contents, prev = await gather_cycle_contents(db, persona_id)
    count = len(contents)
    if count < L2_MIN_CONTENTS and not force:
        return {"ok": False, "count": count,
                "warn": f"才 {count} 条，规律不可靠，建议攒到 ~{L2_MIN_CONTENTS} 条再跑"}

    summary = summarize_metrics(contents)
    prompt = _build_l2_prompt(contents, summary, prev)
    result = await ask_ai(prompt, model=model, task_type="media_review_cycle",
                          system_prompt=L2_SYSTEM, json_mode=True)
    resp = result.get("response", "")
    if resp.startswith("[错误]") or resp.startswith("[费用保护]"):
        return {"ok": False, "count": count, "error": resp,
                "cost": result.get("cost", 0), "model": result.get("model", "")}
    obj = extract_json(resp, expect="object")
    if not obj:
        return {"ok": False, "count": count, "error": "复盘结果无法解析",
                "cost": result.get("cost", 0), "model": result.get("model", "")}

    # 新假设补稳定 id（下轮据此结转判定）
    hyps = [h for h in (obj.get("hypotheses") or []) if isinstance(h, dict)]
    for h in hyps:
        if not h.get("id"):
            h["id"] = "h-" + uuid.uuid4().hex[:8]

    seq = (prev["seq"] + 1) if prev else 1
    cid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_review_cycle "
        "(id,persona_id,level,seq,period_start,period_end,content_ids,"
        " metrics_summary,patterns,hypotheses,hypotheses_tested,"
        " proposed_traits,proposed_audience,advisory,cost,model) "
        "VALUES (?,?,'L2',?,?,datetime('now'),?,?,?,?,?,?,?,?,?,?)",
        (cid, persona_id, seq,
         prev["period_end"] if prev else None,
         json.dumps([c["id"] for c in contents], ensure_ascii=False),
         json.dumps(summary, ensure_ascii=False),
         json.dumps(obj.get("patterns") or [], ensure_ascii=False),
         json.dumps(hyps, ensure_ascii=False),
         json.dumps(obj.get("hypotheses_tested") or [], ensure_ascii=False),
         json.dumps(obj.get("proposed_traits") or [], ensure_ascii=False),
         json.dumps(obj.get("proposed_audience") or [], ensure_ascii=False),
         json.dumps(obj.get("advisory") or {}, ensure_ascii=False),
         result.get("cost", 0), result.get("model", "")))
    await db.commit()
    await log_injection(db, "", "media_review_cycle",
                        [c["id"] for c in contents], result.get("tokens", 0))
    return {"ok": True, "cycle_id": cid, "seq": seq, "count": count,
            "cost": result.get("cost", 0), "model": result.get("model", "")}


_JSON_FIELDS = ("content_ids", "metrics_summary", "patterns", "hypotheses",
                "hypotheses_tested", "proposed_traits", "proposed_audience",
                "advisory")


async def list_cycles(db, persona_id: str) -> list:
    cur = await db.execute(
        "SELECT id, seq, period_start, period_end, content_ids, metrics_summary, "
        "cost, model, created_at FROM media_review_cycle "
        "WHERE persona_id=? AND level='L2' ORDER BY seq DESC", (persona_id,))
    out = []
    for r in await cur.fetchall():
        d = dict(r)
        try:
            d["count"] = len(json.loads(d.get("content_ids") or "[]"))
        except Exception:
            d["count"] = 0
        out.append(d)
    return out


async def get_cycle(db, cycle_id: str):
    cur = await db.execute(
        "SELECT * FROM media_review_cycle WHERE id=?", (cycle_id,))
    row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    for f in _JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or ("[]" if f != "metrics_summary"
                              and f != "advisory" else "{}"))
        except Exception:
            d[f] = [] if f not in ("metrics_summary", "advisory") else {}
    return d
