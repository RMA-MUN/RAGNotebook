"""md5_store.py — MD5Store 持久化行为测试（存储目录重定向到 tmp_path）。"""
import os

import aiofiles
import pytest

from app.rag.md5_manager.md5_store import MD5Store


@pytest.fixture
def make_store(tmp_path):
    """构造一个存储根目录指向 tmp_path 的 MD5Store。"""

    def _make(user_id_root: str = "md5_root") -> MD5Store:
        store = MD5Store()
        # 重定向存储根目录，避免写入 backend/data
        store.base_dir = str(tmp_path / user_id_root)
        return store

    return _make


@pytest.fixture
def user_id():
    return "user-1"


async def test_save_and_check_md5_hex(make_store, user_id):
    store = make_store()
    # 初始不存在
    assert await store.check_md5_hex("abc123", user_id) is False

    await store.save_md5_hex("abc123", "file.txt", "original.txt", user_id)

    assert await store.check_md5_hex("abc123", user_id) is True
    assert await store.check_md5_hex("other-md5", user_id) is False


async def test_save_and_check_public_user(make_store):
    """user_id 为 None 时写入 public_md5 目录。"""
    store = make_store()
    await store.save_md5_hex("pub-md5", "pub.txt", None, None)
    assert await store.check_md5_hex("pub-md5", None) is True
    assert await store.check_md5_hex("pub-md5", "user-2") is False


async def test_check_md5_hex_accepts_legacy_bare_lines(make_store, user_id):
    """老格式的纯 md5 行（非 JSON）也能被识别。"""
    store = make_store()
    md5_dir = store._get_md5_store_dir(user_id)
    os.makedirs(md5_dir, exist_ok=True)
    md5_path = os.path.join(md5_dir, "md5_hex_store.txt")
    async with aiofiles.open(md5_path, "w", encoding="utf-8") as f:
        await f.write("legacy-md5\n")

    assert await store.check_md5_hex("legacy-md5", user_id) is True


async def test_get_md5_info(make_store, user_id):
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", "orig-a.txt", user_id)

    info = await store.get_md5_info(user_id, "m1")
    assert info is not None
    assert info["md5"] == "m1"
    assert info["filename"] == "a.txt"
    assert info["original_filename"] == "orig-a.txt"
    assert "upload_time" in info

    assert await store.get_md5_info(user_id, "unknown") is None


async def test_get_all_md5_records(make_store, user_id):
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", None, user_id)
    await store.save_md5_hex("m2", "b.txt", None, user_id)

    records = await store.get_all_md5_records(user_id)
    assert len(records) == 2
    assert {r["md5"] for r in records} == {"m1", "m2"}

    # 无记录时返回空列表
    assert await store.get_all_md5_records("other-user") == []


async def test_delete_by_filename(make_store, user_id):
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", "orig-a.txt", user_id)
    await store.save_md5_hex("m2", "b.txt", "orig-b.txt", user_id)

    md5 = await store.delete_by_filename(user_id, "a.txt")
    assert md5 == "m1"

    records = await store.get_all_md5_records(user_id)
    assert len(records) == 1
    assert records[0]["md5"] == "m2"

    # 删除不存在的文件名
    assert await store.delete_by_filename(user_id, "not-exist.txt") is None


async def test_delete_by_filename_matches_filename_key_only(make_store, user_id):
    """filename 键存在时按 filename 匹配；original_filename 不被用于匹配。"""
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", "orig-a.txt", user_id)

    # filename 键存在且匹配 → 可删除
    assert await store.delete_by_filename(user_id, "a.txt") == "m1"


async def test_delete_by_filename_uses_original_filename_when_filename_missing(make_store, user_id):
    """filename 键完全缺失时（兼容旧格式记录）回退到 original_filename 匹配。"""
    import json

    store = make_store()
    md5_dir = store._get_md5_store_dir(user_id)
    os.makedirs(md5_dir, exist_ok=True)
    md5_path = os.path.join(md5_dir, "md5_hex_store.txt")
    # 手工构造不含 filename 键的旧格式记录
    async with aiofiles.open(md5_path, "w", encoding="utf-8") as f:
        await f.write(
            json.dumps({"md5": "m1", "original_filename": "orig-a.txt", "upload_time": "t"}) + "\n"
        )

    md5 = await store.delete_by_filename(user_id, "orig-a.txt")
    assert md5 == "m1"
    # 记录已被删除
    assert await store.get_all_md5_records(user_id) == []


async def test_delete_single_md5(make_store, user_id):
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", None, user_id)
    await store.save_md5_hex("m2", "b.txt", None, user_id)

    assert await store.delete_single_md5(user_id, "m1") is True
    assert await store.delete_single_md5(user_id, "m1") is False  # 已删除

    records = await store.get_all_md5_records(user_id)
    assert [r["md5"] for r in records] == ["m2"]

    # 不存在的 md5 → False
    assert await store.delete_single_md5(user_id, "nope") is False


async def test_delete_user_md5_removes_dir_and_file(make_store, user_id):
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", None, user_id)

    md5_dir = store._get_md5_store_dir(user_id)
    assert os.path.exists(md5_dir)

    await store.delete_user_md5(user_id)
    assert os.path.exists(md5_dir) is False
    assert await store.get_all_md5_records(user_id) == []


async def test_write_md5_records_empty_cleans_up(make_store, user_id):
    store = make_store()
    await store.save_md5_hex("m1", "a.txt", None, user_id)
    md5_path = os.path.join(store._get_md5_store_dir(user_id), "md5_hex_store.txt")
    assert os.path.exists(md5_path)

    await store._write_md5_records(md5_path, [])
    assert os.path.exists(md5_path) is False


async def test_read_md5_records_handles_bad_json_lines(make_store, user_id):
    store = make_store()
    md5_dir = store._get_md5_store_dir(user_id)
    os.makedirs(md5_dir, exist_ok=True)
    md5_path = os.path.join(md5_dir, "md5_hex_store.txt")
    async with aiofiles.open(md5_path, "w", encoding="utf-8") as f:
        await f.write("bare-line-1\n")
        await f.write("{broken json\n")

    records = await store._read_md5_records(user_id)
    # _read_md5_records 返回 (path, records) 元组
    assert isinstance(records, tuple) and len(records) == 2
    records_list = records[1]
    assert records_list[0]["md5"] == "bare-line-1"
    assert records_list[0]["filename"] is None
    assert records_list[1]["md5"] == "{broken json"


async def test_save_md5_hex_sync(tmp_path, user_id):
    store = MD5Store()
    store.base_dir = str(tmp_path / "sync_root")
    store.save_md5_hex_sync("sync-md5", "s.txt", None, user_id)
    assert await store.check_md5_hex("sync-md5", user_id) is True