import sqlite3
from app.database import SCHEMA

EXPECTED = {"study_points","study_archetypes","study_questions",
            "study_review","study_records","study_plan","study_settings"}

def test_schema_creates_study_tables():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED <= names
