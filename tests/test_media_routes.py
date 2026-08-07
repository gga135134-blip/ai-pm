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
import app.database as _db_mod
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
def _db_ready(tmp_path_factory):
    """路由测试隔离到临时 DB：不污染用户真实 aipm.db，也不与其它测试抢 WAL 锁。"""
    tmp = tmp_path_factory.mktemp("media_routes_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


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


def test_content_detail_renders_authoring_area():
    """真实渲染详情页创作区：验证新模板 Jinja2 语法（元组解包 for / dict.get /
    latest_review[key]）不炸，且草稿/角度/审稿/素材都出现。"""
    _seed_real()

    async def seed_authoring():
        db = await get_db()
        try:
            await db.execute(
                "UPDATE media_content SET stage='idea', authoring_stage='drafted', "
                "ai_draft='3秒抛谜题的草稿', evidence_gap='缺一个真实转化率' WHERE id='RT1'")
            await db.execute("DELETE FROM media_angle WHERE content_id='RT1'")
            await db.execute("INSERT INTO media_angle "
                "(id,content_id,angle,rationale,is_selected) "
                "VALUES ('ag1','RT1','从我踩过的坑切入','第一人称最可信',1)")
            await db.execute("DELETE FROM media_evidence WHERE content_id='RT1'")
            await db.execute("INSERT INTO media_evidence "
                "(id,content_id,persona_id,item,item_type) "
                "VALUES ('ev1','RT1','RTP','帮鞋厂上客服AI','experience')")
            await db.execute("DELETE FROM media_draft_review WHERE content_id='RT1'")
            await db.execute("INSERT INTO media_draft_review "
                "(id,content_id,fact_flags,gap_flags,score,verdict,notes) "
                "VALUES ('dr1','RT1','[\"80%这个数字没出处\"]','[]',3,'revise','要补真数字')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_authoring())

    r = _client().get("/media/content/RT1")
    assert r.status_code == 200
    html = r.text
    assert "创作辅助" in html
    assert "从我踩过的坑切入" in html      # 角度渲染
    assert "缺一个真实转化率" in html        # 缺口警告
    assert "审稿意见" in html and "要补真数字" in html  # 审稿渲染(元组解包for)
    assert "帮鞋厂上客服AI" in html          # 素材包渲染


def test_settings_page_shows_review_strategy():
    r = _client().get("/settings")
    assert r.status_code == 200
    assert "审稿独立性" in r.text


def _seed_persona_real(pid="RTP2", phase="AI落地期"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona_trait WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "务实落地AI", phase))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def test_persona_interview_questions_route(monkeypatch):
    async def fake(db, persona_id, module, model="auto"):
        return {"ok": True, "questions": ["Q1", "Q2"], "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr("app.api.media.persona_interview_questions", fake)
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/positioning/questions")
    assert r.status_code == 200
    assert r.json()["questions"] == ["Q1", "Q2"]


def test_persona_interview_extract_route(monkeypatch):
    async def fake(db, persona_id, module, answers, model="auto"):
        return {"ok": True, "traits": [{"dimension": "positioning", "content": "帮中小企业",
                "brief": "帮中小企业", "evidence": "原话", "confidence": 4,
                "phase_tag": "AI落地期"}], "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr("app.api.media.persona_interview_extract", fake)
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/positioning/extract",
                       data={"answers": "我帮中小企业落地AI"})
    assert r.status_code == 200
    assert r.json()["traits"][0]["phase_tag"] == "AI落地期"


def _count_traits(pid, **where):
    async def go():
        db = await get_db()
        try:
            sql = "SELECT * FROM media_persona_trait WHERE persona_id=?"
            args = [pid]
            for k, v in where.items():
                sql += f" AND {k}=?"
                args.append(v)
            cur = await db.execute(sql, args)
            rows = [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
        return rows
    return asyncio.run(go())


def test_persona_interview_adopt_writes_interview_source():
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "positioning", "content": "帮中小企业务实落地AI",
        "brief": "帮中小企业落地AI", "confidence": "4",
        "evidence": "我自己就是做这个的", "phase_tag": "AI落地期"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = _count_traits("RTP2", dimension="positioning")
    assert len(rows) == 1
    assert rows[0]["source"] == "interview"
    assert rows[0]["phase_tag"] == "AI落地期"
    assert rows[0]["status"] == "active"


def test_new_phase_archives_old_phase_actives_keeps_permanent():
    _seed_persona_real(phase="旧带货期")
    c = _client()
    # 一条旧阶段定位（会归档）+ 一条永久红线（phase_tag 空，保留）
    c.post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "positioning", "content": "教你月入十万",
        "brief": "月入十万", "confidence": "3", "evidence": "", "phase_tag": "旧带货期"})
    c.post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "taboo", "content": "不编造本人经历",
        "brief": "不编造", "confidence": "5", "evidence": "", "phase_tag": ""})
    r = c.post("/media/persona/RTP2/new-phase", data={"new_phase": "AI落地期"},
               follow_redirects=False)
    assert r.status_code == 302
    actives = _count_traits("RTP2", status="active")
    archived = _count_traits("RTP2", status="archived")
    assert {a["dimension"] for a in actives} == {"taboo"}       # 永久红线还在
    assert {a["dimension"] for a in archived} == {"positioning"}  # 旧阶段定位归档
    # current_phase 已更新
    async def phase():
        db = await get_db()
        try:
            row = await (await db.execute(
                "SELECT current_phase FROM media_persona WHERE id='RTP2'")).fetchone()
        finally:
            await db.close()
        return row["current_phase"]
    assert asyncio.run(phase()) == "AI落地期"
