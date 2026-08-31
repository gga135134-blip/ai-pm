"""助手对话沉淀：propose_lesson 只拟不写，确认后才入库，且可撤。"""
import asyncio
import json
import uuid

import pytest

import app.database as _db_mod
from app.database import get_db, init_db
from app.services.media_assistant import (
    log_action, apply_action, revert_action, list_pending)
from app.services.media_lesson import list_lessons
from app.services import media_agent_tools as mat
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


def test_apply_rejects_empty_brief():
    """apply_action 是另一个入口，空 brief 不该插进库（工具侧拦了，这里也要拦）。"""
    async def run():
        db = await make_db()
        await _persona(db)
        aid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_assistant_action "
            "(id,persona_id,action_type,target_table,target_id,after_json,status) "
            "VALUES (?,?, 'propose_lesson','media_lesson','',?, 'pending')",
            (aid, "P1", json.dumps({"summary": "记个空的", "kind": "lesson", "brief": ""},
                                    ensure_ascii=False)))
        await db.commit()
        ok = await apply_action(db, aid)
        rows = await list_lessons(db, "P1")
        await db.close()
        return ok, rows

    ok, rows = asyncio.run(run())
    assert ok is False
    assert rows == []


@pytest.fixture(scope="module", autouse=False)
def _tool_db(tmp_path_factory):
    """_tool_propose_lesson 内部自己 await get_db()，用不了内存库，得用 tmp-DB_PATH。"""
    tmp = tmp_path_factory.mktemp("propose_lesson_tool_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _tool_seed():
    """每个 test 前清空重种，避免 module-scoped db 跨 test 串数据。"""
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id='P1'")
        await db.execute("DELETE FROM media_persona WHERE id='P1'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('P1','嘉姐','帮中小企业落地AI','涨粉','active')")
        await db.commit()
        await db.close()
    asyncio.run(go())


def test_tool_propose_lesson_stages_pending(_tool_db):
    _tool_seed()
    async def go():
        out = await mat._tool_propose_lesson(
            {"brief": "开头别铺垫", "kind": "lesson", "trigger_context": "口播"}, "P1")
        db = await get_db()
        cur = await db.execute(
            "SELECT status,action_type FROM media_assistant_action WHERE persona_id='P1'")
        row = dict(await cur.fetchone())
        await db.close()
        return out, row

    out, row = asyncio.run(go())
    assert "确认" in out
    assert row["status"] == "pending"
    assert row["action_type"] == "propose_lesson"


def test_tool_propose_lesson_rejects_empty_brief(_tool_db):
    _tool_seed()
    async def go():
        out = await mat._tool_propose_lesson({"brief": "  ", "kind": "lesson"}, "P1")
        db = await get_db()
        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_assistant_action WHERE persona_id='P1'")
        n = (await cur.fetchone())["c"]
        await db.close()
        return out, n

    out, n = asyncio.run(go())
    assert "brief" in out
    assert n == 0


def test_tool_propose_lesson_falls_back_to_lesson_kind(_tool_db):
    _tool_seed()
    async def go():
        await mat._tool_propose_lesson({"brief": "非法kind测试", "kind": "foo"}, "P1")
        db = await get_db()
        cur = await db.execute(
            "SELECT after_json FROM media_assistant_action WHERE persona_id='P1' "
            "AND action_type='propose_lesson' ORDER BY created_at DESC LIMIT 1")
        after = json.loads((await cur.fetchone())["after_json"])
        await db.close()
        return after

    after = asyncio.run(go())
    assert after["kind"] == "lesson"
