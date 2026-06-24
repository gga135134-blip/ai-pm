import datetime
import datetime as _dt, json as _json, uuid as _uuid

INTERVALS = [1, 3, 7, 15, 30]  # 天；stage 为下标 0..4


def next_review(current_stage, passed, today):
    """
    1-3-7 调度。current_stage=-1 表示尚未进复习队列。
    passed=True(自评会/答对): stage 进一档(封顶), due=今天+INTERVALS[新档]。
    passed=False(模糊/不会/答错): stage 重置为 0, due=今天+1(明天)。
    """
    if passed:
        new_stage = min(current_stage + 1, len(INTERVALS) - 1)
    else:
        new_stage = 0
    due = today + datetime.timedelta(days=INTERVALS[new_stage])
    return new_stage, due


def allocate_new_quota(due_count, today, sprint_date, weights, daily_new_target=20):
    """按四科配比分配今日新学名额；复习多则缩、过冲刺日则停。"""
    if today >= sprint_date:
        effective = 0
    else:
        effective = max(0, daily_new_target - due_count)
    quota = {s: int(effective * w) for s, w in weights.items()}
    # 余数补给占比最大的科目，保证总和=effective
    remainder = effective - sum(quota.values())
    if remainder > 0:
        top = max(weights, key=weights.get)
        quota[top] += remainder
    return quota


def build_plan_items(due_reviews, unlearned_by_subject, today, sprint_date,
                     weights, daily_new_target=20):
    """组装今日计划：先全部到期复习，再按各科配额取未学考点作为新学。"""
    items = [{"item_type": r["item_type"], "item_id": r["item_id"],
              "subject": r["subject"], "kind": "review"} for r in due_reviews]
    quota = allocate_new_quota(len(due_reviews), today, sprint_date,
                               weights, daily_new_target)
    for subject, n in quota.items():
        ids = unlearned_by_subject.get(subject, [])
        for pid in ids[:n]:
            items.append({"item_type": "point", "item_id": pid,
                          "subject": subject, "kind": "new"})
    return items


# ── Async DB layer ──────────────────────────────────────────────────────────

PASS_ACTIONS = {"会", "对"}


async def _settings(db):
    cur = await db.execute("SELECT * FROM study_settings WHERE id=1")
    return await cur.fetchone()


async def get_or_build_today_plan(db, today=None):
    today = today or _dt.date.today()
    iso = today.isoformat()
    cur = await db.execute("SELECT * FROM study_plan WHERE plan_date=?", (iso,))
    row = await cur.fetchone()
    if row:
        return _json.loads(row["items"])
    s = await _settings(db)
    weights = _json.loads(s["subject_weights"])
    sprint = _dt.date.fromisoformat(s["sprint_date"])
    # 到期复习
    cur = await db.execute(
        "SELECT item_type,item_id,subject FROM study_review WHERE due_date<=?", (iso,))
    due = [dict(r) for r in await cur.fetchall()]
    # 各科未学考点(不在 study_review 的 point)，按 importance desc
    unlearned = {}
    for subj in weights:
        cur = await db.execute(
            """SELECT id FROM study_points WHERE subject=? AND id NOT IN
               (SELECT item_id FROM study_review WHERE item_type='point')
               ORDER BY importance DESC""", (subj,))
        unlearned[subj] = [r["id"] for r in await cur.fetchall()]
    items = build_plan_items(due, unlearned, today, sprint, weights,
                             s["daily_new_target"])
    await db.execute(
        "INSERT INTO study_plan (id,plan_date,items) VALUES (?,?,?)",
        (str(_uuid.uuid4()), iso, _json.dumps(items, ensure_ascii=False)))
    await db.commit()
    return items


async def record_action(db, item_type, item_id, action, subject=""):
    today = _dt.date.today()
    await db.execute(
        "INSERT INTO study_records (id,item_type,item_id,subject,action,plan_date) "
        "VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), item_type, item_id, subject, action, today.isoformat()))
    passed = action in PASS_ACTIONS
    cur = await db.execute(
        "SELECT * FROM study_review WHERE item_type=? AND item_id=?", (item_type, item_id))
    row = await cur.fetchone()
    cur_stage = row["stage"] if row else -1
    stage, due = next_review(cur_stage, passed, today)
    if row:
        await db.execute(
            "UPDATE study_review SET stage=?,due_date=?,last_result=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (stage, due.isoformat(), action, row["id"]))
    else:
        await db.execute(
            "INSERT INTO study_review (id,item_type,item_id,subject,stage,due_date,last_result) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(_uuid.uuid4()), item_type, item_id, subject, stage, due.isoformat(), action))
    await db.commit()
