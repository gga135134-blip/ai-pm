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

CREATE TABLE IF NOT EXISTS media_persona (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    one_liner TEXT DEFAULT '',
    current_phase TEXT DEFAULT '冷启动',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_persona_trait (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    dimension TEXT DEFAULT 'positioning',
    content TEXT DEFAULT '',
    brief TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    source_content_id TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    confidence INTEGER DEFAULT 3,
    phase_tag TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_account (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_name TEXT DEFAULT '',
    account_url TEXT DEFAULT '',
    fans_count INTEGER DEFAULT 0,
    platform_note TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_topic (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    title TEXT NOT NULL,
    puzzle TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    reason TEXT DEFAULT '',
    angle TEXT DEFAULT '',
    heat INTEGER DEFAULT 3,
    fit_score INTEGER DEFAULT 3,
    decision_score REAL DEFAULT 0,
    decision_report TEXT DEFAULT '',
    related_trait_ids TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pool',
    adopted_content_id TEXT DEFAULT '',
    rejected_reason TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_content (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    title TEXT NOT NULL,
    puzzle TEXT DEFAULT '',
    stage TEXT DEFAULT 'idea',
    idea_source TEXT DEFAULT 'manual',
    idea_reason TEXT DEFAULT '',
    script TEXT DEFAULT '',
    edit_note TEXT DEFAULT '',
    cover_idea TEXT DEFAULT '',
    used_material_ids TEXT DEFAULT '[]',
    used_playbook_ids TEXT DEFAULT '[]',
    topic_fingerprint TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    archived_status TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_publish (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    publish_text TEXT DEFAULT '',
    published_at DATETIME,
    post_url TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    FOREIGN KEY (content_id) REFERENCES media_content(id),
    FOREIGN KEY (account_id) REFERENCES media_account(id)
);

CREATE TABLE IF NOT EXISTS media_metrics (
    id TEXT PRIMARY KEY,
    publish_id TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    new_fans INTEGER DEFAULT 0,
    collected_by TEXT DEFAULT 'manual',
    snapshot_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (publish_id) REFERENCES media_publish(id)
);

CREATE TABLE IF NOT EXISTS media_review (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    scope TEXT DEFAULT 'overall',
    account_id TEXT DEFAULT '',
    what_worked TEXT DEFAULT '',
    what_failed TEXT DEFAULT '',
    next_action TEXT DEFAULT '',
    proposed_traits TEXT DEFAULT '[]',
    generated_by TEXT DEFAULT 'ai',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_case (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    content_id TEXT NOT NULL,
    case_type TEXT DEFAULT 'normal',
    threshold_basis TEXT DEFAULT '',
    topic_factor TEXT DEFAULT '',
    hook_factor TEXT DEFAULT '',
    structure_factor TEXT DEFAULT '',
    material_factor TEXT DEFAULT '',
    emotion_factor TEXT DEFAULT '',
    platform_factor TEXT DEFAULT '',
    external_factor TEXT DEFAULT '',
    replicable INTEGER DEFAULT 3,
    conclusion TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id),
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_injection_log (
    id TEXT PRIMARY KEY,
    content_id TEXT DEFAULT '',
    ai_type TEXT NOT NULL,
    injected_asset_ids TEXT DEFAULT '[]',
    token_count INTEGER DEFAULT 0,
    output_quality REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_feishu_unmatched (
    id TEXT PRIMARY KEY,
    post_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    raw_metrics TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_evidence (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    item TEXT DEFAULT '',
    item_type TEXT DEFAULT 'experience',
    source TEXT DEFAULT 'interview',
    from_material_id TEXT DEFAULT '',
    promoted_to_material_id TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_angle (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    angle TEXT DEFAULT '',
    rationale TEXT DEFAULT '',
    is_selected INTEGER DEFAULT 0,
    status TEXT DEFAULT 'candidate',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_draft_review (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    reviewed_draft TEXT DEFAULT '',
    reviewer_strategy TEXT DEFAULT 'layered',
    reviewer_model TEXT DEFAULT '',
    fact_flags TEXT DEFAULT '[]',
    persona_flags TEXT DEFAULT '[]',
    platform_flags TEXT DEFAULT '[]',
    gap_flags TEXT DEFAULT '[]',
    risk_flags TEXT DEFAULT '[]',
    score INTEGER DEFAULT 3,
    verdict TEXT DEFAULT 'pass',
    notes TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES media_content(id)
);

CREATE TABLE IF NOT EXISTS media_material (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    type TEXT DEFAULT 'story',
    title TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    brief TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    usable_scene TEXT DEFAULT '',
    audience_hit TEXT DEFAULT '',
    used_in TEXT DEFAULT '[]',
    use_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_audience (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    segment TEXT DEFAULT '',
    who TEXT DEFAULT '',
    anxiety TEXT DEFAULT '',
    desire TEXT DEFAULT '',
    objection TEXT DEFAULT '',
    language TEXT DEFAULT '',
    pay_willingness INTEGER DEFAULT 3,
    pay_scene TEXT DEFAULT '',
    pay_ceiling TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    confidence INTEGER DEFAULT 3,
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
);

CREATE TABLE IF NOT EXISTS media_anchor (
    id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    type TEXT DEFAULT 'service',
    value_prop TEXT DEFAULT '',
    price_band TEXT DEFAULT '',
    path TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'validating',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (persona_id) REFERENCES media_persona(id)
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
    "ALTER TABLE media_metrics ADD COLUMN missing_fields TEXT DEFAULT '[]'",
    "ALTER TABLE media_feishu_unmatched ADD COLUMN missing_fields TEXT DEFAULT '[]'",
    "ALTER TABLE media_content ADD COLUMN authoring_stage TEXT DEFAULT 'none'",
    "ALTER TABLE media_content ADD COLUMN brief TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN evidence_gap TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN selected_angle_id TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN ai_draft TEXT DEFAULT ''",
    "ALTER TABLE media_content ADD COLUMN revision_count INTEGER DEFAULT 0",
    "ALTER TABLE media_content ADD COLUMN finalized_at DATETIME",
    "ALTER TABLE media_material ADD COLUMN source TEXT DEFAULT ''",
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
