import datetime

INTERVALS = [1, 3, 7, 15, 30]  # 天；stage 为下标 0..4


def next_review(current_stage, passed, today):
    """
    1-3-7 调度。current_stage=-1 表示尚未进复习队列。
    passed=True(自评会/答对): stage 进一档(封顶), due=今天+INTERVALS[新档]。
    passed=False(模糊/不会/答错): stage 重置为 0, due=今天+1(明天)。
    """
    if passed:
        new_stage = min(current_stage + 1, len(INTERVALS) - 1)
    else:
        new_stage = 0
    due = today + datetime.timedelta(days=INTERVALS[new_stage])
    return new_stage, due
