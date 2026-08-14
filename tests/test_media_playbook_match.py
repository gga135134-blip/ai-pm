"""打法库→选题 隔离匹配。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_ai


def _content(title="老板买了AI用不起来"):
    return {"id": "C1", "title": title, "puzzle": "为什么？", "idea_reason": "受众焦虑"}


def _stub(resp, calls):
    async def go(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        calls.append(prompt)
        return {"response": resp, "model": "deepseek", "tokens": 5, "cost": 0.0}
    return go


def test_match_hit(monkeypatch):
    db = make_db_sync()
    calls = []
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"PB1","reason":"命中焦虑受众"}', calls))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"]["id"] == "PB1" and r["playbook"]["reason"] == "命中焦虑受众"
    assert r["playbook"]["name"] == "痛点自曝法"
    asyncio.run(db.close())


def test_match_none_when_unsuitable(monkeypatch):
    db = make_db_sync()
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"","reason":""}', []))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"] is None
    asyncio.run(db.close())


def test_match_bogus_id(monkeypatch):
    db = make_db_sync()
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"NOPE","reason":"x"}', []))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"] is None
    asyncio.run(db.close())


def test_match_empty_pool_no_ai_call(monkeypatch):
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MPB','嘉','x','涨粉','active')")  # 无 playbook
        await db.commit()
        return db
    db = asyncio.run(go())
    called = []
    monkeypatch.setattr(media_ai, "ask_ai", _stub('{"playbook_id":"PB1"}', called))
    r = asyncio.run(media_ai.match_playbook(db, _content()))
    assert r["playbook"] is None and called == []   # 池空不调 AI
    asyncio.run(db.close())


def make_db_sync():
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('MPB','嘉','x','涨粉','active')")
        for pid, name, wtu in (("PB1", "痛点自曝法", "焦虑/踩坑类选题"),
                               ("PB2", "数据打脸法", "反常识类选题")):
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,when_to_use,structure,status) "
                             "VALUES (?,?,?,?, '抛→转→收','validating')", (pid, "MPB", name, wtu))
        await db.commit()
        return db
    return asyncio.run(go())
