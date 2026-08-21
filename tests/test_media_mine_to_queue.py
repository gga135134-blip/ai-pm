"""mine-to-queue 端点：signature 写候选打标 / essence 非爆款skip / 已挖skip / force。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.api.media as media_api
import app.services.media_batch as media_batch


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mq_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed(cid, is_winner):
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_mine_candidate WHERE persona_id='MQ'")
        await db.execute("DELETE FROM media_content WHERE persona_id='MQ'")
        await db.execute("DELETE FROM media_persona WHERE id='MQ'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MQ','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script,is_winner) "
                         "VALUES (?, 'MQ','t','published','legacy_text','转写正文你要知道',?)", (cid, is_winner))
        await db.commit(); await db.close()
    asyncio.run(go())


def test_signature_enqueues_and_marks(monkeypatch):
    _seed("Q1", 0)
    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": [{"content": "你要知道"}]}
    monkeypatch.setattr(media_batch, "mine_from_transcript", fake_mine)
    r = _client().post("/media/content/Q1/mine-to-queue", data={"kind": "signature"})
    assert r.status_code == 200 and r.json()["added"] == 1
    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT mined_signature_at FROM media_content WHERE id='Q1'")
        assert (await cur.fetchone())["mined_signature_at"] is not None
        cur = await db.execute("SELECT COUNT(*) c FROM media_mine_candidate WHERE kind='signature'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(chk())


def test_essence_skips_non_winner():
    _seed("Q2", 0)
    r = _client().post("/media/content/Q2/mine-to-queue", data={"kind": "essence"})
    assert r.status_code == 200 and r.json()["skipped"] == "not_winner"


def test_already_mined_skips_without_force(monkeypatch):
    _seed("Q3", 0)
    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": [{"content": "x"}]}
    monkeypatch.setattr(media_batch, "mine_from_transcript", fake_mine)
    _client().post("/media/content/Q3/mine-to-queue", data={"kind": "signature"})
    r = _client().post("/media/content/Q3/mine-to-queue", data={"kind": "signature"})
    assert r.json()["skipped"] == "already"
    r2 = _client().post("/media/content/Q3/mine-to-queue", data={"kind": "signature", "force": "1"})
    assert r2.json().get("skipped") != "already"
