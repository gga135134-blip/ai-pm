"""人设总览页 + 聚合。"""
import asyncio
import base64
import json
import pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services.media_overview import persona_overview


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ov_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_content")
            await db.execute("DELETE FROM media_account")
            await db.execute("DELETE FROM media_persona")
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('OA','甲','定位甲','涨粉','active')")
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('OB','乙','定位乙','冷启动','active')")
            await db.execute("INSERT INTO media_account (id,persona_id,platform,account_name) "
                             "VALUES ('AC','OA','抖音','嘉姐')")
            # OA: 3 条内容，1 已发(published阶段)+1 已发爆款，1 已进阶到 reviewed(仍算已发)
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CA1','OA','a1','published',1)")
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CA2','OA','a2','idea',0)")
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CA3','OA','a3','reviewed',0)")
            # OB: 1 条 idea
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                             "VALUES ('CB1','OB','b1','idea',0)")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_persona_overview_aggregates_per_persona():
    _seed()

    async def go():
        db = await get_db()
        try:
            rows = {r["id"]: r for r in await persona_overview(db)}
            assert rows["OA"]["total"] == 3 and rows["OA"]["published"] == 2 and rows["OA"]["winners"] == 1
            assert "抖音" in " ".join(a["platform"] for a in rows["OA"]["accounts"])
            assert rows["OB"]["total"] == 1 and rows["OB"]["published"] == 0 and rows["OB"]["winners"] == 0
            assert rows["OB"]["accounts"] == []           # 账号不串
        finally:
            await db.close()
    asyncio.run(go())


def test_media_root_is_overview_and_board_uses_cookie():
    _seed()
    c = _client()
    r = c.get("/media")
    assert r.status_code == 200 and "定位甲" in r.text and "定位乙" in r.text  # 两人设都列
    c.cookies.set("media_persona", "OB")
    r = c.get("/media/board")
    assert r.status_code == 200 and "乙" in r.text        # 看板显示 cookie 指定的人设
