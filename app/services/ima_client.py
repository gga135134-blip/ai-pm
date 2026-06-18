"""腾讯 IMA OpenAPI 客户端（知识库 + 笔记）。"""
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
        "Content-Type": "application/json; charset=utf-8",
        "X-Ima-Clientid": client_id,
        "X-Ima-Apikey": api_key,
    }


async def _post(path: str, body: dict) -> dict:
    url = f"{IMA_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(url, json=body, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"IMA API 错误 [{data.get('code')}]: {data.get('msg')}")
    return data.get("data", {})


async def test_connection() -> str:
    """测试凭证是否有效，返回描述字符串。"""
    client_id, api_key = _creds()
    if not client_id or not api_key:
        return "❌ 未配置 IMA Client ID 或 API Key"
    try:
        data = await _post("openapi/list_docs", {"limit": 1})
        return f"✅ 连接成功（找到笔记 {data.get('total', '?')} 篇）"
    except Exception as e:
        return f"❌ 连接失败：{e}"


async def list_knowledge_bases() -> list[dict]:
    """列出可用知识库。"""
    data = await _post("openapi/knowledge/list", {"limit": 50})
    return data.get("list") or data.get("items") or []


async def list_kb_items(kb_id: str, limit: int = 100, offset: int = 0) -> dict:
    """列出某个知识库里的条目。"""
    data = await _post("openapi/knowledge/list_media", {
        "knowledge_id": kb_id,
        "limit": limit,
        "offset": offset,
    })
    return data


async def get_doc_content(note_id: str) -> str:
    """读取一篇笔记的正文（Markdown）。"""
    data = await _post("openapi/get_doc_content", {"note_id": note_id})
    return data.get("content") or data.get("markdown") or ""


async def list_docs(limit: int = 100, offset: int = 0) -> dict:
    """列出笔记（notes 模块）。"""
    data = await _post("openapi/list_docs", {"limit": limit, "offset": offset})
    return data
