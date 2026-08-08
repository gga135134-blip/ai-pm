"""二期 🅐 生产线：换脑策略（纯）+ 采访/角度/草稿/审稿/修订（AI，asyncio.run）。"""
import asyncio
import json

from tests.media_helpers import make_db, fake_ai, seed_content
from app.services.media_ai import available_providers, resolve_reviewer_model
from app.services import media_ai


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


def test_extract_evidence_returns_reusable_candidates(monkeypatch):
    # 补料闭环B：AI 标可复用的料，返回给前端当"存入原料库"候选（带 id/material_type/brief）
    monkeypatch.setattr("app.services.media_ai.ask_ai",
                        fake_ai(json.dumps({"items": [
                            {"item": "帮鞋厂上客服AI三周上线", "item_type": "experience",
                             "reusable": True, "material_type": "pit", "brief": "鞋厂客服AI三周上线"},
                            {"item": "那天天气很热", "item_type": "experience",
                             "reusable": False}]}, ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await extract_evidence(db, "C1", "答复")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["count"] == 2
    items = res["items"]
    assert len(items) == 2
    reusable = [i for i in items if i["reusable"]]
    assert len(reusable) == 1
    assert reusable[0]["material_type"] == "pit"
    assert reusable[0]["brief"] == "鞋厂客服AI三周上线"
    assert reusable[0]["id"]  # 带 id，供前端 promote 回填


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


# ---------- write_script 升级（AI）----------

from app.services.media_ai import extract_gap_markers, write_script


def test_extract_gap_markers_pure():
    text = "开场抛问题。【缺真料：需要一个真实客户名字】中间讲案例。【缺真料：转化数字】"
    gaps = extract_gap_markers(text)
    assert gaps == ["需要一个真实客户名字", "转化数字"]


def test_extract_gap_markers_none():
    assert extract_gap_markers("干干净净的稿子") == []


def test_write_script_persists_draft_and_gap(monkeypatch):
    draft = "3秒抛谜题……【缺真料：具体鞋厂转化率】……结尾钩子。"
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai(draft))

    async def go():
        db = await make_db()
        await seed_content(db)
        res = await write_script(db, "C1", mode="full")
        cur = await db.execute(
            "SELECT ai_draft,evidence_gap,authoring_stage,script FROM media_content "
            "WHERE id='C1'")
        c = dict(await cur.fetchone())
        await db.close()
        return res, c

    res, c = asyncio.run(go())
    assert res["ok"] is True and res["script"] == draft
    assert c["ai_draft"] == draft          # 草稿进 ai_draft
    assert c["script"] == ""               # 定稿字段还没动
    assert "鞋厂转化率" in c["evidence_gap"]  # 缺口被抽出
    assert c["authoring_stage"] == "drafted"


def test_write_script_injects_selected_angle(monkeypatch):
    captured = {}

    async def spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        captured["prompt"] = prompt
        return {"response": "稿子", "model": "deepseek", "tokens": 5, "cost": 0}

    monkeypatch.setattr("app.services.media_ai.ask_ai", spy)

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("INSERT INTO media_angle (id,content_id,angle,rationale,is_selected)"
                         " VALUES ('a1','C1','从我踩过的坑切入','最可信',1)")
        await db.execute("UPDATE media_content SET selected_angle_id='a1' WHERE id='C1'")
        await db.execute("INSERT INTO media_evidence (id,content_id,persona_id,item,item_type)"
                         " VALUES ('e1','C1','P1','帮鞋厂上客服AI','experience')")
        await db.commit()
        await write_script(db, "C1", mode="full")
        await db.close()

    asyncio.run(go())
    assert "从我踩过的坑切入" in captured["prompt"]  # 角度注入了
    assert "帮鞋厂上客服AI" in captured["prompt"]    # 证据注入了


# ---------- 独立审稿 + 定向修订（AI）----------

from app.services.media_ai import critique_draft, revise_draft


def test_critique_writes_review_row(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai",
        fake_ai(json.dumps({
            "fact_flags": ["'80%企业'这个数字没出处"],
            "persona_flags": [], "platform_flags": [],
            "gap_flags": ["缺一个真实客户名"], "risk_flags": [],
            "score": 3, "verdict": "revise", "notes": "整体可用但要补一个真数字"
        }, ensure_ascii=False)))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("UPDATE media_content SET ai_draft='一版草稿' WHERE id='C1'")
        await db.commit()
        res = await critique_draft(db, "C1", strategy="layered")
        cur = await db.execute("SELECT * FROM media_draft_review WHERE content_id='C1'")
        row = dict(await cur.fetchone())
        await db.close()
        return res, row

    res, row = asyncio.run(go())
    assert res["ok"] is True and res["verdict"] == "revise" and res["score"] == 3
    assert row["reviewer_strategy"] == "layered"
    assert json.loads(row["fact_flags"])[0].startswith("'80%")
    assert row["reviewed_draft"] == "一版草稿"      # 审的哪版有快照
    assert "补一个真数字" in row["notes"]


def test_critique_needs_a_draft(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai("{}"))

    async def go():
        db = await make_db()
        await seed_content(db)  # ai_draft 为空
        res = await critique_draft(db, "C1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is False and "草稿" in res["error"]


def test_revise_updates_draft_and_counts(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai("改好的第二版草稿"))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute("UPDATE media_content SET ai_draft='第一版' WHERE id='C1'")
        await db.execute(
            "INSERT INTO media_draft_review (id,content_id,notes,verdict) "
            "VALUES ('r1','C1','补个真数字','revise')")
        await db.commit()
        res = await revise_draft(db, "C1")
        cur = await db.execute(
            "SELECT ai_draft,revision_count FROM media_content WHERE id='C1'")
        c = dict(await cur.fetchone())
        await db.close()
        return res, c

    res, c = asyncio.run(go())
    assert res["ok"] is True
    assert c["ai_draft"] == "改好的第二版草稿"
    assert c["revision_count"] == 1


def test_revise_refuses_second_time(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.ask_ai", fake_ai("不该被用到"))

    async def go():
        db = await make_db()
        await seed_content(db)
        await db.execute(
            "UPDATE media_content SET ai_draft='已改过', revision_count=1 WHERE id='C1'")
        await db.commit()
        res = await revise_draft(db, "C1")
        cur = await db.execute("SELECT ai_draft FROM media_content WHERE id='C1'")
        keep = (await cur.fetchone())["ai_draft"]
        await db.close()
        return res, keep

    res, keep = asyncio.run(go())
    assert res["ok"] is False and "一次" in res["error"]
    assert keep == "已改过"  # 没被覆盖


# ---------- 人设访谈（persona_interview_questions）----------

async def _seed_persona(db, pid="P1", phase="AI落地期"):
    await db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "务实落地AI", phase))
    await db.commit()


def test_persona_interview_questions_returns_list(monkeypatch):
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai",
                            fake_ai('{"questions":["你帮谁？","你跟同类不同在哪？"]}'))
        res = await media_ai.persona_interview_questions(db, "P1", "positioning")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["ok"] is True
    assert res["questions"] == ["你帮谁？", "你跟同类不同在哪？"]


def test_persona_interview_questions_unknown_module(monkeypatch):
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai('{"questions":[]}'))
        res = await media_ai.persona_interview_questions(db, "P1", "nonsense")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["ok"] is False
    assert "模块" in res["error"]


# ---------- 人设访谈提炼（persona_interview_extract）----------

def test_persona_interview_extract_returns_candidates_and_does_not_write(monkeypatch):
    payload = ('{"traits":[{"dimension":"positioning","content":"帮中小企业务实落地AI",'
               '"brief":"帮中小企业落地AI","evidence":"我自己就是做这个的","confidence":4}]}')
    async def go():
        db = await make_db()
        await _seed_persona(db, phase="AI落地期")
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai(payload))
        res = await media_ai.persona_interview_extract(
            db, "P1", "positioning", "我帮中小企业落地AI，自己就是做这个的")
        cur = await db.execute("SELECT COUNT(*) c FROM media_persona_trait")
        n = (await cur.fetchone())["c"]
        await db.close()
        return res, n
    res, n = asyncio.run(go())
    assert res["ok"] is True
    assert n == 0                                   # 绝不写库
    t = res["traits"][0]
    assert t["dimension"] == "positioning"
    assert t["phase_tag"] == "AI落地期"             # phase_bound 模块打当前阶段
    assert t["confidence"] == 4


def test_persona_interview_extract_permanent_module_empty_phase(monkeypatch):
    payload = '{"traits":[{"dimension":"taboo","content":"不编造本人经历","confidence":5}]}'
    async def go():
        db = await make_db()
        await _seed_persona(db, phase="AI落地期")
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai(payload))
        res = await media_ai.persona_interview_extract(db, "P1", "taboo", "绝不编造经历")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["traits"][0]["phase_tag"] == ""      # 永久模块 phase_tag 留空


def test_persona_interview_extract_empty_answers(monkeypatch):
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai('{"traits":[]}'))
        res = await media_ai.persona_interview_extract(db, "P1", "positioning", "   ")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["ok"] is False
    assert "空" in res["error"]


def test_persona_interview_extract_clamps_dimension_to_module(monkeypatch):
    # AI 乱给一个不属于本模块的维度，应被夹回本模块首维
    payload = '{"traits":[{"dimension":"taboo","content":"帮中小企业","confidence":3}]}'
    async def go():
        db = await make_db()
        await _seed_persona(db)
        monkeypatch.setattr(media_ai, "ask_ai", fake_ai(payload))
        res = await media_ai.persona_interview_extract(db, "P1", "positioning", "答案")
        await db.close()
        return res
    res = asyncio.run(go())
    assert res["traits"][0]["dimension"] == "positioning"   # 夹回 module_dims 首维
