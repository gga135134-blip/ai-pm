"""助手对话端点：存消息 + 跑 agent。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.api.media as media_api


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ast_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_message WHERE persona_id='AS'")
        await db.execute("DELETE FROM media_persona WHERE id='AS'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('AS','嘉','x','涨粉','active')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_ask_stores_messages(monkeypatch):
    _seed()
    async def fake_loop(prompt, system, ctx=None, tool_schemas=None, dispatch=None, **kw):
        return {"response": "我建了一条选题", "model": "x", "tokens": 1, "cost": 0.01, "steps": [{"tool": "create_topic", "args": {}}]}
    monkeypatch.setattr(media_api, "run_agent_loop", fake_loop)
    r = _client().post("/media/assistant/ask", data={"message": "帮我建个选题"})
    assert r.status_code == 200 and "我建了一条选题" in r.json()["reply"]

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT role FROM media_assistant_message WHERE persona_id='AS' ORDER BY created_at")
        roles = [r["role"] for r in await cur.fetchall()]
        assert roles == ["user", "assistant"]
        await db.close()
    asyncio.run(chk())


def test_page_renders():
    _seed()
    r = _client().get("/media/assistant")
    assert r.status_code == 200
