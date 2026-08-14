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


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pbr_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_status_toggle():
    async def seed():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id='PBP'")
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('PBP','嘉','x','涨粉','active')")
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('PB1','PBP','痛点法','validating')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    r = _client().post("/media/playbook/PB1/status", data={"status": "proven"},
                       follow_redirects=False)
    assert r.status_code in (302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_playbook WHERE id='PB1'")
            assert (await cur.fetchone())["status"] == "proven"
        finally:
            await db.close()
    asyncio.run(check())


def test_status_rejects_bad_value():
    async def seed():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('PB2','PBP','x','validating')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    _client().post("/media/playbook/PB2/status", data={"status": "瞎写"},
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_playbook WHERE id='PB2'")
            assert (await cur.fetchone())["status"] == "validating"   # 未改
        finally:
            await db.close()
    asyncio.run(check())
