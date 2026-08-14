"""人设总览聚合：每人设概况（内容数/账号）。"""


async def persona_overview(db) -> list:
    cur = await db.execute("SELECT * FROM media_persona ORDER BY created_at")
    personas = [dict(r) for r in await cur.fetchall()]
    out = []
    for p in personas:
        cur = await db.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN stage='published' THEN 1 ELSE 0 END) published, "
            "SUM(CASE WHEN is_winner=1 THEN 1 ELSE 0 END) winners "
            "FROM media_content WHERE persona_id=?", (p["id"],))
        c = await cur.fetchone()
        cur = await db.execute(
            "SELECT platform, account_name FROM media_account WHERE persona_id=?", (p["id"],))
        accounts = [dict(r) for r in await cur.fetchall()]
        out.append({"id": p["id"], "name": p["name"], "one_liner": p.get("one_liner", ""),
                    "current_phase": p.get("current_phase", ""),
                    "total": c["total"] or 0, "published": c["published"] or 0,
                    "winners": c["winners"] or 0, "accounts": accounts})
    return out
