"""老文案整理路由 + 撤销还原。"""
import asyncio, base64, json, pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.api.media as media_api
import app.services.media_batch as media_batch
from app.services import media_assistant as ma


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("org_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed():
    async def go():
        db = await get_db()
        await db.execute("DELETE FROM media_assistant_action WHERE persona_id='OG'")
        await db.execute("DELETE FROM media_content WHERE persona_id='OG'")
        await db.execute("DELETE FROM media_persona WHERE id='OG'")
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('OG','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
                         "VALUES ('OC','OG','老文案A','published','legacy_text','原始碎\n行\n正文')")
        await db.commit(); await db.close()
    asyncio.run(go())


def test_organize_updates_and_logs(monkeypatch):
    _seed()
    async def fake_org(script, model="auto"):
        return {"ok": True, "summary": "一句摘要", "formatted": "整理后的正文", "cost": 0, "model": "x"}
    monkeypatch.setattr(media_batch, "organize_content", fake_org)
    r = _client().post("/media/content/OC/organize")
    assert r.status_code == 200 and r.json()["summary"] == "一句摘要"

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT summary,script FROM media_content WHERE id='OC'")
        row = dict(await cur.fetchone())
        assert row["summary"] == "一句摘要" and row["script"] == "整理后的正文"
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action "
                               "WHERE action_type='organize_format' AND target_id='OC'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(chk())


def test_revert_organize_restores_script():
    async def go():
        db = await get_db()
        await db.execute("UPDATE media_content SET script='改过的' WHERE id='OC'")
        aid = await ma.log_action(db, "OG", "organize_format", "media_content", "OC",
                                  before={"script": "原来的"}, after={"script": "改过的"})
        await db.commit()
        ok = await ma.revert_action(db, aid)
        assert ok
        cur = await db.execute("SELECT script FROM media_content WHERE id='OC'")
        assert (await cur.fetchone())["script"] == "原来的"
        await db.close()
    asyncio.run(go())
