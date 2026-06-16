"""项目知识上下文 —— 任务执行时自动加载本项目的核心档和相关参考资料给 AI。

两层策略：
  1. 核心档（is_core=1）：本项目所有核心笔记，全文塞入（这是项目"宪法"，必须看）
  2. 参考资料：按任务标题/描述关键词，从本项目其他笔记里检索最相关的若干篇（摘要）

总量受字符上限保护，避免 token 爆炸。
"""
import re
from app.database import get_db

# 单篇核心笔记最大字符数（防一篇笔记太长撑爆）
CORE_NOTE_MAX_CHARS = 8000
# 单篇参考笔记最大字符数
REF_NOTE_MAX_CHARS = 1500
# 整个上下文（核心+参考）总字符上限
TOTAL_CONTEXT_MAX_CHARS = 50000
# 参考笔记最多取几篇
MAX_REF_NOTES = 8


def _extract_keywords(text: str) -> list[str]:
    """从任务描述里粗暴提取关键词（>=2字的中英文片段）"""
    parts = re.split(r"[\s,，。、？?！!：:；;\"'（）()【】\[\]{}\.]+", text or "")
    return [p for p in parts if len(p) >= 2][:8]


async def build_project_context(project_id: str, task_title: str, task_description: str = "") -> str:
    """构造给任务 AI 的项目背景。返回拼好的文本块，可直接插到 prompt 前面。"""
    if not project_id:
        return ""

    db = await get_db()
    try:
        # ① 项目元信息
        cursor = await db.execute(
            "SELECT code, name, description, status FROM projects WHERE id = ?",
            (project_id,),
        )
        proj_row = await cursor.fetchone()
        if not proj_row:
            return ""
        proj = dict(proj_row)

        # ② 核心档：全部 is_core=1 且未删除
        cursor = await db.execute(
            """SELECT id, title, content, updated_at FROM notes
               WHERE project_id = ? AND is_core = 1 AND deleted_at IS NULL
               ORDER BY updated_at DESC""",
            (project_id,),
        )
        core_notes = [dict(r) for r in await cursor.fetchall()]

        # ②.5 全部笔记标题清单（不读内容，仅 id+标题+源类型；让 AI 看到知识库里到底有什么）
        cursor = await db.execute(
            """SELECT id, title, is_core, source_type FROM notes
               WHERE project_id = ? AND deleted_at IS NULL
               ORDER BY is_core DESC, updated_at DESC""",
            (project_id,),
        )
        all_notes = [dict(r) for r in await cursor.fetchall()]

        # ③ 参考资料：本项目其他笔记按关键词检索打分，取前 MAX_REF_NOTES
        keywords = _extract_keywords(task_title + " " + (task_description or ""))
        ref_notes = []
        if keywords:
            like_parts = " OR ".join(["(title LIKE ? OR content LIKE ? OR tags LIKE ?)"] * len(keywords))
            params = [project_id]
            for kw in keywords:
                params.extend([f"%{kw}%"] * 3)
            cursor = await db.execute(
                f"""SELECT id, title, content, tags FROM notes
                    WHERE project_id = ? AND is_core = 0 AND deleted_at IS NULL
                      AND ({like_parts})
                    ORDER BY updated_at DESC LIMIT 30""",
                params,
            )
            candidates = [dict(r) for r in await cursor.fetchall()]

            def score(n):
                s = 0
                for kw in keywords:
                    if kw in (n["title"] or ""):
                        s += 3
                    if kw in (n["tags"] or ""):
                        s += 2
                    s += min((n["content"] or "").count(kw), 5)
                return s

            candidates.sort(key=score, reverse=True)
            ref_notes = candidates[:MAX_REF_NOTES]
    finally:
        await db.close()

    # 拼上下文
    blocks = []
    blocks.append(f"## 项目背景\n项目编号：{proj['code']}\n项目名称：{proj['name']}\n项目描述：{proj['description'] or '(无)'}")

    # 知识库导航：默认只放一行提示，让 AI 知道仓库存在但不污染上下文。
    # 当任务描述提到"资料/上传/整理/已有/汇总/翻一下"等触发词时，才自动列清单。
    if all_notes:
        task_text = (task_title or "") + " " + (task_description or "")
        triggers = ["上传", "资料", "整理", "汇总", "已有", "翻", "查一下", "看看", "梳理", "归类", "整合", "笔记", "资料库", "知识库"]
        should_list = any(t in task_text for t in triggers)

        if should_list:
            src_map = {"ai_classified": "[AI分类]", "ai_summary": "[AI整理]", "auto_progress": "[进度]",
                       "file_import": "[文件]", "upload": "[上传]", "url_import": "[网页]",
                       "image": "[图片]", "image_article": "[图片整理]", "ai_chat": "[AI问答]",
                       "ai_weekly": "[周报]", "master_ai": "[总AI]", "manual": "[手动]"}
            list_lines = [f"\n## 📚 本项目知识库笔记清单（共 {len(all_notes)} 篇 · 任务提及资料/整理类操作，已为你列出）"]
            for n in all_notes:
                star = "⭐" if n["is_core"] else "  "
                src = src_map.get(n.get("source_type"), "")
                list_lines.append(f"  {star} id={n['id'][:8]} | {src}{n['title']}")
            list_lines.append("\n（读全文用 read_kb_note；不要去文件系统找它们，它们在数据库里）")
            blocks.append("\n".join(list_lines))
        else:
            blocks.append(f"\n💡 本项目知识库另有 {len(all_notes)} 篇笔记（已含上方核心档与相关参考）。如需翻看更多：list_kb_notes 列目录、read_kb_note 读全文。")

    total_chars = sum(len(b) for b in blocks)

    # 核心档
    if core_notes:
        core_block_lines = ["\n## 📖 项目核心档（必读·不许违背）"]
        for n in core_notes:
            content = (n["content"] or "")[:CORE_NOTE_MAX_CHARS]
            if len(n["content"] or "") > CORE_NOTE_MAX_CHARS:
                content += "\n…（核心档过长已截断）"
            piece = f"\n### ⭐ {n['title']}\n{content}"
            if total_chars + len(piece) > TOTAL_CONTEXT_MAX_CHARS:
                core_block_lines.append(f"\n### ⭐ {n['title']}\n（因总上下文上限，本篇被省略，请用 read_file 或追问董事会获取细节）")
            else:
                core_block_lines.append(piece)
                total_chars += len(piece)
        blocks.append("\n".join(core_block_lines))
    else:
        blocks.append("\n## 📖 项目核心档\n（本项目还没有核心档。如果你的任务需要明确的目标/定位/规格等核心信息但找不到，请向董事会索取，不要凭空假设。）")

    # 参考资料
    if ref_notes:
        ref_block_lines = [f"\n## 📚 相关参考资料（自动检索本项目知识库，共 {len(ref_notes)} 篇）"]
        for n in ref_notes:
            content = (n["content"] or "")[:REF_NOTE_MAX_CHARS]
            if len(n["content"] or "") > REF_NOTE_MAX_CHARS:
                content += "\n…（参考资料已截断，需要全文可用 read_file 或追问）"
            piece = f"\n### {n['title']}\n{content}"
            if total_chars + len(piece) > TOTAL_CONTEXT_MAX_CHARS:
                break
            ref_block_lines.append(piece)
            total_chars += len(piece)
        blocks.append("\n".join(ref_block_lines))

    return "\n".join(blocks) + "\n\n---\n"
