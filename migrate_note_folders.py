"""一次性脚本：把所有项目笔记中 folder 为空的记录，自动填入项目名作为文件夹。"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

async def main():
    from app.database import get_db
    db = await get_db()
    try:
        # 查出所有有 project_id 但 folder 为空的笔记
        cursor = await db.execute("""
            SELECT n.id, p.name as project_name
            FROM notes n
            JOIN projects p ON p.id = n.project_id
            WHERE (n.folder = '' OR n.folder IS NULL) AND n.deleted_at IS NULL
        """)
        rows = await cursor.fetchall()
        if not rows:
            print("没有需要迁移的笔记。")
            return
        for row in rows:
            await db.execute(
                "UPDATE notes SET folder = ? WHERE id = ?",
                (row["project_name"], row["id"])
            )
        await db.commit()
        print(f"✅ 已迁移 {len(rows)} 篇笔记，统一归入各自项目文件夹。")
    finally:
        await db.close()

asyncio.run(main())
