"""media 打标 helper 的纯函数/异步测试。"""
from app.services.media_ai import _clean_ids


def test_clean_ids_keeps_valid_drops_bogus():
    valid = {"A", "B", "C"}
    assert _clean_ids(["A", "X", "B"], valid) == ["A", "B"]


def test_clean_ids_dedup_and_order():
    assert _clean_ids(["B", "B", "A"], {"A", "B"}) == ["B", "A"]


def test_clean_ids_non_list_returns_empty():
    assert _clean_ids(None, {"A"}) == []
    assert _clean_ids("A", {"A"}) == []      # 字符串不是 list
    assert _clean_ids([1, 2, {"x": 1}], {"A"}) == []   # 非字符串元素丢弃


def test_build_asset_menu_lists_assets_and_valid_ids():
    import asyncio
    from app.database import get_db, init_db
    from app.services.media_ai import _build_asset_menu

    async def run():
        await init_db()
        db = await get_db()
        try:
            await db.execute("DELETE FROM media_anchor WHERE persona_id='MENUP'")
            await db.execute("DELETE FROM media_audience WHERE persona_id='MENUP'")
            await db.execute("DELETE FROM media_persona WHERE id='MENUP'")
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('MENUP','测试人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_audience (id,persona_id,segment,anxiety,language,"
                "pay_willingness,status) VALUES "
                "('AUD1','MENUP','焦虑老板','落地难','能落地不',5,'active')")
            await db.execute(
                "INSERT INTO media_anchor (id,persona_id,name,value_prop,status) VALUES "
                "('ANC1','MENUP','训练营','教你落地','proven'),"
                "('ANCD','MENUP','微商带货','已放弃','dropped')")
            await db.commit()
            return await _build_asset_menu(db, "MENUP")
        finally:
            await db.close()

    menu = asyncio.run(run())
    assert "焦虑老板" in menu["menu"]
    assert "训练营" in menu["menu"]
    assert "微商带货" in menu["menu"]          # 已放弃方向也列（供护栏）
    assert menu["valid_aud"] == {"AUD1"}
    assert menu["valid_anc"] == {"ANC1"}       # dropped 不进可标集
    assert menu["valid_dropped"] == {"ANCD"}
