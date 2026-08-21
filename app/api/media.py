import json
import logging
import uuid
from pathlib import Path as _Path
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from app.database import get_db
from app.services.media_overview import persona_overview
from app.services.media_reverse import reverse_ingest
from app.services.media_metrics import recognize_screenshot, save_metrics
from app.services.media_feishu_sync import sync_from_feishu
from app.services.media_flow import (
    PLATFORMS, STAGES, STAGE_LABELS, can_transition, next_stage, stage_index,
    PERSONA_MODULES, PERSONA_MODULE_ORDER, completed_modules,
)
from app.services.media_ai import (
    recommend_topics, tag_topics, write_script, generate_platform_copy, review_content,
    interview_questions, extract_evidence, propose_angles,
    critique_draft, revise_draft,
    persona_interview_questions, persona_interview_extract,
    learn_edit_style, draft_audience_segments, draft_anchors,
    mine_from_transcript, mine_structure,
)
from app.services.media_legacy import split_legacy_scripts, create_legacy_contents
from app.services.media_mine_queue import (
    enqueue_candidates, list_pending_grouped, adopt_candidates, discard_candidates)
from app.services.media_playbook import list_playbooks, get_playbook
from app.services.media_flow import finalize_updates, clean_body
from app.services.media_decision import build_decision_context, rank_pool
from app.services.media_review_cycle import (
    run_l2_cycle, list_cycles, get_cycle, delete_cycle)
from app.services.media_phase_review import (
    run_l3_review, list_phase_reviews, get_phase_review, _next_phase)
from app.services.ai_router import _load_config
from app.services.agent_tools import run_agent_loop
from app.services.media_agent_tools import MEDIA_TOOL_SCHEMAS, dispatch_media_tool
from app.services.media_assistant import MEDIA_ASSISTANT_SYSTEM, list_actions, revert_action
from app.services.constitution import with_constitution
from app.config import BASE_DIR

log = logging.getLogger(__name__)

router = APIRouter()

ASR_PUBLIC_DIR = BASE_DIR / "data" / "asr_public"

TRAIT_DIMENSIONS = {
    "positioning": "定位",
    "audience": "受众",
    "tone": "语气",
    "topics": "选题方向",
    "taboo": "内容禁区",
    "signature": "记忆点",
    "differentiator": "差异化",
    "anchor": "生意锚点",
}

# 原料库素材类型（对应 spec §3.3 media_material.type）
MATERIAL_TYPES = {
    "story": "故事",
    "pit": "踩过的坑",
    "judgment": "判断",
    "opinion": "观点",
    "data": "数据素材",
    "quote": "金句",
}
# use_count 到这个数就提示"用旧了"（受众会听腻，逼着补新料）。阈值待实测调，见 spec §8.4。
MATERIAL_FATIGUE = 3


def _tpl(request, name, ctx):
    ctx["request"] = request
    return request.app.state.templates.TemplateResponse(request, name, ctx)


async def _first_persona_id(db) -> str | None:
    """一期只有一个人设；架构上支持多人设，这里取第一个 active 的。"""
    cur = await db.execute(
        "SELECT id FROM media_persona WHERE status='active' ORDER BY created_at LIMIT 1")
    row = await cur.fetchone()
    return row["id"] if row else None


async def _current_persona_id(request, db) -> str | None:
    """当前人设：读 cookie media_persona→校验存在→回落第一个 active。"""
    pid = request.cookies.get("media_persona")
    if pid:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        if await cur.fetchone():
            return pid
    return await _first_persona_id(db)


# ─────────────── 人设 ───────────────

@router.get("/media/persona", response_class=HTMLResponse)
async def persona_home(request: Request):
    """没有人设时引导创建，有则跳到第一个人设档案。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
    finally:
        await db.close()
    if pid:
        return RedirectResponse(f"/media/persona/{pid}", status_code=302)
    return _tpl(request, "media_persona.html",
                {"persona": None, "traits_by_dim": {}, "accounts": [],
                 "dimensions": TRAIT_DIMENSIONS, "platforms": PLATFORMS,
                 "archived": []})


@router.post("/media/persona")
async def persona_create(name: str = Form(...), one_liner: str = Form(""),
                         current_phase: str = Form("冷启动")):
    pid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_persona (id,name,one_liner,current_phase) VALUES (?,?,?,?)",
            (pid, name.strip(), one_liner.strip(), current_phase.strip()))
        await db.commit()
    finally:
        await db.close()
    resp = RedirectResponse("/media/board", status_code=302)
    resp.set_cookie("media_persona", pid, max_age=31536000, httponly=True, samesite="lax")
    return resp


@router.get("/media/persona/{pid}/enter")
async def persona_enter(pid: str):
    """选中人设：设 cookie 后进它的看板。无效 pid 回总览。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT id FROM media_persona WHERE id=?", (pid,))
        ok = await cur.fetchone() is not None
    finally:
        await db.close()
    if not ok:
        return RedirectResponse("/media", status_code=302)
    resp = RedirectResponse("/media/board", status_code=302)
    resp.set_cookie("media_persona", pid, max_age=31536000,
                    httponly=True, samesite="lax")
    return resp


@router.get("/media/persona/{pid}", response_class=HTMLResponse)
async def persona_detail(request: Request, pid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        persona = dict(row) if row else None

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active' "
            "ORDER BY confidence DESC, created_at DESC", (pid,))
        traits = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='archived' "
            "ORDER BY created_at DESC", (pid,))
        archived = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_account WHERE persona_id=? ORDER BY created_at", (pid,))
        accounts = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM media_content WHERE persona_id=? "
            "AND authoring_stage='finalized' AND ai_draft != '' "
            "AND script != '' AND script != ai_draft", (pid,))
        learnable_count = (await cur.fetchone())["n"]

        l2_cycles = await list_cycles(db, pid) if persona else []
        l3_reviews = await list_phase_reviews(db, pid) if persona else []
    finally:
        await db.close()

    traits_by_dim = {}
    for dim in TRAIT_DIMENSIONS:
        hit = [t for t in traits if t["dimension"] == dim]
        if hit:
            traits_by_dim[dim] = hit

    done_modules = completed_modules([t["dimension"] for t in traits])

    return _tpl(request, "media_persona.html",
                {"persona": persona, "traits_by_dim": traits_by_dim,
                 "accounts": accounts, "dimensions": TRAIT_DIMENSIONS,
                 "platforms": PLATFORMS, "archived": archived,
                 "done_count": len(done_modules), "module_total": len(PERSONA_MODULE_ORDER),
                 "learnable_count": learnable_count, "l2_cycles": l2_cycles,
                 "l3_reviews": l3_reviews})


@router.post("/media/persona/{pid}/trait")
async def trait_create(pid: str, dimension: str = Form(...),
                       content: str = Form(...), brief: str = Form(""),
                       confidence: int = Form(3), evidence: str = Form("")):
    db = await get_db()
    try:
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        phase = row["current_phase"] if row else ""
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?,?,?,?,'manual',?,?,?)",
            (str(uuid.uuid4()), pid, dimension, content.strip(),
             brief.strip()[:30], evidence.strip(), confidence, phase))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.post("/media/trait/{tid}/archive")
async def trait_archive(tid: str):
    """归档而非删除 —— 人设演化史要完整留痕。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id FROM media_persona_trait WHERE id=?", (tid,))
        row = await cur.fetchone()
        pid = row["persona_id"] if row else ""
        await db.execute(
            "UPDATE media_persona_trait SET status='archived' WHERE id=?", (tid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.post("/media/persona/{pid}/interview/{module}/questions")
async def persona_interview_q(pid: str, module: str):
    """出题：AJAX，透传 AI 结果给前端。"""
    db = await get_db()
    try:
        res = await persona_interview_questions(db, pid, module)
    finally:
        await db.close()
    return JSONResponse(res)


@router.post("/media/persona/{pid}/interview/{module}/extract")
async def persona_interview_ex(pid: str, module: str, answers: str = Form(...)):
    """提炼候选条目：AJAX，返回 traits 待前端逐条拍板。不写库。"""
    db = await get_db()
    try:
        res = await persona_interview_extract(db, pid, module, answers)
    finally:
        await db.close()
    return JSONResponse(res)


@router.post("/media/persona/{pid}/interview/adopt")
async def persona_interview_adopt(pid: str, dimension: str = Form(...),
                                  content: str = Form(...), brief: str = Form(""),
                                  confidence: int = Form(3), evidence: str = Form(""),
                                  phase_tag: str = Form(""),
                                  source: str = Form("interview")):
    """人拍板：把一条候选条目写进注册表。source 区分来源（interview / learned_edit）。"""
    src = source if source in ("interview", "learned_edit", "reverse_mine",
                               "l2_review") else "interview"
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_persona_trait "
            "(id,persona_id,dimension,content,brief,source,evidence,confidence,phase_tag) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), pid, dimension, content.strip(),
             brief.strip()[:30], src, evidence.strip(), confidence, phase_tag.strip()))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/persona/{pid}/learn-edits")
async def persona_learn_edits(pid: str):
    """功能B：AI 复盘最近定稿的改稿习惯，返回候选（绝不写库，人拍板 adopt 才入）。"""
    db = await get_db()
    try:
        try:
            result = await learn_edit_style(db, pid)
        except Exception as e:
            log.exception("学改稿提炼失败")
            return JSONResponse({"ok": False, "error": str(e),
                                 "traits": [], "pair_count": 0})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/persona/{pid}/new-phase")
async def persona_new_phase(pid: str, new_phase: str = Form(...)):
    """换阶段：归档旧阶段的 active 条目（永久条目 phase_tag 空、不受影响），更新当前阶段。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        old = row["current_phase"] if row else ""
        if old:
            await db.execute(
                "UPDATE media_persona_trait SET status='archived' "
                "WHERE persona_id=? AND status='active' AND phase_tag=?", (pid, old))
        await db.execute(
            "UPDATE media_persona SET current_phase=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (new_phase.strip(), pid))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


@router.get("/media/persona/{pid}/interview", response_class=HTMLResponse)
async def persona_interview_page(request: Request, pid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        persona = dict(row) if row else None
        cur = await db.execute(
            "SELECT dimension FROM media_persona_trait "
            "WHERE persona_id=? AND status='active'", (pid,))
        active_dims = [r["dimension"] for r in await cur.fetchall()]
    finally:
        await db.close()
    done = completed_modules(active_dims)
    return _tpl(request, "media_persona_interview.html",
                {"persona": persona, "modules": PERSONA_MODULES,
                 "module_order": PERSONA_MODULE_ORDER, "done": done})


@router.post("/media/persona/{pid}/account")
async def account_create(pid: str, platform: str = Form(...),
                         account_name: str = Form(""), account_url: str = Form(""),
                         platform_note: str = Form("")):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_account "
            "(id,persona_id,platform,account_name,account_url,platform_note) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), pid, platform, account_name.strip(),
             account_url.strip(), platform_note.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/persona/{pid}", status_code=302)


# ─────────────── 原料库（资产层 media_material）───────────────

@router.get("/media/materials", response_class=HTMLResponse)
async def materials_home(request: Request):
    """原料库查看页：补料闭环沉淀下来的真料"档案柜"。按 type 分组，显示
    brief / use_count / 来源，用旧的（use_count≥阈值）标灰提示补新料。spec §3.3。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona = None
        materials = []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active' "
                "ORDER BY use_count ASC, created_at DESC", (pid,))
            materials = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()

    # 按类型分组，已知类型按 MATERIAL_TYPES 顺序，未知类型兜底进最后
    by_type = {}
    for t in MATERIAL_TYPES:
        hit = [m for m in materials if m["type"] == t]
        if hit:
            by_type[t] = hit
    other = [m for m in materials if m["type"] not in MATERIAL_TYPES]
    if other:
        by_type["_other"] = other

    return _tpl(request, "media_materials.html", {
        "persona": persona, "materials_by_type": by_type,
        "types": MATERIAL_TYPES, "total": len(materials),
        "fatigue": MATERIAL_FATIGUE})


@router.post("/media/materials")
async def material_create(persona_id: str = Form(...), type: str = Form("story"),
                          detail: str = Form(...), brief: str = Form(""),
                          usable_scene: str = Form(""), emotion: str = Form("")):
    """随手记：一句话直接存进原料库（spec §3.3 入库路径①）。"""
    mtype = type if type in MATERIAL_TYPES else "story"
    detail = detail.strip()
    if not detail:
        return RedirectResponse("/media/materials", status_code=302)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_material "
            "(id,persona_id,type,title,detail,brief,emotion,usable_scene,source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), persona_id, mtype, detail[:40], detail,
             (brief.strip() or detail)[:30], emotion.strip(),
             usable_scene.strip(), "随手记"))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/materials", status_code=302)


@router.post("/media/material/{mid}/archive")
async def material_archive(mid: str):
    """归档一条原料（软删，保留演化史，不再注入 AI）。"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_material SET status='archived' WHERE id=?", (mid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/materials", status_code=302)


# ─────────────── 受众画像（资产层🅑 media_audience）───────────────

@router.get("/media/audience", response_class=HTMLResponse)
async def audience_home(request: Request):
    """受众画像查看页：segment 卡片，按付费意愿降序（值钱的靠前）。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona = None
        segments = []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_audience WHERE persona_id=? AND status='active' "
                "ORDER BY pay_willingness DESC, confidence DESC, created_at DESC", (pid,))
            segments = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_audience.html",
                {"persona": persona, "segments": segments, "total": len(segments)})


@router.post("/media/audience")
async def audience_create(persona_id: str = Form(...), segment: str = Form(...),
                          who: str = Form(""), anxiety: str = Form(""),
                          desire: str = Form(""), objection: str = Form(""),
                          language: str = Form(""), pay_willingness: int = Form(3),
                          pay_scene: str = Form(""), pay_ceiling: str = Form(""),
                          evidence: str = Form("")):
    """手动新增一条 segment（source='manual'）。"""
    if not segment.strip():
        return RedirectResponse("/media/audience", status_code=302)
    pw = pay_willingness if 1 <= pay_willingness <= 5 else 3
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_audience (id,persona_id,segment,who,anxiety,desire,"
            "objection,language,pay_willingness,pay_scene,pay_ceiling,evidence,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'manual')",
            (str(uuid.uuid4()), persona_id, segment.strip(), who.strip(), anxiety.strip(),
             desire.strip(), objection.strip(), language.strip(), pw,
             pay_scene.strip(), pay_ceiling.strip(), evidence.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/audience", status_code=302)


@router.post("/media/audience/draft")
async def audience_draft(request: Request, answers: str = Form("")):
    """AI 起草受众画像候选（不写库）。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "先建人设", "segments": []})
        try:
            result = await draft_audience_segments(db, pid, answers)
        except Exception as e:
            log.exception("受众起草失败")
            return JSONResponse({"ok": False, "error": str(e), "segments": []})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/audience/adopt")
async def audience_adopt(persona_id: str = Form(...), segment: str = Form(...),
                         who: str = Form(""), anxiety: str = Form(""),
                         desire: str = Form(""), objection: str = Form(""),
                         language: str = Form(""), pay_willingness: int = Form(3),
                         pay_scene: str = Form(""), pay_ceiling: str = Form(""),
                         evidence: str = Form(""), confidence: int = Form(3)):
    """人拍板：把一条候选 segment 写库（source='interview'）。"""
    pw = pay_willingness if 1 <= pay_willingness <= 5 else 3
    cf = confidence if 1 <= confidence <= 5 else 3
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_audience (id,persona_id,segment,who,anxiety,desire,"
            "objection,language,pay_willingness,pay_scene,pay_ceiling,evidence,"
            "confidence,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'interview')",
            (str(uuid.uuid4()), persona_id, segment.strip(), who.strip(), anxiety.strip(),
             desire.strip(), objection.strip(), language.strip(), pw,
             pay_scene.strip(), pay_ceiling.strip(), evidence.strip(), cf))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/audience/{aid}/archive")
async def audience_archive(aid: str):
    db = await get_db()
    try:
        await db.execute("UPDATE media_audience SET status='archived' WHERE id=?", (aid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/audience", status_code=302)


# ─────────────── 生意锚点（资产层🅑 media_anchor）───────────────

ANCHOR_TYPE_LABELS = {
    "product": "自有产品", "service": "服务", "带货": "带货",
    "广告": "广告", "引流私域": "引流私域",
}
ANCHOR_STATUS_ORDER = ["proven", "validating", "dropped"]
ANCHOR_STATUS_LABELS = {"proven": "已跑通", "validating": "验证中", "dropped": "已放弃"}


@router.get("/media/anchor", response_class=HTMLResponse)
async def anchor_home(request: Request):
    """生意锚点查看页：按 status 分组 proven→validating→dropped。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona = None
        anchors = []
        if pid:
            cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
            row = await cur.fetchone()
            persona = dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM media_anchor WHERE persona_id=? AND status!='archived' "
                "ORDER BY created_at DESC", (pid,))
            anchors = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    by_status = {}
    for st in ANCHOR_STATUS_ORDER:
        hit = [a for a in anchors if a["status"] == st]
        if hit:
            by_status[st] = hit
    return _tpl(request, "media_anchor.html",
                {"persona": persona, "anchors_by_status": by_status, "total": len(anchors),
                 "type_labels": ANCHOR_TYPE_LABELS, "status_labels": ANCHOR_STATUS_LABELS,
                 "status_order": ANCHOR_STATUS_ORDER})


@router.post("/media/anchor")
async def anchor_create(persona_id: str = Form(...), name: str = Form(...),
                        type: str = Form("service"), value_prop: str = Form(""),
                        price_band: str = Form(""), path: str = Form(""),
                        evidence: str = Form(""), status: str = Form("validating")):
    if not name.strip():
        return RedirectResponse("/media/anchor", status_code=302)
    atype = type if type in ANCHOR_TYPE_LABELS else "service"
    st = status if status in ANCHOR_STATUS_LABELS else "validating"
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_anchor (id,persona_id,name,type,value_prop,price_band,"
            "path,evidence,status,source) VALUES (?,?,?,?,?,?,?,?,?, 'manual')",
            (str(uuid.uuid4()), persona_id, name.strip(), atype, value_prop.strip(),
             price_band.strip(), path.strip(), evidence.strip(), st))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/anchor", status_code=302)


@router.post("/media/anchor/draft")
async def anchor_draft(request: Request, answers: str = Form("")):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "先建人设", "anchors": []})
        try:
            result = await draft_anchors(db, pid, answers)
        except Exception as e:
            log.exception("锚点起草失败")
            return JSONResponse({"ok": False, "error": str(e), "anchors": []})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/anchor/adopt")
async def anchor_adopt(persona_id: str = Form(...), name: str = Form(...),
                       type: str = Form("service"), value_prop: str = Form(""),
                       price_band: str = Form(""), path: str = Form(""),
                       evidence: str = Form(""), status: str = Form("validating")):
    atype = type if type in ANCHOR_TYPE_LABELS else "service"
    st = status if status in ANCHOR_STATUS_LABELS else "validating"
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_anchor (id,persona_id,name,type,value_prop,price_band,"
            "path,evidence,status,source) VALUES (?,?,?,?,?,?,?,?,?, 'interview')",
            (str(uuid.uuid4()), persona_id, name.strip(), atype, value_prop.strip(),
             price_band.strip(), path.strip(), evidence.strip(), st))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/anchor/{aid}/archive")
async def anchor_archive(aid: str):
    db = await get_db()
    try:
        await db.execute("UPDATE media_anchor SET status='archived' WHERE id=?", (aid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/anchor", status_code=302)


# ─────────────── 话题库 ───────────────

TOPIC_SOURCES = {
    "manual": "人工", "ai_rec": "AI推荐", "theme": "主题展开", "hot": "热点",
    "comment": "评论区", "competitor": "对标", "review": "复盘衍生",
}


@router.get("/media/topics", response_class=HTMLResponse)
async def topics_home(request: Request, source: str = ""):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        # 已采用但内容还没进发布的，继续留在池子里（标「制作中」）——
        # 写脚本阶段人还可能改主意换选题，立刻消失就找不回来了。
        # 一旦内容进到 ready 及以后（发布/已发/已复盘）才移出池子。
        sql = ("SELECT t.*, c.stage AS content_stage FROM media_topic t "
               "LEFT JOIN media_content c ON c.id = t.adopted_content_id "
               "WHERE t.persona_id=? AND ("
               "  t.status='pool'"
               "  OR (t.status='adopted' AND c.stage IN ('idea','scripted','recording','editing'))"
               ")")
        args = [pid]
        if source:
            sql += " AND t.source=?"
            args.append(source)
        # 待选的排前面（要挑的），制作中的沉到后面（只是留个入口找得回）
        sql += (" ORDER BY CASE WHEN t.status='pool' THEN 0 ELSE 1 END, "
                "t.decision_score DESC, t.fit_score DESC, t.heat DESC, t.created_at DESC")
        cur = await db.execute(sql, tuple(args))
        topics = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_topic WHERE persona_id=? AND status='rejected' "
            "ORDER BY created_at DESC LIMIT 20", (pid,))
        rejected = [dict(r) for r in await cur.fetchall()]
        # 主题库：人投喂的方向，AI 据此展开成选题（见 app/api/media_theme.py）
        # 四个数都是子查询实时算的（不是存下来的），所以采用/弃之后刷新页面就变，
        # 不需要额外的同步逻辑。
        cur = await db.execute(
            "SELECT t.*, "
            "(SELECT COUNT(*) FROM media_topic p WHERE p.theme_id=t.id) AS topic_n, "
            "(SELECT COUNT(*) FROM media_topic p WHERE p.theme_id=t.id AND p.status='pool') AS pool_n, "
            "(SELECT COUNT(*) FROM media_topic p WHERE p.theme_id=t.id AND p.status='adopted') AS adopted_n, "
            "(SELECT COUNT(*) FROM media_topic p WHERE p.theme_id=t.id AND p.status='rejected') AS rejected_n "
            "FROM media_theme t WHERE t.persona_id=? AND COALESCE(t.status,'active')='active' "
            "ORDER BY t.created_at DESC", (pid,))
        themes = [dict(r) for r in await cur.fetchall()]
        # 人设条要显示名字/期数，不能只给 id（否则显示占位的「当前人设」）
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        row = await cur.fetchone()
        persona = dict(row) if row else None
    finally:
        await db.close()
    return _tpl(request, "media_topics.html",
                {"topics": topics, "rejected": rejected, "persona_id": pid,
                 "persona": persona,
                 "themes": themes,
                 # idea 阶段在看板上显示为「待写脚本」，这里保持一致
                 "stage_labels": dict(STAGE_LABELS, idea="待写脚本"),
                 "sources": TOPIC_SOURCES, "cur_source": source})


@router.post("/media/topics")
async def topic_create(persona_id: str = Form(...), title: str = Form(...),
                       puzzle: str = Form(""), reason: str = Form(""),
                       angle: str = Form(""), heat: int = Form(3),
                       fit_score: int = Form(3)):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_topic "
            "(id,persona_id,title,puzzle,source,reason,angle,heat,fit_score) "
            "VALUES (?,?,?,?,'manual',?,?,?,?)",
            (str(uuid.uuid4()), persona_id, title.strip(), puzzle.strip(),
             reason.strip(), angle.strip(), heat, fit_score))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)


async def _adopt_topic(db, topic_id: str) -> str | None:
    """话题 → 内容。把谜题和理由一起带过去，开工时不用重新想。"""
    cur = await db.execute("SELECT * FROM media_topic WHERE id=?", (topic_id,))
    row = await cur.fetchone()
    if not row or row["status"] != "pool":
        return None
    t = dict(row)
    cid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_content "
        "(id,persona_id,title,puzzle,stage,idea_source,idea_reason) "
        "VALUES (?,?,?,?,'idea',?,?)",
        (cid, t["persona_id"], t["title"], t["puzzle"], t["source"], t["reason"]))
    await db.execute(
        "UPDATE media_topic SET status='adopted', adopted_content_id=? WHERE id=?",
        (cid, topic_id))
    await db.commit()
    return cid


@router.post("/media/topic/{tid}/adopt")
async def topic_adopt(tid: str):
    db = await get_db()
    try:
        cid = await _adopt_topic(db, tid)
    finally:
        await db.close()
    if not cid:
        return RedirectResponse("/media/topics", status_code=302)
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/topic/{tid}/reject")
async def topic_reject(tid: str, rejected_reason: str = Form("")):
    """弃单必须留原因 —— 下次 AI 推荐时带上，防止重复推同类垃圾。"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_topic SET status='rejected', rejected_reason=? WHERE id=?",
            (rejected_reason.strip(), tid))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)


# ─────────────── 人设总览 ───────────────

@router.get("/media/overview", response_class=HTMLResponse)
@router.get("/media", response_class=HTMLResponse)
async def media_overview_page(request: Request):
    db = await get_db()
    try:
        rows = await persona_overview(db)
    finally:
        await db.close()
    if not rows:
        return RedirectResponse("/media/persona", status_code=302)   # 零人设→引导建
    return _tpl(request, "media_overview.html", {"personas": rows})


# ─────────────── 内容看板 ───────────────

@router.get("/media/board", response_class=HTMLResponse)
async def board(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return RedirectResponse("/media/persona", status_code=302)
        cur = await db.execute("SELECT * FROM media_persona WHERE id=?", (pid,))
        persona = dict(await cur.fetchone())

        cur = await db.execute(
            "SELECT * FROM media_content WHERE persona_id=? "
            "ORDER BY updated_at DESC, created_at DESC", (pid,))
        contents = [dict(r) for r in await cur.fetchall()]

        # 每条内容的三平台发布状态 + 最新播放量，看板卡片上直接显示
        cur = await db.execute(
            "SELECT p.content_id, p.id AS publish_id, a.platform, p.status, "
            "  (SELECT views FROM media_metrics m WHERE m.publish_id=p.id "
            "   ORDER BY snapshot_at DESC LIMIT 1) AS views "
            "FROM media_publish p JOIN media_account a ON a.id=p.account_id "
            "JOIN media_content c ON c.id=p.content_id WHERE c.persona_id=?", (pid,))
        pubs = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_topic WHERE persona_id=? AND status='pool'",
            (pid,))
        pool_count = (await cur.fetchone())["c"]
    finally:
        await db.close()

    by_content = {}
    for p in pubs:
        by_content.setdefault(p["content_id"], []).append(p)
    for c in contents:
        c["publishes"] = by_content.get(c["id"], [])

    columns = [{"stage": s, "label": STAGE_LABELS[s],
                "cards": [c for c in contents if c["stage"] == s]} for s in STAGES]

    return _tpl(request, "media_board.html",
                {"persona": persona, "columns": columns, "platforms": PLATFORMS,
                 "pool_count": pool_count, "total": len(contents)})


@router.post("/media/content")
async def content_create(persona_id: str = Form(...), title: str = Form(...),
                         puzzle: str = Form("")):
    cid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,puzzle,stage,idea_source) "
            "VALUES (?,?,?,?,'idea','manual')",
            (cid, persona_id, title.strip(), puzzle.strip()))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/content/{cid}/stage")
async def content_stage(cid: str, to: str = Form(...), back: str = Form("")):
    """推进或退回阶段。非法流转静默忽略，不报错打断用户。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if row and can_transition(row["stage"], to):
            await db.execute(
                "UPDATE media_content SET stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (to, cid))
            await db.commit()
    finally:
        await db.close()
    target = "/media" if back == "board" else f"/media/content/{cid}"
    return RedirectResponse(target, status_code=302)


@router.post("/media/reverse-ingest")
async def media_reverse_ingest(request: Request, video_url: str = Form(...)):
    """功能C：贴已发视频链接→转写→AI提选题→建 content。"""
    url = video_url.strip()
    if not url:
        return JSONResponse({"ok": False, "error": "请填视频链接"})
    cfg = (_load_config().get("douyin_asr") or {})
    if not cfg.get("api_key") and not (cfg.get("app_id") and cfg.get("access_key")):
        return JSONResponse({"ok": False, "error": "未配置豆包 ASR 凭证，去设置页填"})
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        public_base = (cfg.get("public_base") or str(request.base_url)).rstrip("/")
        cookies_file = BASE_DIR / "data" / "douyin_cookies.txt"
        cookies_path = cookies_file if cookies_file.exists() else None
        try:
            result = await reverse_ingest(db, pid, url, cfg, public_base,
                                          ASR_PUBLIC_DIR, cookies_path=cookies_path)
        except Exception as e:
            log.exception("视频反向入库失败")
            return JSONResponse({"ok": False, "error": "视频反向入库出错，请查看服务器日志"})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/reverse/paste-text/preview")
async def reverse_paste_preview(text: str = Form("")):
    """切分预览，不写库。"""
    segs = split_legacy_scripts(text)
    return JSONResponse({"ok": True, "count": len(segs), "segments": segs})


@router.post("/media/reverse/paste-text/commit")
async def reverse_paste_commit(persona_id: str = Form(...), segments: str = Form(...)):
    """确认的段落 → 建成 media_content(legacy_text)。"""
    try:
        segs = json.loads(segments)
    except Exception:
        segs = []
    if not isinstance(segs, list) or not segs:
        return JSONResponse({"ok": False, "error": "无有效段落", "count": 0})
    db = await get_db()
    try:
        n = await create_legacy_contents(db, persona_id, [str(s) for s in segs])
    finally:
        await db.close()
    return JSONResponse({"ok": True, "count": n})


@router.post("/media/legacy/mark-winner")
async def legacy_mark_winner(content_ids: list[str] = Form([]),
                             winner: int = Form(1)):
    """普通表单提交 → 标记后跳回老文案页（不是 AJAX，别返 JSON）。"""
    if content_ids:
        db = await get_db()
        try:
            qs = ",".join("?" for _ in content_ids)
            await db.execute(
                f"UPDATE media_content SET is_winner=? WHERE id IN ({qs})",
                [1 if winner else 0, *content_ids])
            await db.commit()
        finally:
            await db.close()
    return RedirectResponse("/media/legacy", status_code=303)


@router.get("/media/legacy", response_class=HTMLResponse)
async def legacy_home(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        rows = []
        if pid:
            cur = await db.execute(
                "SELECT id,title,idea_source,is_winner,mined_signature_at,mined_essence_at "
                "FROM media_content "
                "WHERE persona_id=? AND idea_source IN ('video_reverse','legacy_text') "
                "ORDER BY created_at DESC", (pid,))
            rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_legacy.html", {"items": rows})


@router.get("/media/playbook", response_class=HTMLResponse)
async def playbook_home(request: Request):
    db = await get_db()
    try:
        pbs = await list_playbooks(db)
    finally:
        await db.close()
    return _tpl(request, "media_playbook.html", {"playbooks": pbs})


@router.post("/media/playbook/{pid}/status")
async def playbook_set_status(pid: str, status: str = Form(...)):
    if status in ("validating", "proven"):
        db = await get_db()
        try:
            await db.execute("UPDATE media_playbook SET status=? WHERE id=?", (status, pid))
            await db.commit()
        finally:
            await db.close()
    return RedirectResponse("/media/playbook", status_code=302)


@router.get("/media/asr-audio/{token}")
async def media_asr_audio(token: str):
    """公开免登录返回临时音频（供豆包 ASR 抓）。防目录穿越。"""
    if "/" in token or "\\" in token or ".." in token:
        return JSONResponse({"error": "bad token"}, status_code=404)
    path = _Path(ASR_PUBLIC_DIR) / token
    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(path), media_type="audio/mpeg")


@router.post("/media/topics/ai-recommend")
async def topics_ai_recommend(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        try:
            result = await recommend_topics(db, pid)
        except Exception as e:
            log.exception("AI 推选题失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/topics/tag")
async def topics_tag(request: Request):
    """一键给选题池未打标话题补受众/锚点/护栏标。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先创建人设"})
        try:
            result = await tag_topics(db, pid)
        except Exception as e:
            log.exception("AI 标注失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/topics/rank")
async def topics_rank(request: Request):
    """一键给选题池全部 pool 话题打分，写 decision_score + decision_report。纯计算。"""
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return RedirectResponse("/media/topics", status_code=302)
        cur = await db.execute("SELECT current_phase FROM media_persona WHERE id=?", (pid,))
        prow = await cur.fetchone()
        phase = (prow["current_phase"] if prow else "") or "涨粉"

        cur = await db.execute(
            "SELECT * FROM media_persona_trait WHERE persona_id=? AND status='active'", (pid,))
        traits = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_audience WHERE persona_id=? AND status='active'", (pid,))
        audiences = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_anchor WHERE persona_id=? AND status!='dropped' "
            "AND status!='archived'", (pid,))
        anchors = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_anchor WHERE persona_id=? AND status='dropped'", (pid,))
        dropped_anchors = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active'", (pid,))
        materials = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT title, brief FROM media_content WHERE persona_id=? "
            "ORDER BY created_at DESC LIMIT 5", (pid,))
        recent = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT title, topic_fingerprint, outcome FROM media_content WHERE persona_id=?", (pid,))
        history = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_topic WHERE persona_id=? AND status='pool'", (pid,))
        topics = [dict(r) for r in await cur.fetchall()]

        ctx = build_decision_context(traits, audiences, anchors, materials,
                                     recent, history, dropped_anchors)
        ranked = rank_pool(topics, ctx, phase)
        for t in ranked:
            await db.execute(
                "UPDATE media_topic SET decision_score=?, decision_report=? WHERE id=?",
                (t["decision_score"], t["decision_report"], t["id"]))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/topics", status_code=302)


# ─────────────── 内容详情 ───────────────

@router.get("/media/content/{cid}", response_class=HTMLResponse)
async def content_detail(request: Request, cid: str):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return RedirectResponse("/media", status_code=302)
        content = dict(row)

        cur = await db.execute(
            "SELECT * FROM media_persona WHERE id=?", (content["persona_id"],))
        persona = dict(await cur.fetchone())

        cur = await db.execute(
            "SELECT * FROM media_account WHERE persona_id=? AND status='active' "
            "ORDER BY created_at", (content["persona_id"],))
        accounts = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_publish WHERE content_id=?", (cid,))
        pubs = {r["account_id"]: dict(r) for r in await cur.fetchall()}

        # 每个发布记录的最新数据
        metrics = {}
        for aid, p in pubs.items():
            cur = await db.execute(
                "SELECT * FROM media_metrics WHERE publish_id=? "
                "ORDER BY snapshot_at DESC LIMIT 1", (p["id"],))
            m = await cur.fetchone()
            if m:
                md = dict(m)
                try:
                    md["missing_list"] = json.loads(md.get("missing_fields") or "[]")
                except Exception:
                    md["missing_list"] = []
                metrics[p["id"]] = md

        cur = await db.execute(
            "SELECT * FROM media_review WHERE content_id=? ORDER BY created_at", (cid,))
        reviews = [dict(r) for r in await cur.fetchall()]

        # 二期 🅐：创作区数据 —— 候选角度 / 素材包 / 最近一次独立审稿
        cur = await db.execute(
            "SELECT * FROM media_angle WHERE content_id=? ORDER BY is_selected DESC, "
            "created_at", (cid,))
        angles = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_evidence WHERE content_id=? ORDER BY created_at", (cid,))
        evidence = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM media_draft_review WHERE content_id=? "
            "ORDER BY created_at DESC LIMIT 1", (cid,))
        drow = await cur.fetchone()
        latest_review = dict(drow) if drow else None
        playbooks = await list_playbooks(db)

        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_assistant_action "
            "WHERE target_table='media_content' AND target_id=? AND status='applied'", (cid,))
        assistant_touched = (await cur.fetchone())["c"] > 0
    finally:
        await db.close()

    for r in reviews:
        try:
            r["proposed_traits"] = json.loads(r["proposed_traits"] or "[]")
        except (json.JSONDecodeError, TypeError):
            r["proposed_traits"] = []

    if latest_review:
        for k in ("fact_flags", "persona_flags", "platform_flags",
                  "gap_flags", "risk_flags"):
            try:
                latest_review[k] = json.loads(latest_review.get(k) or "[]")
            except (json.JSONDecodeError, TypeError):
                latest_review[k] = []

    return _tpl(request, "media_content.html",
                {"content": content, "persona": persona, "accounts": accounts,
                 "pubs": pubs, "metrics": metrics, "reviews": reviews,
                 "platforms": PLATFORMS, "stages": STAGES,
                 "stage_labels": STAGE_LABELS,
                 "angles": angles, "evidence": evidence,
                 "latest_review": latest_review,
                 "is_reverse": content.get("idea_source") in ("video_reverse", "legacy_text"),
                 "next_stage": next_stage(content["stage"]),
                 "playbooks": playbooks,
                 "assistant_touched": assistant_touched})


@router.post("/media/content/{cid}/mine")
async def content_mine(cid: str):
    """挖精华：前两桶(经历/口头禅)；is_winner 再挖结构桶。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id,script,is_winner FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        try:
            result = await mine_from_transcript(db, row["persona_id"], row["script"] or "")
            if row["is_winner"]:
                cur = await db.execute(
                    "SELECT name FROM media_playbook "
                    "WHERE status IN ('validating','proven')")
                names = [r["name"] for r in await cur.fetchall()]
                st = await mine_structure(db, row["persona_id"], row["script"] or "", names)
                result["playbook_candidate"] = st.get("playbook") if st.get("ok") else None
        except Exception as e:
            log.exception("挖精华失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.get("/media/mine-review", response_class=HTMLResponse)
async def mine_review(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        grouped = (await list_pending_grouped(db, pid) if pid
                   else {"signature": [], "material": [], "playbook": []})
    finally:
        await db.close()
    return _tpl(request, "media_mine_review.html", {"grouped": grouped})


@router.post("/media/mine-review/adopt")
async def mine_review_adopt(candidate_ids: list[str] = Form([])):
    if candidate_ids:
        db = await get_db()
        try:
            await adopt_candidates(db, candidate_ids)
        finally:
            await db.close()
    return RedirectResponse("/media/mine-review", status_code=303)


@router.post("/media/mine-review/discard")
async def mine_review_discard(candidate_ids: list[str] = Form([])):
    if candidate_ids:
        db = await get_db()
        try:
            await discard_candidates(db, candidate_ids)
        finally:
            await db.close()
    return RedirectResponse("/media/mine-review", status_code=303)


@router.post("/media/content/{cid}/mine-to-queue")
async def content_mine_to_queue(cid: str, kind: str = Form(...), force: int = Form(0)):
    """批量挖矿：逐条调（前端编排）。kind=signature 从任意内容挖口头禅；essence 仅爆款挖素材+打法。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id,script,is_winner,mined_signature_at,mined_essence_at "
            "FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        pid, script = row["persona_id"], row["script"] or ""
        try:
            if kind == "signature":
                if row["mined_signature_at"] and not force:
                    return JSONResponse({"ok": True, "added": 0, "skipped": "already"})
                res = await mine_from_transcript(db, pid, script)
                added = await enqueue_candidates(db, pid, cid, "signature",
                                                 res.get("signatures") or [])
                await db.execute("UPDATE media_content SET mined_signature_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
                await db.commit()
                return JSONResponse({"ok": True, "added": added, "skipped": ""})
            elif kind == "essence":
                if not row["is_winner"]:
                    return JSONResponse({"ok": True, "added": 0, "skipped": "not_winner"})
                if row["mined_essence_at"] and not force:
                    return JSONResponse({"ok": True, "added": 0, "skipped": "already"})
                res = await mine_from_transcript(db, pid, script)
                added = await enqueue_candidates(db, pid, cid, "material",
                                                 res.get("materials") or [])
                st = await mine_structure(db, pid, script)
                if st.get("ok") and st.get("playbook"):
                    added += await enqueue_candidates(db, pid, cid, "playbook", [st["playbook"]])
                await db.execute("UPDATE media_content SET mined_essence_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
                await db.commit()
                return JSONResponse({"ok": True, "added": added, "skipped": ""})
            return JSONResponse({"ok": False, "error": "kind 非法"})
        except Exception as e:
            log.exception("批量挖矿失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()


@router.post("/media/content/{cid}/mine/adopt-material")
async def content_mine_adopt_material(cid: str, type: str = Form("story"),
                                      detail: str = Form(...), brief: str = Form("")):
    """采纳一条挖出的内容料 → 写原料库（source=反向挖料）。"""
    mtype = type if type in MATERIAL_TYPES else "story"
    detail = detail.strip()
    if not detail:
        return JSONResponse({"ok": False, "error": "空内容"})
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        await db.execute(
            "INSERT INTO media_material "
            "(id,persona_id,type,title,detail,brief,source) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), row["persona_id"], mtype, detail[:40], detail,
             (brief.strip() or detail)[:30], "反向挖料"))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/content/{cid}/mine/adopt-playbook")
async def content_mine_adopt_playbook(cid: str, name: str = Form(...),
                                      structure: str = Form(""), when_to_use: str = Form(""),
                                      evidence: str = Form(""), similar_to: str = Form("")):
    """采纳结构候选 → 打法库。similar_to 命中已有打法则追加 evidence(归并)，否则新建。"""
    if not name.strip():
        return JSONResponse({"ok": False, "error": "空打法名"})
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        pid = row["persona_id"]
        merged = False
        if similar_to.strip():
            cur = await db.execute(
                "SELECT id,evidence FROM media_playbook WHERE name=?",
                (similar_to.strip(),))
            ex = await cur.fetchone()
            if ex:
                new_ev = ((ex["evidence"] or "") + "\n---\n" + evidence.strip()).strip()
                await db.execute("UPDATE media_playbook SET evidence=? WHERE id=?",
                                 (new_ev, ex["id"]))
                merged = True
        if not merged:
            await db.execute(
                "INSERT INTO media_playbook "
                "(id,persona_id,name,structure,when_to_use,evidence,source,status) "
                "VALUES (?,?,?,?,?,?, 'legacy_mine','validating')",
                (str(uuid.uuid4()), pid, name.strip(), structure.strip(),
                 when_to_use.strip(), evidence.strip()))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True, "merged": merged})


@router.post("/media/content/{cid}/published-at")
async def content_published_at(cid: str, published_at: str = Form("")):
    """存/改真实视频发布时间。"""
    db = await get_db()
    try:
        await db.execute("UPDATE media_content SET published_at=? WHERE id=?",
                         (published_at.strip() or None, cid))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True, "published_at": published_at.strip()})


@router.post("/media/content/{cid}/delete")
async def content_delete(cid: str):
    """删除一条内容（连同发布/复盘等关联记录）。用于清理重复导入等。"""
    db = await get_db()
    try:
        # 先清子表，避免外键约束报错（metrics 挂在 publish 下，先删）
        await db.execute(
            "DELETE FROM media_metrics WHERE publish_id IN "
            "(SELECT id FROM media_publish WHERE content_id=?)", (cid,))
        for tbl in ("media_publish", "media_review", "media_case",
                    "media_evidence", "media_angle", "media_draft_review"):
            await db.execute(f"DELETE FROM {tbl} WHERE content_id=?", (cid,))
        await db.execute("DELETE FROM media_content WHERE id=?", (cid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media", status_code=303)


@router.post("/media/content/{cid}/script")
async def content_save_script(cid: str, script: str = Form(""),
                              edit_note: str = Form(""), cover_idea: str = Form("")):
    # 保存即定稿：只存口播正文，剥掉时长标注和缺料说明（用户要求 #3）
    script = clean_body(script)
    db = await get_db()
    try:
        await db.execute(
            "UPDATE media_content SET script=?, edit_note=?, cover_idea=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (script, edit_note, cover_idea, cid))
        # 脚本从空变有 → 自动推进到 scripted + 标记定稿（省一次手动点击）
        # 存的 script = 人定稿的真实版；ai_draft 由 write_script 单独持有不动，
        # 二者差异保留供功能 B（AI 学改稿）。
        cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if script.strip() and row and row["stage"] == "idea":
            await db.execute(
                "UPDATE media_content SET stage='scripted', "
                "authoring_stage='finalized', finalized_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (cid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


# ─────────────── 数据采集 ───────────────

@router.post("/media/publish/{pubid}/metrics")
async def metrics_manual(pubid: str, content_id: str = Form(...),
                         views: str = Form("0"), likes: str = Form("0"),
                         comments: str = Form("0"), shares: str = Form("0"),
                         new_fans: str = Form("0")):
    db = await get_db()
    try:
        await save_metrics(db, pubid, {
            "views": views, "likes": likes, "comments": comments,
            "shares": shares, "new_fans": new_fans}, "manual")
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{content_id}", status_code=302)


@router.post("/media/publish/{pubid}/metrics/screenshot")
async def metrics_screenshot(pubid: str, file: UploadFile = File(...)):
    """截图识别。识别失败时返回 ok=false，前端提示改用手填 —— 降级链的第二跳。"""
    try:
        raw = await file.read()
        if not raw:
            return JSONResponse({"ok": False, "error": "文件是空的"})
        media_type = file.content_type or "image/png"
        result = await recognize_screenshot(raw, media_type)
        if result.get("ok"):
            db = await get_db()
            try:
                await save_metrics(db, pubid, result["data"], "screenshot")
            finally:
                await db.close()
    except Exception as e:
        log.exception("截图识别失败")
        return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse(result)


@router.post("/media/content/{cid}/ai-script")
async def content_ai_script(cid: str, mode: str = Form("full"),
                            hint: str = Form(""), playbook_id: str = Form("")):
    db = await get_db()
    try:
        try:
            result = await write_script(db, cid, mode=mode, hint=hint, playbook_id=playbook_id)
        except Exception as e:
            log.exception("AI 写脚本失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


# ─────────────── 二期 🅐：写稿前认知子流程 ───────────────

@router.post("/media/content/{cid}/interview")
async def content_interview(cid: str):
    db = await get_db()
    try:
        try:
            result = await interview_questions(db, cid)
        except Exception as e:
            log.exception("AI 采访提问失败")
            return JSONResponse({"ok": False, "error": str(e), "questions": []})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/evidence")
async def content_evidence(cid: str, answers: str = Form("")):
    db = await get_db()
    try:
        try:
            result = await extract_evidence(db, cid, answers)
        except Exception as e:
            log.exception("提炼素材失败")
            return JSONResponse({"ok": False, "error": str(e), "count": 0})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/evidence/{eid}/promote")
async def evidence_promote(cid: str, eid: str, item: str = Form(...),
                           material_type: str = Form("story"), brief: str = Form("")):
    """把本条补的一条真料，人拍板存进原料库（media_material），供以后写别的稿复用。
    回填 evidence.promoted_to_material_id，避免重复入库。spec §5.5 链路2 / 补料闭环 B。"""
    valid = {"story", "pit", "judgment", "opinion", "data", "quote"}
    mtype = material_type if material_type in valid else "story"
    db = await get_db()
    try:
        cur = await db.execute("SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "内容不存在"})
        pid = row["persona_id"]
        mid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_material (id,persona_id,type,title,detail,brief,source) "
            "VALUES (?,?,?,?,?,?,?)",
            (mid, pid, mtype, item.strip()[:40], item.strip(),
             (brief.strip() or item.strip())[:30], "补料"))
        await db.execute(
            "UPDATE media_evidence SET promoted_to_material_id=? WHERE id=?", (mid, eid))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True, "material_id": mid})


@router.post("/media/content/{cid}/angles")
async def content_angles(cid: str):
    db = await get_db()
    try:
        try:
            result = await propose_angles(db, cid)
        except Exception as e:
            log.exception("出角度失败")
            return JSONResponse({"ok": False, "error": str(e), "count": 0})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/angle/{aid}/select")
async def content_angle_select(cid: str, aid: str):
    """把某个备选角度设为选中（前端随后再触发 ai-script 重出草稿）。"""
    db = await get_db()
    try:
        await db.execute("UPDATE media_angle SET is_selected=0 WHERE content_id=?", (cid,))
        await db.execute(
            "UPDATE media_angle SET is_selected=1, status='selected' WHERE id=?", (aid,))
        await db.execute(
            "UPDATE media_content SET selected_angle_id=? WHERE id=?", (aid, cid))
        await db.commit()
    finally:
        await db.close()
    return JSONResponse({"ok": True})


@router.post("/media/content/{cid}/critique")
async def content_critique(cid: str):
    strategy = _load_config().get("media_review_strategy", "layered")
    db = await get_db()
    try:
        try:
            result = await critique_draft(db, cid, strategy=strategy)
        except Exception as e:
            log.exception("独立审稿失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/revise")
async def content_revise(cid: str):
    db = await get_db()
    try:
        try:
            result = await revise_draft(db, cid)
        except Exception as e:
            log.exception("定向修订失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/finalize")
async def content_finalize(cid: str, script: str = Form("")):
    """定稿：人编辑后的真实版进 script，stage→scripted，authoring→finalized。
    ai_draft 不动 —— 保留 AI 草稿供功能 B 对比。"""
    updates = finalize_updates(script)
    db = await get_db()
    try:
        if updates:
            await db.execute(
                "UPDATE media_content SET script=?, stage=?, authoring_stage=?, "
                "finalized_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (updates["script"], updates["stage"], updates["authoring_stage"], cid))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/settings/review-strategy")
async def set_review_strategy(strategy: str = Form("layered")):
    """换脑审稿策略写进 settings.json。合法值 layered/swap_model/same_model。"""
    if strategy not in ("layered", "swap_model", "same_model"):
        strategy = "layered"
    path = BASE_DIR / "data" / "settings.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        cfg = {}
    cfg["media_review_strategy"] = strategy
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return RedirectResponse("/settings?msg=换脑审稿策略已保存", status_code=302)


# ─────────────── 三平台发布 ───────────────

async def _ensure_publish(db, content_id: str, account_id: str) -> str:
    """取或建该内容在该平台的发布记录。"""
    cur = await db.execute(
        "SELECT id FROM media_publish WHERE content_id=? AND account_id=?",
        (content_id, account_id))
    row = await cur.fetchone()
    if row:
        return row["id"]
    pubid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_publish (id,content_id,account_id) VALUES (?,?,?)",
        (pubid, content_id, account_id))
    await db.commit()
    return pubid


@router.post("/media/content/{cid}/publish/{aid}/copy")
async def publish_ai_copy(cid: str, aid: str):
    db = await get_db()
    try:
        try:
            result = await generate_platform_copy(db, cid, aid)
            if result.get("ok"):
                pubid = await _ensure_publish(db, cid, aid)
                await db.execute(
                    "UPDATE media_publish SET publish_text=? WHERE id=?",
                    (result["publish_text"], pubid))
                await db.commit()
        except Exception as e:
            log.exception("AI 生成平台文案失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/publish/{aid}/save")
async def publish_save(cid: str, aid: str, publish_text: str = Form(""),
                       post_url: str = Form(""), mark_published: str = Form("")):
    db = await get_db()
    try:
        pubid = await _ensure_publish(db, cid, aid)
        if mark_published:
            await db.execute(
                "UPDATE media_publish SET publish_text=?, post_url=?, "
                "status='published', published_at=CURRENT_TIMESTAMP WHERE id=?",
                (publish_text, post_url.strip(), pubid))
        else:
            await db.execute(
                "UPDATE media_publish SET publish_text=?, post_url=? WHERE id=?",
                (publish_text, post_url.strip(), pubid))
        await db.commit()

        # 任一平台已发 → 内容自动进入 published 阶段
        cur = await db.execute(
            "SELECT COUNT(*) c FROM media_publish "
            "WHERE content_id=? AND status='published'", (cid,))
        if (await cur.fetchone())["c"] > 0:
            cur = await db.execute("SELECT stage FROM media_content WHERE id=?", (cid,))
            row = await cur.fetchone()
            if row and stage_index(row["stage"]) < stage_index("published"):
                await db.execute(
                    "UPDATE media_content SET stage='published', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (cid,))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


# ─────────────── 复盘 ───────────────

@router.post("/media/persona/{pid}/l2-review")
async def persona_l2_review(pid: str, force: int = Form(0)):
    db = await get_db()
    try:
        try:
            result = await run_l2_cycle(db, pid, force=bool(force))
        except Exception as e:
            log.exception("L2 周期复盘失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.get("/media/review-cycle/{cid}", response_class=HTMLResponse)
async def review_cycle_detail(cid: str, request: Request):
    db = await get_db()
    try:
        cyc = await get_cycle(db, cid)
    finally:
        await db.close()
    if not cyc:
        return RedirectResponse("/media/persona", status_code=302)
    return _tpl(request, "media_review_cycle.html", {"cyc": cyc})


@router.post("/media/review-cycle/{cid}/delete")
async def review_cycle_delete(cid: str):
    """删除一轮周期复盘（跑坏的试错轮）。删后其纳入内容回到可复盘池。"""
    db = await get_db()
    try:
        await delete_cycle(db, cid)
    finally:
        await db.close()
    return RedirectResponse("/media/persona", status_code=302)


@router.post("/media/persona/{pid}/l3-review")
async def persona_l3_review(pid: str, force: int = Form(0)):
    db = await get_db()
    try:
        try:
            result = await run_l3_review(db, pid, force=bool(force))
        except Exception as e:
            log.exception("L3 阶段复盘失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.get("/media/phase-review/{rid}", response_class=HTMLResponse)
async def phase_review_detail(rid: str, request: Request):
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
    finally:
        await db.close()
    if not rev:
        return RedirectResponse("/media/persona", status_code=302)
    return _tpl(request, "media_phase_review.html", {"rev": rev})


@router.post("/media/phase-review/{rid}/apply-phase")
async def phase_review_apply_phase(rid: str):
    """人拍板：把建议的阶段切换真正写进 persona。仅当目标是当前阶段的下一个。"""
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
        if rev:
            cur = await db.execute(
                "SELECT current_phase FROM media_persona WHERE id=?",
                (rev["persona_id"],))
            prow = await cur.fetchone()
            cur_phase = prow["current_phase"] if prow else ""
            target = rev.get("phase_to") or ""
            if target and target == _next_phase(cur_phase):
                await db.execute(
                    "UPDATE media_persona SET current_phase=? WHERE id=?",
                    (target, rev["persona_id"]))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/phase-review/{rid}", status_code=302)


@router.post("/media/phase-review/{rid}/apply-trait")
async def phase_review_apply_trait(rid: str, trait_id: str = Form(...),
                                   action: str = Form(...)):
    """人拍板：归档/晋升一条人设条目。"""
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
        if rev and action in ("archive", "promote"):
            # 校验 trait 属于该 review 的 persona
            cur = await db.execute(
                "SELECT confidence FROM media_persona_trait "
                "WHERE id=? AND persona_id=?", (trait_id, rev["persona_id"]))
            trow = await cur.fetchone()
            if trow:
                if action == "archive":
                    await db.execute(
                        "UPDATE media_persona_trait SET status='archived' WHERE id=?",
                        (trait_id,))
                else:
                    newc = min(5, (trow["confidence"] or 3) + 1)
                    await db.execute(
                        "UPDATE media_persona_trait SET confidence=? WHERE id=?",
                        (newc, trait_id))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/phase-review/{rid}", status_code=302)


@router.post("/media/phase-review/{rid}/apply-anchor")
async def phase_review_apply_anchor(rid: str, anchor_id: str = Form(...),
                                    target_status: str = Form(...)):
    """人拍板：把锚点标为已跑通/已放弃。目标只能 proven/dropped，校验锚点归属。"""
    db = await get_db()
    try:
        rev = await get_phase_review(db, rid)
        if rev and target_status in ("proven", "dropped"):
            cur = await db.execute(
                "SELECT id FROM media_anchor WHERE id=? AND persona_id=?",
                (anchor_id, rev["persona_id"]))
            if await cur.fetchone():
                await db.execute(
                    "UPDATE media_anchor SET status=? WHERE id=?",
                    (target_status, anchor_id))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/phase-review/{rid}", status_code=302)


@router.post("/media/phase-review/{rid}/delete")
async def phase_review_delete(rid: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM media_phase_review WHERE id=?", (rid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/persona", status_code=302)


@router.post("/media/content/{cid}/ai-review")
async def content_ai_review(cid: str):
    db = await get_db()
    try:
        try:
            result = await review_content(db, cid)
        except Exception as e:
            log.exception("AI 复盘失败")
            return JSONResponse({"ok": False, "error": str(e)})
    finally:
        await db.close()
    return JSONResponse(result)


@router.post("/media/content/{cid}/adopt-trait")
async def adopt_trait(cid: str, dimension: str = Form(...), content: str = Form(...),
                      brief: str = Form(""), evidence: str = Form(""),
                      confidence: int = Form(3)):
    """把 AI 提炼的候选条目写入人设 —— 人拍板这一步是故意保留的。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT persona_id FROM media_content WHERE id=?", (cid,))
        row = await cur.fetchone()
        if row:
            pid = row["persona_id"]
            cur = await db.execute(
                "SELECT current_phase FROM media_persona WHERE id=?", (pid,))
            prow = await cur.fetchone()
            await db.execute(
                "INSERT INTO media_persona_trait "
                "(id,persona_id,dimension,content,brief,source,source_content_id,"
                " evidence,confidence,phase_tag) "
                "VALUES (?,?,?,?,?,'ai_from_review',?,?,?,?)",
                (str(uuid.uuid4()), pid, dimension, content.strip(),
                 brief.strip()[:30], cid, evidence.strip(), confidence,
                 prow["current_phase"] if prow else ""))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/media/content/{cid}", status_code=302)


@router.post("/media/feishu/sync")
async def feishu_sync(request: Request):
    db = await get_db()
    try:
        rep = await sync_from_feishu(db)
    except Exception as e:
        log.exception("feishu sync failed")
        rep = {"ok": False, "error": str(e)}
    finally:
        await db.close()
    return JSONResponse(rep)


@router.get("/media/feishu/review")
async def feishu_review(request: Request):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM media_feishu_unmatched WHERE status='pending' "
            "ORDER BY updated_at DESC")
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT c.id,c.title FROM media_content c ORDER BY c.updated_at DESC "
            "LIMIT 200")
        contents = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT a.id, a.platform, a.account_name, a.persona_id, p.name AS persona_name "
            "FROM media_account a JOIN media_persona p ON p.id=a.persona_id "
            "WHERE a.status='active' ORDER BY a.created_at DESC")
        accounts = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_feishu_review.html",
                {"rows": rows, "contents": contents, "accounts": accounts})


@router.post("/media/feishu/unmatched/{uid}/link")
async def feishu_link(request: Request, uid: str, content_id: str = Form(...),
                      account_id: str = Form(...)):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM media_feishu_unmatched WHERE id=? AND status='pending'",
            (uid,))
        u = await cur.fetchone()
        if u:
            cur = await db.execute(
                "SELECT persona_id FROM media_content WHERE id=?", (content_id,))
            content_row = await cur.fetchone()
            cur = await db.execute(
                "SELECT persona_id FROM media_account WHERE id=?", (account_id,))
            account_row = await cur.fetchone()
            if (content_row and account_row
                    and account_row["persona_id"] == content_row["persona_id"]):
                import uuid as _uuid, json as _json
                pubid = str(_uuid.uuid4())
                await db.execute(
                    "INSERT INTO media_publish (id,content_id,account_id,post_url,status) "
                    "VALUES (?,?,?,?,'published')",
                    (pubid, content_id, account_id, u["post_url"]))
                metrics = _json.loads(u["raw_metrics"] or "{}")
                from app.services.media_metrics import normalize_metrics
                m = normalize_metrics(metrics)
                missing_fields = u["missing_fields"] or "[]"
                await db.execute(
                    "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
                    "shares,new_fans,collected_by,missing_fields) "
                    "VALUES (?,?,?,?,?,?,?,'feishu',?)",
                    (str(_uuid.uuid4()), pubid, m["views"], m["likes"], m["comments"],
                     m["shares"], m["new_fans"], missing_fields))
                await db.execute("UPDATE media_feishu_unmatched SET status='linked',"
                                 "updated_at=CURRENT_TIMESTAMP WHERE id=?", (uid,))
                await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/feishu/review", status_code=302)


@router.post("/media/feishu/unmatched/{uid}/ignore")
async def feishu_ignore(request: Request, uid: str):
    db = await get_db()
    try:
        await db.execute("UPDATE media_feishu_unmatched SET status='ignored',"
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (uid,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/feishu/review", status_code=302)


@router.get("/media/assistant", response_class=HTMLResponse)
async def assistant_page(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        persona, msgs = None, []
        if pid:
            cur = await db.execute("SELECT name,one_liner,current_phase FROM media_persona WHERE id=?", (pid,))
            prow = await cur.fetchone()
            persona = dict(prow) if prow else None
            cur = await db.execute("SELECT role,content FROM media_assistant_message "
                                   "WHERE persona_id=? ORDER BY created_at", (pid,))
            msgs = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return _tpl(request, "media_assistant.html", {"persona": persona, "msgs": msgs})


@router.post("/media/assistant/ask")
async def assistant_ask(request: Request, message: str = Form(...)):
    msg = message.strip()
    if not msg:
        return JSONResponse({"ok": False, "error": "空消息"})
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if not pid:
            return JSONResponse({"ok": False, "error": "请先选人设"})
        cur = await db.execute("SELECT name,one_liner,current_phase FROM media_persona WHERE id=?", (pid,))
        p = dict(await cur.fetchone())
        # 最近 10 条历史
        cur = await db.execute("SELECT role,content FROM media_assistant_message WHERE persona_id=? "
                               "ORDER BY created_at DESC LIMIT 10", (pid,))
        hist = list(reversed([dict(r) for r in await cur.fetchall()]))
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content) VALUES (?,?, 'user',?)",
                         (str(uuid.uuid4()), pid, msg))
        await db.commit()
    finally:
        await db.close()
    persona_line = f"【当前人设】{p['name']}｜{p['one_liner']}｜阶段：{p['current_phase']}"
    hist_text = "\n".join(f"{h['role']}：{h['content']}" for h in hist)
    prompt = (f"{persona_line}\n\n对话历史：\n{hist_text}\n\n──────\n用户：{msg}" if hist_text
              else f"{persona_line}\n\n用户：{msg}")
    try:
        result = await run_agent_loop(prompt, system=with_constitution(MEDIA_ASSISTANT_SYSTEM),
                                      tool_schemas=MEDIA_TOOL_SCHEMAS, dispatch=dispatch_media_tool, ctx=pid)
    except Exception as e:
        log.exception("助手对话失败")
        return JSONResponse({"ok": False, "error": str(e)})
    reply = result.get("response", "")
    db = await get_db()
    try:
        await db.execute("INSERT INTO media_assistant_message (id,persona_id,role,content,cost) "
                         "VALUES (?,?, 'assistant',?,?)",
                         (str(uuid.uuid4()), pid, reply, result.get("cost", 0)))
        await db.commit()
    finally:
        await db.close()
    steps = [s.get("tool") for s in result.get("steps", [])]
    return JSONResponse({"ok": True, "reply": reply, "steps": steps, "cost": result.get("cost", 0)})


@router.post("/media/assistant/clear")
async def assistant_clear(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        if pid:
            await db.execute("DELETE FROM media_assistant_message WHERE persona_id=?", (pid,))
            await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/media/assistant", status_code=303)


@router.get("/media/assistant/actions", response_class=HTMLResponse)
async def assistant_actions(request: Request):
    db = await get_db()
    try:
        pid = await _current_persona_id(request, db)
        acts = await list_actions(db, pid) if pid else []
    finally:
        await db.close()
    for a in acts:
        data = {}
        try:
            data = json.loads(a.get("after_json") or "{}") or json.loads(a.get("before_json") or "{}")
        except (TypeError, ValueError):
            data = {}
        a["summary"] = data.get("title") or data.get("content") or data.get("ai_draft") or ""
    return _tpl(request, "media_assistant_actions.html", {"actions": acts})


@router.post("/media/assistant/action/{aid}/revert")
async def assistant_action_revert(aid: str):
    db = await get_db()
    try:
        await revert_action(db, aid)
    finally:
        await db.close()
    return RedirectResponse("/media/assistant/actions", status_code=303)
