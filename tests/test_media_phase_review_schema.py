"""L3 阶段复盘表 schema 测试。"""
import asyncio
from tests.media_helpers import make_db


def _cols(table):
    async def go():
        db = await make_db()
        try:
            cur = await db.execute(f"PRAGMA table_info({table})")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    return asyncio.run(go())


def test_phase_review_table_has_expected_columns():
    cols = _cols("media_phase_review")
    expected = {
        "id", "persona_id", "seq", "phase_from", "l2_cycle_ids",
        "metrics_trend", "phase_signals", "phase_reco", "phase_to",
        "phase_reason", "trait_actions", "cost", "model",
        "generated_by", "created_at",
    }
    assert expected <= cols
