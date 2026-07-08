import pytest
from app.utils.input_sanitizer import sanitize_content


def test_sanitize_empty_string():
    assert sanitize_content("") == ""


def test_sanitize_none():
    assert sanitize_content(None) is None


def test_sanitize_whitespace_normalization():
    assert sanitize_content("  hello   world  ") == "hello world"


def test_sanitize_newline_normalization():
    assert sanitize_content("hello\n\n\n\nworld") == "hello\n\nworld"


def test_sanitize_strips_leading_trailing_whitespace():
    assert sanitize_content("  hello  ") == "hello"


def test_sanitize_preserves_normal_content():
    content = "This is normal content with proper formatting."
    assert sanitize_content(content) == content


def test_sanitize_large_content_not_truncated():
    long_content = "a" * 150_000
    result = sanitize_content(long_content)
    assert len(result) == 150_000
