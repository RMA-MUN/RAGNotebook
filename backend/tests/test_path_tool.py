"""Unit tests for app.utils.path_tool path helpers."""
import os

from app.utils.path_tool import (
    get_abstract_path,
    get_config_path,
    get_data_path,
    get_project_root,
)


def test_get_project_root_absolute_and_normalized():
    root = get_project_root()
    assert os.path.isabs(root)
    assert os.path.normpath(root) == root
    # root is the backend directory: it contains the app package
    assert os.path.isdir(os.path.join(root, "app"))


def test_get_project_root_matches_module_location():
    # tests/ sits directly inside the backend root
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    expected_root = os.path.dirname(tests_dir)
    assert os.path.normpath(get_project_root()) == os.path.normpath(expected_root)


def test_get_abstract_path_normalizes_relative():
    root = get_project_root()
    p = get_abstract_path("app/config")
    assert os.path.isabs(p)
    assert p == os.path.normpath(os.path.join(root, "app", "config"))


def test_get_abstract_path_handles_dotdot_and_forward_slashes():
    root = get_project_root()
    p = get_abstract_path("data/../app/config")
    assert p == os.path.normpath(os.path.join(root, "app", "config"))
    assert p == get_config_path()


def test_get_data_path_is_under_root_and_exists():
    data = get_data_path()
    assert os.path.isabs(data)
    assert data == get_abstract_path("data")
    assert os.path.normpath(data).startswith(os.path.normpath(get_project_root()))
    assert os.path.isdir(data)


def test_get_config_path_contains_yaml_files():
    cfg = get_config_path()
    assert os.path.isabs(cfg)
    assert cfg == get_abstract_path("app/config")
    assert os.path.isfile(os.path.join(cfg, "prompt.yaml"))
    assert os.path.isfile(os.path.join(cfg, "chroma.yaml"))