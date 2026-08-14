"""复核页 + 批量采纳/丢弃路由。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_mine_queue as q


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mr_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_mine_candidate WHERE persona_id='MR'")
        await db.execute("DELETE FROM media_content WHERE persona_id='MR'")
        await db.execute("DELETE FROM media_persona WHERE id='MR'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MR','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source) "
                         "VALUES ('MRC','MR','标题','published','legacy_text')")
        await q.enqueue_candidates(db, "MR", "MRC", "signature", [{"content": "你要知道", "evidence": "e"}])
        await db.commit()
        cur = await db.execute("SELECT id FROM media_mine_candidate WHERE persona_id='MR' LIMIT 1")
        rid = (await cur.fetchone())["id"]
        await db.close()
        return rid
    return asyncio.run(go())


def test_review_page_renders():
    _seed()
    r = _client().get("/media/mine-review")
    assert r.status_code == 200 and "你要知道" in r.text


def test_adopt_route():
    rid = _seed()
    r = _client().post("/media/mine-review/adopt", data={"candidate_ids": [rid]},
                       follow_redirects=False)
    assert r.status_code in (302, 303)
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait WHERE persona_id='MR'")
        assert (await cur.fetchone())["c"] >= 1
        await db.close()
    asyncio.run(chk())
