"""video_fetch 单测：不真跑网络，桩掉 subprocess。"""
import asyncio
from pathlib import Path

import pytest

from app.services import video_fetch
from app.services.video_fetch import fetch_audio, VideoFetchError, first_url


def test_first_url_extracts_from_douyin_share_blurb():
    blurb = ("2.38 复制打开抖音，看看【自由职业架构师｜嘉姐的作品】建立个人DNA库 "
             "https://v.douyin.com/zcaUD-q2YMo/ :1pm 10/21 FHi:/ s@e.BG")
    assert first_url(blurb) == "https://v.douyin.com/zcaUD-q2YMo/"


def test_first_url_passthrough_and_empty():
    assert first_url("https://v.douyin.com/abc/") == "https://v.douyin.com/abc/"
    assert first_url("  没有链接的文本  ") == "没有链接的文本"
    assert first_url("") == ""


class _FakeProc:
    def __init__(self, returncode, made_file=None, stderr=b"err", stdout=b"out"):
        self.returncode = returncode
        self._made = made_file
        self._stderr = stderr
        self._stdout = stdout

    async def communicate(self):
        if self._made:
            self._made.write_bytes(b"fake-audio")
        return (self._stdout, self._stderr)


def test_fetch_audio_success(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        # yt-dlp 输出模板决定文件名；模拟它产出一个 mp3
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out, stdout=b"UPLOADDATE:20241021\n")

    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    # 让 fetch_audio 用固定文件名，便于断言（见实现：out_dir/<uuid>.mp3）
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    p, upload_date = asyncio.run(fetch_audio("https://v.douyin.com/xxx/", tmp_path))
    assert p.exists() and p.suffix == ".mp3"
    assert upload_date == "2024-10-21"


def test_fetch_audio_no_upload_date_returns_none(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out, stdout=b"no date here")
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    p, upload_date = asyncio.run(fetch_audio("https://x/", tmp_path))
    assert p.exists() and upload_date is None


def test_fetch_audio_nonzero_raises(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(1)
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError):
        asyncio.run(fetch_audio("https://bad/", tmp_path))


def test_fetch_audio_ffmpeg_missing_raises_distinct_error(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(1, stderr=b"ERROR: ffmpeg not found in PATH")
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError, match="ffmpeg"):
        asyncio.run(fetch_audio("https://bad/", tmp_path))


def test_fetch_audio_ytdlp_missing_raises_distinct_error(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(1, stderr=b"/usr/bin/python: No module named yt_dlp")
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError, match="yt-dlp"):
        asyncio.run(fetch_audio("https://x/", tmp_path))


def test_fetch_audio_adds_cookies_when_file_exists(tmp_path, monkeypatch):
    cookies = tmp_path / "c.txt"
    cookies.write_text("# netscape cookies")
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out)

    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    asyncio.run(fetch_audio("https://v.douyin.com/x/", tmp_path, cookies))
    assert "--cookies" in seen["args"] and str(cookies) in seen["args"]


def test_fetch_audio_no_cookies_when_path_missing(tmp_path, monkeypatch):
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out)

    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    asyncio.run(fetch_audio("https://x/", tmp_path, tmp_path / "nope.txt"))
    assert "--cookies" not in seen["args"]


def test_fetch_audio_no_output_file_raises(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(0)  # 退出 0 但没产文件
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError):
        asyncio.run(fetch_audio("https://x/", tmp_path))
