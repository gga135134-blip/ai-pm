"""老文案批量挖矿候选队列：去重写入 / 去重分组 / 采纳落对应库 / 丢弃。纯 DB，不调 AI。"""
import json
import uuid


def _dedup_key(kind: str, payload: dict) -> str:
    if kind == "playbook":
        base = (payload.get("name") or "").strip()
    else:
        base = (payload.get("content") or "").strip()
    return f"{kind}:{base[:60]}"


async def enqueue_candidates(db, persona_id: str, source_content_id: str,
                             kind: str, items: list) -> int:
    n = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        dk = _dedup_key(kind, it)
        # 同一条内容里同句被 AI 返回多次 → 幂等（含 source_content_id，跨内容保留以便计数）
        cur = await db.execute(
            "SELECT 1 FROM media_mine_candidate WHERE persona_id=? AND kind=? "
            "AND dedup_key=? AND source_content_id=? AND status='pending'",
            (persona_id, kind, dk, source_content_id))
        if await cur.fetchone():
            continue
        await db.execute(
            "INSERT INTO media_mine_candidate "
            "(id,persona_id,kind,payload,source_content_id,dedup_key) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), persona_id, kind, json.dumps(it, ensure_ascii=False),
             source_content_id, dk))
        n += 1
    await db.commit()
    return n


async def list_pending_grouped(db, persona_id: str) -> dict:
    cur = await db.execute(
        "SELECT mc.*, c.title AS src_title FROM media_mine_candidate mc "
        "LEFT JOIN media_content c ON c.id=mc.source_content_id "
        "WHERE mc.persona_id=? AND mc.status='pending' ORDER BY mc.created_at",
        (persona_id,))
    rows = [dict(r) for r in await cur.fetchall()]
    groups = {"signature": {}, "material": {}, "playbook": {}}
    for r in rows:
        bucket = groups.get(r["kind"])
        if bucket is None:
            continue
        g = bucket.get(r["dedup_key"])
        if not g:
            g = {"rep_id": r["id"], "payload": json.loads(r["payload"] or "{}"),
                 "count": 0, "sources": []}
            bucket[r["dedup_key"]] = g
        g["count"] += 1
        if r.get("src_title"):
            g["sources"].append(r["src_title"])
    return {k: list(v.values()) for k, v in groups.items()}


async def _adopt_one(db, cand: dict):
    kind, p = cand["kind"], json.loads(cand["payload"] or "{}")
    pid = cand["persona_id"]
    if kind == "signature":
        content = (p.get("content") or "").strip()
        if content:
            await db.execute(
                "INSERT INTO media_persona_trait "
                "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
                "VALUES (?,?, 'signature',?,?, 'reverse_mine',?,3,'')",
                (str(uuid.uuid4()), pid, content, (p.get("brief") or content)[:30],
                 (p.get("evidence") or "").strip()))
    elif kind == "material":
        detail = (p.get("content") or "").strip()
        if detail:
            mtype = p.get("type") or "story"
            await db.execute(
                "INSERT INTO media_material (id,persona_id,type,title,detail,brief,source) "
                "VALUES (?,?,?,?,?,?, '反向挖料')",
                (str(uuid.uuid4()), pid, mtype, detail[:40], detail,
                 (p.get("brief") or detail)[:30]))
    elif kind == "playbook":
        name = (p.get("name") or "").strip()
        if name:
            sim = (p.get("similar_to") or "").strip()
            merged = False
            if sim:
                cur = await db.execute(
                    "SELECT id,evidence FROM media_playbook WHERE persona_id=? AND name=?",
                    (pid, sim))
                ex = await cur.fetchone()
                if ex:
                    new_ev = ((ex["evidence"] or "") + "\n---\n" + (p.get("evidence") or "")).strip()
                    await db.execute("UPDATE media_playbook SET evidence=? WHERE id=?",
                                     (new_ev, ex["id"]))
                    merged = True
            if not merged:
                await db.execute(
                    "INSERT INTO media_playbook "
                    "(id,persona_id,name,structure,when_to_use,evidence,source,status) "
                    "VALUES (?,?,?,?,?,?, 'legacy_mine','validating')",
                    (str(uuid.uuid4()), pid, name, (p.get("structure") or "").strip(),
                     (p.get("when_to_use") or "").strip(), (p.get("evidence") or "").strip()))


async def _resolve_group(db, rep_id: str):
    """由代表 id 找回它那一组（同 persona/kind/dedup_key 的所有 pending）。"""
    cur = await db.execute("SELECT * FROM media_mine_candidate WHERE id=?", (rep_id,))
    rep = await cur.fetchone()
    if not rep:
        return None, []
    rep = dict(rep)
    cur = await db.execute(
        "SELECT id FROM media_mine_candidate WHERE persona_id=? AND kind=? "
        "AND dedup_key=? AND status='pending'",
        (rep["persona_id"], rep["kind"], rep["dedup_key"]))
    ids = [r["id"] for r in await cur.fetchall()]
    return rep, ids


async def adopt_candidates(db, ids: list) -> int:
    n = 0
    for rep_id in ids or []:
        rep, group_ids = await _resolve_group(db, rep_id)
        if not rep or not group_ids:
            continue
        await _adopt_one(db, rep)
        qs = ",".join("?" for _ in group_ids)
        await db.execute(
            f"UPDATE media_mine_candidate SET status='adopted' WHERE id IN ({qs})", group_ids)
        n += 1
    await db.commit()
    return n


async def discard_candidates(db, ids: list) -> int:
    n = 0
    for rep_id in ids or []:
        rep, group_ids = await _resolve_group(db, rep_id)
        if not rep or not group_ids:
            continue
        qs = ",".join("?" for _ in group_ids)
        await db.execute(
            f"UPDATE media_mine_candidate SET status='discarded' WHERE id IN ({qs})", group_ids)
        n += 1
    await db.commit()
    return n
