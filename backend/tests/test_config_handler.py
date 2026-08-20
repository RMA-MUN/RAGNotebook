"""Unit tests for app.utils.config_handler.load_config."""
import pytest

from app.utils.config_handler import load_config


def test_load_config_valid_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("name: test\nitems:\n  - a\n  - b\ncount: 2\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg == {"name": "test", "items": ["a", "b"], "count": 2}


def test_load_config_nested_and_typed_values(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("server:\n  port: 8080\n  enabled: true\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg["server"]["port"] == 8080
    assert cfg["server"]["enabled"] is True


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "does_not_exist.yaml"))


def test_load_config_respects_encoding(tmp_path):
    # text saved in utf-8; default load should decode it correctly
    p = tmp_path / "zh.yaml"
    p.write_text("title: 中文标题\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg["title"] == "中文标题"


def test_load_config_real_app_config_files_exist():
    # app.utils.config already loads these at import time; make sure they are
    # parseable through the same code path. agent.yaml is currently empty,
    # so YAML yields None there (documented production behavior).
    from app.utils.path_tool import get_abstract_path

    prompt = load_config(get_abstract_path("app/config/prompt.yaml"))
    chroma = load_config(get_abstract_path("app/config/chroma.yaml"))
    assert isinstance(prompt, dict) and prompt
    assert isinstance(chroma, dict) and chroma
    # empty file -> None, no exception
    assert load_config(get_abstract_path("app/config/agent.yaml")) is None