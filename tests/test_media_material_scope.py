"""原料库 scope 接口：默认隔离，shared 跨人设可见。"""
import asyncio
from tests.media_helpers import make_db


def test_scope_column_and_shared_visibility():
    async def go():
        db = await make_db()
        try:
            cur = await db.execute("PRAGMA table_info(media_material)")
            assert "scope" in {r["name"] for r in await cur.fetchall()}   # 列存在
            for pid in ("MA", "MB"):
                await db.execute("INSERT INTO media_persona (id,name,current_phase,status) "
                                 "VALUES (?,?, '涨粉','active')", (pid, pid))
            # MA 一条私有、一条公司级
            await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status,scope) "
                             "VALUES ('m1','MA','story','私料','x','active','persona')")
            await db.execute("INSERT INTO media_material (id,persona_id,type,title,detail,status,scope) "
                             "VALUES ('m2','MA','story','公司料','x','active','shared')")
            await db.commit()
            # MB 视角：私有条款 (persona_id=MB OR scope=shared) → 只看到公司料
            cur = await db.execute(
                "SELECT title FROM media_material WHERE (persona_id=? OR scope='shared') AND status='active'",
                ("MB",))
            titles = {r["title"] for r in await cur.fetchall()}
            assert "公司料" in titles and "私料" not in titles
        finally:
            await db.close()
    asyncio.run(go())
