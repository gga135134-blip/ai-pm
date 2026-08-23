"""打法库🅒接决策引擎：迁移列 + 打分器 + 打标 集成测试。"""
import asyncio

import pytest

from app.database import get_db, init_db
import app.database as _db_mod


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    """异步 DB 测试隔离到临时库，不污染真实 aipm.db。"""
    tmp = tmp_path_factory.mktemp("media_pb_decision_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_media_topic_has_playbook_id_column():
    async def run():
        db = await get_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_topic)")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    cols = asyncio.run(run())
    assert "playbook_id" in cols


def test_route_query_wiring_surfaces_playbook_in_report():
    """复现决策路由：查 playbooks 传进 ctx，命中打法的选题报告含打法行。"""
    from app.services.media_decision import build_decision_context, rank_pool

    async def run():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('RT','人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_playbook (id,persona_id,name,structure,when_to_use,"
                "evidence,source,status) VALUES "
                "('RTPB','RT','反常识开场','','','','legacy_mine','proven')")
            await db.execute(
                "INSERT INTO media_topic (id,persona_id,title,puzzle,status,tagged,playbook_id) "
                "VALUES ('RTT','RT','选题','谜','pool',1,'RTPB')")
            await db.commit()
            # —— 复现路由 line 1062-1068 的查询与编排 ——
            cur = await db.execute(
                "SELECT id,name,status FROM media_playbook "
                "WHERE persona_id=? AND status IN ('validating','proven')", ("RT",))
            playbooks = [dict(r) for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT * FROM media_topic WHERE persona_id=? AND status='pool'", ("RT",))
            topics = [dict(r) for r in await cur.fetchall()]
            ctx = build_decision_context([], [], [], [], [], [], [], playbooks=playbooks)
            ranked = rank_pool(topics, ctx, "涨粉")
            return ranked[0]["decision_report"]
        finally:
            await db.close()
    report = asyncio.run(run())
    assert "匹配到打法《反常识开场》" in report
