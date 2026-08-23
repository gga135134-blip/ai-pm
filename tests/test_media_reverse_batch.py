"""视频批量反向入库：parse_urls 纯函数 + 后台跑器。"""
import asyncio
from pathlib import Path

import pytest

from app.database import init_db
import app.database as _db_mod
import app.services.media_reverse_batch as mrb


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rev_batch_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


def test_parse_urls_extracts_dedups_drops_nonurl():
    text = ("https://v.douyin.com/a/\n"
            "看这个 https://v.douyin.com/b/ 不错\n"
            "\n"
            "https://v.douyin.com/a/\n"          # 重复
            "没有链接的一行")                      # first_url 原样返回，非 http 丢弃
    assert mrb.parse_urls(text) == ["https://v.douyin.com/a/", "https://v.douyin.com/b/"]


def test_parse_urls_caps_at_10():
    text = "\n".join(f"https://v.douyin.com/{i}/" for i in range(15))
    assert len(mrb.parse_urls(text, cap=10)) == 10


def test_parse_urls_empty_or_no_url():
    assert mrb.parse_urls("") == []
    assert mrb.parse_urls("就是文字\n没有链接") == []


def test_runner_processes_all_and_continues_on_failure(monkeypatch):
    async def go():
        async def fake_ingest(db, pid, url, cfg, public_base, audio_dir, cookies_path=None):
            if url == "u2":
                raise RuntimeError("boom")     # 中间一条炸
            return {"ok": True, "content_id": "c", "title": "T-" + url, "error": ""}
        monkeypatch.setattr(mrb, "reverse_ingest", fake_ingest)
        assert mrb.start_reverse_batch("RB1", ["u1", "u2", "u3"], {}, "http://x", Path(".")) is True
        for _ in range(100):
            if not mrb.get_reverse_status("RB1")["running"]:
                break
            await asyncio.sleep(0.02)
        st = mrb.get_reverse_status("RB1")
        assert st["running"] is False and st["done"] == 3
        assert [r["ok"] for r in st["results"]] == [True, False, True]   # 失败不中断
        assert st["results"][0]["title"] == "T-u1"
    asyncio.run(go())


def test_start_rejected_when_already_running(monkeypatch):
    async def go():
        async def slow_ingest(db, pid, url, cfg, public_base, audio_dir, cookies_path=None):
            await asyncio.sleep(0.1)
            return {"ok": True, "title": "x", "error": ""}
        monkeypatch.setattr(mrb, "reverse_ingest", slow_ingest)
        assert mrb.start_reverse_batch("RB2", ["a"], {}, "http://x", Path(".")) is True
        assert mrb.start_reverse_batch("RB2", ["b"], {}, "http://x", Path(".")) is False  # 已在跑
        for _ in range(100):
            if not mrb.get_reverse_status("RB2")["running"]:
                break
            await asyncio.sleep(0.02)
    asyncio.run(go())


def test_status_empty_when_no_job():
    st = mrb.get_reverse_status("NOJOB")
    assert st["running"] is False and st["total"] == 0 and st["results"] == []
