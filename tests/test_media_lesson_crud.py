"""media_lesson 的 CRUD service。"""
import asyncio

from app.services.media_lesson import (
    list_lessons, create_lesson, update_lesson,
    set_lesson_status, delete_lesson, count_redlines)
from tests.media_helpers import make_db


async def _persona(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "帮中小企业落地AI", "涨粉"))
    await db.commit()


def test_create_then_list():
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "redline", "不许编造数据",
                                  evidence="8/20 复盘")
        rows = await list_lessons(db, "P1")
        await db.close()
        return lid, rows

    lid, rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["id"] == lid
    assert rows[0]["kind"] == "redline"
    assert rows[0]["hit_count"] == 0
    assert rows[0]["source"] == "manual"


def test_list_excludes_archived_by_default():
    async def run():
        db = await make_db()
        await _persona(db)
        keep = await create_lesson(db, "P1", "lesson", "留着的")
        gone = await create_lesson(db, "P1", "lesson", "归档的")
        await set_lesson_status(db, gone, "archived")
        default = await list_lessons(db, "P1")
        withall = await list_lessons(db, "P1", include_archived=True)
        await db.close()
        return keep, default, withall

    keep, default, withall = asyncio.run(run())
    assert [r["id"] for r in default] == [keep]
    assert len(withall) == 2


def test_list_is_scoped_to_persona():
    """按人设独享（宪法第 4 条）：别的人设的本子不该串过来。"""
    async def run():
        db = await make_db()
        await _persona(db, "P1")
        await _persona(db, "P2")
        await create_lesson(db, "P1", "lesson", "P1的")
        await create_lesson(db, "P2", "lesson", "P2的")
        rows = await list_lessons(db, "P1")
        await db.close()
        return rows

    rows = asyncio.run(run())
    assert [r["brief"] for r in rows] == ["P1的"]


def test_update_changes_fields():
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "原来的")
        ok = await update_lesson(db, lid, brief="改过的", trigger_context="讲方法论")
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows[0]

    ok, row = asyncio.run(run())
    assert ok is True
    assert row["brief"] == "改过的"
    assert row["trigger_context"] == "讲方法论"


def test_update_rejects_unknown_column():
    """白名单防注入：不在允许列表里的字段一律忽略，不拼进 SQL。"""
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "原来的")
        ok = await update_lesson(db, lid, hit_count=999, persona_id="P9")
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows[0]

    ok, row = asyncio.run(run())
    assert ok is False
    assert row["hit_count"] == 0
    assert row["persona_id"] == "P1"


def test_count_redlines_only_counts_active_redlines():
    async def run():
        db = await make_db()
        await _persona(db)
        await create_lesson(db, "P1", "redline", "红一")
        await create_lesson(db, "P1", "lesson", "教一")
        gone = await create_lesson(db, "P1", "redline", "红二归档")
        await set_lesson_status(db, gone, "archived")
        n = await count_redlines(db, "P1")
        await db.close()
        return n

    assert asyncio.run(run()) == 1


def test_delete_removes_row():
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "删我")
        ok = await delete_lesson(db, lid)
        rows = await list_lessons(db, "P1", include_archived=True)
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is True and rows == []


def test_create_rejects_blank_brief():
    """brief 是唯一进提示词的字段，空的没有意义。"""
    async def run():
        db = await make_db()
        await _persona(db)
        lid = await create_lesson(db, "P1", "lesson", "   ")
        rows = await list_lessons(db, "P1")
        await db.close()
        return lid, rows

    lid, rows = asyncio.run(run())
    assert lid == "" and rows == []
