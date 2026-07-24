from app.services.media_flow import (
    STAGES, STAGE_LABELS, PLATFORMS,
    stage_index, next_stage, can_transition, is_published,
)


def test_stages_order_is_the_content_lifecycle():
    assert STAGES == ["idea", "scripted", "recording",
                      "editing", "ready", "published", "reviewed"]


def test_every_stage_has_a_chinese_label():
    assert set(STAGE_LABELS) == set(STAGES)
    assert STAGE_LABELS["idea"] == "选题"
    assert STAGE_LABELS["reviewed"] == "已复盘"


def test_three_platforms_present():
    assert PLATFORMS == {"douyin": "抖音", "xhs": "小红书", "shipinhao": "视频号"}


def test_stage_index():
    assert stage_index("idea") == 0
    assert stage_index("reviewed") == 6
    assert stage_index("nonsense") == -1


def test_next_stage_advances_one_step():
    assert next_stage("idea") == "scripted"
    assert next_stage("ready") == "published"


def test_next_stage_at_terminal_is_none():
    assert next_stage("reviewed") is None


def test_next_stage_unknown_is_none():
    assert next_stage("nonsense") is None


def test_can_advance_exactly_one_step():
    assert can_transition("idea", "scripted") is True


def test_cannot_skip_stages_forward():
    # 不能跳步：选题不能直接跳到已发
    assert can_transition("idea", "published") is False


def test_can_go_back_any_number_of_steps():
    # 允许退回：发现脚本要重写，可以从待剪退回脚本
    assert can_transition("editing", "scripted") is True
    assert can_transition("published", "idea") is True


def test_cannot_transition_to_self():
    assert can_transition("idea", "idea") is False


def test_cannot_transition_with_unknown_stage():
    assert can_transition("idea", "nonsense") is False
    assert can_transition("nonsense", "idea") is False


def test_is_published_covers_published_and_reviewed():
    assert is_published("published") is True
    assert is_published("reviewed") is True
    assert is_published("ready") is False
