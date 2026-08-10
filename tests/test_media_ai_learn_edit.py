"""功能B learn_edit_style 单测：AI 打桩，验取数/夹取/兜底/成本记账。"""
import asyncio
import json

import app.services.media_ai as mai
from tests.media_helpers import make_db   # 内存 DB 已应用 SCHEMA + MIGRATIONS（含 ai_draft 等列）


async def _seed(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,current_phase,status) "
        "VALUES (?,?,?, 'active')", (pid, "嘉姐", "AI落地期"))
    # 3 条定稿：ai_draft 与 script 不同（有改动）；1 条 script==ai_draft（无改动，应排除）；
    # 1 条 authoring_stage!=finalized（应排除）
    rows = [
        ("C1", pid, "finalized", "首先我们要明确一个概念，那就是落地。", "落地。就这两个字。"),
        ("C2", pid, "finalized", "其次呢，我认为这个方案是可行的。", "这方案能成。"),
        ("C3", pid, "finalized", "总而言之，效果非常好。", "说白了，真香。"),
        ("C4", pid, "finalized", "一样的内容不该被学。", "一样的内容不该被学。"),
        ("C5", pid, "drafting",  "没定稿的稿。", "没定稿的改。"),
    ]
    for cid, p, stage, draft, script in rows:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,authoring_stage,"
            "ai_draft,script,finalized_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (cid, p, cid, stage, draft, script))
    await db.commit()


def test_learn_edit_style_only_reads_finalized_changed_pairs(monkeypatch):
    captured = {}

    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        captured["prompt"] = prompt
        return {"response": json.dumps({"traits": [
            {"dimension": "tone", "content": "开头不铺垫，直接抛结论",
             "brief": "开头直接抛结论", "evidence": "把'首先我们要明确'删成'落地。'",
             "confidence": 4}]}),
            "cost": 0.001, "model": "x", "tokens": 100}

    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.learn_edit_style(db, "P1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    assert res["pair_count"] == 3            # C1/C2/C3，排除 C4(无改动) C5(未定稿)
    assert "首先我们要明确" in captured["prompt"]   # 喂进了真实草稿
    assert "落地。就这两个字。" in captured["prompt"]  # 喂进了真实定稿
    assert res["traits"][0]["dimension"] == "tone"
    assert res["traits"][0]["phase_tag"] == ""       # tone 永久，phase_tag 空
    assert res["traits"][0]["confidence"] == 4


def test_learn_edit_style_clamps_dimension_and_confidence(monkeypatch):
    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps({"traits": [
            {"dimension": "positioning", "content": "越界维度归 tone",
             "brief": "b", "evidence": "e", "confidence": 99},
            {"dimension": "signature", "content": "招牌口头禅'说白了'",
             "brief": "说白了", "evidence": "多条都加了说白了", "confidence": "x"}]}),
            "cost": 0, "model": "x", "tokens": 10}

    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.learn_edit_style(db, "P1")
        await db.close()
        return res

    res = asyncio.run(go())
    dims = [t["dimension"] for t in res["traits"]]
    assert dims == ["tone", "signature"]             # positioning 越界→夹成 tone
    assert res["traits"][0]["confidence"] == 3       # 99 非法→默认 3
    assert res["traits"][1]["confidence"] == 3       # "x" 非法→默认 3


def test_learn_edit_style_empty_when_no_pairs(monkeypatch):
    async def fake_ask(*a, **k):
        raise AssertionError("没有可学的定稿时不该调 AI")

    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await db.execute(
            "INSERT INTO media_persona (id,name,current_phase,status) "
            "VALUES ('P1','嘉姐','AI落地期','active')")
        await db.commit()
        res = await mai.learn_edit_style(db, "P1")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True and res["pair_count"] == 0 and res["traits"] == []
