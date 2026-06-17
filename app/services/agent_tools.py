"""AI 工具层 —— 给执行 agent 装上"手脚"，让它能真正动手干活而不只是产出文字。

工具：
- web_fetch   联网抓取网页正文（调研、查资料）
- run_python  在服务器上真实执行 Python（爬数据、算数据、生成文件）
- write_file  写文件到项目工作区（产出 Excel/脚本/文档，可下载）
- read_file   读回工作区的文件继续加工
- list_files  列出工作区文件

注意：按董事会决策，当前阶段**不做沙箱**，run_python 直接在服务器上执行。
工作区隔离在 data/workspace/<project_id>/，文件可通过 /workspace 访问下载。
"""
import sys
import json
import asyncio
import logging
from pathlib import Path
from app.config import BASE_DIR
from app.services.ai_router import QWEN_BASE_URL, PRICE_TABLE, _load_config

log = logging.getLogger(__name__)

WORKSPACE_ROOT = BASE_DIR / "data" / "workspace"
RUN_TIMEOUT = 120  # 单次代码执行最长 120 秒，防卡死（这是防挂起，不是沙箱）
MAX_TOOL_OUTPUT = 12000  # 工具返回给 AI 的内容上限，防止撑爆上下文


def _workspace(project_id: str | None) -> Path:
    d = WORKSPACE_ROOT / (project_id or "general")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n…（输出过长，已截断，共 {len(text)} 字符）"
    return text


def _sanitize_output(text: str) -> str:
    """清掉模型泄漏的工具调用标记（DeepSeek 特殊 token、伪 XML invoke/parameter 等），
    这些是内部机制，不该展示给董事会。"""
    import re as _re
    if not text:
        return text
    # DeepSeek 工具调用特殊 token，如 <｜tool▁calls▁begin｜>
    text = _re.sub(r'<[｜|][^>]*[｜|]>', '', text)
    # 伪 XML 工具标记 <tool_calls> <invoke ...> <parameter ...> 及其闭合
    text = _re.sub(r'</?\s*(?:tool_calls|invoke|parameter)\b[^>]*>', '', text)
    # 残留的 invoke name= / parameter name= 行
    text = _re.sub(r'^\s*(?:invoke|parameter)\s+name=.*$', '', text, flags=_re.MULTILINE)
    # 收敛多余空行
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── 工具实现 ──────────────────────────────────────────

async def tool_web_fetch(url: str) -> str:
    import httpx
    from app.services.importer import _TextExtractor

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        }) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        return f"抓取失败: {e}"
    parser = _TextExtractor()
    try:
        parser.feed(resp.text)
    except Exception:
        pass
    text = parser.get_text()
    return _truncate(f"标题: {parser.title}\n来源: {url}\n\n{text}") if text else f"未提取到正文内容: {url}"


async def tool_run_python(code: str, project_id: str | None) -> str:
    workdir = _workspace(project_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return f"执行超时（超过 {RUN_TIMEOUT} 秒已强制终止）"
    except Exception as e:
        return f"执行出错: {e}"
    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    rc = proc.returncode
    head = f"[退出码 {rc}]\n" if rc != 0 else ""
    return _truncate(head + (out or "（无输出）"))


def tool_write_file(filename: str, content: str, project_id: str | None) -> str:
    if not content or not content.strip():
        return (
            "❌ write_file 收到空内容，文件未写入。\n"
            "这通常是因为内容太长导致函数调用参数被截断。\n"
            "解决办法：用 run_python 写文件，把内容嵌入 Python 字符串，例如：\n"
            "```python\n"
            "content = \"\"\"（完整内容粘贴到这里）\"\"\"\n"
            "with open('filename.md', 'w', encoding='utf-8') as f:\n"
            "    f.write(content)\n"
            "print('写入成功，字符数:', len(content))\n"
            "```"
        )
    workdir = _workspace(project_id)
    safe = Path(filename).name  # 防目录穿越
    fpath = workdir / safe
    try:
        fpath.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"写入失败: {e}"
    pid = project_id or "general"
    return f"已写入文件: {safe}（{len(content)} 字符），下载地址 /workspace/{pid}/{safe}"


def tool_read_file(filename: str, project_id: str | None) -> str:
    workdir = _workspace(project_id)
    safe = Path(filename).name
    fpath = workdir / safe
    if not fpath.exists():
        return f"文件不存在: {safe}"
    try:
        return _truncate(fpath.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return f"读取失败: {e}"


def tool_list_files(project_id: str | None) -> str:
    workdir = _workspace(project_id)
    files = [f.name for f in workdir.iterdir() if f.is_file()]
    return "工作区文件: " + (", ".join(files) if files else "（空）")


async def tool_list_kb_notes(project_id: str | None) -> str:
    """列出本项目知识库里所有笔记标题（含时间戳和源类型，不读内容，节省 tokens）"""
    from app.database import get_db
    if not project_id:
        return "无项目上下文，无法列知识库笔记"
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, title, is_core, source_type, tags, created_at, updated_at FROM notes
               WHERE project_id = ? AND deleted_at IS NULL
               ORDER BY is_core DESC, updated_at DESC""",
            (project_id,),
        )
        notes = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    if not notes:
        return "本项目知识库暂无笔记"
    # 源类型含义说明：标签后面括号里是这种笔记的真实来源
    src_map = {
        "ai_classified": "[聊天分类·用户原料]",  # 董事会粘贴聊天/录音，AI 帮分层归类，原料是董事会的
        "ai_summary": "[AI整理·用户原料]",      # 董事会原料经 AI 整理产出
        "auto_progress": "[执行进度]",          # 自动执行 agent 写的进度
        "file_import": "[文件导入·用户上传]",
        "upload": "[直接上传·用户上传]",
        "url_import": "[网页导入·用户上传]",
        "image": "[图片·用户上传]",
        "image_article": "[图片整理·用户原料]",
        "ai_chat": "[AI问答存档]",
        "ai_weekly": "[周报]",
        "master_ai": "[总AI对话存档]",
    }
    lines = [
        f"本项目知识库共 {len(notes)} 篇笔记（按更新时间倒序；要读全文用 read_kb_note）：",
        "格式：[更新日期] ⭐核心档 id=前8位 | [源类型] 标题 #tags",
        "源类型里「用户上传」「用户原料」都是董事会提供的内容；list_kb_notes 的结果 = 用户说的「项目资料库」，没有第二份。",
    ]
    for n in notes:
        star = "⭐" if n["is_core"] else "  "
        src = src_map.get(n.get("source_type"), f"[{n.get('source_type') or '未知'}]")
        tags = f" #{n['tags']}" if n.get("tags") else ""
        # 截取日期部分（YYYY-MM-DD），ISO 时间戳的前 10 位
        date = (n.get("updated_at") or n.get("created_at") or "")[:10] or "未知日期"
        lines.append(f"  [{date}] {star} id={n['id'][:8]} | {src} {n['title']}{tags}")
    return "\n".join(lines)


async def tool_create_kb_note(title: str, content: str, project_id: str | None,
                              folder: str = "", tags: str = "", is_core: bool = False) -> str:
    """在知识库创建一篇新笔记"""
    from app.database import get_db
    import uuid
    if not project_id:
        return "无项目上下文，无法创建笔记"
    if not title or not content:
        return "title 和 content 不能为空"
    now = __import__('datetime').datetime.now().isoformat()
    note_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notes (id, title, content, author, project_id, folder, tags, source_type, is_core, created_at, updated_at)
               VALUES (?, ?, ?, 'agent', ?, ?, ?, 'agent_created', ?, ?, ?)""",
            (note_id, title, content, project_id, folder or "", tags or "", 1 if is_core else 0, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return f"✅ 已创建笔记「{title}」id={note_id[:8]}"


async def tool_update_kb_note(query: str, project_id: str | None,
                               new_title: str = "", new_content: str = "",
                               new_tags: str = "", is_core: int = -1) -> str:
    """更新知识库已有笔记（支持按 id 前缀或标题匹配）。只传要改的字段，其余保持原值。"""
    from app.database import get_db
    q = (query or "").strip()
    if not q:
        return "请提供笔记 id 或标题"
    db = await get_db()
    try:
        # 查找目标笔记
        cursor = await db.execute("SELECT id, title FROM notes WHERE id = ? AND deleted_at IS NULL", (q,))
        row = await cursor.fetchone()
        if not row:
            cursor = await db.execute("SELECT id, title FROM notes WHERE id LIKE ? AND deleted_at IS NULL LIMIT 1", (q + "%",))
            row = await cursor.fetchone()
        if not row and project_id:
            cursor = await db.execute(
                "SELECT id, title FROM notes WHERE project_id = ? AND title LIKE ? AND deleted_at IS NULL ORDER BY is_core DESC LIMIT 1",
                (project_id, f"%{q}%"),
            )
            row = await cursor.fetchone()
        if not row:
            return f"没找到笔记: {q}"
        note_id, old_title = row["id"], row["title"]
        now = __import__('datetime').datetime.now().isoformat()
        sets, vals = [], []
        if new_title:
            sets.append("title = ?"); vals.append(new_title)
        if new_content:
            sets.append("content = ?"); vals.append(new_content)
        if new_tags:
            sets.append("tags = ?"); vals.append(new_tags)
        if is_core >= 0:
            sets.append("is_core = ?"); vals.append(is_core)
        if not sets:
            return "没有传入任何要更新的字段"
        sets.append("updated_at = ?"); vals.append(now)
        vals.append(note_id)
        await db.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", vals)
        await db.commit()
    finally:
        await db.close()
    return f"✅ 已更新笔记「{old_title}」(id={note_id[:8]})"


async def tool_delete_kb_note(query: str, project_id: str | None) -> str:
    """软删除知识库笔记（移入回收站，可还原）。支持按 id 前缀或标题匹配。"""
    from app.database import get_db
    q = (query or "").strip()
    if not q:
        return "请提供笔记 id 或标题"
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, title FROM notes WHERE id = ? AND deleted_at IS NULL", (q,))
        row = await cursor.fetchone()
        if not row:
            cursor = await db.execute("SELECT id, title FROM notes WHERE id LIKE ? AND deleted_at IS NULL LIMIT 1", (q + "%",))
            row = await cursor.fetchone()
        if not row and project_id:
            cursor = await db.execute(
                "SELECT id, title FROM notes WHERE project_id = ? AND title LIKE ? AND deleted_at IS NULL ORDER BY is_core DESC LIMIT 1",
                (project_id, f"%{q}%"),
            )
            row = await cursor.fetchone()
        if not row:
            return f"没找到笔记: {q}"
        note_id, title = row["id"], row["title"]
        now = __import__('datetime').datetime.now().isoformat()
        await db.execute("UPDATE notes SET deleted_at = ? WHERE id = ?", (now, note_id))
        await db.commit()
    finally:
        await db.close()
    return f"✅ 已移入回收站「{title}」(id={note_id[:8]})（可在知识库回收站还原）"


async def tool_read_kb_note(query: str, project_id: str | None) -> str:
    """读取知识库笔记全文。query 可以是笔记 id 前缀（8位）、id 全文，或笔记标题（模糊匹配）"""
    from app.database import get_db
    q = (query or "").strip()
    if not q:
        return "请提供笔记 id 或标题"
    db = await get_db()
    try:
        # 1. 优先按 id 完整匹配
        cursor = await db.execute(
            "SELECT id, title, content, is_core FROM notes WHERE id = ? AND deleted_at IS NULL",
            (q,),
        )
        row = await cursor.fetchone()
        # 2. 按 id 前缀匹配
        if not row:
            cursor = await db.execute(
                "SELECT id, title, content, is_core FROM notes WHERE id LIKE ? AND deleted_at IS NULL LIMIT 1",
                (q + "%",),
            )
            row = await cursor.fetchone()
        # 3. 按标题模糊匹配（限当前项目）
        if not row and project_id:
            cursor = await db.execute(
                "SELECT id, title, content, is_core FROM notes WHERE project_id = ? AND title LIKE ? AND deleted_at IS NULL ORDER BY is_core DESC, updated_at DESC LIMIT 1",
                (project_id, f"%{q}%"),
            )
            row = await cursor.fetchone()
        if not row:
            return f"没找到笔记: {q}"
        note = dict(row)
    finally:
        await db.close()
    star = "⭐ " if note["is_core"] else ""
    return _truncate(f"# {star}{note['title']}\n\n{note['content'] or '(空)'}")


# ── 工具 schema（OpenAI 函数调用格式）──────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取一个网页的正文内容，用于联网调研、查资料、看竞品页面。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "网页完整地址"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "在服务器上真实执行 Python 代码并返回输出。可用于爬数据、处理数据、调用 API、生成文件。已预装常见库（requests/httpx 等）。代码在项目工作区目录下运行，生成的文件留在工作区。",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "要执行的完整 Python 代码"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写成文件保存到项目工作区，董事会可下载。适合产出报告、CSV、脚本等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "文件名，如 report.md、data.csv"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目工作区里已有的文件内容。",
            "parameters": {
                "type": "object",
                "properties": {"filename": {"type": "string"}},
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出项目工作区里的所有**临时文件**（仅是 run_python/write_file 产出的文件，不包含知识库笔记）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_kb_notes",
            "description": "列出本项目知识库的全部笔记标题（带更新日期、id、源类型、tags，⭐ 是核心档）。这是用户说的'项目资料库'的全部内容——董事会上传的资料、用户粘贴 AI 帮分类的聊天记录、AI 整理产出的成果都在这里，没有第二个存储。想读全文用 read_kb_note。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_kb_note",
            "description": "读取本项目知识库笔记的全文。query 可以是 id 前缀（8位）、id 全文、或笔记标题（模糊匹配）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "笔记 id 或标题"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_kb_note",
            "description": "在本项目知识库创建一篇新笔记。用于整理产出、合并结果、分析报告等需要永久保存的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "笔记标题"},
                    "content": {"type": "string", "description": "笔记正文（Markdown 格式）"},
                    "folder": {"type": "string", "description": "文件夹路径，如「资料/产品线」，可不填"},
                    "tags": {"type": "string", "description": "标签，逗号分隔，如「产品,出海」，可不填"},
                    "is_core": {"type": "boolean", "description": "是否设为核心档（⭐），默认 false"},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_kb_note",
            "description": "更新知识库已有笔记的标题、内容、标签或核心档状态。只传要改的字段。query 可以是 id 前缀或标题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "笔记 id 前缀或标题"},
                    "new_title": {"type": "string", "description": "新标题（不改则不传）"},
                    "new_content": {"type": "string", "description": "新正文（不改则不传）"},
                    "new_tags": {"type": "string", "description": "新标签（不改则不传）"},
                    "is_core": {"type": "integer", "description": "1=设为核心档，0=取消核心档，-1=不改（默认）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_kb_note",
            "description": "将知识库笔记移入回收站（软删除，可还原）。用于删除已合并/过期的旧笔记。query 可以是 id 前缀或标题。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "笔记 id 前缀或标题"}},
                "required": ["query"],
            },
        },
    },
]


async def _dispatch_tool(name: str, args: dict, project_id: str | None) -> str:
    try:
        if name == "web_fetch":
            return await tool_web_fetch(args.get("url", ""))
        if name == "run_python":
            return await tool_run_python(args.get("code", ""), project_id)
        if name == "write_file":
            return tool_write_file(args.get("filename", "untitled.txt"), args.get("content", ""), project_id)
        if name == "read_file":
            return tool_read_file(args.get("filename", ""), project_id)
        if name == "list_files":
            return tool_list_files(project_id)
        if name == "list_kb_notes":
            return await tool_list_kb_notes(project_id)
        if name == "read_kb_note":
            return await tool_read_kb_note(args.get("query", ""), project_id)
        if name == "create_kb_note":
            return await tool_create_kb_note(
                args.get("title", ""), args.get("content", ""), project_id,
                folder=args.get("folder", ""), tags=args.get("tags", ""),
                is_core=bool(args.get("is_core", False)),
            )
        if name == "update_kb_note":
            return await tool_update_kb_note(
                args.get("query", ""), project_id,
                new_title=args.get("new_title", ""), new_content=args.get("new_content", ""),
                new_tags=args.get("new_tags", ""), is_core=int(args.get("is_core", -1)),
            )
        if name == "delete_kb_note":
            return await tool_delete_kb_note(args.get("query", ""), project_id)
        return f"未知工具: {name}"
    except Exception as e:
        return f"工具 {name} 执行异常: {e}"


# ── 工具调用客户端（OpenAI 兼容：DeepSeek/通义千问/OpenAI）──

def _get_tool_client():
    """选一个支持函数调用的 OpenAI 兼容模型。返回 (client, model_name, price_key)。"""
    from openai import AsyncOpenAI
    config = _load_config()
    if config.get("deepseek_api_key"):
        return AsyncOpenAI(api_key=config["deepseek_api_key"], base_url="https://api.deepseek.com"), "deepseek-chat", "deepseek"
    if config.get("qwen_api_key"):
        return AsyncOpenAI(api_key=config["qwen_api_key"], base_url=QWEN_BASE_URL), "qwen-plus", "qwen"
    if config.get("openai_api_key"):
        return AsyncOpenAI(api_key=config["openai_api_key"]), "gpt-4o", "openai"
    return None, None, None


async def run_agent_loop(prompt: str, system: str, project_id: str | None = None, max_steps: int = 18,
                          on_step: callable = None) -> dict:
    """带工具的执行循环：AI 自主调用工具直到完成任务。
    返回 {response, model, tokens, cost, steps}（steps 是工具调用日志，给人看 agent 干了啥）。"""
    client, model_name, price_key = _get_tool_client()
    if not client:
        return {"response": "[错误] 工具执行需要 DeepSeek / 通义千问 / OpenAI 的 API Key（支持函数调用）。请到设置页配置。",
                "model": "none", "tokens": 0, "cost": 0, "steps": []}

    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    prices = PRICE_TABLE.get(price_key, PRICE_TABLE["deepseek"])
    total_tokens, total_cost, steps = 0, 0.0, []

    for _ in range(max_steps):
        try:
            resp = await client.chat.completions.create(
                model=model_name, messages=messages, tools=TOOL_SCHEMAS,
                tool_choice="auto", max_tokens=4096,
            )
        except Exception as e:
            return {"response": f"[错误] 模型调用失败: {e}", "model": model_name, "tokens": total_tokens, "cost": round(total_cost, 6), "steps": steps}

        if resp.usage:
            total_tokens += resp.usage.total_tokens
            total_cost += resp.usage.prompt_tokens * prices["input"] + resp.usage.completion_tokens * prices["output"]

        msg = resp.choices[0].message
        if not msg.tool_calls:
            # 没有再调工具，说明任务完成
            return {"response": _sanitize_output(msg.content) or "（无输出）", "model": f"{model_name}(agent)",
                    "tokens": total_tokens, "cost": round(total_cost, 6), "steps": steps}

        # 记录并执行工具调用
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            # 实时回调：告诉外部"我现在要调这个工具"
            if on_step:
                try:
                    on_step({"phase": "calling", "tool": tc.function.name, "args": args, "step": len(steps) + 1})
                except Exception:
                    pass
            result = await _dispatch_tool(tc.function.name, args, project_id)
            steps.append({"tool": tc.function.name, "args": args, "result": result[:500]})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            if on_step:
                try:
                    on_step({"phase": "done", "tool": tc.function.name, "step": len(steps)})
                except Exception:
                    pass

    # 达到步数上限，再让模型基于已有信息给个总结
    messages.append({"role": "user", "content": "已达到工具调用上限，请基于目前掌握的信息给出最终结果。"})
    try:
        resp = await client.chat.completions.create(model=model_name, messages=messages, max_tokens=4096)
        if resp.usage:
            total_tokens += resp.usage.total_tokens
            total_cost += resp.usage.prompt_tokens * prices["input"] + resp.usage.completion_tokens * prices["output"]
        final = _sanitize_output(resp.choices[0].message.content) or "（无输出）"
    except Exception as e:
        final = f"（达到步数上限，且总结失败: {e}）"
    return {"response": final, "model": f"{model_name}(agent)", "tokens": total_tokens, "cost": round(total_cost, 6), "steps": steps}
