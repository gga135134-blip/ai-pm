import sqlite3
from app.database import SCHEMA, MIGRATIONS

EXPECTED = {
    "media_persona", "media_persona_trait", "media_account",
    "media_topic", "media_content", "media_publish", "media_metrics",
    "media_review", "media_case", "media_injection_log",
}


def _tables():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _cols(table):
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_schema_creates_all_media_tables():
    assert EXPECTED <= _tables()


def test_trait_has_brief_and_confidence():
    # brief 是注入预算机制的基础，confidence 是截断排序依据
    cols = _cols("media_persona_trait")
    assert {"brief", "confidence", "dimension", "status", "persona_id"} <= cols


def test_content_has_fingerprint_and_outcome():
    # 三期查重与归因分析依赖这两个字段，一期就必须写入
    cols = _cols("media_content")
    assert {"topic_fingerprint", "outcome", "stage", "puzzle", "persona_id"} <= cols


def test_topic_has_puzzle_and_reject_reason():
    cols = _cols("media_topic")
    assert {"puzzle", "rejected_reason", "status", "decision_score"} <= cols


def test_metrics_hangs_off_publish():
    cols = _cols("media_metrics")
    assert {"publish_id", "views", "likes", "comments", "shares",
            "new_fans", "collected_by", "snapshot_at"} <= cols


def test_injection_log_records_assets_and_tokens():
    cols = _cols("media_injection_log")
    assert {"content_id", "ai_type", "injected_asset_ids", "token_count"} <= cols


def test_case_has_replicable_and_factors():
    cols = _cols("media_case")
    assert {"replicable", "topic_factor", "hook_factor", "case_type"} <= cols


# ─────────────── 二期 🅐 生产线：新表 + media_content 新列 ───────────────

def _cols_migrated(table):
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            con.execute(sql)
        except Exception:
            pass
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_new_pipeline_tables_exist():
    assert {"media_evidence", "media_angle",
            "media_draft_review", "media_material"} <= _tables()


def test_evidence_columns():
    cols = _cols("media_evidence")
    assert {"content_id", "persona_id", "item", "item_type", "source",
            "from_material_id", "promoted_to_material_id"} <= cols


def test_angle_columns():
    cols = _cols("media_angle")
    assert {"content_id", "angle", "rationale", "is_selected", "status"} <= cols


def test_draft_review_columns():
    cols = _cols("media_draft_review")
    assert {"content_id", "reviewed_draft", "reviewer_strategy", "reviewer_model",
            "fact_flags", "persona_flags", "platform_flags", "gap_flags",
            "risk_flags", "score", "verdict", "notes"} <= cols


def test_material_columns():
    cols = _cols("media_material")
    assert {"persona_id", "type", "detail", "brief", "emotion",
            "usable_scene", "audience_hit", "used_in", "use_count", "status"} <= cols


def test_content_gains_authoring_columns():
    cols = _cols_migrated("media_content")
    assert {"authoring_stage", "brief", "evidence_gap", "selected_angle_id",
            "ai_draft", "revision_count", "finalized_at"} <= cols


# ─────────────── 二期资产层🅑：受众画像 + 生意锚点 ───────────────

def test_audience_anchor_tables_exist():
    assert {"media_audience", "media_anchor"} <= _tables()


def test_audience_columns():
    cols = _cols("media_audience")
    assert {"persona_id", "segment", "who", "anxiety", "desire", "objection",
            "language", "pay_willingness", "pay_scene", "pay_ceiling",
            "evidence", "confidence", "source", "status"} <= cols


def test_anchor_columns():
    cols = _cols("media_anchor")
    assert {"persona_id", "name", "type", "value_prop", "price_band",
            "path", "evidence", "source", "status"} <= cols


def test_media_topic_has_tagging_columns():
    import asyncio
    from app.database import get_db, init_db

    async def check():
        await init_db()
        db = await get_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_topic)")
            cols = {r["name"] for r in await cur.fetchall()}
            return cols
        finally:
            await db.close()

    cols = asyncio.run(check())
    assert {"audience_ids", "anchor_ids", "dropped_drift_ids", "tagged"} <= cols


def test_media_content_has_published_at():
    import asyncio
    from app.database import get_db, init_db

    async def check():
        await init_db()
        db = await get_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_content)")
            return {r["name"] for r in await cur.fetchall()}
        finally:
            await db.close()
    assert "published_at" in asyncio.run(check())
