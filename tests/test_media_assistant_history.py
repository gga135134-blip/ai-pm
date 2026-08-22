"""助手对话历史端点。"""
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
    tmp = tmp_path_factory.mktemp("hist_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_history():
    async def seed():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='HP'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('HP','嘉','x','涨粉','active')")
        import uuid
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content) "
                         "VALUES (?, 'HP','user','你好')", (str(uuid.uuid4()),))
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content) "
                         "VALUES (?, 'HP','assistant','在的')", (str(uuid.uuid4()),))
        await db.commit(); await db.close()
    asyncio.run(seed())
    r = _client().get("/media/assistant/history")
    d = r.json()
    assert [m["role"] for m in d["messages"]] == ["user", "assistant"]
    assert d["messages"][1]["content"] == "在的"
