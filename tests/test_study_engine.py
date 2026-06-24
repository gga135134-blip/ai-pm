import datetime
from app.services.study_engine import next_review, INTERVALS, allocate_new_quota

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

W = {"建筑实务": 0.32, "管理": 0.32, "法规": 0.20, "经济": 0.16}
SPRINT = datetime.date(2026, 8, 15)

def test_quota_sums_to_effective_new():
    q = allocate_new_quota(due_count=0, today=datetime.date(2026,6,24),
                           sprint_date=SPRINT, weights=W, daily_new_target=20)
    assert sum(q.values()) == 20
    assert q["建筑实务"] >= q["法规"] >= q["经济"]

def test_quota_shrinks_with_review_load():
    q = allocate_new_quota(due_count=15, today=datetime.date(2026,6,24),
                           sprint_date=SPRINT, weights=W, daily_new_target=20)
    assert sum(q.values()) == 5

def test_quota_zero_when_review_exceeds_target():
    q = allocate_new_quota(due_count=25, today=datetime.date(2026,6,24),
                           sprint_date=SPRINT, weights=W, daily_new_target=20)
    assert sum(q.values()) == 0

def test_quota_zero_after_sprint():
    q = allocate_new_quota(due_count=0, today=datetime.date(2026,8,20),
                           sprint_date=SPRINT, weights=W, daily_new_target=20)
    assert sum(q.values()) == 0
