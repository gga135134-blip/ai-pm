from app.services.feishu_client import _parse_records


def test_parse_records_extracts_items_and_page_token():
    payload = {
        "code": 0,
        "data": {
            "items": [
                {"record_id": "r1", "fields": {"视频链接": "http://a", "播放量": "1.2万"}},
                {"record_id": "r2", "fields": {"视频链接": "http://b", "播放量": 3000}},
            ],
            "has_more": True,
            "page_token": "tok123",
        },
    }
    records, page_token = _parse_records(payload)
    assert len(records) == 2
    assert records[0]["fields"]["视频链接"] == "http://a"
    assert page_token == "tok123"


def test_parse_records_no_more_returns_none_token():
    payload = {"code": 0, "data": {"items": [], "has_more": False}}
    records, page_token = _parse_records(payload)
    assert records == []
    assert page_token is None


def test_parse_records_error_code_raises():
    payload = {"code": 1254005, "msg": "table not found", "data": {}}
    try:
        _parse_records(payload)
        assert False, "should raise"
    except RuntimeError as e:
        assert "1254005" in str(e)
