"""助手对话沉淀：propose_lesson 只拟不写，确认后才入库，且可撤。"""
import asyncio
import json
import uuid

from app.services.media_assistant import (
    log_action, apply_action, revert_action, list_pending)
from app.services.media_lesson import list_lessons
from tests.media_helpers import make_db


async def _persona(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "帮中小企业落地AI", "涨粉"))
    await db.commit()


async def _stage(db, pid="P1", kind="lesson"):
    aid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_assistant_action "
        "(id,persona_id,action_type,target_table,target_id,after_json,status) "
        "VALUES (?,?, 'propose_lesson','media_lesson','',?, 'pending')",
        (aid, pid, json.dumps({
            "summary": "把「开头别铺垫」记进本子",
            "kind": kind, "brief": "开头别铺垫",
            "trigger_context": "口播", "evidence": "用户 8/31 说的"},
            ensure_ascii=False)))
    await db.commit()
    return aid


def test_pending_does_not_write_lesson_table():
    """核心安全语义：拟好之后本子里必须还是空的。"""
    async def run():
        db = await make_db()
        await _persona(db)
        await _stage(db)
        pend = await list_pending(db, "P1")
        rows = await list_lessons(db, "P1")
        await db.close()
        return pend, rows

    pend, rows = asyncio.run(run())
    assert len(pend) == 1
    assert rows == []


def test_apply_writes_lesson():
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db)
        ok = await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is True
    assert len(rows) == 1
    assert rows[0]["brief"] == "开头别铺垫"
    assert rows[0]["trigger_context"] == "口播"
    assert rows[0]["source"] == "assistant"


def test_apply_respects_redline_kind():
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db, kind="redline")
        await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return rows

    assert asyncio.run(run())[0]["kind"] == "redline"


def test_revert_removes_the_lesson():
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db)
        await apply_action(db, aid)
        ok = await revert_action(db, aid)
        rows = await list_lessons(db, "P1", include_archived=True)
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is True and rows == []


def test_apply_twice_is_noop():
    """已 applied 的动作再 apply 一次不该重复写库。"""
    async def run():
        db = await make_db()
        await _persona(db)
        aid = await _stage(db)
        await apply_action(db, aid)
        second = await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return second, rows

    second, rows = asyncio.run(run())
    assert second is False and len(rows) == 1
