"""write_script 注入一条打法骨架。"""
import asyncio
import json
from tests.media_helpers import make_db
from app.services import media_ai


def _setup():
    async def go():
        db = await make_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('WP','嘉','帮企业落地AI','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source) "
                         "VALUES ('WC','WP','老板买AI用不起来','为什么','idea','manual')")
        for pid, name in (("PB1", "痛点自曝法"), ("PB2", "数据打脸法")):
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,when_to_use,structure,status) "
                             "VALUES (?,?,?, '焦虑选题','前3秒抛痛点→自曝→给法','proven')", (pid, "WP", name))
        await db.commit()
        return db
    return asyncio.run(go())


def _capture_write(captured):
    """替 write 那次 ask_ai：记录 prompt，返回固定脚本。"""
    async def go(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        captured["prompt"] = prompt
        return {"response": "这是脚本正文。", "model": "deepseek", "tokens": 9, "cost": 0.0}
    return go


def _fixed_match(pb):
    async def go(db, content, model="auto"):
        return {"ok": True, "playbook": pb, "cost": 0, "model": ""}
    return go


def _pb(pid="PB1", name="痛点自曝法"):
    return {"id": pid, "name": name, "structure": "前3秒抛痛点→自曝→给法",
            "when_to_use": "焦虑选题", "status": "proven", "reason": "命中焦虑受众"}


def test_auto_match_injects_and_records(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    monkeypatch.setattr(media_ai, "match_playbook", _fixed_match(_pb()))
    r = asyncio.run(media_ai.write_script(db, "WC"))
    assert r["ok"] and r["playbook"]["id"] == "PB1"
    assert "【打法骨架】" in cap["prompt"] and "痛点自曝法" in cap["prompt"]
    assert "数据打脸法" not in cap["prompt"]     # 只注一条
    cur = asyncio.run(db.execute("SELECT used_playbook_ids FROM media_content WHERE id='WC'"))
    row = asyncio.run(cur.fetchone())
    assert json.loads(row["used_playbook_ids"]) == ["PB1"]
    asyncio.run(db.close())


def test_none_skips(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    called = []
    async def _spy(db, content, model="auto"):
        called.append(1); return {"ok": True, "playbook": _pb()}
    monkeypatch.setattr(media_ai, "match_playbook", _spy)
    r = asyncio.run(media_ai.write_script(db, "WC", playbook_id="none"))
    assert r["playbook"] is None and called == []       # 不匹配
    assert "【打法骨架】" not in cap["prompt"]
    cur = asyncio.run(db.execute("SELECT used_playbook_ids FROM media_content WHERE id='WC'"))
    assert json.loads((asyncio.run(cur.fetchone()))["used_playbook_ids"]) == []
    asyncio.run(db.close())


def test_explicit_id_no_match_call(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    called = []
    async def _spy(db, content, model="auto"):
        called.append(1); return {"ok": True, "playbook": _pb()}
    monkeypatch.setattr(media_ai, "match_playbook", _spy)
    r = asyncio.run(media_ai.write_script(db, "WC", playbook_id="PB2"))
    assert r["playbook"]["id"] == "PB2" and called == []   # 指定了就不再匹配
    assert "数据打脸法" in cap["prompt"] and "痛点自曝法" not in cap["prompt"]
    asyncio.run(db.close())


def test_lean_no_inject(monkeypatch):
    db = _setup()
    cap = {}
    monkeypatch.setattr(media_ai, "ask_ai", _capture_write(cap))
    r = asyncio.run(media_ai.write_script(db, "WC", mode="lean"))
    assert r["playbook"] is None and "【打法骨架】" not in cap["prompt"]
    asyncio.run(db.close())


def test_empty_response_retries_once_then_succeeds(monkeypatch):
    """DeepSeek 偶发返空：第一次空→悄悄重试一次→拿到内容算成功，两次都算钱。"""
    db = _setup()
    calls = []
    async def flaky(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        calls.append(1)
        if len(calls) == 1:
            return {"response": "   ", "model": "deepseek", "tokens": 0, "cost": 0.001}
        return {"response": "重试后的脚本正文。", "model": "deepseek", "tokens": 9, "cost": 0.002}
    monkeypatch.setattr(media_ai, "ask_ai", flaky)
    monkeypatch.setattr(media_ai, "match_playbook", _fixed_match(_pb()))
    r = asyncio.run(media_ai.write_script(db, "WC"))
    assert r["ok"] is True and r["script"] == "重试后的脚本正文。"
    assert len(calls) == 2                          # 空响应触发了一次重试
    assert abs(r["cost"] - 0.003) < 1e-9            # 两次调用都算钱
    asyncio.run(db.close())


def test_empty_both_times_reports_error(monkeypatch):
    """两次都空：如实报错、合计两次费用、只重试一次不无限刷。"""
    db = _setup()
    calls = []
    async def always_empty(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        calls.append(1)
        return {"response": "", "model": "deepseek", "tokens": 0, "cost": 0.001}
    monkeypatch.setattr(media_ai, "ask_ai", always_empty)
    monkeypatch.setattr(media_ai, "match_playbook", _fixed_match(_pb()))
    r = asyncio.run(media_ai.write_script(db, "WC"))
    assert r["ok"] is False and "空内容" in r["error"]
    assert len(calls) == 2                          # 重试一次后就放弃，不死循环
    assert abs(r["cost"] - 0.002) < 1e-9            # 两次费用合计
    asyncio.run(db.close())


def test_error_response_not_retried(monkeypatch):
    """返回的是 [错误]/[费用保护] 不是空——不重试，直接把错误透出。"""
    db = _setup()
    calls = []
    async def err(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        calls.append(1)
        return {"response": "[错误] 上游超时", "model": "deepseek", "tokens": 0, "cost": 0.001}
    monkeypatch.setattr(media_ai, "ask_ai", err)
    monkeypatch.setattr(media_ai, "match_playbook", _fixed_match(_pb()))
    r = asyncio.run(media_ai.write_script(db, "WC"))
    assert r["ok"] is False and r["error"] == "[错误] 上游超时"
    assert len(calls) == 1                          # 明确错误不触发重试
    asyncio.run(db.close())
