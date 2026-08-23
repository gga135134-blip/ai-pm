"""视频批量反向入库 路由：去重 + 校验（TestClient）。"""
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
    c.cookies.set("media_persona", "RP")     # 固定当前人设
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rev_batch_route_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())

    async def seed():
        db = await get_db()
        try:
            await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                             "VALUES ('RP','人设','一句话','涨粉','active')")
            # 已入库一条 idea_reason=urlA 的 video_reverse 内容
            await db.execute(
                "INSERT INTO media_content (id,persona_id,title,stage,idea_source,idea_reason) "
                "VALUES ('RC1','RP','旧','published','video_reverse','https://v.douyin.com/A/')")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(seed())
    yield
    _db_mod.DB_PATH = orig


def _creds(monkeypatch):
    monkeypatch.setattr(media_api, "_load_config", lambda: {"douyin_asr": {"api_key": "k"}})


def test_batch_dedup_and_start(monkeypatch):
    _creds(monkeypatch)
    captured = {}
    def spy_start(pid, urls, cfg, public_base, audio_dir, cookies_path=None):
        captured["pid"] = pid; captured["urls"] = list(urls); return True
    monkeypatch.setattr(media_api, "start_reverse_batch", spy_start)
    r = _client().post("/media/reverse/batch",
                       data={"urls": "https://v.douyin.com/A/\nhttps://v.douyin.com/B/"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["started"] is True and d["skipped"] == 1 and d["queued"] == 1
    assert captured["pid"] == "RP"
    assert captured["urls"] == ["https://v.douyin.com/B/"]   # A 已入库被跳过


def test_batch_all_duplicate_not_started(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setattr(media_api, "start_reverse_batch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该起任务")))
    r = _client().post("/media/reverse/batch", data={"urls": "https://v.douyin.com/A/"})
    d = r.json()
    assert d["ok"] and d["started"] is False and d["skipped"] == 1 and d["queued"] == 0


def test_batch_no_valid_url_error(monkeypatch):
    _creds(monkeypatch)
    r = _client().post("/media/reverse/batch", data={"urls": "就是文字\n没有链接"})
    assert r.json()["ok"] is False


def test_batch_no_creds_error(monkeypatch):
    monkeypatch.setattr(media_api, "_load_config", lambda: {})
    r = _client().post("/media/reverse/batch", data={"urls": "https://v.douyin.com/B/"})
    assert r.json()["ok"] is False


def test_batch_status_shape():
    d = _client().get("/media/reverse/batch-status").json()
    assert "running" in d and "results" in d
