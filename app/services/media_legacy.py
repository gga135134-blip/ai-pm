"""老文案批量入库：按序号切分 → 建成 media_content(已发/反向-legacy_text)。"""
import re
import uuid

_NUM_LINE = re.compile(r'^\s*\d+\s*[.、)）]\s*')


def split_legacy_scripts(text: str) -> list:
    """带序号的 TXT/文本切成多条。序号行(1. / 2、 / 3) / 4）)为界；
    无序号回落空行分隔。剥掉每段开头的序号前缀。空段忽略。"""
    raw = (text or "").replace("\r\n", "\n")
    lines = raw.split("\n")
    idxs = [i for i, ln in enumerate(lines) if _NUM_LINE.match(ln)]
    if len(idxs) >= 2:
        segs = []
        for j, start in enumerate(idxs):
            end = idxs[j + 1] if j + 1 < len(idxs) else len(lines)
            block = "\n".join(lines[start:end]).strip()
            block = _NUM_LINE.sub("", block, count=1).strip()
            if block:
                segs.append(block)
        return segs
    return [b.strip() for b in re.split(r'\n\s*\n', raw) if b.strip()]


async def create_legacy_contents(db, persona_id: str, segments: list) -> int:
    n = 0
    for seg in segments:
        seg = (seg or "").strip()
        if not seg:
            continue
        title = (seg.split("\n")[0][:40]) or "老文案"
        await db.execute(
            "INSERT INTO media_content (id,persona_id,title,stage,idea_source,script) "
            "VALUES (?,?,?, 'published','legacy_text',?)",
            (str(uuid.uuid4()), persona_id, title, seg))
        n += 1
    await db.commit()
    return n
