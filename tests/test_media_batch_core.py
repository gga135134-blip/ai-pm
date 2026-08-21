"""per-content 核心：整理/挖矿逐条落库。"""
import asyncio
from tests.media_helpers import make_db
from app.services import media_batch as mb


async def _seed():
    db = await make_db()
    await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                     "VALUES ('P','嘉','x','涨粉','active')")
    await db.execute("INSERT INTO media_content (id,persona_id,title,stage,idea_source,script,is_winner) "
                     "VALUES ('C1','P','老文案','published','legacy_text','碎\n行\n正文',1)")
    await db.commit()
    return db


def test_run_organize_one(monkeypatch):
    async def fake_org(script, model="auto"):
        return {"ok": True, "summary": "一句摘要", "formatted": "整理后", "cost": 0, "model": "x"}
    monkeypatch.setattr(mb, "organize_content", fake_org)

    async def go():
        db = await _seed()
        r = await mb.run_organize_one(db, "C1")
        assert r["ok"] and r["summary"] == "一句摘要"
        cur = await db.execute("SELECT summary,script FROM media_content WHERE id='C1'")
        row = dict(await cur.fetchone())
        assert row["summary"] == "一句摘要" and row["script"] == "整理后"
        cur = await db.execute("SELECT COUNT(*) c FROM media_assistant_action WHERE action_type='organize_format'")
        assert (await cur.fetchone())["c"] == 1
        await db.close()
    asyncio.run(go())


def test_run_mine_one_signature(monkeypatch):
    async def fake_mine(db, pid, transcript, model="auto"):
        return {"ok": True, "materials": [], "signatures": [{"content": "你要知道"}]}
    monkeypatch.setattr(mb, "mine_from_transcript", fake_mine)

    async def go():
        db = await _seed()
        r = await mb.run_mine_one(db, "C1", "signature")
        assert r["ok"] and r["added"] == 1
        cur = await db.execute("SELECT mined_signature_at FROM media_content WHERE id='C1'")
        assert (await cur.fetchone())["mined_signature_at"] is not None
        await db.close()
    asyncio.run(go())
