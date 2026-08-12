"""豆包 ASR 凭证保存路由测试。CONFIG_FILE 隔离到 tmp，不碰真配置。"""
import base64
import json

from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_or_create_session_secret
import app.api.settings as st


def _client():
    signer = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", signer.sign(
        base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


def test_load_settings_has_asr_default(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CONFIG_FILE", tmp_path / "s.json")
    cfg = st.load_settings()
    assert "douyin_asr" in cfg and cfg["douyin_asr"]["app_id"] == ""


def test_save_asr_creds_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CONFIG_FILE", tmp_path / "s.json")
    _client().post("/settings/asr", data={
        "douyin_asr_api_key": "APIKEY123",
        "douyin_asr_app_id": "APP123", "douyin_asr_access_key": "AK123",
        "douyin_asr_resource_id": "volc.bigasr.auc",
        "douyin_asr_public_base": "http://159.75.200.213:8000"}, follow_redirects=False)
    cfg = st.load_settings()
    assert cfg["douyin_asr"]["api_key"] == "APIKEY123"
    assert cfg["douyin_asr"]["app_id"] == "APP123"
    assert cfg["douyin_asr"]["access_key"] == "AK123"
    assert cfg["douyin_asr"]["public_base"] == "http://159.75.200.213:8000"
