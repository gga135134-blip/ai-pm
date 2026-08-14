"""老文案进料/winner 路由。"""
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


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("legacy_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid="LGP"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES (?,?,?, '涨粉','active')", (pid, "嘉", "x"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_preview_then_commit_creates_contents():
    _seed_persona("LGP")
    r = _client().post("/media/reverse/paste-text/preview",
                       data={"text": "1. 甲\n正文\n2. 乙"})
    assert r.status_code == 200
    segs = r.json()["segments"]
    assert len(segs) == 2
    r2 = _client().post("/media/reverse/paste-text/commit",
                        data={"persona_id": "LGP", "segments": json.dumps(segs)})
    assert r2.status_code == 200 and r2.json()["count"] == 2

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT COUNT(*) n FROM media_content "
                                   "WHERE persona_id='LGP' AND idea_source='legacy_text'")
            assert (await cur.fetchone())["n"] == 2
        finally:
            await db.close()
    asyncio.run(check())


def test_mark_winner_batch():
    _seed_persona("LGP2")

    async def seed():
        db = await get_db()
        try:
            for cid in ("W1", "W2"):
                await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source) "
                                 "VALUES (?, 'LGP2', ?, 'published','legacy_text')", (cid, cid))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    r = _client().post("/media/legacy/mark-winner",
                       data={"content_ids": ["W1", "W2"], "winner": 1})
    assert r.status_code == 200

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT is_winner FROM media_content WHERE id='W1'")
            assert (await cur.fetchone())["is_winner"] == 1
        finally:
            await db.close()
    asyncio.run(check())
