"""AI 口播草稿的历史版本。只留最新几版，老的自动删。

以前 `media_content.ai_draft` 是**单个字段**，AI 重写一次就把上一版覆盖掉——
用户想对比两版、或者回到上一版，都做不到（2026-09-01 用户要求：
「数据只存三版就可以了，能进入填进来的，永远只有最新的三版」）。

`ai_draft` 保留不动：功能B 学改稿、写稿页的兜底填充都还在读它，
它等于「最新那一版」的快捷方式。这里存的是完整的版本队列。
"""
import uuid

KEEP = 3


async def add_draft(db, content_id: str, text: str,
                    model: str = "", cost: float = 0, keep: int = KEEP) -> str:
    """记一版草稿，并把超出 keep 的老版本删掉。空文本不记。"""
    text = (text or "").strip()
    if not text:
        return ""
    did = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_draft (id,content_id,text,model,cost) VALUES (?,?,?,?,?)",
        (did, content_id, text, model or "", cost or 0))
    # 保留最新 keep 版。按 created_at 再按 rowid 排——同一秒内写入的两版
    # created_at 会一模一样，只靠时间排会随机丢版本。
    await db.execute(
        "DELETE FROM media_draft WHERE content_id=? AND id NOT IN ("
        "  SELECT id FROM media_draft WHERE content_id=?"
        "  ORDER BY created_at DESC, rowid DESC LIMIT ?)",
        (content_id, content_id, keep))
    await db.commit()
    return did


async def list_drafts(db, content_id: str) -> list:
    """最新的排最前。"""
    cur = await db.execute(
        "SELECT * FROM media_draft WHERE content_id=? "
        "ORDER BY created_at DESC, rowid DESC", (content_id,))
    return [dict(r) for r in await cur.fetchall()]
