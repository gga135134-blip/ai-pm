"""确认/取消/待确认清单路由。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_assistant as ma


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cf_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_pending():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id='CF'")
        await db.execute("DELETE FROM media_content WHERE persona_id='CF'")
        await db.execute("DELETE FROM media_persona WHERE id='CF'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('CF','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,is_winner) "
                         "VALUES ('CFC','CF','内容','published',0)")
        aid = await ma.log_action(db, "CF", "mark_winner", "media_content", "CFC",
                                  after={"summary": "标爆款《内容》", "content_id": "CFC"}, status="pending")
        await db.close()
        return aid
    return asyncio.run(go())


def test_pending_then_apply():
    aid = _seed_pending()
    r = _client().get("/media/assistant/pending")
    d = r.json()
    assert d["pending"] and d["pending"][0]["id"] == aid and "标爆款" in d["pending"][0]["summary"]
    r = _client().post(f"/media/assistant/action/{aid}/apply")
    assert r.status_code == 200 and r.json()["ok"] is True

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT is_winner FROM media_content WHERE id='CFC'")
        assert (await cur.fetchone())["is_winner"] == 1
        await db.close()
    asyncio.run(chk())


def test_cancel_route():
    aid = _seed_pending()
    r = _client().post(f"/media/assistant/action/{aid}/cancel")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = _client().get("/media/assistant/pending")
    assert r.json()["pending"] == []
