"""Unit tests for app.utils.image_extractor.

True image extraction needs a real PDF + PyMuPDF; here we test the pure
path/mapping logic and the error paths of the extraction function.
"""
import os

import pytest

import app.utils.image_extractor as image_extractor
from app.utils.image_extractor import (
    delete_image_directory,
    delete_user_all_images,
    extract_images_from_pdf,
    get_image_storage_dir,
)


@pytest.fixture
def fake_data_dir(tmp_path, monkeypatch):
    """Point the module's data root at a tmp dir."""
    monkeypatch.setattr(image_extractor, "get_data_path", lambda: str(tmp_path))
    return tmp_path


def test_get_image_storage_dir_constructs_and_creates_path(fake_data_dir):
    storage = get_image_storage_dir("user-1", "abc123")
    expected = os.path.join(str(fake_data_dir), "extracted_images", "user-1", "abc123")
    assert storage == expected
    assert os.path.isdir(storage)
    assert os.path.isdir(os.path.join(str(fake_data_dir), "extracted_images", "user-1"))


def test_get_image_storage_dir_is_idempotent(fake_data_dir):
    first = get_image_storage_dir("u", "m")
    second = get_image_storage_dir("u", "m")
    assert first == second
    assert os.path.isdir(first)


def test_get_image_storage_dir_separates_users_and_md5(fake_data_dir):
    a = get_image_storage_dir("user-a", "md5-a")
    b = get_image_storage_dir("user-b", "md5-a")
    c = get_image_storage_dir("user-a", "md5-b")
    assert a != b != c
    assert a.startswith(os.path.join(str(fake_data_dir), "extracted_images", "user-a"))
    assert b.startswith(os.path.join(str(fake_data_dir), "extracted_images", "user-b"))
    assert c.endswith(os.path.join("user-a", "md5-b"))


def test_delete_image_directory_removes_dir(fake_data_dir):
    storage = get_image_storage_dir("user-1", "md5-1")
    marker = os.path.join(storage, "p0_i0.png")
    open(marker, "w").close()
    assert os.path.exists(marker)

    assert delete_image_directory("user-1", "md5-1") is True
    assert not os.path.exists(storage)
    # deleting again -> False
    assert delete_image_directory("user-1", "md5-1") is False


def test_delete_image_directory_missing_returns_false(fake_data_dir):
    assert delete_image_directory("ghost-user", "ghost-md5") is False


def test_delete_user_all_images_removes_all_md5_dirs(fake_data_dir):
    d1 = get_image_storage_dir("user-9", "m1")
    d2 = get_image_storage_dir("user-9", "m2")
    assert os.path.isdir(d1) and os.path.isdir(d2)

    assert delete_user_all_images("user-9") is True
    assert not os.path.exists(os.path.dirname(d1))  # user dir gone
    assert delete_user_all_images("user-9") is False


def test_extract_images_from_pdf_missing_file_returns_empty(fake_data_dir):
    missing = os.path.join(str(fake_data_dir), "no-such.pdf")
    assert extract_images_from_pdf(missing, "user-1", "md5-x") == {}


def test_extract_images_from_pdf_relative_missing_returns_empty(fake_data_dir):
    # relative paths are resolved against the project root; this one won't exist
    assert extract_images_from_pdf("no/such/file.pdf", "user-1", "md5-x") == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))