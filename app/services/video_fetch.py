"""链接→音频：用 yt-dlp 抽已发视频的音频。脆弱环节隔离在此，可换解析源。"""
import asyncio
import logging
import re
import sys
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_TIMEOUT = 120  # 秒，防挂起
_URL_RE = re.compile(r"https?://[^\s]+")
_DATE_RE = re.compile(r"UPLOADDATE:(\d{8})")


class VideoFetchError(Exception):
    """拿不到视频音频（平台防爬、链接失效、yt-dlp 出错）。"""


def first_url(text: str) -> str:
    """从粘贴的分享文案里抠出第一个 http(s) 链接。

    抖音复制出来是一大段文案（如 "2.38 复制打开抖音…【…】 https://v.douyin.com/xxx/ :1pm…"），
    这里正则提第一个链接（到空白为止，自然甩掉后面的防爬乱码）。找不到就原样返回去空白。
    """
    if not text:
        return ""
    m = _URL_RE.search(text)
    return m.group(0) if m else text.strip()


async def fetch_audio(url: str, out_dir: Path, cookies_path: Path = None) -> tuple[Path, str | None]:
    """用 yt-dlp 把 url 的音频抽成 mp3 到 out_dir/<uuid>.mp3，返回 (路径, 发布日期)。

    返回 (音频路径, 发布日期字符串或 None)。发布日期为 'YYYY-MM-DD' 格式或 None（无法获取时）。
    失败（yt-dlp 非零退出 / 超时 / 没产出文件）抛 VideoFetchError。
    cookies_path：可选 Netscape 格式 cookie 文件（抖音等防爬站需要），存在才带上。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = str(uuid.uuid4())
    target = out_dir / f"{name}.mp3"
    # 用当前 venv 的 python 以模块方式调 yt_dlp（sys.executable -m yt_dlp），
    # 绕开 systemd PATH 不含 venv/bin 导致裸命令 yt-dlp 找不到的坑。
    # -x 抽音频，--audio-format mp3，-o 固定输出名（%(ext)s 会被替换成 mp3）
    args = [sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "mp3",
            "--no-playlist", "--no-simulate", "--print", "UPLOADDATE:%(upload_date)s",
            "-o", str(out_dir / f"{name}.%(ext)s")]
    if cookies_path and Path(cookies_path).exists():
        args += ["--cookies", str(cookies_path)]
    args.append(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
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
        if b"cookies" in err_lower:
            raise VideoFetchError(
                "抖音需要 cookie：导出浏览器 cookie 存到服务器 data/douyin_cookies.txt")
        raise VideoFetchError("拿不到视频音频（平台可能防爬或链接失效）")
    if not target.exists():
        raise VideoFetchError("拿不到视频音频（未生成音频文件）")
    upload_date = None
    m = _DATE_RE.search((out or b"").decode("utf-8", "ignore"))
    if m:
        d = m.group(1)  # YYYYMMDD
        upload_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return target, upload_date
