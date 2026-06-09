import uuid
import os
from datetime import datetime
from pathlib import Path
from app.database import get_db

SUPPORTED_TEXT = {".md", ".txt", ".csv", ".json", ".py", ".js", ".html", ".css", ".yaml", ".yml", ".toml", ".ini", ".log", ".xml"}


async def import_file(file_path: str, project_id: str = "", author: str = "", extra_tags: str = "") -> dict:
    path = Path(file_path)
    if not path.exists():
        return {"error": f"文件不存在: {file_path}"}
    if not path.is_file():
        return {"error": f"不是文件: {file_path}"}

    ext = path.suffix.lower()
    if ext not in SUPPORTED_TEXT:
        return {"error": f"不支持的文件类型: {ext}，支持: {', '.join(sorted(SUPPORTED_TEXT))}"}

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = path.read_text(encoding="gbk")
        except Exception:
            return {"error": f"无法读取文件编码: {file_path}"}

    tags = [ext.lstrip("."), "导入"]
    if extra_tags:
        tags.extend(t.strip() for t in extra_tags.split(",") if t.strip())
    clean_tags = ",".join(tags)

    now = datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'file_import', ?, ?)""",
            (note_id, path.name, content, author, project_id or None, clean_tags, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": note_id, "title": path.name, "size": len(content)}


async def import_folder(folder_path: str, project_id: str = "", author: str = "", extra_tags: str = "") -> dict:
    path = Path(folder_path)
    if not path.exists() or not path.is_dir():
        return {"error": f"文件夹不存在: {folder_path}", "imported": [], "skipped": []}

    imported = []
    skipped = []

    for root, dirs, files in os.walk(str(path)):
        # 跳过隐藏目录和常见忽略目录
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "node_modules", ".git", "venv"}]
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = Path(root) / fname
            ext = fpath.suffix.lower()
            if ext not in SUPPORTED_TEXT:
                skipped.append(str(fpath))
                continue
            result = await import_file(str(fpath), project_id, author, extra_tags)
            if "error" in result:
                skipped.append(f"{fpath}: {result['error']}")
            else:
                imported.append(result)

    return {"imported": imported, "skipped": skipped}


async def import_upload(filename: str, content: bytes, project_id: str = "", author: str = "", extra_tags: str = "") -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_TEXT:
        return {"error": f"不支持的文件类型: {ext}"}

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("gbk")
        except Exception:
            return {"error": "无法解码文件内容"}

    tags = [ext.lstrip("."), "上传"]
    if extra_tags:
        tags.extend(t.strip() for t in extra_tags.split(",") if t.strip())
    clean_tags = ",".join(tags)

    now = datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'upload', ?, ?)""",
            (note_id, filename, text, author, project_id or None, clean_tags, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": note_id, "title": filename, "size": len(text)}
