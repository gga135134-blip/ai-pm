"""打法库读取。"""


async def list_playbooks(db) -> list:
    cur = await db.execute(
        "SELECT * FROM media_playbook "
        "ORDER BY CASE status WHEN 'proven' THEN 0 ELSE 1 END, created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def get_playbook(db, playbook_id: str):
    cur = await db.execute("SELECT * FROM media_playbook WHERE id=?", (playbook_id,))
    row = await cur.fetchone()
    return dict(row) if row else None
