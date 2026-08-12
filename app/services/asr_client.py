"""豆包/火山「录音文件识别大模型」(bigmodel, 投 URL 异步) 适配器。

接口稳定：transcribe_url(audio_url, cfg) -> 全文。换 ASR 只动此文件。
凭证/base_url 全从 cfg（settings.json），不硬编码。
"""
import asyncio
import logging
import uuid

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
_MAX_POLLS = 60          # 轮询上限次
_POLL_INTERVAL = 3       # 秒
_STATUS_DONE = "20000000"
_STATUS_PROCESSING = {"20000001", "20000002"}


class ASRError(Exception):
    """转写失败或超时。"""


def _headers(cfg: dict, request_id: str) -> dict:
    """按配置选鉴权方式：填了 api_key 走新版单串 X-Api-Key，否则老式 app_id+access_key。"""
    h = {
        "X-Api-Resource-Id": cfg.get("resource_id") or "volc.bigasr.auc",
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
        "Content-Type": "application/json",
    }
    if cfg.get("api_key"):
        h["X-Api-Key"] = cfg["api_key"]          # 新版豆包语音「API Key」单串鉴权
    else:
        h["X-Api-App-Key"] = cfg.get("app_id", "")
        h["X-Api-Access-Key"] = cfg.get("access_key", "")
    return h


async def transcribe_url(audio_url: str, cfg: dict) -> str:
    """提交音频 URL → 轮询 → 返回转写全文。失败/超时抛 ASRError。"""
    if not cfg.get("api_key") and not (cfg.get("app_id") and cfg.get("access_key")):
        raise ASRError("未配置豆包 ASR 凭证")
    base = (cfg.get("base_url") or _DEFAULT_BASE).rstrip("/")
    request_id = str(uuid.uuid4())
    submit_body = {
        "user": {"uid": "ai-pm"},
        "audio": {"url": audio_url, "format": "mp3"},
        "request": {
            "model_name": "bigmodel",
            "model_version": "400",
            "enable_itn": True,
            "enable_punc": True,
            "show_utterances": True,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base}/submit", headers=_headers(cfg, request_id),
                              json=submit_body)
        if r.headers.get("X-Api-Status-Code") != _STATUS_DONE:
            code = r.headers.get("X-Api-Status-Code")
            msg = r.headers.get("X-Api-Message") or (r.text or "")[:200]
            log.warning("ASR submit 失败 code=%s msg=%s", code, msg)
            raise ASRError(f"提交转写任务失败（{code}：{msg}）")

        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            q = await client.post(f"{base}/query", headers=_headers(cfg, request_id),
                                  json={})
            status = q.headers.get("X-Api-Status-Code", "")
            if status == _STATUS_DONE:
                return _extract_text(q.json())
            if status in _STATUS_PROCESSING:
                continue
            msg = q.headers.get("X-Api-Message") or (q.text or "")[:200]
            log.warning("ASR query 失败 code=%s msg=%s", status, msg)
            raise ASRError(f"转写失败（{status}：{msg}）")
    raise ASRError("转写超时")


def _extract_text(body: dict) -> str:
    """从 query 响应体拿全文。兼容 result.text / result.utterances 拼接。"""
    result = body.get("result") or {}
    if isinstance(result, dict):
        if result.get("text"):
            return str(result["text"]).strip()
        utts = result.get("utterances") or []
        if utts:
            return "".join(u.get("text", "") for u in utts if isinstance(u, dict)).strip()
    return ""
