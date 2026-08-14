"""多人设：当前人设 cookie 选择 + enter 路由。"""
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
from app.api.media import _current_persona_id


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("psw_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_two():
    async def go():
        db = await get_db()
        try:
            for pid, nm in (("PA", "甲设"), ("PB", "乙设")):
                await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
                await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                                 "VALUES (?,?,?, '涨粉','active')", (pid, nm, "x"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


class _Req:
    def __init__(self, cookie=None):
        self.cookies = {"media_persona": cookie} if cookie else {}


def test_current_persona_id_cookie_hit_miss_fallback():
    _seed_two()

    async def go():
        db = await get_db()
        try:
            assert await _current_persona_id(_Req("PB"), db) == "PB"      # 命中
            assert await _current_persona_id(_Req("nope"), db) in ("PA", "PB")  # 无效→回落
            assert await _current_persona_id(_Req(None), db) in ("PA", "PB")    # 无cookie→回落
        finally:
            await db.close()
    asyncio.run(go())


def test_enter_sets_cookie_and_redirects():
    _seed_two()
    r = _client().get("/media/persona/PB/enter", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/media/board"
    assert "media_persona=PB" in r.headers.get("set-cookie", "")
