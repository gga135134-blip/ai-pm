"""批量后台端点：起 + 查进度。"""
import asyncio, base64, json, pytest
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
    tmp = tmp_path_factory.mktemp("br_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_content WHERE persona_id='RP'")
        await db.execute("DELETE FROM media_persona WHERE id='RP'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('RP','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                         "VALUES ('RC','RP','t','published','legacy_text','正文')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_batch_start_and_status(monkeypatch):
    _seed()
    monkeypatch.setattr("app.api.media.start_batch", lambda pid, op, ids: True)
    r = _client().post("/media/legacy/batch", data={"op": "organize", "content_ids": ["RC"]})
    assert r.status_code == 200 and r.json()["started"] is True

    monkeypatch.setattr("app.api.media.batch_get_status",
                        lambda pid: {"running": True, "op_label": "整理", "done": 1, "total": 3})
    r = _client().get("/media/legacy/batch-status")
    d = r.json()
    assert d["running"] is True and d["done"] == 1 and d["total"] == 3 and d["op"] == "整理"


def test_batch_empty_ids():
    _seed()
    r = _client().post("/media/legacy/batch", data={"op": "organize"})
    assert r.json()["ok"] is False
