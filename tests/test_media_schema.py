import sqlite3
from app.database import SCHEMA

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
