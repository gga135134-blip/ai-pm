import shutil
import uuid
from datetime import datetime
from pathlib import Path
from app.config import BASE_DIR, DB_PATH
from app.database import get_db

BACKUP_DIR = BASE_DIR / "backups"


async def create_backup() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    filename = f"aipm_backup_{now.strftime('%Y%m%d_%H%M%S')}.db"
    dest = BACKUP_DIR / filename
    shutil.copy2(str(DB_PATH), str(dest))
    size = dest.stat().st_size

    db = await get_db()
    try:
        backup_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO backups (id, filename, size_bytes, created_at) VALUES (?, ?, ?, ?)",
            (backup_id, filename, size, now.isoformat()),
        )
        await db.commit()
    finally:
        await db.close()

    return {"id": backup_id, "filename": filename, "size": size}


async def list_backups() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM backups ORDER BY created_at DESC")
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def cleanup_old_backups(keep: int = 10):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, filename FROM backups ORDER BY created_at DESC")
        all_backups = [dict(row) for row in await cursor.fetchall()]
        to_delete = all_backups[keep:]
        for b in to_delete:
            path = BACKUP_DIR / b["filename"]
            if path.exists():
                path.unlink()
            await db.execute("DELETE FROM backups WHERE id = ?", (b["id"],))
        await db.commit()
    finally:
        await db.close()
