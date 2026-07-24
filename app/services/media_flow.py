"""自媒体模块的常量与状态机。全部为纯函数，无 DB / AI 依赖。"""

STAGES = ["idea", "scripted", "recording", "editing", "ready", "published", "reviewed"]

STAGE_LABELS = {
    "idea": "选题",
    "scripted": "脚本",
    "recording": "待录",
    "editing": "待剪",
    "ready": "待发",
    "published": "已发",
    "reviewed": "已复盘",
}

PLATFORMS = {"douyin": "抖音", "xhs": "小红书", "shipinhao": "视频号"}


def stage_index(stage: str) -> int:
    """阶段在流程中的位置。未知阶段返回 -1。"""
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


def next_stage(current: str) -> str | None:
    """下一个阶段。已是末态或阶段未知时返回 None。"""
    i = stage_index(current)
    if i < 0 or i >= len(STAGES) - 1:
        return None
    return STAGES[i + 1]


def can_transition(frm: str, to: str) -> bool:
    """前进只允许一步（防跳步漏工序），后退允许任意步（返工是正常的）。"""
    a, b = stage_index(frm), stage_index(to)
    if a < 0 or b < 0 or a == b:
        return False
    if b > a:
        return b == a + 1
    return True


def is_published(stage: str) -> bool:
    """是否已经发出去了（已发或已复盘）。"""
    return stage_index(stage) >= stage_index("published")
