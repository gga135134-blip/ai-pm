"""受众/锚点 AI 起草函数单测：AI 打桩，验夹取/兜底/成本记账/空回答短路。"""
import asyncio
import json

import app.services.media_ai as mai
from tests.media_helpers import make_db


async def _seed(db, pid="P1"):
    await db.execute(
        "INSERT INTO media_persona (id,name,current_phase,status) "
        "VALUES (?,?,?, 'active')", (pid, "嘉姐", "AI落地期"))
    await db.commit()


def test_draft_audience_clamps_fields(monkeypatch):
    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps({"segments": [
            {"segment": "焦虑的中小老板", "who": "35-50岁传统行业老板",
             "anxiety": "怕被AI淘汰又不懂", "desire": "花小钱把AI用起来",
             "objection": "怕交智商税", "language": "这玩意到底能不能落地",
             "pay_willingness": 99, "pay_scene": "看到同行用上了",
             "pay_ceiling": "几千", "evidence": "私信常问", "confidence": "x"}]}),
            "cost": 0, "model": "m", "tokens": 50}
    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.draft_audience_segments(db, "P1", "我的粉丝大多是传统老板")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    seg = res["segments"][0]
    assert seg["pay_willingness"] == 3      # 99 非法→默认 3
    assert seg["confidence"] == 3           # "x" 非法→默认 3
    assert seg["language"] == "这玩意到底能不能落地"


def test_draft_audience_empty_answer_no_ai(monkeypatch):
    async def fake_ask(*a, **k):
        raise AssertionError("空回答不该调 AI")
    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.draft_audience_segments(db, "P1", "   ")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["ok"] is False and res["segments"] == []


def test_draft_anchors_clamps_type(monkeypatch):
    async def fake_ask(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps({"anchors": [
            {"name": "1v1陪跑", "type": "乱写的类型", "value_prop": "手把手落地",
             "price_band": "几千", "path": "内容→私信→付费", "evidence": "成过3单"},
            {"name": "训练营", "type": "service", "value_prop": "系统课",
             "price_band": "低", "path": "内容→社群→报名", "evidence": ""}]}),
            "cost": 0, "model": "m", "tokens": 30}
    monkeypatch.setattr(mai, "ask_ai", fake_ask)

    async def go():
        db = await make_db()
        await _seed(db)
        res = await mai.draft_anchors(db, "P1", "我靠陪跑和训练营变现")
        await db.close()
        return res

    res = asyncio.run(go())
    assert res["anchors"][0]["type"] == "service"   # 越界→默认 service
    assert res["anchors"][1]["type"] == "service"
    assert res["anchors"][0]["name"] == "1v1陪跑"
