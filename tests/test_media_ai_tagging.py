"""media 打标 helper 的纯函数/异步测试。"""
import asyncio

import pytest

from app.database import get_db, init_db
import app.database as _db_mod
from app.services.media_ai import _clean_ids, _build_asset_menu, _clean_one_id, tag_topics
import app.services.media_ai as media_ai


@pytest.fixture(scope="module", autouse=True)
def _db_ready(tmp_path_factory):
    """异步 DB 测试隔离到临时库：不污染用户真实 aipm.db，也不与其它测试抢 WAL 锁。"""
    tmp = tmp_path_factory.mktemp("media_tagging_db") / "test.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())
    yield
    _db_mod.DB_PATH = orig


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
    async def run():
        db = await get_db()
        try:
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


def test_clean_one_id_keeps_valid_drops_bogus():
    assert _clean_one_id("A", {"A", "B"}) == "A"
    assert _clean_one_id("X", {"A", "B"}) == ""
    assert _clean_one_id(None, {"A"}) == ""
    assert _clean_one_id(123, {"A"}) == ""


def test_build_asset_menu_lists_playbooks():
    async def run():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('PBMENU','人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_playbook (id,persona_id,name,structure,when_to_use,"
                "evidence,source,status) VALUES "
                "('PBX','PBMENU','反常识开场','钩子-冲突-收','标题反直觉时','','legacy_mine','proven'),"
                "('PBV','PBMENU','痛点先行','','','','legacy_mine','validating')")
            await db.commit()
            return await _build_asset_menu(db, "PBMENU")
        finally:
            await db.close()
    menu = asyncio.run(run())
    assert "反常识开场" in menu["menu"]
    assert menu["valid_pb"] == {"PBX", "PBV"}


def test_tag_topics_writes_cleaned_playbook_id(monkeypatch):
    async def run():
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO media_persona (id,name,one_liner,current_phase) "
                "VALUES ('TAGP','人设','一句话','涨粉')")
            await db.execute(
                "INSERT INTO media_playbook (id,persona_id,name,structure,when_to_use,"
                "evidence,source,status) VALUES "
                "('GOODPB','TAGP','反常识开场','','','','legacy_mine','proven')")
            await db.execute(
                "INSERT INTO media_topic (id,persona_id,title,puzzle,status,tagged) VALUES "
                "('T_OK','TAGP','选题甲','谜','pool',0),"
                "('T_BOGUS','TAGP','选题乙','谜','pool',0)")
            await db.commit()

            async def fake_ask_ai(prompt, **kw):
                return {"response":
                        '[{"id":"T_OK","audience_ids":[],"anchor_ids":[],'
                        '"dropped_drift_ids":[],"playbook_id":"GOODPB"},'
                        '{"id":"T_BOGUS","audience_ids":[],"anchor_ids":[],'
                        '"dropped_drift_ids":[],"playbook_id":"编造的id"}]',
                        "cost": 0, "model": "test", "tokens": 0}
            monkeypatch.setattr(media_ai, "ask_ai", fake_ask_ai)
            await tag_topics(db, "TAGP")
            cur = await db.execute(
                "SELECT id,playbook_id FROM media_topic WHERE persona_id='TAGP'")
            return {r["id"]: r["playbook_id"] for r in await cur.fetchall()}
        finally:
            await db.close()
    got = asyncio.run(run())
    assert got["T_OK"] == "GOODPB"       # 合法 id 写入
    assert got["T_BOGUS"] == ""          # 编造 id 被清洗成空
