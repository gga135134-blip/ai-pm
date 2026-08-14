"""打法库共享：跨人设一池 + similar_to 跨池归并。"""
import asyncio
from tests.media_helpers import make_db
from app.services.media_playbook import list_playbooks


def test_list_playbooks_is_global():
    async def go():
        db = await make_db()
        try:
            for pid in ("SPA", "SPB"):
                await db.execute("INSERT INTO media_persona (id,name,current_phase,status) "
                                 "VALUES (?,?, '涨粉','active')", (pid, pid))
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('P1','SPA','痛点法','proven')")
            await db.execute("INSERT INTO media_playbook (id,persona_id,name,status) "
                             "VALUES ('P2','SPB','悬念法','validating')")
            await db.commit()
            rows = await list_playbooks(db)          # 不传 persona
            names = [r["name"] for r in rows]
            assert "痛点法" in names and "悬念法" in names   # 两人设的都在
            assert names[0] == "痛点法"                     # proven 在前
        finally:
            await db.close()
    asyncio.run(go())
