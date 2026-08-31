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
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("lesson_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())

    async def seed():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES ('LSP','嘉','x','涨粉','active')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    yield
    _db_mod.DB_PATH = orig


def test_lessons_home_shows_brief():
    async def seed():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_lesson (id,persona_id,kind,brief,source) "
                "VALUES ('LID1','LSP','lesson','开头别铺垫，第一句抛冲突','manual')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    r = _client().get("/media/lessons")
    assert r.status_code == 200
    assert "开头别铺垫，第一句抛冲突" in r.text


def test_lesson_create_shows_on_page():
    r = _client().post("/media/lesson/create", data={
        "kind": "redline", "brief": "不许编造数据或案例",
        "trigger_context": "", "evidence": "手动新增",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)

    r = _client().get("/media/lessons")
    assert "不许编造数据或案例" in r.text


def test_lesson_adopt_dedupes():
    payload = {
        "kind": "lesson", "brief": "讲方法论别往技术细节钻",
        "trigger_context": "方法论类内容", "evidence": "8/20 复盘", "cycle_id": "",
    }
    r1 = _client().post("/media/lesson/adopt", data=payload, follow_redirects=False)
    assert r1.status_code in (302, 303)
    r2 = _client().post("/media/lesson/adopt", data=payload, follow_redirects=False)
    assert r2.status_code in (302, 303)

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT * FROM media_lesson WHERE persona_id='LSP' AND kind='lesson' "
                "AND brief='讲方法论别往技术细节钻'")
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0]["source"] == "l2_advisory"
        finally:
            await db.close()
    asyncio.run(check())


def test_review_cycle_detail_handles_legacy_string_advisory():
    cid = str(uuid.uuid4())

    async def seed():
        db = await get_db()
        try:
            metrics = {"content_count": 0, "avg": {"views": 0}, "median": {"views": 0},
                       "hit_count": 0, "flop_count": 0}
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,level,seq,metrics_summary,advisory) "
                "VALUES (?,?,?,?,?,?)",
                (cid, "LSP", "L2", 1, json.dumps(metrics, ensure_ascii=False),
                 json.dumps({"lessons": ["旧格式教训字符串"], "redlines": ["旧格式红线字符串"]},
                            ensure_ascii=False)))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    r = _client().get(f"/media/review-cycle/{cid}")
    assert r.status_code == 200
    assert "旧格式教训字符串" in r.text
