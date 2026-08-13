"""L2 周期复盘表 schema 测试。"""
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


def test_review_cycle_table_has_expected_columns():
    cols = _cols("media_review_cycle")
    expected = {
        "id", "persona_id", "level", "seq", "period_start", "period_end",
        "content_ids", "metrics_summary", "patterns", "hypotheses",
        "hypotheses_tested", "proposed_traits", "proposed_audience",
        "advisory", "cost", "model", "generated_by", "created_at",
    }
    assert expected <= cols
