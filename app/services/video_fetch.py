"""链接→音频：用 yt-dlp 抽已发视频的音频。脆弱环节隔离在此，可换解析源。"""
import asyncio
import logging
import sys
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_TIMEOUT = 120  # 秒，防挂起


class VideoFetchError(Exception):
    """拿不到视频音频（平台防爬、链接失效、yt-dlp 出错）。"""


async def fetch_audio(url: str, out_dir: Path) -> Path:
    """用 yt-dlp 把 url 的音频抽成 mp3 到 out_dir/<uuid>.mp3，返回路径。

    失败（yt-dlp 非零退出 / 超时 / 没产出文件）抛 VideoFetchError。
    一期不带 cookie，尽力而为。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = str(uuid.uuid4())
    target = out_dir / f"{name}.mp3"
    # 用当前 venv 的 python 以模块方式调 yt_dlp（sys.executable -m yt_dlp），
    # 绕开 systemd PATH 不含 venv/bin 导致裸命令 yt-dlp 找不到的坑。
    # -x 抽音频，--audio-format mp3，-o 固定输出名（%(ext)s 会被替换成 mp3）
    args = [sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "mp3",
            "--no-playlist", "-o", str(out_dir / f"{name}.%(ext)s"), url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise VideoFetchError("拿不到视频音频（下载超时）")
    except FileNotFoundError:
        raise VideoFetchError("服务器 python 环境异常（找不到解释器）")
    if proc.returncode != 0:
        log.warning("yt-dlp 失败 rc=%s err=%s", proc.returncode, (err or b"")[:500])
        err_lower = (err or b"").lower()
        if b"no module named" in err_lower and b"yt_dlp" in err_lower:
            raise VideoFetchError("服务器未安装 yt-dlp（在 ai-pm 的 venv 里 pip install yt-dlp）")
        if b"ffmpeg" in err_lower or b"ffprobe" in err_lower:
            raise VideoFetchError("服务器缺 ffmpeg（yt-dlp 抽音频需要它，请安装 ffmpeg）")
        raise VideoFetchError("拿不到视频音频（平台可能防爬或链接失效）")
    if not target.exists():
        raise VideoFetchError("拿不到视频音频（未生成音频文件）")
    return target
