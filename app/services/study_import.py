import json, os

SUBJECTS = ["建筑实务", "管理", "法规", "经济"]


def point_rows(knowledge_json, subject):
    out = []
    for k in knowledge_json:
        out.append({
            "id": k["id"], "subject": k.get("subject", subject),
            "chapter": k.get("chapter", ""), "title": k.get("title", ""),
            "content": k.get("content", ""),
            "type": json.dumps(k.get("type", []), ensure_ascii=False),
            "importance": k.get("importance", 3),
            "memory_method": k.get("memory_method", ""),
            "related": json.dumps(k.get("related", []), ensure_ascii=False),
            "source_refs": json.dumps(k.get("source_refs", []), ensure_ascii=False),
        })
    return out


def archetype_rows(arch_json, subject):
    out = []
    for a in arch_json:
        out.append({
            "id": a["id"], "subject": a.get("subject", subject),
            "stem": a.get("stem", ""), "solution_logic": a.get("solution_logic", ""),
            "knowledge_points": json.dumps(a.get("knowledge_points", []), ensure_ascii=False),
            "variants": json.dumps(a.get("variants", []), ensure_ascii=False),
        })
    return out


def question_rows(q_json, subject):
    out = []
    for q in q_json:
        out.append({
            "id": q["id"], "subject": q.get("subject", subject),
            "source": q.get("source", ""), "qtype": q.get("type", "单选"),
            "stem": q.get("stem", ""),
            "options": json.dumps(q.get("options", []), ensure_ascii=False),
            "answer": q.get("answer", ""),
            "maps_to": json.dumps(q.get("maps_to", []), ensure_ascii=False),
        })
    return out


def _load(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # 源文件损坏/读不出时返回空，避免整次导入崩溃(服务器 git pull→重导流程更稳)
        return []


async def _bulk(db, table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    await db.executemany(sql, [[r[c] for c in cols] for r in rows])


async def import_all(db, source_dir):
    """读 source_dir 下四科 JSON，覆盖式导入 study_points/archetypes/questions。"""
    for t in ("study_points", "study_archetypes", "study_questions"):
        await db.execute(f"DELETE FROM {t}")
    for subj in SUBJECTS:
        await _bulk(db, "study_points",
                    point_rows(_load(os.path.join(source_dir, f"{subj}_knowledge.json")), subj))
        await _bulk(db, "study_archetypes",
                    archetype_rows(_load(os.path.join(source_dir, f"{subj}_archetypes.json")), subj))
        await _bulk(db, "study_questions",
                    question_rows(_load(os.path.join(source_dir, f"{subj}_2026预测密卷.json")), subj))
    await db.commit()
