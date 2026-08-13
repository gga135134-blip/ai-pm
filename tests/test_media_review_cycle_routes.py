"""L2 路由：触发（stub 后端）、详情页、adopt 白名单。"""
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
    tmp = tmp_path_factory.mktemp("l2_routes_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid="LP"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "一句话", "涨粉"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_trigger_route_passes_through(monkeypatch):
    _seed_persona("LP")

    async def fake(db, persona_id, model="auto", force=False):
        return {"ok": False, "warn": "才 2 条", "count": 2}
    monkeypatch.setattr("app.api.media.run_l2_cycle", fake)
    r = _client().post("/media/persona/LP/l2-review", data={"force": 0})
    assert r.status_code == 200 and r.json()["warn"].startswith("才")


def test_detail_page_renders():
    cid = str(uuid.uuid4())

    async def go():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "metrics_summary,patterns,hypotheses,period_end) "
                "VALUES (?,?,?,?,?,?,?,datetime('now'))",
                (cid, "LP", 1, json.dumps(["x"]),
                 json.dumps({"content_count": 1, "avg": {"views": 10},
                             "median": {"views": 10}, "hit_count": 0, "flop_count": 0,
                             "hit_content_ids": [], "flop_content_ids": []}),
                 json.dumps([{"pattern": "P", "evidence": "e", "confidence": "low"}]),
                 json.dumps([{"id": "h-1", "statement": "假设", "how_to_test": "t"}])))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())
    r = _client().get(f"/media/review-cycle/{cid}")
    assert r.status_code == 200 and "假设" in r.text


def test_adopt_trait_whitelist_accepts_l2_review():
    # 白名单加了 l2_review 后，interview/adopt 带 source=l2_review 应原样入库
    from app.services import media_ai  # noqa
    _seed_persona("LP2")
    r = _client().post("/media/persona/LP2/interview/adopt", data={
        "dimension": "signature", "content": "爱反问", "brief": "反问",
        "evidence": "", "confidence": 3, "source": "l2_review"})
    assert r.status_code in (200, 302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT source FROM media_persona_trait WHERE persona_id='LP2'")
            rows = [x["source"] for x in await cur.fetchall()]
            assert "l2_review" in rows
        finally:
            await db.close()
    asyncio.run(check())


def test_delete_route_removes_cycle_and_redirects():
    _seed_persona("LPDEL")          # 独立 persona，避免与他测的 cycle 引用冲突
    cid = str(uuid.uuid4())

    async def seed():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "period_end) VALUES (?,?,?,?,datetime('now'))",
                (cid, "LPDEL", 9, json.dumps(["x"])))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    r = _client().post(f"/media/review-cycle/{cid}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/media/persona"

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) n FROM media_review_cycle WHERE id=?", (cid,))
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(check())
