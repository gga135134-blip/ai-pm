import aiosqlite
from app.config import DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    owner TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_task_id TEXT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    assignee TEXT DEFAULT '',
    ai_model TEXT DEFAULT 'auto',
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 3,
    result TEXT DEFAULT '',
    progress INTEGER DEFAULT 0,
    needs_human BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    model TEXT NOT NULL,
    prompt TEXT DEFAULT '',
    response TEXT DEFAULT '',
    tokens_used INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    status TEXT DEFAULT 'running',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_id TEXT,
    channel TEXT DEFAULT 'system',
    content TEXT DEFAULT '',
    direction TEXT DEFAULT 'out',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    author TEXT DEFAULT '',
    project_id TEXT,
    task_id TEXT,
    tags TEXT DEFAULT '',
    source_type TEXT DEFAULT 'manual',
    is_pinned BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    context TEXT DEFAULT '',
    decision TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    made_by TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS folders (
    path TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    amount REAL DEFAULT 0,
    category TEXT DEFAULT '其他',
    note TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    size_bytes INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_points (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    chapter TEXT DEFAULT '',
    title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    type TEXT DEFAULT '[]',
    importance INTEGER DEFAULT 3,
    memory_method TEXT DEFAULT '',
    related TEXT DEFAULT '[]',
    source_refs TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS study_archetypes (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    stem TEXT DEFAULT '',
    solution_logic TEXT DEFAULT '',
    knowledge_points TEXT DEFAULT '[]',
    variants TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS study_questions (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    source TEXT DEFAULT '',
    qtype TEXT DEFAULT '单选',
    stem TEXT DEFAULT '',
    options TEXT DEFAULT '[]',
    answer TEXT DEFAULT '',
    maps_to TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS study_review (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    subject TEXT DEFAULT '',
    stage INTEGER DEFAULT 0,
    due_date DATE,
    last_result TEXT DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_records (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    subject TEXT DEFAULT '',
    action TEXT DEFAULT '',
    plan_date DATE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_plan (
    id TEXT PRIMARY KEY,
    plan_date DATE NOT NULL,
    items TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    daily_minutes INTEGER DEFAULT 150,
    rest_weekday INTEGER DEFAULT 6,
    subject_weights TEXT DEFAULT '{"建筑实务":0.32,"管理":0.32,"法规":0.20,"经济":0.16}',
    exam_date DATE DEFAULT '2026-09-12',
    sprint_date DATE DEFAULT '2026-08-15',
    daily_new_target INTEGER DEFAULT 150,
    reminder_hour INTEGER DEFAULT 8,
    reminder_last_sent DATE DEFAULT NULL,
    reminded_noon DATE DEFAULT NULL,
    reminded_evening DATE DEFAULT NULL,
    reminded_night DATE DEFAULT NULL
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


MIGRATIONS = [
    "ALTER TABLE projects ADD COLUMN budget REAL DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN revenue REAL DEFAULT 0",
    "ALTER TABLE notes ADD COLUMN source_type TEXT DEFAULT 'manual'",
    "ALTER TABLE notes ADD COLUMN folder TEXT DEFAULT ''",
    "ALTER TABLE notes ADD COLUMN deleted_at TEXT DEFAULT NULL",
    "ALTER TABLE notes ADD COLUMN image_path TEXT DEFAULT ''",
    "ALTER TABLE projects ADD COLUMN automation_level TEXT DEFAULT 'manual'",
    "ALTER TABLE projects ADD COLUMN ai_budget REAL DEFAULT 0",
    "ALTER TABLE projects ADD COLUMN code TEXT DEFAULT ''",
    "ALTER TABLE notes ADD COLUMN is_core BOOLEAN DEFAULT 0",
    "ALTER TABLE notes ADD COLUMN external_id TEXT DEFAULT ''",
    "ALTER TABLE notes ADD COLUMN share_token TEXT DEFAULT NULL",
    "ALTER TABLE study_settings ADD COLUMN reminder_hour INTEGER DEFAULT 8",
    "ALTER TABLE study_settings ADD COLUMN reminder_last_sent DATE DEFAULT NULL",
    "ALTER TABLE study_settings ADD COLUMN reminded_noon DATE DEFAULT NULL",
    "ALTER TABLE study_settings ADD COLUMN reminded_evening DATE DEFAULT NULL",
    "ALTER TABLE study_settings ADD COLUMN reminded_night DATE DEFAULT NULL",
]


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                await db.execute(sql)
            except Exception:
                pass  # column already exists
        await db.execute(
            "INSERT OR IGNORE INTO study_settings (id) VALUES (1)"
        )
        await db.commit()
    finally:
        await db.close()
