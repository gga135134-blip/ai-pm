import datetime
from app.services.study_engine import next_review, INTERVALS

T = datetime.date(2026, 6, 24)

def test_intervals_value():
    assert INTERVALS == [1, 3, 7, 15, 30]

def test_first_pass_enters_stage0_due_plus1():
    stage, due = next_review(-1, True, T)
    assert stage == 0
    assert due == T + datetime.timedelta(days=1)

def test_consecutive_pass_advances():
    stage, due = next_review(0, True, T)
    assert stage == 1 and due == T + datetime.timedelta(days=3)
    stage, due = next_review(1, True, T)
    assert stage == 2 and due == T + datetime.timedelta(days=7)

def test_pass_caps_at_last_stage():
    stage, due = next_review(4, True, T)
    assert stage == 4 and due == T + datetime.timedelta(days=30)

def test_fail_resets_to_stage0_due_tomorrow():
    stage, due = next_review(3, False, T)
    assert stage == 0 and due == T + datetime.timedelta(days=1)
