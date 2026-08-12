"""功能C 编排器：链接→音频→ASR→提选题→建 content。串起脆弱外部依赖。"""
import logging
import uuid
from pathlib import Path

from app.services.video_fetch import fetch_audio, VideoFetchError
from app.services.asr_client import transcribe_url, ASRError
from app.services.media_ai import extract_from_transcript
from app.services.media_context import log_injection

log = logging.getLogger(__name__)


async def reverse_ingest(db, persona_id: str, video_url: str, cfg: dict,
                         public_base: str, audio_dir: Path,
                         model: str = "auto") -> dict:
    """串 ①抽音频 ②托管 ③ASR ④提选题 ⑤建content+publish ⑥清理。

    cfg=豆包凭证；public_base=对外可达前缀；audio_dir=公开音频目录。
    fetch/asr 硬失败不建行；extract 失败建 fallback 行（稿不丢）。
    返回 {ok, content_id, title, error}。
    """
    audio_dir = Path(audio_dir)
    audio_file = None
    try:
        # ① 抽音频到公开目录（文件名即随机 token）
        try:
            audio_file = await fetch_audio(video_url, audio_dir)
        except VideoFetchError as e:
            return {"ok": False, "content_id": "", "title": "", "error": str(e)}

        token = audio_file.name  # <uuid>.mp3
        public_url = f"{public_base.rstrip('/')}/media/asr-audio/{token}"

        # ③ ASR
        try:
            transcript = await transcribe_url(public_url, cfg)
        except ASRError as e:
            return {"ok": False, "content_id": "", "title": "", "error": str(e)}
        if not transcript.strip():
            return {"ok": False, "content_id": "", "title": "", "error": "转写结果为空"}

        # ④ 提选题（失败兜底）
        ext = await extract_from_transcript(transcript, model=model)
        if ext.get("_tokens"):
            await log_injection(db, "", "extract_from_transcript", [], ext["_tokens"])
        title = ext.get("title") or video_url          # 兜底用链接
        puzzle = ext.get("puzzle", "")
        fingerprint = ext.get("topic_fingerprint", "")

        # ⑤ 建 content（已发）
        content_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO media_content "
            "(id,persona_id,title,puzzle,stage,idea_source,idea_reason,script,"
            " topic_fingerprint) VALUES (?,?,?,?,'published','video_reverse',?,?,?)",
            (content_id, persona_id, title, puzzle, video_url, transcript, fingerprint))

        # 挂 publish 到该人设第一个 account；没号则只建 content
        cur = await db.execute(
            "SELECT id FROM media_account WHERE persona_id=? ORDER BY created_at LIMIT 1",
            (persona_id,))
        acc = await cur.fetchone()
        if acc:
            await db.execute(
                "INSERT INTO media_publish "
                "(id,content_id,account_id,post_url,published_at,status) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP,'published')",
                (str(uuid.uuid4()), content_id, acc["id"], video_url))
        await db.commit()
        return {"ok": True, "content_id": content_id, "title": title, "error": ""}
    finally:
        # ⑥ 清理临时音频（成败都删）
        try:
            if audio_file and Path(audio_file).exists():
                Path(audio_file).unlink()
        except OSError:
            log.warning("清理临时音频失败：%s", audio_file)
