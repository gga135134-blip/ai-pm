"""项目知识库的标准文件夹结构。

每个项目固定四个一级子文件夹，归属清晰、人/AI 资料分离：
- 配置：项目"宪法"——核心档、定位、规格、品牌设定（人定，AI 必读不可违背）
- 资料：人提供的原始素材——参考、竞品、上传文件、模板（**只放人给的料，不混 AI 产出**）
- 执行：AI/协作的过程与产出——会议纪要、分析、进度、网站开发（AI 自己看/产的归这里）
- 文档：对外交付物——方案、报告、项目文档

classify / reorg 都引用这里的常量，保证全站一致。
"""
from app.database import get_db

STANDARD_SUBFOLDERS = ["配置", "资料", "执行", "文档"]

# 各类的用途说明，喂给 AI 做自动归类时用
SUBFOLDER_GUIDE = {
    "配置": "项目核心档、定位、规格、品牌设定等需要长期遵守的设定",
    "资料": "人提供的原始素材：参考资料、竞品、上传的文件、模板（只放人给的料）",
    "执行": "执行过程与 AI 产出：会议纪要、分析、进度记录、调研结果、网站开发等",
    "文档": "对外交付物：方案、报告、最终文档",
}

# AI 自动执行写进度笔记的去处（归到「执行」下，绝不进「资料」）
PROGRESS_SUBPATH = "执行/进度"


async def ensure_project_folders(project_name: str):
    """为一个项目建好标准四个子文件夹（配置/资料/执行/文档）。幂等，可重复调用。"""
    if not project_name or not project_name.strip():
        return
    name = project_name.strip().strip("/")
    db = await get_db()
    try:
        for sub in STANDARD_SUBFOLDERS:
            await db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (f"{name}/{sub}",))
        await db.commit()
    finally:
        await db.close()
