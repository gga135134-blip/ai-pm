"""自媒体模块的常量与状态机。全部为纯函数，无 DB / AI 依赖。"""
import re

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


# ─────────────── 二期 🅐：写稿前认知子流程 ───────────────
# 刻意只三档：证据/角度/审稿是详情页可展开的产物，不是用户逐步点的关卡。
AUTHORING_STAGES = ["none", "drafted", "finalized"]

AUTHORING_LABELS = {
    "none": "未出稿",
    "drafted": "AI已出草稿",
    "finalized": "已定稿",
}


# 时长节奏标注行 + 缺真料标记。保存/复制时剥掉，只留正文。
# AI 有时加粗 **0-5秒｜开场**、有时括号 （15-30秒｜拆解…）、有时裸行 —— 三种都要清。
# 判据：整行以（可选 括号/星号 包裹的）"数字…秒｜" 开头。
_TIMING_LINE = re.compile(
    r'^\s*[（(]?\*{0,2}\s*[\d０-９][\d０-９\-–—~～到至.\s]*秒\s*[｜|][^\n]*$', re.M)
_GAP_MARK = re.compile(r'【缺真料：[^】]*】')


def clean_body(text: str) -> str:
    """剥掉时长节奏标注和缺真料说明，只留口播正文（人实际要念的话）。

    编辑时保留标注帮看节奏/知道缺啥；复制和保存(定稿)时走这个只留正文。
    """
    if not text:
        return ""
    t = _TIMING_LINE.sub('', text)
    t = _GAP_MARK.sub('', t)
    t = re.sub(r'\n{3,}', '\n\n', t)   # 去掉剥完留下的多余空行
    return t.strip()


def finalize_updates(script: str) -> dict:
    """定稿时要写入 media_content 的字段。空脚本返回空 dict（不推进）。

    定稿 = 人编辑后的真实版进 script；同时 stage 翻 scripted、authoring 翻 finalized。
    ai_draft 由 write_script 单独持有，定稿不动它 —— 保留"AI草稿 vs 定稿"差异供功能B。
    """
    if not (script or "").strip():
        return {}
    return {
        "script": script,
        "stage": "scripted",
        "authoring_stage": "finalized",
    }


# ─────────────── 二期 · 人设框架地基 ───────────────
# 7 个访谈模块 → 8 个 dimension。phase_bound=True 的模块打当前阶段标签，
# False 的（声音/记忆点/红线）是跨阶段永久条目，phase_tag 留空、换阶段不归档。
PERSONA_MODULES = {
    "positioning": {"label": "你是谁·定位", "dims": ["positioning", "differentiator"], "phase_bound": True},
    "audience":    {"label": "说给谁听·受众", "dims": ["audience"], "phase_bound": True},
    "topics":      {"label": "讲什么·选题域", "dims": ["topics"], "phase_bound": True},
    "tone":        {"label": "怎么说·声音", "dims": ["tone"], "phase_bound": False},
    "signature":   {"label": "招牌·记忆点", "dims": ["signature"], "phase_bound": False},
    "taboo":       {"label": "红线·禁忌", "dims": ["taboo"], "phase_bound": False},
    "anchor":      {"label": "生意锚点", "dims": ["anchor"], "phase_bound": True},
}
PERSONA_MODULE_ORDER = [
    "positioning", "audience", "topics", "tone", "signature", "taboo", "anchor",
]


def module_dims(module: str) -> list[str]:
    """模块允许写入的维度。未知模块返回空列表。"""
    m = PERSONA_MODULES.get(module)
    return list(m["dims"]) if m else []


def default_phase_tag(module: str, current_phase: str) -> str:
    """模块提炼出的条目默认打什么阶段标签。永久模块返回空串。"""
    m = PERSONA_MODULES.get(module)
    if m and m["phase_bound"]:
        return current_phase or ""
    return ""


def is_injectable(trait: dict, current_phase: str) -> bool:
    """写稿注入时这条 trait 该不该喂：active 且（永久 或 属当前阶段）。"""
    if (trait.get("status") or "active") != "active":
        return False
    ptag = trait.get("phase_tag") or ""
    return ptag == "" or ptag == current_phase


def archive_targets(traits: list[dict], old_phase: str) -> list[str]:
    """换阶段时要归档的 trait id：仅 active 且 phase_tag==old_phase 的。
    永久条目 phase_tag 为空，old_phase 非空时永不命中 —— 天然不误伤。"""
    if not old_phase:
        return []
    return [t["id"] for t in traits
            if (t.get("status") or "active") == "active"
            and (t.get("phase_tag") or "") == old_phase]


def completed_modules(active_dims: list[str]) -> set[str]:
    """哪些模块已至少采纳过一条 active 条目（详情页 N/7 进度）。"""
    dims = set(active_dims)
    return {mod for mod, m in PERSONA_MODULES.items()
            if dims & set(m["dims"])}
