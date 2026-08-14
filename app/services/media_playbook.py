"""打法库读取。"""


async def list_playbooks(db, persona_id: str) -> list:
    cur = await db.execute(
        "SELECT * FROM media_playbook WHERE persona_id=? "
        "ORDER BY CASE status WHEN 'proven' THEN 0 ELSE 1 END, created_at DESC",
        (persona_id,))
    return [dict(r) for r in await cur.fetchall()]


async def get_playbook(db, playbook_id: str):
    cur = await db.execute("SELECT * FROM media_playbook WHERE id=?", (playbook_id,))
    row = await cur.fetchone()
    return dict(row) if row else None
