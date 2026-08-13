"""L3 编排：门槛拦截、写库、AI 输出校验（phase_to 只能下一个、trait_id 过滤）。"""
import asyncio
import json
import uuid
from tests.media_helpers import make_db, seed_content
from app.services import media_phase_review as pr


async def _seed_l2(db, persona_id, cid, seq, views=5000, new_fans=20,
                   hit=1, confirmed=1, created_at=None):
    await db.execute(
        "INSERT INTO media_review_cycle (id,persona_id,level,seq,metrics_summary,"
        "hypotheses_tested,patterns,created_at) VALUES (?,?,'L2',?,?,?,?,COALESCE(?,datetime('now')))",
        (cid, persona_id, seq,
         json.dumps({"avg": {"views": views, "new_fans": new_fans},
                     "hit_count": hit}),
         json.dumps([{"verdict": "confirmed"}] * confirmed),
         json.dumps([{"pattern": "带故事更火"}]), created_at))
    await db.commit()


def test_below_threshold_warns_no_write():
    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="s", stage="idea")
            await _seed_l2(db, "P1", "a", 1)
            await _seed_l2(db, "P1", "b", 2)          # 只 2 轮 < 3
            res = await pr.run_l3_review(db, "P1")
            assert res["ok"] is False and res["count"] == 2 and "warn" in res
            cur = await db.execute("SELECT COUNT(*) n FROM media_phase_review")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await db.close()
    asyncio.run(go())


def _seed_persona_phase(db, pid, phase):
    return db.execute(
        "INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
        "VALUES (?,?,?,?, 'active')", (pid, "嘉姐", "一句话", phase))


def test_run_writes_row_validates_phase_and_traits(monkeypatch):
    fake = {
        "phase_reco": "advance", "phase_to": "涨粉",
        "phase_reason": "累计爆款达标、播放站上基线",
        "trait_actions": [
            {"trait_id": "T-REAL", "action": "promote",
             "evidence": "近轮规律印证", "reason": "反复印证"},
            {"trait_id": "T-FAKE", "action": "promote", "evidence": "x",
             "reason": "y"},                                  # 瞎编 id，应被过滤
            {"trait_id": "T-REAL", "action": "bogus"},        # 非法 action，过滤
        ],
    }

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek",
                "tokens": 20, "cost": 0.01}
    monkeypatch.setattr(pr, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await _seed_persona_phase(db, "P1", "冷启动")
            await db.execute(
                "INSERT INTO media_persona_trait (id,persona_id,dimension,content,"
                "status) VALUES ('T-REAL','P1','signature','爱反问','active')")
            for i in range(3):
                await _seed_l2(db, "P1", f"c{i}", i + 1)
            res = await pr.run_l3_review(db, "P1")
            assert res["ok"] is True and res["count"] == 3
            row = await pr.get_phase_review(db, res["review_id"])
            assert row["phase_from"] == "冷启动"
            assert row["phase_reco"] == "advance" and row["phase_to"] == "涨粉"
            acts = row["trait_actions"]
            assert len(acts) == 1 and acts[0]["trait_id"] == "T-REAL"   # 只剩合法的
            # 未自动改人设
            cur = await db.execute("SELECT current_phase FROM media_persona WHERE id='P1'")
            assert (await cur.fetchone())["current_phase"] == "冷启动"
            cur = await db.execute("SELECT status FROM media_persona_trait WHERE id='T-REAL'")
            assert (await cur.fetchone())["status"] == "active"
            # 成本记 log_injection
            cur = await db.execute("SELECT COUNT(*) n FROM media_injection_log "
                                   "WHERE ai_type='media_phase_review'")
            assert (await cur.fetchone())["n"] == 1
        finally:
            await db.close()
    asyncio.run(go())


def test_illegal_phase_to_falls_back_to_stay(monkeypatch):
    fake = {"phase_reco": "advance", "phase_to": "转化",   # 冷启动的下一个是涨粉，非转化
            "phase_reason": "跳级", "trait_actions": []}

    async def stub(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        return {"response": json.dumps(fake), "model": "deepseek", "tokens": 5, "cost": 0}
    monkeypatch.setattr(pr, "ask_ai", stub)

    async def go():
        db = await make_db()
        try:
            await _seed_persona_phase(db, "P1", "冷启动")
            for i in range(3):
                await _seed_l2(db, "P1", f"c{i}", i + 1)
            res = await pr.run_l3_review(db, "P1")
            row = await pr.get_phase_review(db, res["review_id"])
            assert row["phase_reco"] == "stay" and row["phase_to"] == ""  # 跳级被回落
        finally:
            await db.close()
    asyncio.run(go())
