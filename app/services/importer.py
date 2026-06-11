import re
import uuid
import os
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from app.database import get_db

SUPPORTED_TEXT = {".md", ".txt", ".csv", ".json", ".py", ".js", ".html", ".css", ".yaml", ".yml", ".toml", ".ini", ".log", ".xml"}


class _TextExtractor(HTMLParser):
    """从 HTML 提取正文文本，跳过脚本/样式/导航"""
    SKIP_TAGS = {"script", "style", "noscript", "header", "footer", "nav", "aside", "iframe", "svg"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
        elif self._skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text + " ")

    def get_text(self) -> str:
        text = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


async def import_url(url: str, project_id: str = "", author: str = "", extra_tags: str = "", folder: str = "") -> dict:
    import httpx

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        }) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return {"error": f"网页返回错误 {e.response.status_code}: {url}"}
    except Exception as e:
        return {"error": f"抓取失败: {e}"}

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return {"error": f"不是网页内容（{content_type}），无法导入"}

    parser = _TextExtractor()
    try:
        parser.feed(resp.text)
    except Exception:
        pass
    text = parser.get_text()
    if not text:
        return {"error": f"未能从网页提取到文字内容: {url}"}

    # 防止超大网页撑爆数据库/后续 AI 调用
    MAX_CHARS = 100_000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n…（网页过长已截断）"

    title = parser.title or url
    content = f"来源: {url}\n\n{text}"

    tags = ["网页", "导入"]
    if extra_tags:
        tags.extend(t.strip() for t in extra_tags.split(",") if t.strip())
    clean_tags = ",".join(tags)

    now = datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, folder, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'url_import', ?, ?, ?)""",
            (note_id, title[:200], content, author, project_id or None, clean_tags, folder, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": note_id, "title": title[:200], "size": len(text)}


async def import_file(file_path: str, project_id: str = "", author: str = "", extra_tags: str = "", folder: str = "") -> dict:
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
            """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, folder, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'file_import', ?, ?, ?)""",
            (note_id, path.name, content, author, project_id or None, clean_tags, folder, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": note_id, "title": path.name, "size": len(content)}


async def import_folder(folder_path: str, project_id: str = "", author: str = "", extra_tags: str = "", folder: str = "") -> dict:
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
            result = await import_file(str(fpath), project_id, author, extra_tags, folder)
            if "error" in result:
                skipped.append(f"{fpath}: {result['error']}")
            else:
                imported.append(result)

    return {"imported": imported, "skipped": skipped}


async def import_upload(filename: str, content: bytes, project_id: str = "", author: str = "", extra_tags: str = "", folder: str = "") -> dict:
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
            """INSERT INTO notes (id, title, content, author, project_id, tags, source_type, folder, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'upload', ?, ?, ?)""",
            (note_id, filename, text, author, project_id or None, clean_tags, folder, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": note_id, "title": filename, "size": len(text)}
