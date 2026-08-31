"""Claude 响应取正文：Opus 5 默认开思考，content 里会混 thinking 块。

这是换模型时最容易静默炸的地方——旧代码 resp.content[0].text 在思考块
排第一时会 AttributeError（thinking 块没有 .text）。
"""
from app.services.ai_router import _claude_text, CLAUDE_MODEL, PRICE_TABLE


class _Blk:
    """仿 anthropic SDK 的内容块（只要 type + 对应字段）。"""
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


def test_plain_text_block():
    assert _claude_text([_Blk("text", text="稿子正文")]) == "稿子正文"


def test_thinking_block_first_is_skipped():
    """Opus 5 的典型形状：思考块在前、正文在后。旧写法就是死在这。"""
    content = [_Blk("thinking", thinking="让我想想…"), _Blk("text", text="稿子正文")]
    assert _claude_text(content) == "稿子正文"


def test_multiple_text_blocks_concatenated_in_order():
    content = [_Blk("text", text="上半段"), _Blk("thinking", thinking="嗯"),
               _Blk("text", text="下半段")]
    assert _claude_text(content) == "上半段下半段"


def test_only_thinking_returns_empty_string():
    """全是思考块没正文时返回空串，交给上层的空响应重试逻辑处理，不抛异常。"""
    assert _claude_text([_Blk("thinking", thinking="…")]) == ""


def test_empty_content():
    assert _claude_text([]) == ""


def test_unknown_block_type_ignored():
    """将来 SDK 加了新块类型也不该炸。"""
    assert _claude_text([_Blk("some_future_type", data=1),
                         _Blk("text", text="正文")]) == "正文"


def test_block_without_type_attr_ignored():
    class _Bare:
        pass
    assert _claude_text([_Bare(), _Blk("text", text="正文")]) == "正文"


def test_model_id_has_no_date_suffix():
    """模型 id 是完整的，不能带日期后缀（带了会 404）。"""
    assert CLAUDE_MODEL == "claude-opus-5"


def test_price_table_matches_opus5():
    """Opus 5 官价 $5 / $25 per 1M tokens。价格写错会让费用预估和记账全歪。"""
    p = PRICE_TABLE["claude"]
    assert p["input"] * 1_000_000 == 5.0
    assert p["output"] * 1_000_000 == 25.0
