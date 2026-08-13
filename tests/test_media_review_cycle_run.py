"""L2 编排：门槛拦截、写库、假设补 id、读取助手。"""
import asyncio
import json
import uuid
import pytest
from tests.media_helpers import make_db, seed_content
from app.services import media_review_cycle as rc


async def _seed_published(db, persona_id, cid, views, first=False):
    if first:
        await seed_content(db, persona_id=persona_id, content_id=cid, stage="published")
    else:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage) "
            "VALUES (?,?,?,?, 'published')", (cid, persona_id, "标题" + cid, "谜题"))
    aid = "ACC-" + cid
    await db.execute("INSERT INTO media_account (id,persona_id,platform,account_name) "
                     "VALUES (?,?,?,?)", (aid, persona_id, "抖音", "@x"))
    pid = "PUB-" + cid
    await db.execute("INSERT INTO media_publish (id,content_id,account_id,status) "
                     "VALUES (?,?,?, 'published')", (pid, cid, aid))
    await db.execute("INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
                     "shares,new_fans) VALUES (?,?,?,0,0,0,0)",
                     (str(uuid.uuid4()), pid, views))
    await db.commit()


def test_below_threshold_returns_warn_no_write(monkeypatch):
    async def go():
        db = await make_db()
        try:
            await _seed_published(db, "P1", "c1", 1000, first=True)
            await _seed_published(db, "P1", "c2", 2000)
            res = await rc.run_l2_cycle(db, "P1")   # 只有 2 条 < 5
            assert res["ok"] is False and res["count"] == 2 and "warn" in res
            cur = await db.execute("SELECT COUNT(*) n FROM media_review_cycle")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(go())


def test_run_writes_row_and_assigns_hypothesis_ids(monkeypatch):
    fake = {
        "patterns": [{"pattern": "带故事的更火", "evidence": "c3", "confidence": "medium"}],
        "hypotheses": [{"statement": "前3秒抛问题提完播", "how_to_test": "下轮3条采用", "basis": "x"}],
        "hypotheses_tested": [],
        "proposed_traits": [{"dimension": "signature", "content": "爱用反问",
                             "brief": "反问", "evidence": "c3", "confidence": 3}],
        "proposed_audience": [],
        "advisory": {"weight_suggestion": "涨粉期抬 fit"},
    }

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek", "tokens": 20, "cost": 0.01}
    monkeypatch.setattr(rc, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            for i in range(5):
                await _seed_published(db, "P1", f"c{i}", 1000 + i * 500, first=(i == 0))
            res = await rc.run_l2_cycle(db, "P1")
            assert res["ok"] is True and res["count"] == 5 and res["seq"] == 1
            cyc = await rc.get_cycle(db, res["cycle_id"])
            assert cyc["metrics_summary"]["content_count"] == 5
            assert cyc["hypotheses"][0]["id"].startswith("h-")   # 补了稳定 id
            assert cyc["patterns"][0]["pattern"].startswith("带故事")
            # 成本记 log_injection
            cur = await db.execute("SELECT COUNT(*) n FROM media_injection_log "
                                   "WHERE ai_type='media_review_cycle'")
            assert (await cur.fetchone())["n"] == 1
        finally:
            await db.close()
    asyncio.run(go())


def test_second_cycle_carries_prev_hypotheses(monkeypatch):
    seen = {}

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": json.dumps({"patterns": [], "hypotheses": [],
                "hypotheses_tested": [], "proposed_traits": [],
                "proposed_audience": [], "advisory": {}}),
                "model": "deepseek", "tokens": 5, "cost": 0}
    monkeypatch.setattr(rc, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="seed", stage="idea")
            await db.execute(
                "INSERT INTO media_review_cycle (id,persona_id,seq,content_ids,"
                "hypotheses,period_end) VALUES (?,?,?,?,?,datetime('now'))",
                (str(uuid.uuid4()), "P1", 1, json.dumps(["old"]),
                 json.dumps([{"id": "h-prev0001", "statement": "老假设A"}])))
            await db.commit()
            for i in range(5):
                await _seed_published(db, "P1", f"n{i}", 1000, first=False)
            res = await rc.run_l2_cycle(db, "P1")
            assert res["seq"] == 2                    # 第二轮
            assert "老假设A" in seen["prompt"]         # 上轮假设进了 prompt
        finally:
            await db.close()
    asyncio.run(go())
