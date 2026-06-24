import json
from app.services.study_import import point_rows, question_rows


def test_point_rows_maps_and_serializes():
    kj = [{"id":"ZS-经济-0001","subject":"经济","chapter":"一","title":"利息",
           "content":"...","type":["数字"],"importance":4,
           "memory_method":"数字集中归类","related":["ZS-经济-0002"],
           "source_refs":["速记 p1"]}]
    rows = point_rows(kj, "经济")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "ZS-经济-0001" and r["importance"] == 4
    assert json.loads(r["type"]) == ["数字"]          # JSON 字段已序列化为字符串
    assert json.loads(r["related"]) == ["ZS-经济-0002"]


def test_question_rows_maps_qtype_options():
    qj = [{"id":"Q-2026预测-经济-1","subject":"经济","source":"2026预测密卷",
           "type":"单选","stem":"...","options":["A","B"],"answer":"B","maps_to":[]}]
    rows = question_rows(qj, "经济")
    r = rows[0]
    assert r["qtype"] == "单选" and r["answer"] == "B"
    assert json.loads(r["options"]) == ["A","B"]
