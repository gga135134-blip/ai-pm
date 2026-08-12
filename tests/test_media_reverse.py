"""编排器单测：桩掉 fetch/asr/extract，隔离到临时 DB。"""
import asyncio
from pathlib import Path

import pytest

from app.database import get_db, init_db
import app.database as _db_mod
from app.services import media_reverse
from app.services.media_reverse import reverse_ingest
from app.services.video_fetch import VideoFetchError
from app.services.asr_client import ASRError


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reverse_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def _seed_persona(pid="REVP", with_account=True):
    async def go():
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_persona WHERE id=?", (pid,))
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES (?,?,?,?)", (pid, "测试", "一句话", "涨粉"))
            if with_account:
                await db.execute(
                    "INSERT INTO media_account (id,persona_id,platform,account_name) "
                    "VALUES (?,?,?,?)", ("ACC_"+pid, pid, "douyin", "测试号"))
            await db.commit()
        finally:
            await db.close()
    asyncio.run(go())


def _patch(monkeypatch, audio_ok=True, asr_text="老板买AI工具用不起来", extract=None,
           upload_date="2024-10-21"):
    async def fake_fetch(url, out_dir, cookies_path=None):
        if not audio_ok:
            raise VideoFetchError("拿不到")
        p = Path(out_dir) / "a.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p, upload_date
    async def fake_asr(audio_url, cfg):
        if asr_text is None:
            raise ASRError("转写失败")
        return asr_text
    async def fake_extract(transcript, model="auto"):
        return extract if extract is not None else {
            "title": "老板买AI工具用不起来", "puzzle": "卡在哪", "topic_fingerprint": "AI落地",
            "_cost": 0, "_tokens": 10}
    monkeypatch.setattr(media_reverse, "fetch_audio", fake_fetch)
    monkeypatch.setattr(media_reverse, "transcribe_url", fake_asr)
    monkeypatch.setattr(media_reverse, "extract_from_transcript", fake_extract)


def _run(pid, tmp_path):
    async def go():
        db = await get_db()
        try:
            return await reverse_ingest(
                db, pid, "https://v.douyin.com/x/", cfg={"app_id": "A", "access_key": "K"},
                public_base="http://pub", audio_dir=tmp_path / "pub")
        finally:
            await db.close()
    return asyncio.run(go())


def _content_rows(pid):
    async def go():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT stage,idea_source,title,script,topic_fingerprint,published_at "
                "FROM media_content WHERE persona_id=?", (pid,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()
    return asyncio.run(go())


def test_success_creates_published_content_and_publish(tmp_path, monkeypatch):
    _seed_persona("REVP1")
    _patch(monkeypatch)
    res = _run("REVP1", tmp_path)
    assert res["ok"] and res["content_id"]
    rows = _content_rows("REVP1")
    assert len(rows) == 1
    assert rows[0]["stage"] == "published"
    assert rows[0]["idea_source"] == "video_reverse"
    assert rows[0]["script"] == "老板买AI工具用不起来"
    assert rows[0]["topic_fingerprint"] == "AI落地"
    assert rows[0]["published_at"] == "2024-10-21"


def test_no_upload_date_leaves_published_at_null(tmp_path, monkeypatch):
    _seed_persona("REVP7")
    _patch(monkeypatch, upload_date=None)
    _run("REVP7", tmp_path)
    rows = _content_rows("REVP7")
    assert rows[0]["published_at"] is None


def test_audio_dir_cleaned_after_success(tmp_path, monkeypatch):
    _seed_persona("REVP2")
    _patch(monkeypatch)
    _run("REVP2", tmp_path)
    # 公开音频目录里不应残留文件
    pub = tmp_path / "pub"
    assert not any(pub.glob("*.mp3")) if pub.exists() else True


def test_fetch_fail_creates_no_row(tmp_path, monkeypatch):
    _seed_persona("REVP3")
    _patch(monkeypatch, audio_ok=False)
    res = _run("REVP3", tmp_path)
    assert not res["ok"] and res["error"]
    assert _content_rows("REVP3") == []


def test_asr_fail_creates_no_row(tmp_path, monkeypatch):
    _seed_persona("REVP4")
    _patch(monkeypatch, asr_text=None)
    res = _run("REVP4", tmp_path)
    assert not res["ok"]
    assert _content_rows("REVP4") == []


def test_extract_fail_creates_fallback_row(tmp_path, monkeypatch):
    _seed_persona("REVP5")
    _patch(monkeypatch, extract={"_cost": 0, "_tokens": 5})  # 无 title = 提取失败
    res = _run("REVP5", tmp_path)
    assert res["ok"] and res["content_id"]
    rows = _content_rows("REVP5")
    assert len(rows) == 1
    assert rows[0]["title"] == "https://v.douyin.com/x/"   # 链接兜底
    assert rows[0]["script"] == "老板买AI工具用不起来"       # 稿不丢


def test_no_account_creates_content_without_publish(tmp_path, monkeypatch):
    _seed_persona("REVP6", with_account=False)
    _patch(monkeypatch)
    res = _run("REVP6", tmp_path)
    assert res["ok"] and res["content_id"]

    async def pub_count():
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) c FROM media_publish p JOIN media_content c "
                "ON p.content_id=c.id WHERE c.persona_id='REVP6'")
            return (await cur.fetchone())["c"]
        finally:
            await db.close()
    assert asyncio.run(pub_count()) == 0   # 没号 → 不建 publish
