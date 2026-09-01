"""草稿区片段端点：助手写完后实时刷新拉的就是它。

真机踩到：片段用了 ic.icon()，但 {% import "_icons.html" as ic %} 写在
media_content.html 顶上——被 include 时能从外层上下文继承到，**单独渲染时不能**，
于是这个端点 500，前端又不检查状态码，把「Internal Server Error」那张错误页
原样塞进了草稿区。单元测试之前完全没覆盖「片段被单独渲染」这条路。
"""
import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.database as _db_mod
from app.api.auth import get_or_create_session_secret
from app.database import get_db, init_db
from app.main import app


def _client():
    s = TimestampSigner(get_or_create_session_secret())
    c = TestClient(app)
    c.cookies.set("session", s.sign(
        base64.b64encode(json.dumps({"user": "t"}).encode())).decode())
    return c


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dh_db") / "t.db"
    orig = _db_mod.DB_PATH
    _db_mod.DB_PATH = tmp
    asyncio.run(init_db())

    async def seed():
        db = await get_db()
        await db.execute("INSERT INTO media_persona (id,name,one_liner,current_phase,status) "
                         "VALUES ('DP','嘉','x','涨粉','active')")
        await db.execute("INSERT INTO media_content (id,persona_id,title,stage,ai_draft) "
                         "VALUES ('DC','DP','测试内容','scripted','草稿正文')")
        await db.commit()
        await db.close()

    asyncio.run(seed())
    yield
    _db_mod.DB_PATH = orig


def test_fragment_renders_standalone():
    """这条就是那个 500 的回归测试——片段自己得能 import 图标。"""
    r = _client().get("/media/content/DC/drafts-html")
    assert r.status_code == 200
    assert "Internal Server Error" not in r.text
    assert "draft-card" in r.text
    assert "草稿正文" in r.text


def test_backfills_legacy_ai_draft():
    """ai_draft 里那版没进历史表时，这个端点也要补上——
    否则助手写完刷新，看到的还是旧列表。"""
    r = _client().get("/media/content/DC/drafts-html")
    assert "草稿正文" in r.text

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_draft WHERE content_id='DC'")
        n = (await cur.fetchone())["c"]
        await db.close()
        return n

    assert asyncio.run(chk()) == 1


def test_repeat_calls_do_not_pile_up_drafts():
    """多刷几次页面不该越刷越多版。"""
    c = _client()
    for _ in range(3):
        c.get("/media/content/DC/drafts-html")

    async def chk():
        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) c FROM media_draft WHERE content_id='DC'")
        n = (await cur.fetchone())["c"]
        await db.close()
        return n

    assert asyncio.run(chk()) == 1


def test_unknown_content_returns_empty_not_error():
    """内容不存在时给空串，别让前端把错误页塞进草稿区。"""
    r = _client().get("/media/content/NOPE/drafts-html")
    assert r.status_code == 200
    assert r.text.strip() == ""
