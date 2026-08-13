"""L3 应用路由：切阶段/trait 归档晋升 —— 真改人设，且带校验。"""
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
    tmp = tmp_path_factory.mktemp("l3_apply_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


async def _seed(db, pid, phase, rid, phase_to):
    await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "x", phase))
    await db.execute(
        "INSERT INTO media_phase_review (id,persona_id,seq,phase_from,phase_reco,"
        "phase_to,created_at) VALUES (?,?,?,?, 'advance',?,datetime('now'))",
        (rid, pid, 1, phase, phase_to))
    await db.commit()


def test_apply_phase_advances_when_legal():
    pid, rid = "AP1", str(uuid.uuid4())
    asyncio.run(_run_seed(pid, "冷启动", rid, "涨粉"))
    r = _client().post(f"/media/phase-review/{rid}/apply-phase",
                       follow_redirects=False)
    assert r.status_code in (302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
            assert (await cur.fetchone())["current_phase"] == "涨粉"
        finally:
            await db.close()
    asyncio.run(check())


def test_apply_phase_rejects_illegal_jump():
    pid, rid = "AP2", str(uuid.uuid4())
    asyncio.run(_run_seed(pid, "冷启动", rid, "转化"))   # 跳级，非法
    _client().post(f"/media/phase-review/{rid}/apply-phase",
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
            assert (await cur.fetchone())["current_phase"] == "冷启动"   # 没变
        finally:
            await db.close()
    asyncio.run(check())


def test_apply_trait_archive_and_promote():
    pid, rid = "AP3", str(uuid.uuid4())
    tid_a, tid_p = "TA", "TP"
    asyncio.run(_run_seed(pid, "涨粉", rid, "转化"))

    async def seed_traits():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona_trait (id,persona_id,dimension,content,"
                "status,confidence) VALUES (?,?, 'signature','旧','active',3)", (tid_a, pid))
            await db.execute(
                "INSERT INTO media_persona_trait (id,persona_id,dimension,content,"
                "status,confidence) VALUES (?,?, 'signature','好','active',3)", (tid_p, pid))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_traits())

    _client().post(f"/media/phase-review/{rid}/apply-trait",
                   data={"trait_id": tid_a, "action": "archive"},
                   follow_redirects=False)
    _client().post(f"/media/phase-review/{rid}/apply-trait",
                   data={"trait_id": tid_p, "action": "promote"},
                   follow_redirects=False)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute("SELECT status FROM media_persona_trait WHERE id=?", (tid_a,))
            assert (await cur.fetchone())["status"] == "archived"
            cur = await db.execute("SELECT confidence FROM media_persona_trait WHERE id=?", (tid_p,))
            assert (await cur.fetchone())["confidence"] == 4    # 3+1
        finally:
            await db.close()
    asyncio.run(check())


async def _run_seed(pid, phase, rid, phase_to):
    db = await get_db()
    try:
        await _seed(db, pid, phase, rid, phase_to)
    finally:
        await db.close()
