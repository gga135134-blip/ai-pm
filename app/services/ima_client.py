"""腾讯 IMA OpenAPI 客户端（笔记模块）。"""
import json
import logging
import httpx
from app.config import BASE_DIR

log = logging.getLogger(__name__)
IMA_BASE = "https://ima.qq.com"
CFG = BASE_DIR / "data" / "settings.json"


def _creds() -> tuple[str, str]:
    if CFG.exists():
        with open(CFG, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("ima_client_id", ""), d.get("ima_api_key", "")
    return "", ""


def _headers() -> dict:
    client_id, api_key = _creds()
    return {
        "Content-Type": "application/json",
        "ima-openapi-clientid": client_id,
        "ima-openapi-apikey": api_key,
    }


async def _post(path: str, body: dict) -> dict:
    url = f"{IMA_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(url, json=body, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        raise RuntimeError(f"IMA API 错误 [{code}]: {data.get('msg', data)}")
    return data.get("data", {})


async def test_connection() -> str:
    """测试凭证是否有效，返回描述字符串。"""
    client_id, api_key = _creds()
    if not client_id or not api_key:
        return "❌ 未配置 IMA Client ID 或 API Key"
    try:
        data = await _post("openapi/note/v1/list_note_folder_by_cursor", {"cursor": "0", "limit": 1})
        folders = data.get("note_book_folders") or []
        return f"✅ 连接成功（找到 {len(folders)} 个笔记本）"
    except Exception as e:
        return f"❌ 连接失败：{e}"


async def list_note_folders() -> list[dict]:
    """列出所有笔记本，返回 basic_info 列表。"""
    folders = []
    cursor = "0"
    while True:
        data = await _post("openapi/note/v1/list_note_folder_by_cursor", {"cursor": cursor, "limit": 20})
        items = data.get("note_book_folders") or []
        for item in items:
            nb = (item.get("folder") or {}).get("basic_info") or {}
            if nb:
                folders.append(nb)
        if data.get("is_end", True):
            break
        cursor = data.get("next_cursor", "")
        if not cursor:
            break
    return folders


async def get_all_notes_folder_id() -> str:
    """返回「全部笔记」文件夹的 folder_id（folder_type=1）。"""
    folders = await list_note_folders()
    for f in folders:
        if f.get("folder_type") == 1:
            return f.get("folder_id", "")
    # 没找到时返回空，调用方用空字符串兜底
    return ""


async def list_notes_in_folder(folder_id: str = "", limit: int = 20, cursor: str = "") -> dict:
    """列出某笔记本下的笔记（单页）。"""
    return await _post("openapi/note/v1/list_note_by_folder_id", {
        "folder_id": folder_id,
        "cursor": cursor,
        "limit": limit,
    })


async def get_doc_content(doc_id: str) -> str:
    """读取一篇笔记的纯文本内容。"""
    data = await _post("openapi/note/v1/get_doc_content", {
        "doc_id": doc_id,
        "target_content_format": 0,  # 0=纯文本
    })
    return data.get("content") or ""
