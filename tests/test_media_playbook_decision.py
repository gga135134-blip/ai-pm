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
