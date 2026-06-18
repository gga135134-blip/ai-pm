import uuid
from datetime import datetime
from fastapi import APIRouter, Request, Form, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from app.database import get_db
from app.services.importer import import_file, import_folder, import_upload, import_url, save_image_upload
from app.services.note_ai import classify_content, summarize_notes, generate_weekly_report, chat_with_notes, organize_notes, apply_organize_actions, analyze_image_paths
from app.services.backup import create_backup, list_backups, cleanup_old_backups

router = APIRouter()


@router.get("/notes", response_class=HTMLResponse)
async def note_list(request: Request, tag: str = "", q: str = "", project_id: str = "", folder: str = ""):
    db = await get_db()
    try:
        # 构建查询（回收站里的不显示）
        where_parts = ["n.deleted_at IS NULL"]
        params = []
        if tag:
            where_parts.append("(',' || n.tags || ',' LIKE ?)")
            params.append(f"%,{tag},%")
        if q:
            where_parts.append("(n.title LIKE ? OR n.content LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if project_id:
            where_parts.append("n.project_id = ?")
            params.append(project_id)
        if folder == "__unfiled__":
            where_parts.append("(n.folder = '' OR n.folder IS NULL)")
        elif folder:
            # 匹配自身和子文件夹（如 folder=资料 时也命中 资料/竞品）
            where_parts.append("(n.folder = ? OR n.folder LIKE ?)")
            params.extend([folder, f"{folder}/%"])

        where_clause = "WHERE " + " AND ".join(where_parts)

        cursor = await db.execute(f"""
            SELECT n.*, p.name as project_name
            FROM notes n
            LEFT JOIN projects p ON p.id = n.project_id
            {where_clause}
            ORDER BY n.is_pinned DESC, n.updated_at DESC
        """, params)
        notes = [dict(row) for row in await cursor.fetchall()]

        # 获取所有标签用于侧边栏
        cursor = await db.execute("SELECT tags FROM notes WHERE tags != '' AND deleted_at IS NULL")
        all_tags_raw = [row["tags"] for row in await cursor.fetchall()]
        tag_counts = {}
        for raw in all_tags_raw:
            for t in raw.split(","):
                t = t.strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1

        # 文件夹树（含每个文件夹的笔记数 + 空文件夹 + 自动补全父级）
        cursor = await db.execute("SELECT folder, COUNT(*) as cnt FROM notes WHERE folder != '' AND deleted_at IS NULL GROUP BY folder")
        counts = {row["folder"]: row["cnt"] for row in await cursor.fetchall()}
        cursor = await db.execute("SELECT path FROM folders")
        for row in await cursor.fetchall():
            counts.setdefault(row["path"], 0)
        for p in list(counts.keys()):
            parts = p.split("/")
            for i in range(1, len(parts)):
                counts.setdefault("/".join(parts[:i]), 0)
        folder_counts = sorted(counts.items())
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM notes WHERE (folder = '' OR folder IS NULL) AND deleted_at IS NULL")
        row = await cursor.fetchone()
        unfiled_count = row["cnt"]

        # 获取项目列表用于筛选
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    return request.app.state.templates.TemplateResponse(
        request, "notes.html",
        {
            "request": request,
            "notes": notes,
            "tag_counts": tag_counts,
            "folder_counts": folder_counts,
            "unfiled_count": unfiled_count,
            "projects": projects,
            "current_tag": tag,
            "current_q": q,
            "current_project": project_id,
            "current_folder": folder,
        },
    )


async def _get_all_folders() -> list[str]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT DISTINCT folder FROM notes WHERE folder != '' AND deleted_at IS NULL ORDER BY folder")
        return [row["folder"] for row in await cursor.fetchall()]
    finally:
        await db.close()


@router.get("/notes/new", response_class=HTMLResponse)
async def note_new_form(request: Request, project_id: str = "", task_id: str = "", folder: str = ""):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    folders = await _get_all_folders()
    return request.app.state.templates.TemplateResponse(
        request, "note_form.html",
        {"request": request, "note": None, "projects": projects, "folders": folders,
         "pre_project": project_id, "pre_task": task_id, "pre_folder": folder},
    )


@router.post("/notes/new")
async def note_create(
    title: str = Form(...),
    content: str = Form(""),
    author: str = Form(""),
    project_id: str = Form(""),
    task_id: str = Form(""),
    tags: str = Form(""),
    folder: str = Form(""),
):
    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    # 清理标签格式
    clean_tags = ",".join(t.strip() for t in tags.split(",") if t.strip())
    folder = folder.strip().strip("/")
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, author, project_id, task_id, tags, folder, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (note_id, title, content, author, project_id or None, task_id or None, clean_tags, folder, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


# ── 文件导入（必须在 {note_id} 之前注册）──────────────

@router.get("/notes/import", response_class=HTMLResponse)
async def import_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    folders = await _get_all_folders()
    return request.app.state.templates.TemplateResponse(
        request, "note_import.html", {"request": request, "projects": projects, "folders": folders, "result": None}
    )


async def _import_result_page(request: Request, result_type: str, items: list):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    folders = await _get_all_folders()
    return request.app.state.templates.TemplateResponse(
        request, "note_import.html",
        {"request": request, "projects": projects, "folders": folders, "result": {"type": result_type, "items": items}},
    )


@router.post("/notes/import/upload")
async def import_upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    project_id: str = Form(""),
    author: str = Form(""),
    tags: str = Form(""),
    folder: str = Form(""),
):
    folder = folder.strip().strip("/")
    results = []
    for f in files:
        content = await f.read()
        r = await import_upload(f.filename, content, project_id, author, tags, folder)
        results.append(r)
    return await _import_result_page(request, "upload", results)


@router.post("/notes/import/images")
async def import_images(
    request: Request,
    files: list[UploadFile] = File(...),
    mode: str = Form("plain"),
    project_id: str = Form(""),
    author: str = Form(""),
    tags: str = Form(""),
    folder: str = Form(""),
):
    folder = folder.strip().strip("/")
    clean_tags = ",".join(["图片"] + [t.strip() for t in tags.split(",") if t.strip()])
    now = datetime.now().isoformat()

    # 1. 先保存所有图片
    saved, errors = [], []
    for f in files[:10]:
        content = await f.read()
        r = save_image_upload(f.filename, content)
        if "error" in r:
            errors.append({"error": r["error"]})
        else:
            saved.append(r)

    items = list(errors)
    db = await get_db()
    try:
        if mode == "article" and saved:
            # 多图合并成一篇知识文章
            paths = [s["path"] for s in saved]
            ai_result = await analyze_image_paths(paths, mode="article")
            content_text = ai_result["response"]
            # 第一行如果是 # 标题，提取为笔记标题
            title = f"图片整理 {now[:10]}"
            first_line = content_text.strip().splitlines()[0] if content_text.strip() else ""
            if first_line.startswith("#"):
                title = first_line.lstrip("#").strip()[:100]
            note_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, folder, image_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'image_article', ?, ?, ?, ?)""",
                (note_id, title, content_text, author, project_id or None, clean_tags, folder, ",".join(paths), now, now),
            )
            items.append({"id": note_id, "title": f"{title}（{len(paths)} 张图，费用 ${ai_result['cost']:.4f}）"})
        else:
            # 每张图一条笔记（plain 不分析 / analyze 逐张分析）
            for s in saved:
                content_text = f"（图片笔记：{s['original']}）"
                cost_note = ""
                if mode == "analyze":
                    ai_result = await analyze_image_paths([s["path"]], mode="analyze")
                    content_text = ai_result["response"]
                    cost_note = f"（已 AI 分析，费用 ${ai_result['cost']:.4f}）"
                note_id = str(uuid.uuid4())
                await db.execute(
                    """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, folder, image_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'image', ?, ?, ?, ?)""",
                    (note_id, s["original"], content_text, author, project_id or None, clean_tags, folder, s["path"], now, now),
                )
                items.append({"id": note_id, "title": s["original"] + cost_note})
        await db.commit()
    finally:
        await db.close()

    return await _import_result_page(request, "images", items)


@router.post("/notes/import/url")
async def import_from_url(
    request: Request,
    url: str = Form(...),
    project_id: str = Form(""),
    author: str = Form(""),
    tags: str = Form(""),
    folder: str = Form(""),
):
    folder = folder.strip().strip("/")
    # 支持一次粘贴多个链接（每行一个）
    urls = [u.strip() for u in url.replace(",", "\n").splitlines() if u.strip()]
    results = []
    for u in urls[:10]:  # 一次最多 10 个，防滥用
        r = await import_url(u, project_id, author, tags, folder)
        results.append(r)
    return await _import_result_page(request, "url", results)


@router.post("/notes/import/path")
async def import_from_path(
    request: Request,
    path: str = Form(...),
    project_id: str = Form(""),
    author: str = Form(""),
    tags: str = Form(""),
    folder: str = Form(""),
):
    from pathlib import Path as P
    # 清理用户输入：去掉首尾引号和空格
    path = path.strip().strip('"').strip("'").strip()
    folder = folder.strip().strip("/")
    p = P(path)
    if p.is_file():
        r = await import_file(path, project_id, author, tags, folder)
        items = [r]
    elif p.is_dir():
        r = await import_folder(path, project_id, author, tags, folder)
        items = r.get("imported", [])
        if r.get("skipped"):
            items.append({"skipped": r["skipped"]})
    else:
        items = [{"error": f"路径不存在或无法访问: {path}（提示：服务器在 Linux 上，无法访问你电脑上的 Windows 路径如 C:\\... 或 D:\\...，请改用上传方式）"}]
    return await _import_result_page(request, "path", items)


# ── AI 智能功能（必须在 {note_id} 之前注册）──────────

@router.get("/notes/classify", response_class=HTMLResponse)
async def classify_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_classify.html", {"request": request, "projects": projects, "result": None}
    )


@router.post("/notes/classify")
async def classify_submit(
    request: Request,
    text: str = Form(...),
    project_id: str = Form(""),
    model: str = Form("auto"),
):
    created = await classify_content(text, project_id, model)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "note_classify.html", {"request": request, "projects": projects, "result": created}
    )


# ── 知识库 AI 问答（必须在 {note_id} 之前注册）────────

@router.get("/notes/chat", response_class=HTMLResponse)
async def notes_chat_page(request: Request):
    folders = await _get_all_folders()
    db = await get_db()
    try:
        cursor = await db.execute("SELECT tags FROM notes WHERE tags != '' AND deleted_at IS NULL")
        all_tags_raw = [row["tags"] for row in await cursor.fetchall()]
    finally:
        await db.close()
    tags = sorted({t.strip() for raw in all_tags_raw for t in raw.split(",") if t.strip()})
    return request.app.state.templates.TemplateResponse(
        request, "note_chat.html", {"request": request, "folders": folders, "tags": tags}
    )


@router.post("/notes/chat/ask")
async def notes_chat_ask(question: str = Form(...), history: str = Form(""), scope: str = Form("auto")):
    from fastapi.responses import JSONResponse
    result = await chat_with_notes(question, history, scope=scope)
    return JSONResponse(result)


@router.post("/notes/chat/organize")
async def notes_chat_organize(question: str = Form(...), scope: str = Form("all")):
    from fastapi.responses import JSONResponse
    result = await organize_notes(question, scope)
    return JSONResponse(result)


@router.post("/notes/chat/apply")
async def notes_chat_apply(actions: str = Form(...)):
    import json as _json
    from fastapi.responses import JSONResponse
    try:
        action_list = _json.loads(actions)
        assert isinstance(action_list, list)
    except Exception:
        return JSONResponse({"error": "方案数据格式错误"}, status_code=400)
    updated = await apply_organize_actions(action_list)
    return JSONResponse({"updated": updated})


@router.post("/notes/chat/save")
async def notes_chat_save(
    question: str = Form(...),
    answer: str = Form(...),
    folder: str = Form(""),
):
    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    folder = folder.strip().strip("/")
    content = f"## 问题\n{question}\n\n## 回答\n{answer}"
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, tags, source_type, folder, created_at, updated_at)
            VALUES (?, ?, ?, 'AI问答', 'ai_chat', ?, ?, ?)""",
            (note_id, question[:100], content, folder, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.post("/notes/summarize")
async def summarize_submit(tag: str = Form("")):
    result = await summarize_notes(tag=tag)
    now = datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'ai_summary', ?, ?)""",
            (note_id, f"AI 整理: {tag or '全部笔记'}", result["summary"], f"AI整理,{tag}" if tag else "AI整理", now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.get("/notes/weekly", response_class=HTMLResponse)
async def weekly_report_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "weekly_report.html", {"request": request, "report": None}
    )


@router.post("/notes/weekly")
async def weekly_report_generate(request: Request, model: str = Form("auto")):
    result = await generate_weekly_report(model)
    return request.app.state.templates.TemplateResponse(
        request, "weekly_report.html", {"request": request, "report": result}
    )


# ── 回收站（必须在 {note_id} 之前注册）────────────────

TRASH_KEEP_DAYS = 30


def _delete_image_files(image_paths: list[str]):
    """彻底删除笔记时清掉对应的图片文件"""
    from app.config import BASE_DIR
    for p in image_paths:
        for part in (p or "").split(","):
            name = part.replace("/uploads/", "").strip()
            if name:
                f = BASE_DIR / "data" / "uploads" / name
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass


async def _purge_expired_trash():
    """清除回收站里超过 30 天的笔记（每次打开回收站/列表时顺手清理）"""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=TRASH_KEEP_DAYS)).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute("SELECT image_path FROM notes WHERE deleted_at IS NOT NULL AND deleted_at < ? AND image_path != ''", (cutoff,))
        imgs = [row["image_path"] for row in await cursor.fetchall()]
        await db.execute("DELETE FROM notes WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))
        await db.commit()
    finally:
        await db.close()
    _delete_image_files(imgs)


@router.get("/notes/trash", response_class=HTMLResponse)
async def trash_page(request: Request):
    await _purge_expired_trash()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, title, folder, tags, deleted_at FROM notes WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        )
        trashed = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    # 计算每条剩余天数
    from datetime import timedelta
    for t in trashed:
        try:
            expire = datetime.fromisoformat(t["deleted_at"]) + timedelta(days=TRASH_KEEP_DAYS)
            t["days_left"] = max((expire - datetime.now()).days, 0)
        except (ValueError, TypeError):
            t["days_left"] = TRASH_KEEP_DAYS
    return request.app.state.templates.TemplateResponse(
        request, "note_trash.html", {"request": request, "trashed": trashed, "keep_days": TRASH_KEEP_DAYS}
    )


@router.post("/notes/trash/restore")
async def trash_restore(ids: str = Form(...)):
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if id_list:
        placeholders = ",".join(["?"] * len(id_list))
        db = await get_db()
        try:
            await db.execute(f"UPDATE notes SET deleted_at = NULL WHERE id IN ({placeholders})", id_list)
            await db.commit()
        finally:
            await db.close()
    return RedirectResponse("/notes/trash", status_code=303)


@router.post("/notes/trash/purge")
async def trash_purge(ids: str = Form("")):
    db = await get_db()
    try:
        if ids.strip():
            # 彻底删除指定笔记
            id_list = [i.strip() for i in ids.split(",") if i.strip()]
            placeholders = ",".join(["?"] * len(id_list))
            cursor = await db.execute(f"SELECT image_path FROM notes WHERE deleted_at IS NOT NULL AND id IN ({placeholders}) AND image_path != ''", id_list)
            imgs = [row["image_path"] for row in await cursor.fetchall()]
            await db.execute(f"DELETE FROM notes WHERE deleted_at IS NOT NULL AND id IN ({placeholders})", id_list)
        else:
            # 清空整个回收站
            cursor = await db.execute("SELECT image_path FROM notes WHERE deleted_at IS NOT NULL AND image_path != ''")
            imgs = [row["image_path"] for row in await cursor.fetchall()]
            await db.execute("DELETE FROM notes WHERE deleted_at IS NOT NULL")
        await db.commit()
    finally:
        await db.close()
    _delete_image_files(imgs)
    return RedirectResponse("/notes/trash", status_code=303)


# ── 批量管理 ──────────────────────────────────────────

@router.post("/notes/batch")
async def notes_batch(action: str = Form(...), ids: str = Form(...), folder: str = Form("")):
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        return RedirectResponse("/notes", status_code=303)
    now = datetime.now().isoformat()
    placeholders = ",".join(["?"] * len(id_list))
    db = await get_db()
    try:
        if action == "move":
            clean_folder = folder.strip().strip("/")
            await db.execute(
                f"UPDATE notes SET folder = ?, updated_at = ? WHERE id IN ({placeholders})",
                [clean_folder, now] + id_list,
            )
        elif action == "delete":
            # 软删除：移入回收站
            await db.execute(
                f"UPDATE notes SET deleted_at = ? WHERE id IN ({placeholders})",
                [now] + id_list,
            )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


# ── 文件夹管理 ────────────────────────────────────────

@router.post("/folders/create")
async def folder_create(path: str = Form(...)):
    clean = path.strip().strip("/")
    if clean:
        db = await get_db()
        try:
            await db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (clean,))
            await db.commit()
        finally:
            await db.close()
    return RedirectResponse(f"/notes?folder={clean}" if clean else "/notes", status_code=303)


@router.post("/folders/rename")
async def folder_rename(old: str = Form(...), new: str = Form(...)):
    old_clean = old.strip().strip("/")
    new_clean = new.strip().strip("/")
    if not old_clean or not new_clean or old_clean == new_clean:
        return RedirectResponse("/notes", status_code=303)
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        # 改名/移动：本文件夹和所有子文件夹的笔记路径前缀替换
        await db.execute(
            "UPDATE notes SET folder = ? || substr(folder, ?), updated_at = ? WHERE folder = ? OR folder LIKE ?",
            (new_clean, len(old_clean) + 1, now, old_clean, f"{old_clean}/%"),
        )
        # 同步 folders 表里的记录
        cursor = await db.execute("SELECT path FROM folders WHERE path = ? OR path LIKE ?", (old_clean, f"{old_clean}/%"))
        old_paths = [row["path"] for row in await cursor.fetchall()]
        for p in old_paths:
            await db.execute("DELETE FROM folders WHERE path = ?", (p,))
            await db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (new_clean + p[len(old_clean):],))
        await db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (new_clean,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes?folder={new_clean}", status_code=303)


@router.post("/folders/delete")
async def folder_delete(path: str = Form(...)):
    clean = path.strip().strip("/")
    if not clean:
        return RedirectResponse("/notes", status_code=303)
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        # 笔记不删除，移到未分类
        await db.execute(
            "UPDATE notes SET folder = '', updated_at = ? WHERE folder = ? OR folder LIKE ?",
            (now, clean, f"{clean}/%"),
        )
        await db.execute("DELETE FROM folders WHERE path = ? OR path LIKE ?", (clean, f"{clean}/%"))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


@router.post("/notes/{note_id}/rename")
async def note_rename(note_id: str, title: str = Form(...)):
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute("UPDATE notes SET title = ?, updated_at = ? WHERE id = ?", (title.strip(), now, note_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


# ── 笔记详情（{note_id} 路由放最后）──────────────────

@router.get("/notes/{note_id}", response_class=HTMLResponse)
async def note_detail(request: Request, note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT n.*, p.name as project_name
            FROM notes n
            LEFT JOIN projects p ON p.id = n.project_id
            WHERE n.id = ?
        """, (note_id,))
        note = dict(await cursor.fetchone())

        # 找到同标签的相关笔记
        related = []
        if note["tags"]:
            tag_list = [t.strip() for t in note["tags"].split(",") if t.strip()]
            if tag_list:
                like_parts = " OR ".join(["(',' || tags || ',' LIKE ?)" for _ in tag_list])
                params = [f"%,{t},%" for t in tag_list]
                params.append(note_id)
                cursor = await db.execute(
                    f"SELECT id, title, tags, updated_at FROM notes WHERE ({like_parts}) AND id != ? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 10",
                    params,
                )
                related = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    tag_list = [t.strip() for t in note["tags"].split(",") if t.strip()] if note["tags"] else []

    return request.app.state.templates.TemplateResponse(
        request, "note_detail.html",
        {"request": request, "note": note, "tag_list": tag_list, "related": related},
    )


@router.get("/notes/{note_id}/edit", response_class=HTMLResponse)
async def note_edit_form(request: Request, note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        note = dict(await cursor.fetchone())
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    folders = await _get_all_folders()
    return request.app.state.templates.TemplateResponse(
        request, "note_form.html",
        {"request": request, "note": note, "projects": projects, "folders": folders,
         "pre_project": "", "pre_task": "", "pre_folder": ""},
    )


@router.post("/notes/{note_id}/edit")
async def note_update(
    note_id: str,
    title: str = Form(...),
    content: str = Form(""),
    author: str = Form(""),
    project_id: str = Form(""),
    task_id: str = Form(""),
    tags: str = Form(""),
    folder: str = Form(""),
):
    now = datetime.now().isoformat()
    clean_tags = ",".join(t.strip() for t in tags.split(",") if t.strip())
    folder = folder.strip().strip("/")
    db = await get_db()
    try:
        await db.execute(
            "UPDATE notes SET title=?, content=?, author=?, project_id=?, task_id=?, tags=?, folder=?, updated_at=? WHERE id=?",
            (title, content, author, project_id or None, task_id or None, clean_tags, folder, now, note_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.post("/notes/{note_id}/analyze-images")
async def note_analyze_images(note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT image_path, content FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if row and row["image_path"]:
        paths = [p for p in row["image_path"].split(",") if p.strip()]
        mode = "article" if len(paths) > 1 else "analyze"
        ai_result = await analyze_image_paths(paths, mode=mode)
        now = datetime.now().isoformat()
        new_content = (row["content"] or "") + f"\n\n---\n[AI 图片分析 {now[:16]}]\n\n" + ai_result["response"]
        db = await get_db()
        try:
            await db.execute("UPDATE notes SET content = ?, updated_at = ? WHERE id = ?", (new_content, now, note_id))
            await db.commit()
        finally:
            await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.post("/notes/{note_id}/toggle-core")
async def note_toggle_core(note_id: str):
    """切换核心档标记：⭐ 标的笔记每次任务执行都会强制全文加载给 AI"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT is_core FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()
        new_val = 0 if row["is_core"] else 1
        now = datetime.now().isoformat()
        await db.execute(
            "UPDATE notes SET is_core = ?, updated_at = ? WHERE id = ?",
            (new_val, now, note_id),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@router.post("/notes/{note_id}/pin")
async def note_toggle_pin(note_id: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT is_pinned FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()
        new_val = 0 if row["is_pinned"] else 1
        await db.execute("UPDATE notes SET is_pinned = ? WHERE id = ?", (new_val, note_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


@router.post("/notes/{note_id}/delete")
async def note_delete(note_id: str):
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        # 软删除：移入回收站，30 天后自动清除
        await db.execute("UPDATE notes SET deleted_at = ? WHERE id = ?", (now, note_id))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/notes", status_code=303)


# ── 决策日志 ──────────────────────────────────────────

@router.get("/decisions", response_class=HTMLResponse)
async def decisions_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT d.*, p.name as project_name
            FROM decisions d
            LEFT JOIN projects p ON p.id = d.project_id
            ORDER BY d.created_at DESC
        """)
        decisions = [dict(row) for row in await cursor.fetchall()]
        cursor = await db.execute("SELECT id, name FROM projects ORDER BY name")
        projects = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    return request.app.state.templates.TemplateResponse(
        request, "decisions.html", {"request": request, "decisions": decisions, "projects": projects}
    )


@router.post("/decisions/new")
async def decision_create(
    title: str = Form(...),
    context: str = Form(""),
    decision: str = Form(""),
    reason: str = Form(""),
    made_by: str = Form(""),
    project_id: str = Form(""),
):
    dec_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO decisions (id, project_id, title, context, decision, reason, made_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dec_id, project_id or None, title, context, decision, reason, made_by, now),
        )
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse("/decisions", status_code=303)


# ── 备份 ──────────────────────────────────────────────

@router.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request):
    backups = await list_backups()
    return request.app.state.templates.TemplateResponse(
        request, "backups.html", {"request": request, "backups": backups}
    )


@router.post("/backups/create")
async def backup_create():
    await create_backup()
    await cleanup_old_backups(keep=10)
    return RedirectResponse("/backups", status_code=303)


# ── IMA 同步 ──────────────────────────────────────────────
from fastapi.responses import JSONResponse

@router.post("/notes/ima/test")
async def ima_test():
    try:
        from app.services.ima_client import test_connection
        result = await test_connection()
    except ImportError as e:
        result = f"❌ 缺少依赖包：{e}（请在服务器运行 pip install httpx）"
    except Exception as e:
        result = f"❌ 错误：{e}"
    return JSONResponse({"result": result})


@router.post("/notes/ima/sync")
async def ima_sync():
    """从 IMA 笔记同步到本地知识库（游标翻页，external_id 去重）。"""
    try:
        from app.services.ima_client import list_notes_in_folder, get_doc_content, _creds, get_all_notes_folder_id
    except ImportError as e:
        return JSONResponse({"ok": False, "msg": f"缺少依赖包：{e}（请在服务器运行 pip install httpx）"})

    client_id, api_key = _creds()
    if not client_id or not api_key:
        return JSONResponse({"ok": False, "msg": "未配置 IMA 凭证，请先在设置页填写"})

    created = updated = skipped = 0
    errors = []
    now = datetime.now().isoformat()

    try:
        # 先拿「全部笔记」文件夹的真实 folder_id（folder_type=1）
        all_notes_folder_id = await get_all_notes_folder_id()

        # 游标翻页拉取全部笔记
        cursor = ""
        while True:
            data = await list_notes_in_folder(folder_id=all_notes_folder_id, limit=20, cursor=cursor)
            items = data.get("note_book_list") or []
            if not items:
                break

            db = await get_db()
            try:
                for item in items:
                    info = (item.get("basic_info") or {})
                    ext_id = info.get("docid") or ""
                    if not ext_id:
                        continue
                    title = info.get("title") or "IMA 笔记"
                    # 检查是否已存在
                    cur2 = await db.execute(
                        "SELECT id FROM notes WHERE external_id = ?", (ext_id,)
                    )
                    existing = await cur2.fetchone()
                    # 拉取正文
                    try:
                        content = await get_doc_content(ext_id)
                    except Exception as e:
                        errors.append(f"{title}: {e}")
                        skipped += 1
                        continue

                    if existing:
                        await db.execute(
                            "UPDATE notes SET title=?, content=?, updated_at=? WHERE id=?",
                            (title, content, now, existing["id"]),
                        )
                        updated += 1
                    else:
                        await db.execute(
                            """INSERT INTO notes (id,title,content,source_type,folder,external_id,created_at,updated_at)
                               VALUES (?,?,?,'ima_sync','IMA同步',?,?,?)""",
                            (str(uuid.uuid4()), title, content, ext_id, now, now),
                        )
                        created += 1
                await db.commit()
            finally:
                await db.close()

            if data.get("is_end", True):
                break
            cursor = data.get("next_cursor", "")
            if not cursor:
                break

    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"同步失败：{e}"})

    msg = f"✅ 同步完成：新建 {created} 篇，更新 {updated} 篇，跳过 {skipped} 篇"
    if errors:
        msg += f"（{len(errors)} 条错误：{errors[:3]}）"
    return JSONResponse({"ok": True, "msg": msg})
