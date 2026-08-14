"""ai-script 路由透传 playbook_id。"""
import asyncio
import base64
import json
import pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
import app.database as _db_mod
import app.api.media as media_api


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("aiscript_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_route_passes_playbook_id(monkeypatch):
    captured = {}
    async def fake_write(db, cid, mode="full", model="auto", hint="", playbook_id=""):
        captured["playbook_id"] = playbook_id
        captured["cid"] = cid
        return {"ok": True, "script": "s", "playbook": None}
    monkeypatch.setattr(media_api, "write_script", fake_write)
    r = _client().post("/media/content/XX/ai-script",
                       data={"mode": "full", "playbook_id": "PB9"})
    assert r.status_code == 200
    assert captured["playbook_id"] == "PB9" and captured["cid"] == "XX"


def test_route_default_empty(monkeypatch):
    captured = {}
    async def fake_write(db, cid, mode="full", model="auto", hint="", playbook_id=""):
        captured["playbook_id"] = playbook_id
        return {"ok": True, "script": "s", "playbook": None}
    monkeypatch.setattr(media_api, "write_script", fake_write)
    r = _client().post("/media/content/XX/ai-script", data={"mode": "full"})
    assert r.status_code == 200 and captured["playbook_id"] == ""
