"""选题池的写操作。从 api/media.py 挪过来，好让助手工具也能调。

原来 `_adopt_topic` 住在 api 层，服务层导不到（反向依赖会绕成循环），
结果助手没有「把选题采纳成内容」这把工具——它能写稿却造不出可以写的那张纸，
只能拿 write_next 硬凑，续出方向完全跑偏的内容（2026-09-01 真机踩到）。
"""
import uuid


async def adopt_topic(db, topic_id: str) -> str | None:
    """选题 → 内容。把谜题和理由一起带过去，开工时不用重新想。

    只采纳还在池子里（status='pool'）的；已采纳/已弃的返回 None。
    返回新建内容的 id。
    """
    cur = await db.execute("SELECT * FROM media_topic WHERE id=?", (topic_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "pool":
        return None
    t = dict(row)
    cid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_content "
        "(id,persona_id,title,puzzle,stage,idea_source,idea_reason) "
        "VALUES (?,?,?,?,'idea',?,?)",
        (cid, t["persona_id"], t["title"], t["puzzle"], t["source"], t["reason"]))
    await db.execute(
        "UPDATE media_topic SET status='adopted', adopted_content_id=? WHERE id=?",
        (cid, topic_id))
    await db.commit()
    return cid


async def unadopt_topic(db, topic_id: str, content_id: str) -> bool:
    """撤销采纳：删掉建出来的内容，选题退回池子。

    **内容已经有草稿或正文时拒绝撤销**（返回 False）——那意味着人已经在
    这条上干过活了，静默删掉等于把成果扔了。这种情况让人去内容页手动删。
    """
    cur = await db.execute(
        "SELECT ai_draft, script FROM media_content WHERE id=?", (content_id,))
    row = await cur.fetchone()
    if not row:
        return False
    if (row["ai_draft"] or "").strip() or (row["script"] or "").strip():
        return False
    await db.execute("DELETE FROM media_content WHERE id=?", (content_id,))
    await db.execute(
        "UPDATE media_topic SET status='pool', adopted_content_id='' WHERE id=?",
        (topic_id,))
    await db.commit()
    return True
