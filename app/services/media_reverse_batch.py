"""视频批量反向入库：解析链接 + 后台跑器（仿 media_batch）。逐条串行调 reverse_ingest。"""
import asyncio
import threading
from pathlib import Path  # noqa: F401  (audio_dir 传入即 Path，保留供类型直觉)

from app.database import get_db
from app.services.video_fetch import first_url
from app.services.media_reverse import reverse_ingest


def parse_urls(text: str, cap: int = 10) -> list:
    """按行拆，每行抠链接（支持贴整段分享文案），丢空/非链接行，保序去重，截断到 cap。
    注意：first_url 找不到链接会原样返回 text.strip()，故只保留 http 开头的结果。"""
    out, seen = [], set()
    for line in (text or "").splitlines():
        u = first_url(line).strip()
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= cap:
            break
    return out


# ─────────────── 后台跑器（每人设一个活跃任务·内存进度） ───────────────
_rev_jobs = {}
_rev_lock = threading.Lock()


def get_reverse_status(persona_id):
    with _rev_lock:
        j = _rev_jobs.get(persona_id)
        if not j:
            return {"running": False, "op": "reverse", "done": 0, "total": 0, "results": []}
        d = dict(j)
        d["results"] = list(j["results"])   # 别把在跑的 list 引用交出去
        return d


def start_reverse_batch(persona_id, urls, cfg, public_base, audio_dir, cookies_path=None) -> bool:
    us = [str(u) for u in (urls or []) if str(u).strip()]
    if not us:
        return False
    with _rev_lock:
        j = _rev_jobs.get(persona_id)
        if j and j.get("running"):
            return False
        _rev_jobs[persona_id] = {"op": "reverse", "done": 0, "total": len(us),
                                 "running": True, "results": []}
    asyncio.create_task(_run_reverse_batch(persona_id, us, cfg, public_base, audio_dir, cookies_path))
    return True


async def _run_reverse_batch(persona_id, urls, cfg, public_base, audio_dir, cookies_path):
    try:
        for url in urls:
            db = await get_db()
            try:
                r = await reverse_ingest(db, persona_id, url, cfg, public_base,
                                         audio_dir, cookies_path=cookies_path)
            except Exception as e:
                r = {"ok": False, "title": "", "error": str(e) or "入库出错"}
            finally:
                await db.close()
            with _rev_lock:
                j = _rev_jobs.get(persona_id)
                if j:
                    j["results"].append({"url": url, "ok": bool(r.get("ok")),
                                         "title": r.get("title", ""), "error": r.get("error", "")})
                    j["done"] += 1
    finally:
        with _rev_lock:
            j = _rev_jobs.get(persona_id)
            if j:
                j["running"] = False
