"""二期 🅐 生产线：换脑策略（纯）+ 采访/角度/草稿/审稿/修订（AI，asyncio.run）。"""
import asyncio
import json

from tests.media_helpers import make_db, fake_ai, seed_content
from app.services.media_ai import available_providers, resolve_reviewer_model


# ---------- 换脑策略（纯函数）----------

def test_available_providers_orders_by_configured_keys():
    cfg = {"deepseek_api_key": "x", "anthropic_api_key": "y"}
    assert available_providers(cfg) == ["claude", "deepseek"]


def test_available_providers_empty():
    assert available_providers({}) == []


def test_swap_model_forces_different_provider():
    got = resolve_reviewer_model("swap_model", "deepseek", ["claude", "deepseek"])
    assert got == "claude"


def test_swap_model_single_provider_degrades():
    got = resolve_reviewer_model("swap_model", "deepseek", ["deepseek"])
    assert got == "deepseek"


def test_same_model_returns_writer():
    assert resolve_reviewer_model("same_model", "deepseek", ["claude", "deepseek"]) == "deepseek"


def test_layered_returns_auto():
    assert resolve_reviewer_model("layered", "deepseek", ["claude", "deepseek"]) == "auto"
    assert resolve_reviewer_model("", "deepseek", ["claude"]) == "auto"  # 缺省即 layered


# ---------- 采访补料（AI）----------

from app.services.media_ai import interview_questions, extract_evidence


def test_interview_questions_returns_list(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"questions": [
                            "你自己帮企业落地AI时最惨的一次是什么？",
                            "有没有具体的转化率/成本数字？"]}, ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await interview_questions(db, "C1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    assert len(res["questions"]) == 2
    assert "转化率" in res["questions"][1]


def test_extract_evidence_writes_rows(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"items": [
                            {"item": "帮做鞋厂上客服AI，三周上线", "item_type": "experience"},
                            {"item": "人力省了2个", "item_type": "data"}]},
                            ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await extract_evidence(db, "C1", "我去年帮一个鞋厂做的……省了2个人力")
        cur = await db.execute(
            "SELECT item,item_type,source FROM media_evidence WHERE content_id='C1' "
            "ORDER BY item_type")
        rows = [dict(r) for r in await cur.fetchall()]
        await db.close()
        return res, rows

    res, rows = asyncio.run(go())
    assert res["ok"] is True and res["count"] == 2
    assert all(r["source"] == "interview" for r in rows)
    assert any("鞋厂" in r["item"] for r in rows)


# ---------- 角度候选（AI）----------

from app.services.media_ai import propose_angles


def test_propose_angles_writes_and_selects_first(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"angles": [
                            {"angle": "从我踩过的坑切入", "rationale": "第一人称最可信"},
                            {"angle": "从一个鞋厂案例切入", "rationale": "具体可感"}]},
                            ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await propose_angles(db, "C1")
        cur = await db.execute(
            "SELECT id,angle,is_selected FROM media_angle WHERE content_id='C1' "
            "ORDER BY is_selected DESC")
        angles = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT selected_angle_id FROM media_content WHERE id='C1'")
        sel = (await cur.fetchone())["selected_angle_id"]
        await db.close()
        return res, angles, sel

    res, angles, sel = asyncio.run(go())
    assert res["ok"] is True and res["count"] == 2
    assert angles[0]["is_selected"] == 1 and "踩过的坑" in angles[0]["angle"]
    assert sum(a["is_selected"] for a in angles) == 1  # 只选一个
    assert sel == angles[0]["id"] == res["selected_id"]


def test_propose_angles_replaces_old(monkeypatch):
    # 二次调用应清掉旧角度，不堆积
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"angles": [{"angle": "新角度", "rationale": "r"}]},
                                           ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("INSERT INTO media_angle (id,content_id,angle) VALUES "
                         "('old','C1','旧角度')")
        await db.commit()
        await propose_angles(db, "C1")
        cur = await db.execute("SELECT angle FROM media_angle WHERE content_id='C1'")
        rows = [r["angle"] for r in await cur.fetchall()]
        await db.close()
        return rows

    rows = asyncio.run(go())
    assert rows == ["新角度"]  # 旧的被清掉
