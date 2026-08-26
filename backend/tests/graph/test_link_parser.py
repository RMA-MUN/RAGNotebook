from app.graph.extraction.link_parser import parse_links


def test_basic_link():
    assert parse_links("见 [[机器学习]] 一文") == ["机器学习"]


def test_multiple_and_nested_marker():
    assert parse_links("[[A]] 与 [[B]]") == ["A", "B"]


def test_unclosed_ignored():
    assert parse_links("未闭合的 [[A") == []


def test_chinese_and_spaces():
    assert parse_links("[[大语言模型]] 与 [[Python 编程]]") == ["大语言模型", "Python 编程"]


def test_dedupe_keep_order():
    assert parse_links("[[A]] 和 [[A]] 和 [[B]]") == ["A", "B"]
