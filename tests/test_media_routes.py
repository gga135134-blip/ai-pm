"""🅐 过程端点路由测试。用 TestClient；AI 能力打桩，不真调模型。

登录：伪造 starlette SessionMiddleware 的签名 cookie（不输真实密码）。
"""
import asyncio
import base64
import json

import pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
from tests.media_helpers import seed_content


def _client():
    """已登录的 TestClient：塞一个 SessionMiddleware 能验的签名 cookie。"""
    signer = TimestampSigner(get_or_create_session_secret())
    data = base64.b64encode(json.dumps({"user": "test"}).encode())
    cookie = signer.sign(data).decode()
    c = TestClient(app)
    c.cookies.set("session", cookie)
    return c


@pytest.fixture(scope="module", autouse=True)
def _db_ready():
    asyncio.run(init_db())


def _seed_real():
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_content WHERE id='RT1'")
            await db.execute("DELETE FROM media_persona WHERE id='RTP'")
            await seed_content(db, persona_id="RTP", content_id="RT1")
        finally:
            await db.close()
    asyncio.run(go())


def test_interview_endpoint(monkeypatch):
    async def fake(db, cid, model="auto"):
        return {"ok": True, "questions": ["Q1", "Q2"], "cost": 0, "model": "x", "error": ""}
    monkeypatch.setattr("app.api.media.interview_questions", fake)
    _seed_real()
    r = _client().post("/media/content/RT1/interview")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["questions"] == ["Q1", "Q2"]


def test_evidence_endpoint(monkeypatch):
    async def fake(db, cid, answers, model="auto"):
        assert answers == "我的回答"
        return {"ok": True, "count": 3, "cost": 0, "model": "x", "error": ""}
    monkeypatch.setattr("app.api.media.extract_evidence", fake)
    _seed_real()
    r = _client().post("/media/content/RT1/evidence", data={"answers": "我的回答"})
    assert r.json()["count"] == 3


def test_finalize_sets_scripted():
    _seed_real()
    r = _client().post("/media/content/RT1/finalize", data={"script": "定稿内容"},
                       follow_redirects=False)
    assert r.status_code == 302

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT stage,authoring_stage,script FROM media_content WHERE id='RT1'")
            return dict(await cur.fetchone())
        finally:
            await db.close()
    c = asyncio.run(check())
    assert c["stage"] == "scripted" and c["authoring_stage"] == "finalized"
    assert c["script"] == "定稿内容"


def test_critique_endpoint(monkeypatch):
    async def fake(db, cid, strategy="layered", model="auto"):
        assert strategy in ("layered", "swap_model", "same_model")
        return {"ok": True, "score": 4, "verdict": "pass", "review_id": "rv",
                "reviewer_model": "claude", "cost": 0, "model": "x", "error": ""}
    monkeypatch.setattr("app.api.media.critique_draft", fake)
    _seed_real()
    r = _client().post("/media/content/RT1/critique")
    assert r.json()["verdict"] == "pass"
