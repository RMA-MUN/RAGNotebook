import json
import os
from datetime import datetime

import aiofiles
from aiofiles import os as aio_os

from app.core.logger_handler import logger
from app.utils.config import chroma_config
from app.utils.path_tool import get_abstract_path


class MD5Store:
    """MD5存储管理器"""

    def __init__(self):
        self.base_dir = os.path.dirname(get_abstract_path(chroma_config['md5_hex_store']))

    def _get_md5_store_dir(self, user_id: str = None) -> str:
        """
        获取MD5存储目录
        :param user_id: 用户ID，为None时返回公共目录
        :return: MD5存储目录路径
        """
        if user_id:
            return os.path.join(self.base_dir, 'user_md5', user_id)
        else:
            return os.path.join(self.base_dir, 'public_md5')

    async def check_md5_hex(self, md5_for_check: str, user_id: str = None) -> bool:
        """
        异步检查md5
        :param md5_for_check: 要检查的MD5值
        :param user_id: 用户ID，为None时检查公共知识库
        :return: 是否存在
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not await aio_os.path.exists(md5_dir):
            await aio_os.makedirs(md5_dir, exist_ok=True)
            async with aiofiles.open(md5_path, 'w', encoding="utf-8"):
                pass
            return False

        if not await aio_os.path.exists(md5_path):
            async with aiofiles.open(md5_path, 'w', encoding="utf-8"):
                pass
            return False

        try:
            async with aiofiles.open(md5_path, encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('{'):
                        try:
                            data = json.loads(line)
                            if data.get('md5') == md5_for_check:
                                return True
                        except json.JSONDecodeError:
                            if line == md5_for_check:
                                return True
                    else:
                        if line == md5_for_check:
                            return True
            return False
        except Exception as e:
            logger.error(f"【向量数据库】检查MD5时出错: {e}")
            return False

    async def save_md5_hex(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        """
        异步保存md5
        :param md5_hex: 要保存的MD5值
        :param filename: 文件名（可选）
        :param original_filename: 原始文件名（可选）
        :param user_id: 用户ID，为None时保存到公共知识库
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not await aio_os.path.exists(md5_dir):
            await aio_os.makedirs(md5_dir, exist_ok=True)

        data = {
            'md5': md5_hex,
            'filename': filename,
            'original_filename': original_filename,
            'upload_time': datetime.now().isoformat()
        }

        async with aiofiles.open(md5_path, 'a', encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False) + '\n')

    def save_md5_hex_sync(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        """
        同步保存md5（用于多线程场景）
        :param md5_hex: 要保存的MD5值
        :param filename: 文件名（可选）
        :param original_filename: 原始文件名（可选）
        :param user_id: 用户ID，为None时保存到公共知识库
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not os.path.exists(md5_dir):
            os.makedirs(md5_dir, exist_ok=True)

        data = {
            'md5': md5_hex,
            'filename': filename,
            'original_filename': original_filename,
            'upload_time': datetime.now().isoformat()
        }

        with open(md5_path, 'a', encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    async def _read_md5_records(self, user_id: str = None) -> tuple:
        """
        读取用户的MD5记录文件
        :param user_id: 用户ID，为None时读取公共知识库
        :return: (file_path, records列表)，每条记录为dict
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not await aio_os.path.exists(md5_path):
            return md5_path, []

        records = []
        async with aiofiles.open(md5_path, encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('{'):
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        records.append({
                            'md5': line, 'filename': None,
                            'original_filename': None, 'upload_time': None
                        })
                else:
                    records.append({
                        'md5': line, 'filename': None,
                        'original_filename': None, 'upload_time': None
                    })
        return md5_path, records

    async def _write_md5_records(self, md5_path: str, records: list):
        """
        写入MD5记录文件，空列表时自动清理文件及目录
        :param md5_path: 文件路径
        :param records: 记录列表
        """
        if not records:
            md5_dir = os.path.dirname(md5_path)
            if await aio_os.path.exists(md5_path):
                await aio_os.remove(md5_path)
            if await aio_os.path.exists(md5_dir):
                try:
                    await aio_os.rmdir(md5_dir)
                except OSError:
                    pass
            return

        async with aiofiles.open(md5_path, 'w', encoding="utf-8") as f:
            for record in records:
                await f.write(json.dumps(record, ensure_ascii=False) + '\n')

    async def delete_user_md5(self, user_id: str):
        """
        删除用户的整个MD5记录目录
        :param user_id: 用户ID
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')
        if await aio_os.path.exists(md5_path):
            await aio_os.remove(md5_path)
        if await aio_os.path.exists(md5_dir):
            await aio_os.rmdir(md5_dir)
        logger.info(f"【MD5存储】已删除用户 {user_id} 的MD5记录")

    async def delete_by_filename(self, user_id: str, filename: str):
        """
        通过文件名删除MD5记录
        :param user_id: 用户ID
        :param filename: 文件名
        :return: 被删记录的md5值，不存在返回None
        """
        md5_path, records = await self._read_md5_records(user_id)
        if not records:
            return None

        found_md5 = None
        remaining = []
        for record in records:
            record_filename = record.get('filename', record.get('original_filename'))
            if record_filename == filename:
                found_md5 = record.get('md5')
            else:
                remaining.append(record)

        if found_md5 is None:
            return None

        await self._write_md5_records(md5_path, remaining)
        logger.info(f"【MD5存储】已删除用户 {user_id} 的文件 {filename} 的MD5记录")
        return found_md5

    async def delete_single_md5(self, user_id: str, md5_to_delete: str) -> bool:
        """
        删除单个MD5记录
        :param user_id: 用户ID
        :param md5_to_delete: 要删除的MD5值
        :return: 是否成功删除
        """
        md5_path, records = await self._read_md5_records(user_id)
        if not records:
            return False

        remaining = [r for r in records if r.get('md5') != md5_to_delete]
        if len(remaining) == len(records):
            return False

        await self._write_md5_records(md5_path, remaining)
        logger.info(f"【MD5存储】已删除用户 {user_id} 的MD5记录: {md5_to_delete}")
        return True

    async def get_md5_info(self, user_id: str, md5_value: str):
        """
        获取MD5对应的文档信息
        :param user_id: 用户ID
        :param md5_value: MD5值
        :return: MD5信息字典，不存在返回None
        """
        _, records = await self._read_md5_records(user_id)
        for record in records:
            if record.get('md5') == md5_value:
                return record
        return None

    async def get_all_md5_records(self, user_id: str) -> list:
        """
        获取用户的所有MD5记录
        :param user_id: 用户ID
        :return: MD5记录列表
        """
        _, records = await self._read_md5_records(user_id)
        return records
