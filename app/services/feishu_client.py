"""飞书开放平台 OpenAPI 客户端（系统层通用能力）。

只负责「拿 token、读多维表格记录」，不含任何媒体业务概念。
以后会议室等其它模块要读飞书，直接复用本模块。
飞书文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list
"""
import time
import httpx

from app.config import settings

_BASE = "https://open.feishu.cn/open-apis"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# tenant_access_token 内存缓存：{"token": str, "expire_at": float}
_token_cache: dict = {}


def _parse_records(payload: dict) -> tuple[list[dict], str | None]:
    """从飞书响应体提取 (records, next_page_token)。code!=0 抛错。纯函数。"""
    code = payload.get("code", -1)
    if code != 0:
        raise RuntimeError(
            f"飞书 API 报错 code={code}: {payload.get('msg', '')}")
    data = payload.get("data") or {}
    items = data.get("items") or []
    page_token = data.get("page_token") if data.get("has_more") else None
    return items, page_token


async def get_tenant_access_token() -> str:
    """换取 tenant_access_token，带内存缓存（有效期约 2 小时，提前 5 分钟过期）。"""
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("expire_at", 0) > now:
        return _token_cache["token"]
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("飞书 app_id/app_secret 未配置")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id,
                  "app_secret": settings.feishu_app_secret})
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书取 token 失败: {data.get('msg')}")
    token = data["tenant_access_token"]
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + data.get("expire", 7200) - 300
    return token


async def list_bitable_records(app_token: str, table_id: str,
                               page_size: int = 500) -> list[dict]:
    """读多维表格全部记录（自动翻页）。返回原始 record 列表。"""
    token = await get_tenant_access_token()
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}
    all_records: list[dict] = []
    page_token: str | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            records, page_token = _parse_records(resp.json())
            all_records.extend(records)
            if not page_token:
                break
    return all_records


def is_configured() -> bool:
    return bool(settings.feishu_app_id and settings.feishu_app_secret)
