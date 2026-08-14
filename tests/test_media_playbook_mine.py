"""结构桶 mine + adopt(归并/新建)。"""
import asyncio
import base64
import json
import uuid
import pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_ai


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pbmine_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed(pid, cid, is_winner):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES (?,?,?, '涨粉','active')", (pid, "嘉", "x"))
            await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script,is_winner) "
                             "VALUES (?,?, 't','published','legacy_text','转写正文…',?)",
                             (cid, pid, is_winner))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_mine_winner_returns_playbook_candidate(monkeypatch):
    _seed("MP1", "MC1", 1)

    async def fake_mine(db, persona_id, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": []}

    async def fake_struct(db, persona_id, transcript, existing_names=None, model="auto"):
        return {"ok": True, "playbook": {"name": "痛点自曝法", "structure": "抛痛点→自曝→给法",
                "when_to_use": "焦虑选题", "evidence": "片段", "similar_to": ""}}
    monkeypatch.setattr("app.api.media.mine_from_transcript", fake_mine)
    monkeypatch.setattr("app.api.media.mine_structure", fake_struct)
    r = _client().post("/media/content/MC1/mine")
    assert r.status_code == 200 and r.json()["playbook_candidate"]["name"] == "痛点自曝法"


def test_mine_non_winner_no_playbook(monkeypatch):
    _seed("MP2", "MC2", 0)

    async def fake_mine(db, persona_id, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": []}
    monkeypatch.setattr("app.api.media.mine_from_transcript", fake_mine)
    # mine_structure 不该被调；若被调用 name 会出现，断言其不在
    r = _client().post("/media/content/MC2/mine")
    d = r.json()
    assert d.get("playbook_candidate") in (None,) and "playbook_candidate" not in d or d.get("playbook_candidate") is None


def test_adopt_playbook_new_then_merge():
    _seed("MP3", "MC3", 1)
    # 新建
    _client().post("/media/content/MC3/mine/adopt-playbook", data={
        "name": "痛点自曝法", "structure": "抛痛点→自曝→给法",
        "when_to_use": "焦虑选题", "evidence": "出自A", "similar_to": ""})
    # 归并（similar_to 命中）
    _client().post("/media/content/MC3/mine/adopt-playbook", data={
        "name": "痛点自曝法-又一例", "structure": "x", "when_to_use": "y",
        "evidence": "出自B", "similar_to": "痛点自曝法"})

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT evidence FROM media_playbook WHERE persona_id='MP3' AND name='痛点自曝法'")
            rows = [dict(r) for r in await cur.fetchall()]
            assert len(rows) == 1                    # 没新增第二条
            assert "出自A" in rows[0]["evidence"] and "出自B" in rows[0]["evidence"]  # evidence累积
        finally:
            await db.close()
    asyncio.run(check())
