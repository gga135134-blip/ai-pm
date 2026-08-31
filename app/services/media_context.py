"""media 模块的 AI 输入输出管道：注入预算控制、上下文拼装、AI 输出解析。

设计约束（spec §6）：体系重在库里，不在提示词里。
任何一次 AI 调用看到的资产不超过 INJECTION_BUDGET 的总和。
体系涨到 500 条资产，注入量恒定不变 —— 这是"体系可以无限重"的前提。
"""
import json
import logging
import re
import uuid

from app.services.media_flow import is_injectable
from app.services.media_decision import _overlap as _text_overlap

log = logging.getLogger(__name__)

# 各注入槽位的硬上限。改动这里需同步更新 spec §6。
INJECTION_BUDGET = {
    "trait": 8,       # 人设条目，按 confidence 降序
    "signature": 3,   # 记忆点，单独占槽，少而硬
    "playbook": 2,    # 二期：只给最匹配的已验证打法
    "material": 3,    # 二期：只给未用过且最匹配的原料
    "lesson": 3,      # 教训：按 trigger_context 与选题文本的重合度取前 3
    "redline": 2,     # 红线：无条件带，不做匹配。单独占槽不与 lesson 竞争
    "audience": 1,    # 二期：只注本条瞄准的那个 segment
}

# 没有 brief 时，content 的截断长度
_BRIEF_FALLBACK_CHARS = 40


def select_by_budget(items: list[dict], slot: str,
                     score_key: str = "confidence") -> list[dict]:
    """按分数降序取前 N 条，N 由 INJECTION_BUDGET[slot] 决定。

    未在预算表中登记的槽位一律返回空 —— 防止新增注入点时绕过预算。
    """
    cap = INJECTION_BUDGET.get(slot)
    if not cap:
        log.warning("select_by_budget: 未登记的注入槽位 %s，拒绝注入", slot)
        return []
    ranked = sorted(items, key=lambda i: i.get(score_key) or 0, reverse=True)
    return ranked[:cap]


def render_brief_list(items: list[dict], label: str) -> str:
    """渲染成 brief 清单。只放 brief，detail 留给 AI 按需读取。"""
    if not items:
        return ""
    lines = [f"【{label}】"]
    for i in items:
        brief = (i.get("brief") or "").strip()
        if not brief:
            brief = (i.get("content") or "").strip()[:_BRIEF_FALLBACK_CHARS]
        if brief:
            lines.append(f"- {brief}")
    return "\n".join(lines) if len(lines) > 1 else ""


def build_script_context(persona: dict, traits: list[dict]) -> tuple[str, list[str]]:
    """拼装写脚本用的上下文。返回 (注入文本, 注入的资产 id 列表)。

    signature（记忆点）单独占预算槽，不与普通条目竞争 ——
    记忆点是 IP 资产的核心，绝不能被高置信度的普通条目挤掉。
    """
    phase = persona.get("current_phase", "")
    active = [t for t in traits if is_injectable(t, phase)]
    signatures = [t for t in active if t.get("dimension") == "signature"]
    others = [t for t in active if t.get("dimension") != "signature"]

    picked_sig = select_by_budget(signatures, "signature")
    picked_other = select_by_budget(others, "trait")

    parts = [
        f"【人设】{persona.get('name', '')}｜{persona.get('one_liner', '')}"
        f"｜当前阶段：{persona.get('current_phase', '')}"
    ]
    other_text = render_brief_list(picked_other, "人设条目")
    if other_text:
        parts.append(other_text)
    sig_text = render_brief_list(picked_sig, "记忆点（必须植入）")
    if sig_text:
        parts.append(sig_text)

    ids = [t["id"] for t in picked_other] + [t["id"] for t in picked_sig]
    return "\n\n".join(parts), ids


def extract_json(text: str, expect: str = "object"):
    """从 AI 回复里稳健提取 JSON。解析失败返回空容器，绝不抛异常。

    绝不做 unicode_escape —— 会把中文搅成乱码（项目历史坑）。
    """
    empty = [] if expect == "array" else {}
    raw = (text or "").strip()
    if not raw:
        return empty

    candidate = raw
    m = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if m:
        candidate = m.group(1).strip()

    # strict=False 允许字符串里有真实换行符，DeepSeek 常这么返回
    try:
        obj = json.loads(candidate, strict=False)
        if (expect == "array" and isinstance(obj, list)) or \
           (expect == "object" and isinstance(obj, dict)):
            return obj
    except json.JSONDecodeError:
        pass

    open_c, close_c = ("[", "]") if expect == "array" else ("{", "}")
    start, end = candidate.find(open_c), candidate.rfind(close_c)
    if start != -1 and end > start:
        try:
            obj = json.loads(candidate[start:end + 1], strict=False)
            if (expect == "array" and isinstance(obj, list)) or \
               (expect == "object" and isinstance(obj, dict)):
                return obj
        except json.JSONDecodeError:
            pass

    log.warning("extract_json 解析失败，raw[:120]=%s", raw[:120])
    return empty


async def log_injection(db, content_id: str, ai_type: str,
                        asset_ids: list[str], token_count: int) -> None:
    """记录本次 AI 调用注入了什么。三期据此分析哪些注入真的有效。

    一期就写入，避免三期开工时从零等待数据积累。
    """
    await db.execute(
        "INSERT INTO media_injection_log "
        "(id, content_id, ai_type, injected_asset_ids, token_count) "
        "VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), content_id or "", ai_type,
         json.dumps(asset_ids, ensure_ascii=False), token_count),
    )
    await db.commit()


# ─────────────── 二期 🅐：证据/角度/原料 注入拼装 ───────────────

def render_evidence_block(evidence: list[dict]) -> str:
    """本条内容的真实素材包。全部注入（本条真料数量少，不走预算截断）。"""
    items = [e for e in evidence if (e.get("item") or "").strip()]
    if not items:
        return ""
    lines = ["【真实素材（只能用这些真料，缺了就标缺口，绝不编造）】"]
    for e in items:
        lines.append(f"- [{e.get('item_type', '')}] {e['item'].strip()}")
    return "\n".join(lines)


def render_angle_block(angle: str, rationale: str) -> str:
    """选中的切入角度。angle 为空返回空串（还没选角度就不加这块）。"""
    if not (angle or "").strip():
        return ""
    text = f"【本条切入角度（必须按这个角度写）】{angle.strip()}"
    if (rationale or "").strip():
        text += f"（理由：{rationale.strip()}）"
    return text


def select_materials(materials: list[dict]) -> list[dict]:
    """原料库雏形注入：优先未用过的（use_count 升序），取前 INJECTION_BUDGET['material'] 条。

    与 select_by_budget（按分数降序）方向相反 —— 原料越少用越该优先，
    避免同一个故事被反复调用听腻（spec §5.5 链路1）。
    """
    cap = INJECTION_BUDGET.get("material", 0)
    if not cap:
        return []
    ranked = sorted(materials, key=lambda m: m.get("use_count") or 0)
    return ranked[:cap]


def render_material_block(materials: list[dict]) -> str:
    """可复用原料的 brief 清单（detail 留给 AI 按需，不塞进提示词）。"""
    if not materials:
        return ""
    lines = ["【可复用原料（来自原料库，优先复用避免每条都采访）】"]
    for m in materials:
        brief = (m.get("brief") or "").strip() or (m.get("title") or "").strip()
        if brief:
            lines.append(f"- {brief}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ─────────────── 教训/红线库注入 ───────────────
# 红线与教训分槽，互不挤占 —— 照 signature（记忆点）的先例：
# 硬约束不能被一条恰好匹配度高的软建议挤掉（spec §4.2）。

def select_redlines(lessons: list[dict]) -> list[dict]:
    """红线：无条件取前 INJECTION_BUDGET['redline'] 条（created_at 升序）。

    不做适用性匹配 —— 红线语义就是「任何时候都不许」，对它做匹配自相矛盾。
    上限 2 是有意的设计压力：红线一多就不值钱。
    """
    cap = INJECTION_BUDGET.get("redline")
    if not cap:
        log.warning("select_redlines: redline 槽位未登记，拒绝注入")
        return []
    reds = [x for x in lessons if (x.get("kind") or "") == "redline"]
    reds.sort(key=lambda x: x.get("created_at") or "")
    return reds[:cap]


def select_lessons(lessons: list[dict], topic_text: str) -> list[dict]:
    """教训：按 trigger_context 与 topic_text 的 bigram 重合度降序取前 N。

    trigger_context 为空视为 0 分，排最后（配额有余时仍可进，不主动排除）。
    sorted 是稳定排序，同分保持原有顺序。
    """
    cap = INJECTION_BUDGET.get("lesson")
    if not cap:
        log.warning("select_lessons: lesson 槽位未登记，拒绝注入")
        return []
    items = [x for x in lessons if (x.get("kind") or "") == "lesson"]
    ranked = sorted(
        items,
        key=lambda x: _text_overlap(x.get("trigger_context") or "", topic_text or ""),
        reverse=True)
    return ranked[:cap]


def _brief_lines(items: list[dict], title: str) -> str:
    lines = [title]
    lines += [f"- {b}" for b in
              ((x.get("brief") or "").strip() for x in items) if b]
    return "\n".join(lines) if len(lines) > 1 else ""


def render_lesson_block(redlines: list[dict], lessons: list[dict]) -> str:
    """渲染本子注入块。只放 brief —— detail 永不进提示词（注意力纪律）。

    两者皆空（或 brief 全空）返回空串，绝不产生只有标题的空块。
    """
    parts = [t for t in (
        _brief_lines(redlines, "【红线（绝对不许违反）】"),
        _brief_lines(lessons, "【教训（这次特别注意）】"),
    ) if t]
    return "\n\n".join(parts)
