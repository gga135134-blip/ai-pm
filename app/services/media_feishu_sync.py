"""飞书 → media_metrics 同步（媒体层）。

读飞书表 → 按 post_url(标题兜底) 匹配 media_publish → 写 metrics 快照。
匹配不上的持久化进 media_feishu_unmatched 让用户手动补。
"""
import json
import re
import uuid
from datetime import date

from app.services.feishu_client import list_bitable_records
from app.services.media_metrics import normalize_metrics, METRIC_FIELDS

_PUNCT = re.compile(r"[\s　\W_]+", re.UNICODE)


def norm_title(s: str) -> str:
    """归一化标题用于模糊匹配：去空白/标点/emoji，转小写。"""
    if not s:
        return ""
    return _PUNCT.sub("", str(s)).lower()


def _cell(fields: dict, col_name: str):
    """从飞书 fields 取一列的值。飞书文本列可能是 [{'text':..}] 结构，做个兼容。"""
    v = fields.get(col_name)
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v[0].get("text", "")
    return v


def map_feishu_row(fields: dict, field_map: dict) -> dict | None:
    """把一行飞书 fields 按映射提取。无 post_url 且无 title 返回 None。"""
    fm = (field_map or {}).get("fields") or {}
    post_url = str(_cell(fields, fm.get("post_url", "")) or "").strip()
    title = str(_cell(fields, fm.get("title", "")) or "").strip()
    if not post_url and not title:
        return None
    raw = {}
    missing = []
    for f in METRIC_FIELDS:
        col = fm.get(f)
        val = _cell(fields, col) if col else None
        if col and val not in (None, ""):
            raw[f] = val
        else:
            missing.append(f)  # 飞书没映射或没给值 → 标待手填
    return {"post_url": post_url, "title": title,
            "metrics": normalize_metrics(raw), "missing_fields": missing}


async def _load_field_map():
    import app.api.settings as st
    return st.load_settings().get("feishu_media_map") or {}


async def _write_feishu_snapshot(db, publish_id, metrics, missing_fields):
    """当天已有 feishu 快照则更新，否则插入。带 missing_fields。"""
    today = date.today().isoformat()
    cur = await db.execute(
        "SELECT id FROM media_metrics WHERE publish_id=? AND collected_by='feishu' "
        "AND date(snapshot_at)=?", (publish_id, today))
    existing = await cur.fetchone()
    mf = json.dumps(missing_fields, ensure_ascii=False)
    if existing:
        await db.execute(
            "UPDATE media_metrics SET views=?,likes=?,comments=?,shares=?,"
            "new_fans=?,missing_fields=?,snapshot_at=CURRENT_TIMESTAMP WHERE id=?",
            (metrics["views"], metrics["likes"], metrics["comments"],
             metrics["shares"], metrics["new_fans"], mf, existing["id"]))
    else:
        await db.execute(
            "INSERT INTO media_metrics (id,publish_id,views,likes,comments,"
            "shares,new_fans,collected_by,missing_fields) "
            "VALUES (?,?,?,?,?,?,?,'feishu',?)",
            (str(uuid.uuid4()), publish_id, metrics["views"], metrics["likes"],
             metrics["comments"], metrics["shares"], metrics["new_fans"], mf))


async def _upsert_unmatched(db, post_url, title, metrics):
    key = post_url or title
    cur = await db.execute(
        "SELECT id FROM media_feishu_unmatched WHERE (post_url=? AND post_url<>'') "
        "OR (post_url='' AND title=?)", (post_url, title))
    row = await cur.fetchone()
    raw = json.dumps(metrics, ensure_ascii=False)
    if row:
        await db.execute("UPDATE media_feishu_unmatched SET raw_metrics=?,"
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (raw, row["id"]))
    else:
        await db.execute(
            "INSERT INTO media_feishu_unmatched (id,post_url,title,raw_metrics) "
            "VALUES (?,?,?,?)", (str(uuid.uuid4()), post_url, title, raw))


async def sync_from_feishu(db, records=None) -> dict:
    """主同步。records 为 None 时真调飞书；测试可注入假数据。"""
    field_map = await _load_field_map()
    if not field_map.get("fields"):
        return {"ok": False, "error": "飞书字段映射未配置", "synced": 0,
                "updated": 0, "unmatched": 0, "suspected": 0}
    if records is None:
        try:
            records = await list_bitable_records(
                field_map.get("app_token"), field_map.get("table_id"))
        except Exception as e:
            return {"ok": False, "error": f"读飞书失败: {e}", "synced": 0,
                    "updated": 0, "unmatched": 0, "suspected": 0}

    # 建 url→publish、title→publish 索引
    cur = await db.execute("SELECT id,post_url,content_id FROM media_publish "
                           "WHERE post_url<>''")
    by_url = {}
    for r in await cur.fetchall():
        by_url[r["post_url"].strip()] = r["id"]
    cur = await db.execute(
        "SELECT p.id pid, c.title FROM media_publish p "
        "JOIN media_content c ON c.id=p.content_id")
    by_title = {}
    for r in await cur.fetchall():
        by_title.setdefault(norm_title(r["title"]), r["pid"])

    synced = suspected = unmatched = 0
    for rec in records:
        mapped = map_feishu_row(rec.get("fields") or {}, field_map)
        if not mapped:
            continue
        pubid = by_url.get(mapped["post_url"]) if mapped["post_url"] else None
        if not pubid and mapped["title"]:
            pubid = by_title.get(norm_title(mapped["title"]))
            if pubid:
                suspected += 1  # 靠标题匹配，标疑似
        if pubid:
            await _write_feishu_snapshot(db, pubid, mapped["metrics"],
                                         mapped["missing_fields"])
            synced += 1
        else:
            await _upsert_unmatched(db, mapped["post_url"], mapped["title"],
                                    mapped["metrics"])
            unmatched += 1
    await db.commit()
    return {"ok": True, "synced": synced, "updated": 0, "unmatched": unmatched,
            "suspected": suspected, "error": ""}
