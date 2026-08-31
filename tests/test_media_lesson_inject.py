"""教训/红线注入 write_script 的路径。用内存 DB + 假 AI，不打真模型。"""
import asyncio
import uuid

from app.services import media_ai
from tests.media_helpers import make_db, fake_ai, seed_content


async def _seed_lesson(db, persona_id, kind, brief, trigger="", status="active", detail=""):
    lid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO media_lesson (id,persona_id,kind,brief,trigger_context,status,detail) "
        "VALUES (?,?,?,?,?,?,?)", (lid, persona_id, kind, brief, trigger, status, detail))
    await db.commit()
    return lid


def test_redline_and_lesson_both_injected(monkeypatch):
    """红线无条件进，教训按匹配进，两者同时出现在提示词里。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await _seed_lesson(db, "P1", "lesson", "开头别铺垫", trigger="企业落地AI")
        out = await media_ai.write_script(db, cid)
        await db.close()
        return out

    out = asyncio.run(run())
    assert out["ok"] is True
    assert "【红线（绝对不许违反）】" in seen["prompt"]
    assert "不许编造数据" in seen["prompt"]
    assert "开头别铺垫" in seen["prompt"]


def test_lesson_block_sits_before_final_instruction(monkeypatch):
    """位置：本子在「请写出…」那句之前（近因效应，spec §4.5）。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    p = seen["prompt"]
    assert p.index("不许编造数据") < p.index("请写出这条内容的口播脚本。")


def test_lean_mode_skips_lessons(monkeypatch):
    """lean 模式语义是「只给身份行做对照」，不注入本子。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        lid = await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await media_ai.write_script(db, cid, mode="lean")
        cur = await db.execute("SELECT hit_count FROM media_lesson WHERE id=?", (lid,))
        n = (await cur.fetchone())["hit_count"]
        await db.close()
        return n

    n = asyncio.run(run())
    assert "不许编造数据" not in seen["prompt"]
    assert n == 0


def test_detail_never_enters_prompt(monkeypatch):
    """项目宪法核心不变量：只有 brief 进提示词，detail 永不进——即便它被 SELECT * 读进内存。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "不许编造数据",
                           detail="这是一段很长的详情文字，只用于内部参考，绝不该出现在给写稿AI的提示词里")
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    assert "不许编造数据" in seen["prompt"]
    assert "这是一段很长的详情文字，只用于内部参考，绝不该出现在给写稿AI的提示词里" not in seen["prompt"]


def test_archived_lesson_not_injected(monkeypatch):
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await _seed_lesson(db, "P1", "redline", "已归档的红线", status="archived")
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    assert "已归档的红线" not in seen["prompt"]


def test_hit_count_increments_on_success(monkeypatch):
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai("稿子正文"))

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        lid = await _seed_lesson(db, "P1", "redline", "不许编造数据")
        await media_ai.write_script(db, cid)
        cur = await db.execute("SELECT hit_count FROM media_lesson WHERE id=?", (lid,))
        n = (await cur.fetchone())["hit_count"]
        await db.close()
        return n

    assert asyncio.run(run()) == 1


def test_hit_count_not_incremented_on_ai_error(monkeypatch):
    """AI 报错时不计数——hit_count 要回答的是「参与过一次成品生产吗」。"""
    monkeypatch.setattr(media_ai, "ask_ai", fake_ai("[错误] 模型不可用"))

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        lid = await _seed_lesson(db, "P1", "redline", "不许编造数据")
        out = await media_ai.write_script(db, cid)
        cur = await db.execute("SELECT hit_count FROM media_lesson WHERE id=?", (lid,))
        n = (await cur.fetchone())["hit_count"]
        await db.close()
        return out, n

    out, n = asyncio.run(run())
    assert out["ok"] is False and n == 0


def test_no_lessons_prompt_has_no_empty_block(monkeypatch):
    """库里一条都没有时，提示词里不该多出空标题。"""
    seen = {}

    async def _spy(prompt, model="auto", task_type="", system_prompt="", json_mode=False):
        seen["prompt"] = prompt
        return {"response": "稿子正文", "model": "deepseek", "tokens": 10, "cost": 0.0}

    monkeypatch.setattr(media_ai, "ask_ai", _spy)

    async def run():
        db = await make_db()
        cid = await seed_content(db)
        await media_ai.write_script(db, cid)
        await db.close()

    asyncio.run(run())
    assert "【红线" not in seen["prompt"] and "【教训" not in seen["prompt"]
