import json
import re
import uuid
from datetime import datetime
from app.database import get_db
from app.services.ai_router import ask_ai, ask_ai_vision

IMAGE_ANALYZE_SYSTEM = """你是图片分析专家。请分析用户给的图片：

输出格式：
## 图片文字
（完整提取图片中的所有文字，保持原有结构；没有文字就写"无"）

## 内容说明
（2-4 句话说明图片的关键信息：是什么、讲了什么、有什么值得注意的）

简洁准确，不要废话。"""

IMAGE_ARTICLE_SYSTEM = """你是知识整理专家。用户会给你多张图片（截图、白板照片、文档照片等），请把所有图片中的文字和信息整合成一篇结构化的知识文章。

要求：
- 起一个准确的标题（第一行用 # 标题 格式）
- 内容有层级（## 小节），要点清晰
- 多张图重复的内容去重，相关的内容合并到一起
- 保留重要的数字、名称、结论
- 直接输出文章，不要解释你做了什么"""

CHAT_SYSTEM = """你是一个知识库 AI 助手。用户会给你一批笔记和一条指令（可能是提问、整理、分类、总结、提炼待办等任何要求）。

规则：
- 严格按用户的指令执行，用户要什么格式就给什么格式
- 引用笔记时注明笔记标题（格式：【笔记标题】）
- 如果是提问：优先根据笔记内容回答，笔记里没有的可以用自己的知识补充，但要注明"（笔记中未涉及，以下是补充信息）"
- 如果是整理/分类/总结：按用户要求的维度组织内容，结构清晰
- 回答简洁、结构化，不要客套话"""

CLASSIFY_SYSTEM = """你是一个信息分类专家。用户会给你一段文字（可能是聊天记录、会议纪要、录音转文字等），请将内容分层提取。

请返回 JSON：
{
  "layers": [
    {
      "type": "knowledge",
      "title": "提取的标题",
      "content": "具体内容",
      "tags": ["标签1", "标签2"]
    }
  ]
}

type 只能是以下几种：
- "knowledge": 框架性知识、方法论、经验总结
- "progress": 项目进度、任务跟进、待办事项
- "decision": 关键决策、方案选择、原因说明
- "status": 阶段状态、里程碑、问题风险
- "idea": 灵感想法、未来计划、待验证假设

规则：
- 一段文字可能包含多个层次的信息，全部提取
- 每条信息要有明确的标题和完整内容
- 标签要精准，2-4个
- 只返回 JSON"""

SUMMARIZE_SYSTEM = """你是一个笔记整理专家。用户会给你一批笔记的标题和内容，请整理汇总。

输出格式：
1. 先给出一个整体摘要（3-5句话）
2. 然后按主题分类列出要点
3. 标注哪些内容需要跟进或行动

语言要简洁、有条理。"""

WEEKLY_SYSTEM = """你是一个项目管理助手。根据本周的任务完成情况、笔记和决策，生成一份简洁的周报。

格式：
## 本周概览
（1-2句总结）

## 完成的事
- 列表

## 进行中
- 列表

## 关键决策
- 列表

## 下周计划
- 基于当前进度的建议

## 风险提示
- 如果有的话

语言简洁直接，不要客套话。"""


async def classify_content(text: str, project_id: str = "", model: str = "auto") -> list[dict]:
    result = await ask_ai(prompt=text, model=model, task_type="analysis", system_prompt=CLASSIFY_SYSTEM)

    try:
        resp = result["response"]
        if "```json" in resp:
            resp = resp.split("```json")[1].split("```")[0]
        elif "```" in resp:
            resp = resp.split("```")[1].split("```")[0]
        parsed = json.loads(resp.strip())
        layers = parsed.get("layers", [])
    except (json.JSONDecodeError, IndexError, KeyError):
        layers = [{"type": "knowledge", "title": "AI 分类失败", "content": text, "tags": []}]

    TYPE_LABELS = {
        "knowledge": "知识",
        "progress": "进度",
        "decision": "决策",
        "status": "状态",
        "idea": "想法",
    }

    db = await get_db()
    created = []
    try:
        now = datetime.now().isoformat()
        for layer in layers:
            layer_type = layer.get("type", "knowledge")
            tags_list = layer.get("tags", [])
            tags_list.append(TYPE_LABELS.get(layer_type, layer_type))
            clean_tags = ",".join(tags_list)

            if layer_type == "decision" and project_id:
                dec_id = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO decisions (id, project_id, title, context, decision, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (dec_id, project_id or None, layer.get("title", ""), text[:500], layer.get("content", ""), "", now),
                )

            note_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO notes (id, title, content, project_id, tags, source_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ai_classified', ?, ?)""",
                (note_id, layer.get("title", "未命名"), layer.get("content", ""), project_id or None, clean_tags, now, now),
            )
            created.append({"id": note_id, "type": layer_type, "title": layer.get("title", "")})

        # 记录 AI 调用
        run_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO agent_runs (id, task_id, model, prompt, response, tokens_used, cost, status, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, 'success', ?)""",
            (run_id, result["model"], f"[分类] {text[:200]}", result["response"], result["tokens"], result["cost"], now),
        )
        await db.commit()
    finally:
        await db.close()

    return created


async def summarize_notes(note_ids: list[str] = None, tag: str = "", model: str = "auto") -> dict:
    db = await get_db()
    try:
        if note_ids:
            placeholders = ",".join(["?" for _ in note_ids])
            cursor = await db.execute(f"SELECT title, content, tags FROM notes WHERE id IN ({placeholders}) AND deleted_at IS NULL", note_ids)
        elif tag:
            cursor = await db.execute("SELECT title, content, tags FROM notes WHERE ',' || tags || ',' LIKE ? AND deleted_at IS NULL", (f"%,{tag},%",))
        else:
            cursor = await db.execute("SELECT title, content, tags FROM notes WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 20")
        notes = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    if not notes:
        return {"summary": "没有找到笔记可以整理"}

    # 防超限：单条笔记截断 + 分批整理 + 最后汇总
    NOTE_MAX_CHARS = 2000     # 单条笔记最多取前 2000 字
    BATCH_MAX_CHARS = 50000   # 每批总量约 5 万字符（DeepSeek 安全范围内）
    MAX_BATCHES = 8           # 最多 8 批，再多就提示用户缩小范围

    pieces = []
    for n in notes:
        content = (n["content"] or "")[:NOTE_MAX_CHARS]
        if len(n["content"] or "") > NOTE_MAX_CHARS:
            content += "\n…（内容过长已截断）"
        pieces.append(f"### {n['title']}\n{content}")

    # 按字符量分批
    batches, current, current_len = [], [], 0
    for p in pieces:
        if current and current_len + len(p) > BATCH_MAX_CHARS:
            batches.append(current)
            current, current_len = [], 0
        current.append(p)
        current_len += len(p)
    if current:
        batches.append(current)

    skipped_note = ""
    if len(batches) > MAX_BATCHES:
        skipped = sum(len(b) for b in batches[MAX_BATCHES:])
        batches = batches[:MAX_BATCHES]
        skipped_note = f"\n\n> 注意：笔记太多，本次只整理了前 {sum(len(b) for b in batches)} 条，跳过了 {skipped} 条。建议按标签/文件夹分次整理。"

    total_cost, used_model = 0.0, ""

    if len(batches) == 1:
        text = "\n\n".join(batches[0])
        result = await ask_ai(prompt=f"请整理以下 {len(batches[0])} 条笔记：\n\n{text}", model=model, task_type="analysis", system_prompt=SUMMARIZE_SYSTEM)
        return {"summary": result["response"] + skipped_note, "model": result["model"], "cost": result["cost"]}

    # 多批：先各批整理，再汇总
    batch_summaries = []
    for i, batch in enumerate(batches):
        text = "\n\n".join(batch)
        result = await ask_ai(prompt=f"请整理以下 {len(batch)} 条笔记（这是第 {i+1}/{len(batches)} 批）：\n\n{text}", model=model, task_type="analysis", system_prompt=SUMMARIZE_SYSTEM)
        total_cost += result["cost"]
        used_model = result["model"]
        batch_summaries.append(f"## 第 {i+1} 批整理结果\n{result['response']}")

    merge_prompt = "以下是分批整理的笔记摘要，请合并成一份完整的整理报告（去重、归类、突出重点和待办）：\n\n" + "\n\n".join(batch_summaries)
    final = await ask_ai(prompt=merge_prompt, model=model, task_type="analysis", system_prompt=SUMMARIZE_SYSTEM)
    total_cost += final["cost"]

    return {"summary": final["response"] + skipped_note, "model": used_model, "cost": round(total_cost, 6)}


ORGANIZE_SYSTEM = """你是知识库整理执行器。用户会给你一批笔记（每条有 id）和一条整理指令，你要决定每条笔记怎么处理。

只返回 JSON，格式：
{
  "summary": "一句话说明整理思路",
  "actions": [
    {"id": "笔记id", "folder": "目标文件夹", "add_tags": ["新增标签"]}
  ]
}

规则：
- folder 用 / 分层（如 资料/竞品），不想移动就填 null
- add_tags 是要新增的标签数组，不加就填 []
- 不需要任何改动的笔记不要出现在 actions 里
- 你只能移动文件夹和加标签，不能删除或修改内容
- 只返回 JSON，不要其他文字"""


async def _fetch_notes_by_scope(scope: str, question: str = "", limit: int = 80) -> list[dict]:
    """按范围取笔记：all / folder:xxx / tag:xxx / auto（关键词检索）"""
    db = await get_db()
    try:
        if scope == "all":
            cursor = await db.execute(
                "SELECT id, title, content, tags, folder FROM notes WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in await cursor.fetchall()]
        if scope.startswith("folder:"):
            f = scope[len("folder:"):]
            cursor = await db.execute(
                "SELECT id, title, content, tags, folder FROM notes WHERE (folder = ? OR folder LIKE ?) AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
                (f, f"{f}/%", limit),
            )
            return [dict(row) for row in await cursor.fetchall()]
        if scope.startswith("tag:"):
            t = scope[len("tag:"):]
            cursor = await db.execute(
                "SELECT id, title, content, tags, folder FROM notes WHERE ',' || tags || ',' LIKE ? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
                (f"%,{t},%", limit),
            )
            return [dict(row) for row in await cursor.fetchall()]
        # auto：关键词检索
        words = [w for w in re.split(r"[\s,，。、？?！!：:；;\"'（）()]+", question) if len(w) >= 2][:8]
        if not words:
            return []
        like_parts = " OR ".join(["(title LIKE ? OR content LIKE ? OR tags LIKE ?)"] * len(words))
        params = []
        for w in words:
            params.extend([f"%{w}%"] * 3)
        cursor = await db.execute(
            f"SELECT id, title, content, tags, folder FROM notes WHERE ({like_parts}) AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 30",
            params,
        )
        candidates = [dict(row) for row in await cursor.fetchall()]

        def score(n):
            s = 0
            for w in words:
                if w in (n["title"] or ""):
                    s += 3
                if w in (n["tags"] or ""):
                    s += 2
                s += min((n["content"] or "").count(w), 5)
            return s

        candidates.sort(key=score, reverse=True)
        return candidates[:6]
    finally:
        await db.close()


async def organize_notes(instruction: str, scope: str = "all", model: str = "auto") -> dict:
    """整理模式：AI 给出移动/打标签方案（不直接写库，由前端确认后 apply）"""
    notes = await _fetch_notes_by_scope(scope, instruction)
    if not notes:
        return {"summary": "该范围内没有找到笔记", "actions": [], "model": "none", "cost": 0}

    lines = []
    for n in notes:
        content_preview = (n["content"] or "")[:400].replace("\n", " ")
        lines.append(f"- id: {n['id']}\n  标题: {n['title']}\n  当前文件夹: {n['folder'] or '(未分类)'}\n  当前标签: {n['tags'] or '(无)'}\n  内容预览: {content_preview}")

    prompt = f"笔记列表（共 {len(notes)} 条）：\n\n" + "\n\n".join(lines) + f"\n\n---\n\n整理指令：{instruction}"
    result = await ask_ai(prompt=prompt, model=model, task_type="analysis", system_prompt=ORGANIZE_SYSTEM)

    try:
        text = result["response"]
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        parsed = json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return {"summary": "AI 返回的方案解析失败，请换个说法再试。原始回复：" + result["response"][:300],
                "actions": [], "model": result["model"], "cost": result["cost"]}

    # 校验 action 里的 id 真实存在，并带上标题方便前端展示
    title_map = {n["id"]: n["title"] for n in notes}
    actions = []
    for a in parsed.get("actions", []):
        if a.get("id") in title_map:
            actions.append({
                "id": a["id"],
                "title": title_map[a["id"]],
                "folder": a.get("folder") or None,
                "add_tags": [t for t in (a.get("add_tags") or []) if t],
            })

    return {"summary": parsed.get("summary", ""), "actions": actions, "model": result["model"], "cost": result["cost"]}


async def apply_organize_actions(actions: list[dict]) -> int:
    """执行整理方案：移动文件夹 + 合并新标签"""
    now = datetime.now().isoformat()
    updated = 0
    db = await get_db()
    try:
        for a in actions:
            cursor = await db.execute("SELECT tags FROM notes WHERE id = ?", (a.get("id"),))
            row = await cursor.fetchone()
            if not row:
                continue
            sets, params = [], []
            if a.get("folder"):
                sets.append("folder = ?")
                params.append(str(a["folder"]).strip().strip("/"))
            if a.get("add_tags"):
                existing = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
                merged = existing + [t for t in a["add_tags"] if t not in existing]
                sets.append("tags = ?")
                params.append(",".join(merged))
            if not sets:
                continue
            sets.append("updated_at = ?")
            params.extend([now, a["id"]])
            await db.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", params)
            updated += 1
        await db.commit()
    finally:
        await db.close()
    return updated


async def chat_with_notes(question: str, history: str = "", model: str = "auto", scope: str = "auto") -> dict:
    """知识库 AI 助手：按范围取笔记，再让 AI 按指令执行（提问/整理/分类/总结）

    scope: auto（按关键词智能检索）/ all（最近笔记）/ folder:xxx / tag:xxx
    """
    TOTAL_MAX_CHARS = 80_000  # 喂给 AI 的笔记总量上限

    notes = await _fetch_notes_by_scope(scope, question)

    # 拼上下文：单条截断 + 总量控制，超出的笔记只列标题
    per_note_max = 2500 if len(notes) <= 10 else 1200
    context_parts, used_notes, total = [], [], 0
    title_only = []
    for n in notes:
        content = (n["content"] or "")[:per_note_max]
        piece = f"【{n['title']}】\n{content}"
        if total + len(piece) > TOTAL_MAX_CHARS:
            title_only.append(n["title"])
            continue
        context_parts.append(piece)
        used_notes.append(n)
        total += len(piece)

    context = "\n\n---\n\n".join(context_parts) if context_parts else "（没有找到相关笔记）"
    if title_only:
        context += f"\n\n（另有 {len(title_only)} 条笔记因篇幅只列标题：" + "、".join(title_only[:30]) + "）"

    prompt = ""
    if history:
        prompt += f"之前的对话：\n{history}\n\n"
    prompt += f"知识库笔记（共 {len(used_notes)} 条）：\n\n{context}\n\n---\n\n用户指令：{question}"

    result = await ask_ai(prompt=prompt, model=model, task_type="analysis", system_prompt=CHAT_SYSTEM)

    return {
        "answer": result["response"],
        "sources": [{"id": n["id"], "title": n["title"]} for n in used_notes],
        "note_count": len(used_notes),
        "model": result["model"],
        "cost": result["cost"],
    }


async def analyze_image_paths(paths: list[str], mode: str = "analyze") -> dict:
    """分析图片。mode=analyze（提取文字+说明）/ article（多图合并成知识文章）"""
    from app.services.importer import load_images_base64

    images = load_images_base64(paths)
    if not images:
        return {"response": "[错误] 没有可分析的图片文件", "model": "none", "tokens": 0, "cost": 0}

    if mode == "article":
        prompt = f"请把这 {len(images)} 张图片的内容整合成一篇知识文章。"
        system = IMAGE_ARTICLE_SYSTEM
    else:
        prompt = "请分析这张图片。" if len(images) == 1 else f"请逐张分析这 {len(images)} 张图片（用 ### 图1、### 图2 分隔）。"
        system = IMAGE_ANALYZE_SYSTEM

    return await ask_ai_vision(prompt=prompt, images=images, system_prompt=system)


async def generate_weekly_report(model: str = "auto") -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT t.title, t.status, t.result, p.name as project_name
            FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE t.updated_at >= datetime('now', '-7 days')
            ORDER BY t.updated_at DESC
        """)
        tasks = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute("SELECT title, content, tags FROM notes WHERE created_at >= datetime('now', '-7 days') AND deleted_at IS NULL ORDER BY created_at DESC")
        notes = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute("SELECT title, decision, reason, made_by FROM decisions WHERE created_at >= datetime('now', '-7 days') ORDER BY created_at DESC")
        decisions = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    parts = []
    if tasks:
        task_text = "\n".join(f"- [{t['status']}] {t['project_name']} / {t['title']}" for t in tasks)
        parts.append(f"## 本周任务（{len(tasks)} 条）\n{task_text}")
    if notes:
        note_text = "\n".join(f"- {n['title']}" for n in notes[:10])
        parts.append(f"## 本周笔记（{len(notes)} 条）\n{note_text}")
    if decisions:
        dec_text = "\n".join(f"- {d['title']}: {d['decision']}" for d in decisions)
        parts.append(f"## 本周决策（{len(decisions)} 条）\n{dec_text}")

    if not parts:
        return {"report": "本周暂无数据，周报无法生成。", "model": "none", "cost": 0}

    prompt = "请根据以下信息生成本周周报：\n\n" + "\n\n".join(parts)
    result = await ask_ai(prompt=prompt, model=model, task_type="analysis", system_prompt=WEEKLY_SYSTEM)

    # 保存为笔记
    now = datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, tags, source_type, created_at, updated_at)
            VALUES (?, ?, ?, 'AI周报,自动生成', 'ai_weekly', ?, ?)""",
            (note_id, f"周报 {now[:10]}", result["response"], now, now),
        )
        await db.commit()
    finally:
        await db.close()

    return {"report": result["response"], "note_id": note_id, "model": result["model"], "cost": result["cost"]}
