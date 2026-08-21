"""助手改动记录页 + 撤销路由。"""
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
    tmp = tmp_path_factory.mktemp("act_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_action():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id='AA'")
        await db.execute("DELETE FROM media_topic WHERE persona_id='AA'")
        await db.execute("DELETE FROM media_persona WHERE id='AA'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('AA','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_topic (id,persona_id,title,source,status) "
                         "VALUES ('TT','AA','助手建的选题','assistant','pool')")
        aid = await ma.log_action(db, "AA", "create_topic", "media_topic", "TT", after={"title": "助手建的选题"})
        await db.close()
        return aid
    return asyncio.run(go())


def test_actions_page_renders():
    _seed_action()
    r = _client().get("/media/assistant/actions")
    assert r.status_code == 200 and "助手建的选题" in r.text


def test_revert_route():
    aid = _seed_action()
    r = _client().post(f"/media/assistant/action/{aid}/revert", follow_redirects=False)
    assert r.status_code in (302, 303)
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_topic WHERE id='TT'")
        assert (await cur.fetchone())["c"] == 0
        await db.close()
    asyncio.run(chk())
