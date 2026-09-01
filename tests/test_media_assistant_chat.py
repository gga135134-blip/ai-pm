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


def _clear_contents():
    """先清内容再清人设——外键开着，人设被内容引用时删不掉，
    单跑不报、连跑会把事务卡死（踩过）。"""
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_content WHERE persona_id IN ('AS','AS2')")
        await db.commit(); await db.close()
    asyncio.run(go())


def _seed_content(cid="AC1", title="热血广西革命历史", stage="scripted"):
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_content WHERE id=?", (cid,))
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,stage) VALUES (?, 'AS',?,?)",
            (cid, title, stage))
        await db.commit(); await db.close()
    asyncio.run(go())


def _capture_prompt(monkeypatch):
    """把喂给 agent 的 prompt 抓出来。"""
    seen = {}

    async def fake_loop(prompt, system, ctx=None, tool_schemas=None, dispatch=None, **kw):
        seen["prompt"] = prompt
        seen["system"] = system
        return {"response": "好", "model": "x", "tokens": 1, "cost": 0, "steps": []}

    monkeypatch.setattr(media_api, "run_agent_loop", fake_loop)
    return seen


def test_page_content_reaches_the_prompt(monkeypatch):
    """助手要知道用户此刻在看哪条——没有它只能反问「是哪条内容」，
    或者干脆在对话里写完稿再问「要不要保存」（真机踩到过）。"""
    _clear_contents(); _seed(); _seed_content()
    seen = _capture_prompt(monkeypatch)
    _client().post("/media/assistant/ask",
                   data={"message": "这条重写一版", "page_content_id": "AC1"})
    assert "用户此刻正在看" in seen["prompt"]
    assert "热血广西革命历史" in seen["prompt"]
    assert "AC1" in seen["prompt"]


def test_no_page_context_when_not_given(monkeypatch):
    """独立助手页没有当前内容，别硬塞一段假的上下文。"""
    _clear_contents(); _seed()
    seen = _capture_prompt(monkeypatch)
    _client().post("/media/assistant/ask", data={"message": "随便聊聊"})
    assert "用户此刻正在看" not in seen["prompt"]


def test_page_content_of_another_persona_is_ignored(monkeypatch):
    """前端传来的 id 不能盲信——不属于当前人设的内容不该被当成上下文。"""
    _seed()
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_persona WHERE id='AS2'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('AS2','别人','y','冷启动','active')")
        await db.execute("DELETE FROM media_content WHERE id='AC9'")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage) "
                         "VALUES ('AC9','AS2','别人的稿子','idea')")
        await db.commit(); await db.close()
    asyncio.run(go())

    seen = _capture_prompt(monkeypatch)
    _client().post("/media/assistant/ask",
                   data={"message": "这条重写", "page_content_id": "AC9"})
    assert "别人的稿子" not in seen["prompt"]


def test_system_prompt_forbids_writing_scripts_in_chat(monkeypatch):
    """纪律必须真的进系统提示词——真机上助手是在对话里写完稿再问
    「要不要我保存到内容库」，等于白写：没注入、不进库、脚本框看不到。"""
    _clear_contents(); _seed()
    seen = _capture_prompt(monkeypatch)
    _client().post("/media/assistant/ask", data={"message": "写一版"})
    assert "必须调 draft_script" in seen["system"]
    assert "要不要我保存到内容库" in seen["system"]
