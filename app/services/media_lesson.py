"""教训/红线库的 CRUD。教训与红线同表、kind 区分、按人设独享。

这里只管存取，不掺 AI、不掺注入逻辑 ——
筛选与渲染在 media_context.py，注入在 media_ai.write_script。
spec: docs/superpowers/specs/2026-08-31-media-lesson-memory-design.md
"""
import uuid

KINDS = ("lesson", "redline")
STATUSES = ("active", "archived")

# update_lesson 允许改的列白名单。persona_id / kind / hit_count / created_at
# 不在其中：改归属和类型该走新建，hit_count 是系统记账不该手改。
_UPDATABLE = ("brief", "detail", "trigger_context", "evidence")


async def list_lessons(db, persona_id: str, include_archived: bool = False) -> list:
    """按人设列出。默认只给 active —— 注入侧和 UI 主视图都只关心 active。"""
    sql = "SELECT * FROM media_lesson WHERE persona_id=?"
    args = [persona_id]
    if not include_archived:
        sql += " AND status='active'"
    sql += " ORDER BY kind DESC, created_at"
    cur = await db.execute(sql, tuple(args))
    return [dict(r) for r in await cur.fetchall()]


async def create_lesson(db, persona_id: str, kind: str, brief: str,
                        detail: str = "", trigger_context: str = "",
                        evidence: str = "", source: str = "manual") -> str:
    """新建一条。brief 为空或 kind 非法则拒绝，返回空串。"""
    brief = (brief or "").strip()
    if not brief or kind not in KINDS:
        return ""
    lid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_lesson "
        "(id,persona_id,kind,brief,detail,trigger_context,evidence,source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (lid, persona_id, kind, brief, (detail or "").strip(),
         (trigger_context or "").strip(), (evidence or "").strip(), source))
    await db.commit()
    return lid


async def update_lesson(db, lesson_id: str, **fields) -> bool:
    """只改白名单里的列。有任何列名不在白名单就整个拒绝（不静默吞掉）。"""
    if not fields or any(k not in _UPDATABLE for k in fields):
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    args = [(v or "").strip() for v in fields.values()] + [lesson_id]
    cur = await db.execute(
        f"UPDATE media_lesson SET {sets} WHERE id=?", tuple(args))
    await db.commit()
    return cur.rowcount > 0


async def set_lesson_status(db, lesson_id: str, status: str) -> bool:
    """归档/恢复。归档的不注入但保留证据链（与 media_material 同构）。"""
    if status not in STATUSES:
        return False
    cur = await db.execute(
        "UPDATE media_lesson SET status=? WHERE id=?", (status, lesson_id))
    await db.commit()
    return cur.rowcount > 0


async def delete_lesson(db, lesson_id: str) -> bool:
    cur = await db.execute("DELETE FROM media_lesson WHERE id=?", (lesson_id,))
    await db.commit()
    return cur.rowcount > 0


async def count_redlines(db, persona_id: str) -> int:
    """当前 active 红线条数。UI 据此提示「注入只带前 2 条」。"""
    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM media_lesson "
        "WHERE persona_id=? AND kind='redline' AND status='active'", (persona_id,))
    return (await cur.fetchone())["n"]
