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
            # 先清依赖行，避免外键约束挡住删除（测试间不互相污染）
            for t in ("media_evidence", "media_angle", "media_draft_review"):
                await db.execute(f"DELETE FROM {t} WHERE content_id='RT1'")
            await db.execute("DELETE FROM media_material WHERE persona_id='RTP'")
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


def test_evidence_promote_writes_material_and_backfills():
    """补料闭环B：人拍板把一条evidence存进原料库→写media_material+回填promoted_to_material_id。"""
    _seed_real()

    async def seed_ev():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_evidence WHERE id='EVP1'")
            await db.execute("INSERT INTO media_evidence "
                "(id,content_id,persona_id,item,item_type) "
                "VALUES ('EVP1','RT1','RTP','帮鞋厂上客服AI三周上线','experience')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_ev())

    client = TestClient(app)
    signer = TimestampSigner(get_or_create_session_secret())
    client.cookies.set("session", signer.sign(
        base64.b64encode(json.dumps({"user": "test"}).encode())).decode())
    r = client.post("/media/content/RT1/evidence/EVP1/promote",
                    data={"item": "帮鞋厂上客服AI三周上线", "material_type": "pit",
                          "brief": "鞋厂客服AI三周上线"})
    assert r.status_code == 200 and r.json()["ok"] is True
    mid = r.json()["material_id"]

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT type,detail,brief FROM media_material WHERE id=?", (mid,))
            mat = dict(await cur.fetchone())
            cur = await db.execute(
                "SELECT promoted_to_material_id FROM media_evidence WHERE id='EVP1'")
            promoted = (await cur.fetchone())["promoted_to_material_id"]
            return mat, promoted
        finally:
            await db.close()
    mat, promoted = asyncio.run(check())
    assert mat["type"] == "pit" and "鞋厂" in mat["detail"]
    assert mat["brief"] == "鞋厂客服AI三周上线"
    assert promoted == mid  # 回填了，避免重复入库


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
            await db.execute("DELETE FROM media_material WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_audience WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_anchor WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_content WHERE persona_id=?", (pid,))
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


def test_persona_interview_page_renders_seven_modules():
    _seed_persona_real()
    r = _client().get("/media/persona/RTP2/interview")
    assert r.status_code == 200
    assert "你是谁·定位" in r.text
    assert "生意锚点" in r.text        # 第 7 模块 anchor 在页面上


# ─────────────── 原料库 ───────────────

def _only_active_persona(pid="RTP2", phase="AI落地期"):
    """页面用 _first_persona_id 取第一个 active 人设。先把其它人设归档，
    保证 pid 是页面会命中的那个（消除测试间人设互相干扰）。"""
    async def go():
        db = await get_db()
        try:
            await db.execute("UPDATE media_persona SET status='archived'")
            await db.execute("DELETE FROM media_material WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_audience WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_anchor WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_content WHERE persona_id=?", (pid,))
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "务实落地AI", phase))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def _seed_material(pid, mid, mtype="story", detail="帮鞋厂三周上线客服AI",
                   brief="鞋厂客服AI三周", use_count=0, source="补料"):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_material WHERE id=?", (mid,))
            await db.execute(
                "INSERT INTO media_material "
                "(id,persona_id,type,title,detail,brief,use_count,source) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (mid, pid, mtype, detail[:40], detail, brief, use_count, source))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def _material_row(mid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute("SELECT * FROM media_material WHERE id=?", (mid,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await db.close()
    return asyncio.run(go())


def test_materials_page_groups_by_type_and_shows_fatigue():
    _only_active_persona()
    _seed_material("RTP2", "MAT1", mtype="pit",
                   detail="帮鞋厂三周上线客服AI但没算准并发", brief="鞋厂客服AI三周", use_count=0)
    _seed_material("RTP2", "MAT2", mtype="quote",
                   detail="自己试过才算数", brief="自己试过才算数", use_count=5)  # 用旧了
    r = _client().get("/media/materials")
    assert r.status_code == 200
    html = r.text
    assert "原料库" in html
    assert "踩过的坑" in html and "金句" in html    # 类型中文标签分组
    assert "鞋厂客服AI三周" in html                  # brief 渲染
    assert "用过 5 次" in html                        # use_count 渲染
    assert "该换新料了" in html                       # 疲劳提示（use_count≥阈值3）


def test_material_manual_create_writes_row():
    _seed_persona_real()
    r = _client().post("/media/materials", data={
        "persona_id": "RTP2", "type": "judgment",
        "detail": "小公司别自己训模型，调 API 更划算",
        "brief": "小公司调API别自训", "usable_scene": "劝退自训模型",
        "emotion": "笃定"}, follow_redirects=False)
    assert r.status_code == 302

    async def find():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT * FROM media_material WHERE persona_id='RTP2' AND type='judgment'")
            return dict(await cur.fetchone())
        finally:
            await db.close()
    row = asyncio.run(find())
    assert row["source"] == "随手记"
    assert "调 API" in row["detail"] and row["brief"] == "小公司调API别自训"
    assert row["status"] == "active"


def test_material_archive_soft_deletes():
    _seed_persona_real()
    _seed_material("RTP2", "MATA", use_count=0)
    r = _client().post("/media/material/MATA/archive", follow_redirects=False)
    assert r.status_code == 302
    assert _material_row("MATA")["status"] == "archived"


def test_archived_material_not_shown_on_page():
    _only_active_persona()
    _seed_material("RTP2", "MATH", detail="只出现在列表的活跃料", brief="活跃料唯一标记")
    _seed_material("RTP2", "MATG", detail="已归档不该出现", brief="归档料唯一标记")
    asyncio.run(_archive("MATG"))
    html = _client().get("/media/materials").text
    assert "活跃料唯一标记" in html
    assert "归档料唯一标记" not in html


async def _archive(mid):
    db = await get_db()
    try:
        await db.execute("UPDATE media_material SET status='archived' WHERE id=?", (mid,))
        await db.commit()
    finally:
        await db.close()


# ─────────────── 功能B：AI 学改稿 ───────────────

def test_adopt_accepts_learned_edit_source():
    """adopt 加 source 参数：功能B 传 learned_edit，写进 trait.source。"""
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "tone", "content": "开头不铺垫直接抛结论",
        "brief": "开头直接抛结论", "confidence": "4",
        "evidence": "把'首先我们要明确'删成'落地。'", "phase_tag": "",
        "source": "learned_edit"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = _count_traits("RTP2", dimension="tone")
    assert len(rows) == 1
    assert rows[0]["source"] == "learned_edit"


def test_adopt_source_defaults_to_interview():
    """不传 source 时仍写 interview —— 保护现有访谈流程向后兼容。"""
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/interview/adopt", data={
        "dimension": "signature", "content": "招牌收尾'说白了'",
        "brief": "说白了", "confidence": "5", "evidence": "", "phase_tag": ""})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = _count_traits("RTP2", dimension="signature")
    assert rows[0]["source"] == "interview"


def test_learn_edits_route_returns_candidates(monkeypatch):
    async def fake(db, persona_id, model="auto"):
        return {"ok": True, "traits": [{"dimension": "tone",
                "content": "长句拆短", "brief": "长句拆短", "evidence": "例子",
                "confidence": 4, "phase_tag": ""}],
                "pair_count": 3, "error": "", "cost": 0, "model": "x"}
    monkeypatch.setattr("app.api.media.learn_edit_style", fake)
    _seed_persona_real()
    r = _client().post("/media/persona/RTP2/learn-edits")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["pair_count"] == 3
    assert body["traits"][0]["dimension"] == "tone"


def test_persona_page_shows_learn_edit_block_with_count():
    """人设页渲染学改稿块，显示可学定稿数。"""
    _seed_persona_real()

    async def seed_finalized():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_content WHERE persona_id='RTP2'")
            # 2 条有改动的定稿 + 1 条无改动（不计入）
            await db.execute(
                "INSERT INTO media_content (id,persona_id,title,authoring_stage,"
                "ai_draft,script,finalized_at) VALUES "
                "('LC1','RTP2','t1','finalized','AI草稿一','定稿一',CURRENT_TIMESTAMP),"
                "('LC2','RTP2','t2','finalized','AI草稿二','定稿二',CURRENT_TIMESTAMP),"
                "('LC3','RTP2','t3','finalized','一样的','一样的',CURRENT_TIMESTAMP)")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_finalized())

    r = _client().get("/media/persona/RTP2")
    assert r.status_code == 200
    assert "AI 学我改稿" in r.text
    assert "2 条" in r.text          # learnable_count=2（LC3 无改动排除）


# ─────────────── 受众画像 ───────────────

def _audience_rows(pid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT * FROM media_audience WHERE persona_id=? ORDER BY created_at", (pid,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    return asyncio.run(go())


def test_audience_manual_create():
    _seed_persona_real()
    r = _client().post("/media/audience", data={
        "persona_id": "RTP2", "segment": "焦虑的中小老板", "who": "35-50传统行业",
        "anxiety": "怕被AI淘汰", "language": "这玩意能落地不", "pay_willingness": "4"},
        follow_redirects=False)
    assert r.status_code == 302
    rows = [x for x in _audience_rows("RTP2") if x["segment"] == "焦虑的中小老板"]
    assert len(rows) == 1
    assert rows[0]["source"] == "manual" and rows[0]["pay_willingness"] == 4
    assert rows[0]["language"] == "这玩意能落地不"


def test_audience_draft_route(monkeypatch):
    async def fake(db, pid, answers, model="auto"):
        return {"ok": True, "segments": [{"segment": "S1", "who": "w", "anxiety": "a",
                "desire": "d", "objection": "o", "language": "原话", "pay_willingness": 4,
                "pay_scene": "ps", "pay_ceiling": "pc", "evidence": "e", "confidence": 3}],
                "error": "", "cost": 0, "model": "m"}
    monkeypatch.setattr("app.api.media.draft_audience_segments", fake)
    _seed_persona_real()
    r = _client().post("/media/audience/draft", data={"answers": "我的粉丝是老板"})
    assert r.status_code == 200
    assert r.json()["segments"][0]["language"] == "原话"


def test_audience_adopt_writes_row():
    _seed_persona_real()
    r = _client().post("/media/audience/adopt", data={
        "persona_id": "RTP2", "segment": "S2", "who": "w", "anxiety": "a",
        "language": "原话2", "pay_willingness": "5", "confidence": "4"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = [x for x in _audience_rows("RTP2") if x["segment"] == "S2"]
    assert rows[0]["source"] == "interview" and rows[0]["pay_willingness"] == 5


def test_audience_archive_and_page():
    _only_active_persona()

    async def seed():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_audience WHERE persona_id='RTP2'")
            await db.execute("INSERT INTO media_audience "
                "(id,persona_id,segment,pay_willingness,status) VALUES "
                "('AUD1','RTP2','高付费段',5,'active'),"
                "('AUD2','RTP2','低付费段',2,'active')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    r = _client().post("/media/audience/AUD2/archive", follow_redirects=False)
    assert r.status_code == 302

    html = _client().get("/media/audience").text
    assert "高付费段" in html            # active 显示
    assert "低付费段" not in html         # archived 不显示


# ─────────────── 生意锚点 ───────────────

def _anchor_rows(pid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT * FROM media_anchor WHERE persona_id=? ORDER BY created_at", (pid,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    return asyncio.run(go())


def test_anchor_manual_create():
    _seed_persona_real()
    r = _client().post("/media/anchor", data={
        "persona_id": "RTP2", "name": "1v1陪跑", "type": "service",
        "value_prop": "手把手落地", "price_band": "几千", "path": "内容→私信→付费"},
        follow_redirects=False)
    assert r.status_code == 302
    rows = [x for x in _anchor_rows("RTP2") if x["name"] == "1v1陪跑"]
    assert rows[0]["source"] == "manual" and rows[0]["type"] == "service"


def test_anchor_draft_route(monkeypatch):
    async def fake(db, pid, answers, model="auto"):
        return {"ok": True, "anchors": [{"name": "训练营", "type": "service",
                "value_prop": "系统课", "price_band": "低", "path": "内容→社群→报名",
                "evidence": ""}], "error": "", "cost": 0, "model": "m"}
    monkeypatch.setattr("app.api.media.draft_anchors", fake)
    _seed_persona_real()
    r = _client().post("/media/anchor/draft", data={"answers": "我靠训练营变现"})
    assert r.status_code == 200
    assert r.json()["anchors"][0]["name"] == "训练营"


def test_anchor_adopt_and_archive_and_page():
    _only_active_persona()
    c = _client()
    r = c.post("/media/anchor/adopt", data={
        "persona_id": "RTP2", "name": "已跑通锚点", "type": "service",
        "value_prop": "vp", "status": "proven"})
    assert r.json()["ok"] is True

    async def seed_more():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_anchor "
                "(id,persona_id,name,type,status) VALUES ('ANC_D','RTP2','废弃锚点','service','dropped')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed_more())

    r = c.post("/media/anchor/ANC_D/archive", follow_redirects=False)
    assert r.status_code == 302
    html = c.get("/media/anchor").text
    assert "已跑通锚点" in html         # active 显示
    assert "废弃锚点" not in html        # archived 不显示


# ─────────────── 决策引擎 ───────────────

def test_topics_rank_writes_scores():
    _only_active_persona()

    async def seed():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_topic WHERE persona_id='RTP2'")
            await db.execute("INSERT INTO media_audience "
                "(id,persona_id,segment,anxiety,language,pay_willingness,status) VALUES "
                "('SG1','RTP2','焦虑老板','中小企业AI落地难','能落地不',5,'active')")
            await db.execute("INSERT INTO media_topic "
                "(id,persona_id,title,puzzle,fit_score,heat,status) VALUES "
                "('TP_HI','RTP2','中小企业AI落地为什么这么难','',5,5,'pool'),"
                "('TP_LO','RTP2','随便一个不相关话题','',1,1,'pool')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())

    r = _client().post("/media/topics/rank", follow_redirects=False)
    assert r.status_code == 302

    async def check():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT id,decision_score,decision_report FROM media_topic "
                "WHERE persona_id='RTP2' ORDER BY decision_score DESC")
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    rows = asyncio.run(check())
    assert rows[0]["id"] == "TP_HI"                 # 高契合+命中受众 排前
    assert rows[0]["decision_score"] > rows[1]["decision_score"]
    assert "决策得分" in rows[0]["decision_report"]
    assert "未计" in rows[0]["decision_report"]      # C 类降级标注在
