"""老文案切分 + 入库。"""
import asyncio
from tests.media_helpers import make_db, seed_content
from app.services import media_legacy as ml


def test_split_by_serial_numbers():
    txt = "1. 第一条内容\n讲了个坑\n\n2、第二条\n另一个故事\n3) 第三条"
    segs = ml.split_legacy_scripts(txt)
    assert len(segs) == 3
    assert segs[0].startswith("第一条内容")     # 序号前缀被剥掉
    assert segs[1].startswith("第二条")


def test_split_fallback_blank_line():
    segs = ml.split_legacy_scripts("第一段没序号\n\n第二段\n\n第三段")
    assert len(segs) == 3


def test_split_empty():
    assert ml.split_legacy_scripts("") == []
    assert ml.split_legacy_scripts("   \n  ") == []


def test_create_legacy_contents():
    async def go():
        db = await make_db()
        try:
            await seed_content(db, persona_id="P1", content_id="seed", stage="idea")
            n = await ml.create_legacy_contents(db, "P1", ["文案甲\n正文", "文案乙"])
            assert n == 2
            cur = await db.execute(
                "SELECT title,stage,idea_source,script,is_winner FROM media_content "
                "WHERE persona_id='P1' AND idea_source='legacy_text'")
            rows = [dict(r) for r in await cur.fetchall()]
            assert len(rows) == 2
            by_title = {r["title"]: r for r in rows}
            jia = by_title["文案甲"]
            assert jia["stage"] == "published" and jia["is_winner"] == 0
            assert jia["script"].startswith("文案甲")
        finally:
            await db.close()
    asyncio.run(go())
