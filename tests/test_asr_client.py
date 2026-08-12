"""asr_client 单测：桩掉 httpx，不真调豆包。"""
import asyncio

import pytest

from app.services import asr_client
from app.services.asr_client import transcribe_url, ASRError

_CFG = {"app_id": "APPID", "access_key": "AK", "resource_id": "volc.bigasr.auc"}


class _Resp:
    def __init__(self, status_code=200, headers=None, json_body=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_body or {}

    def json(self):
        return self._json


class _FakeClient:
    """按预设脚本依次返回响应。submit 一次 + query 若干次。"""
    def __init__(self, script):
        self._script = list(script)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        return self._script.pop(0)


def _patch(monkeypatch, script):
    async def _mock_sleep(*a, **k):
        pass

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda *a, **k: _FakeClient(script))
    monkeypatch.setattr(asr_client.asyncio, "sleep", _mock_sleep)


def test_transcribe_success_after_polling(monkeypatch):
    script = [
        _Resp(headers={"X-Api-Status-Code": "20000000"}),                       # submit 受理
        _Resp(headers={"X-Api-Status-Code": "20000001"}),                       # query 处理中
        _Resp(headers={"X-Api-Status-Code": "20000000"},                        # query 完成
              json_body={"result": {"text": "老板买了一堆AI工具还是用不起来"}}),
    ]
    _patch(monkeypatch, script)
    text = asyncio.run(transcribe_url("http://pub/aud.mp3", _CFG))
    assert "AI工具" in text


def test_transcribe_submit_rejected_raises(monkeypatch):
    _patch(monkeypatch, [_Resp(status_code=403, headers={"X-Api-Status-Code": "40000"})])
    with pytest.raises(ASRError):
        asyncio.run(transcribe_url("http://pub/aud.mp3", _CFG))


def test_transcribe_timeout_raises(monkeypatch):
    # submit ok，之后一直处理中 → 触发轮询上限
    script = [_Resp(headers={"X-Api-Status-Code": "20000000"})] + \
             [_Resp(headers={"X-Api-Status-Code": "20000001"})] * 200
    _patch(monkeypatch, script)
    monkeypatch.setattr(asr_client, "_MAX_POLLS", 3)
    with pytest.raises(ASRError):
        asyncio.run(transcribe_url("http://pub/aud.mp3", _CFG))


def test_transcribe_missing_creds_raises(monkeypatch):
    with pytest.raises(ASRError):
        asyncio.run(transcribe_url("http://pub/aud.mp3", {"app_id": "", "access_key": ""}))
