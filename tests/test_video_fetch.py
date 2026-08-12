"""video_fetch 单测：不真跑网络，桩掉 subprocess。"""
import asyncio
from pathlib import Path

import pytest

from app.services import video_fetch
from app.services.video_fetch import fetch_audio, VideoFetchError


class _FakeProc:
    def __init__(self, returncode, made_file=None, stderr=b"err"):
        self.returncode = returncode
        self._made = made_file
        self._stderr = stderr

    async def communicate(self):
        if self._made:
            self._made.write_bytes(b"fake-audio")
        return (b"out", self._stderr)


def test_fetch_audio_success(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        # yt-dlp 输出模板决定文件名；模拟它产出一个 mp3
        out = tmp_path / "aud.mp3"
        return _FakeProc(0, made_file=out)

    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    # 让 fetch_audio 用固定文件名，便于断言（见实现：out_dir/<uuid>.mp3）
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    p = asyncio.run(fetch_audio("https://v.douyin.com/xxx/", tmp_path))
    assert p.exists() and p.suffix == ".mp3"


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


def test_fetch_audio_no_output_file_raises(tmp_path, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(0)  # 退出 0 但没产文件
    monkeypatch.setattr(video_fetch.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(video_fetch.uuid, "uuid4", lambda: "aud")
    with pytest.raises(VideoFetchError):
        asyncio.run(fetch_audio("https://x/", tmp_path))
