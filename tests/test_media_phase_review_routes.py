"""L3 读路由：触发（stub）、详情、删除。"""
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


def _client():
    signer = TimestampSigner(get_or_create_session_secret())
    data = base64.b64encode(json.dumps({"user": "test"}).encode())
    c = TestClient(app)
    c.cookies.set("session", signer.sign(data).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("l3_routes_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid, phase="冷启动"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "一句话", phase))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_trigger_passes_through(monkeypatch):
    _seed_persona("LP3")

    async def fake(db, persona_id, model="auto", force=False):
        return {"ok": False, "warn": "才 1 轮 L2", "count": 1}
    monkeypatch.setattr("app.api.media.run_l3_review", fake)
    r = _client().post("/media/persona/LP3/l3-review", data={"force": 0})
    assert r.status_code == 200 and r.json()["warn"].startswith("才")


def test_detail_renders():
    _seed_persona("LP3D")
    rid = str(uuid.uuid4())

    async def go():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_phase_review (id,persona_id,seq,phase_from,"
                "phase_reco,phase_to,phase_reason,phase_signals,metrics_trend,"
                "trait_actions,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (rid, "LP3D", 1, "冷启动", "advance", "涨粉", "数据达标",
                 json.dumps([{"signal": "累计爆款数", "value": 2, "ref": 1,
                              "met": True}]),
                 json.dumps({"series": []}), json.dumps([])))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())
    r = _client().get(f"/media/phase-review/{rid}")
    assert r.status_code == 200 and "数据达标" in r.text


def test_delete_removes_and_redirects():
    _seed_persona("LP3X")
    rid = str(uuid.uuid4())

    async def seed():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_phase_review (id,persona_id,seq,phase_from,"
                "created_at) VALUES (?,?,?,?,datetime('now'))",
                (rid, "LP3X", 1, "冷启动"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    r = _client().post(f"/media/phase-review/{rid}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/media/persona"

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) n FROM media_phase_review WHERE id=?", (rid,))
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(check())
