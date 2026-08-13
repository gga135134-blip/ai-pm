"""L3 纯计算：阶段序列/趋势/退出信号/纳入范围。"""
import asyncio
import json
import uuid
from tests.media_helpers import make_db, seed_content
from app.services import media_phase_review as pr


def test_next_phase():
    assert pr._next_phase("冷启动") == "涨粉"
    assert pr._next_phase("涨粉") == "转化"
    assert pr._next_phase("转化") is None
    assert pr._next_phase("瞎写") is None


def test_summarize_trend():
    l2 = [
        {"seq": 1, "metrics_summary": {"avg": {"views": 8000, "new_fans": 30},
                                       "hit_count": 0}},
        {"seq": 2, "metrics_summary": {"avg": {"views": 12000, "new_fans": 55},
                                       "hit_count": 1}},
    ]
    t = pr.summarize_trend(l2)
    assert t["series"][0]["avg_views"] == 8000
    assert t["series"][1]["avg_new_fans"] == 55
    assert t["series"][1]["hit_count"] == 1


def test_exit_signals_cold_phase():
    l2 = [
        {"metrics_summary": {"avg": {"views": 2000}, "hit_count": 0}},
        {"metrics_summary": {"avg": {"views": 5000}, "hit_count": 1}},
    ]
    sig = pr.phase_exit_signals("冷启动", l2)
    by = {s["signal"]: s for s in sig}
    assert by["累计爆款数"]["value"] == 1 and by["累计爆款数"]["met"] is True
    # 最近一轮均值播放 5000 >= 3000
    assert by["最近一轮均值播放"]["value"] == 5000 and by["最近一轮均值播放"]["met"] is True


def test_exit_signals_growth_phase():
    l2 = [
        {"metrics_summary": {"avg": {"new_fans": 20}},
         "hypotheses_tested": [{"verdict": "confirmed"}, {"verdict": "refuted"}]},
        {"metrics_summary": {"avg": {"new_fans": 40}},
         "hypotheses_tested": [{"verdict": "confirmed"}]},
    ]
    sig = pr.phase_exit_signals("涨粉", l2)
    by = {s["signal"]: s for s in sig}
    assert by["新增粉丝持续为正"]["met"] is True          # 20,40 全正
    assert by["累计已验证假设"]["value"] == 2 and by["累计已验证假设"]["met"] is True


def test_exit_signals_conversion_phase_empty():
    assert pr.phase_exit_signals("转化", [{"metrics_summary": {}}]) == []


def test_gather_l2_since_only_after_last_l3():
    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="seed", stage="idea")
            # 两轮 L2
            for i, cid in enumerate(["L2A", "L2B"]):
                await db.execute(
                    "INSERT INTO media_review_cycle (id,persona_id,level,seq,"
                    "metrics_summary,hypotheses_tested,patterns,created_at) "
                    "VALUES (?,?,'L2',?,?,?,?,?)",
                    (cid, "P1", i + 1, json.dumps({"avg": {"views": 100}}),
                     json.dumps([]), json.dumps([]),
                     f"2026-08-1{i}T00:00:00"))
            # 一轮旧 L3（时间在 L2A 之后、L2B 之前）
            await db.execute(
                "INSERT INTO media_phase_review (id,persona_id,seq,created_at) "
                "VALUES (?,?,?,?)", (str(uuid.uuid4()), "P1", 1,
                                     "2026-08-10T12:00:00"))
            await db.commit()
            l2, prev = await pr.gather_l2_since(db, "P1")
            ids = {c["id"] for c in l2}
            assert ids == {"L2B"}          # 只纳入上轮 L3 之后的 L2
            assert prev is not None and prev["seq"] == 1
            assert isinstance(l2[0]["metrics_summary"], dict)   # 已解析
        finally:
            await db.close()
    asyncio.run(go())


def test_count_topics_serving():
    topics = [
        {"anchor_ids": '["a1","a2"]'},      # JSON 字符串形式（DB 原样）
        {"anchor_ids": ["a1"]},             # list 形式
        {"anchor_ids": '[]'},
        {"anchor_ids": None},
    ]
    assert pr.count_topics_serving("a1", topics) == 2
    assert pr.count_topics_serving("a2", topics) == 1
    assert pr.count_topics_serving("zzz", topics) == 0


def test_phase_review_has_anchor_actions_column():
    async def go():
        db = await make_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_phase_review)")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    assert "anchor_actions" in asyncio.run(go())
