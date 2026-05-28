import asyncio
import hashlib
import os
import sys

import aiofiles
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    UnstructuredPDFLoader,
    UnstructuredPowerPointLoader,
)
from langchain_core.documents import Document

from app.core.logger_handler import logger
from app.utils.path_tool import get_abstract_path


class FontBBoxStreamFilter:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        if 'FontBBox from font descriptor' not in data:
            self.stream.write(data)

    def flush(self):
        self.stream.flush()

sys.stderr = FontBBoxStreamFilter(sys.stderr)

async def get_file_md5_hex(file_path: str) -> str:
    """获取文件的md5值"""
    # 处理路径，确保使用绝对路径
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path

    if not os.path.exists(abs_file_path):
        logger.error(f"【md5计算】文件路径 {abs_file_path} 不存在")
        return ""

    if not os.path.isfile(abs_file_path):
        logger.error(f"【md5计算】文件路径 {abs_file_path} 不是文件")
        return ""

    md5_object = hashlib.md5()
    chunk_size = 1024
    try:
        async with aiofiles.open(abs_file_path, "rb") as f:
            while chunk := await f.read(chunk_size):
                md5_object.update(chunk)
    except Exception as e:
        logger.error(f"【md5计算】读取文件 {abs_file_path} 时出错: {e}")
        return ""

    return md5_object.hexdigest()

async def listdir_allowed_type(path: str, allowed_types: tuple[str]) -> tuple:
    """
    获取指定目录下所有允许的文件类型
    :param path: 目录路径
    :param allowed_types: 允许的文件类型元组
    :return: 符合条件的文件路径列表
    """
    # 处理路径，确保使用绝对路径
    abs_path = get_abstract_path(path) if not os.path.isabs(path) else path

    if not os.path.exists(abs_path):
        logger.error(f"【文件列表】目录路径 {abs_path} 不存在")
        return ()

    if not os.path.isdir(abs_path):
        logger.error(f"【文件列表】目录路径 {abs_path} 不是目录")
        return ()

    file_list = []
    for f in await asyncio.to_thread(os.listdir, abs_path):
        if f.endswith(allowed_types):
            file_path = os.path.join(abs_path, f)
            file_list.append(file_path)

    return tuple(file_list)



async def pdf_loader(file_path: str, password: str = None) -> list[Document]:
    """
    加载PDF文件内容（支持包含图片和文字的混合PDF）
    :param file_path: PDF文件路径
    :param password: PDF密码（如果有）
    :return: PDF文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path

    if password:
        loader = PyPDFLoader(abs_file_path, password=password)
        return await asyncio.to_thread(loader.load)

    try:
        loader = UnstructuredPDFLoader(abs_file_path)
        docs = await asyncio.to_thread(loader.load)
        if docs and any(len(doc.page_content.strip()) > 0 for doc in docs):
            return docs
    except Exception as e:
        logger.warning(f"【PDF加载】UnstructuredPDFLoader失败，尝试PyPDFLoader: {e}")

    loader = PyPDFLoader(abs_file_path)
    return await asyncio.to_thread(loader.load)


async def txt_loader(file_path: str) -> list[Document]:
    """
    加载TXT文件内容
    :param file_path: TXT文件路径
    :return: TXT文件内容
    """
    # 处理路径，确保使用绝对路径
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path

    # 使用不同的编码加载文件
    encodings = ['utf-8', 'gbk']
    for encoding in encodings:
        try:
            loader = TextLoader(abs_file_path, encoding=encoding)
            return await asyncio.to_thread(loader.load)
        except Exception as e:
            logger.error(f"【文本文件加载】使用编码 {encoding} 加载文件 {abs_file_path} 时出错: {e}")
            continue
    # 所有编码都失败，返回空列表
    return []

async def word_loader(file_path: str) -> list[Document]:
    """
    加载WORD文件内容
    :param file_path: WORD文件路径
    :return: WORD文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    try:
        loader = TextLoader(abs_file_path, encoding='utf-8')
        return await asyncio.to_thread(loader.load)
    except Exception as e:
        logger.error(f"【WORD文件加载】加载文件 {abs_file_path} 时出错: {e}")
        return []

async def markdown_loader(file_path: str) -> list[Document]:
    """
    加载Markdown文件内容
    :param file_path: Markdown文件路径
    :return: Markdown文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    try:
        loader = UnstructuredMarkdownLoader(abs_file_path, mode="single")
        return await asyncio.to_thread(loader.load)
    except Exception as e:
        logger.error(f"【Markdown文件加载】加载文件 {abs_file_path} 时出错: {e}")
        return []


async def ppt_loader(file_path: str) -> list[Document]:
    """
    加载PPT/PPTX文件内容
    :param file_path: PPT文件路径
    :return: PPT文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    try:
        loader = UnstructuredPowerPointLoader(abs_file_path, mode="single")
        return await asyncio.to_thread(loader.load)
    except Exception as e:
        logger.error(f"【PPT文件加载】加载文件 {abs_file_path} 时出错: {e}")
        return []


def get_file_md5_hex_sync(file_path: str) -> str:
    """同步获取文件的md5值（用于多线程场景）"""
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path

    if not os.path.exists(abs_file_path):
        logger.error(f"【md5计算】文件路径 {abs_file_path} 不存在")
        return ""

    if not os.path.isfile(abs_file_path):
        logger.error(f"【md5计算】文件路径 {abs_file_path} 不是文件")
        return ""

    md5_object = hashlib.md5()
    chunk_size = 1024
    try:
        with open(abs_file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                md5_object.update(chunk)
    except Exception as e:
        logger.error(f"【md5计算】读取文件 {abs_file_path} 时出错: {e}")
        return ""

    return md5_object.hexdigest()


def pdf_loader_sync(file_path: str, password: str = None) -> list[Document]:
    """
    同步加载PDF文件内容（用于多线程场景，支持包含图片和文字的混合PDF）
    :param file_path: PDF文件路径
    :param password: PDF密码（如果有）
    :return: PDF文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path

    if password:
        loader = PyPDFLoader(abs_file_path, password=password)
        return loader.load()

    try:
        loader = UnstructuredPDFLoader(abs_file_path)
        docs = loader.load()
        if docs and any(len(doc.page_content.strip()) > 0 for doc in docs):
            return docs
    except Exception as e:
        logger.warning(f"【PDF加载】UnstructuredPDFLoader失败，尝试PyPDFLoader: {e}")

    loader = PyPDFLoader(abs_file_path)
    return loader.load()


def txt_loader_sync(file_path: str) -> list[Document]:
    """
    同步加载TXT文件内容（用于多线程场景）
    :param file_path: TXT文件路径
    :return: TXT文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path

    encodings = ['utf-8', 'gbk']
    for encoding in encodings:
        try:
            loader = TextLoader(abs_file_path, encoding=encoding)
            return loader.load()
        except Exception as e:
            logger.error(f"【文本文件加载】使用编码 {encoding} 加载文件 {abs_file_path} 时出错: {e}")
            continue
    return []


def word_loader_sync(file_path: str) -> list[Document]:
    """
    同步加载WORD文件内容（用于多线程场景）
    :param file_path: WORD文件路径
    :return: WORD文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    try:
        loader = TextLoader(abs_file_path, encoding='utf-8')
        return loader.load()
    except Exception as e:
        logger.error(f"【WORD文件加载】加载文件 {abs_file_path} 时出错: {e}")
        return []


def markdown_loader_sync(file_path: str) -> list[Document]:
    """
    同步加载Markdown文件内容（用于多线程场景）
    :param file_path: Markdown文件路径
    :return: Markdown文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    try:
        loader = UnstructuredMarkdownLoader(abs_file_path, mode="single")
        return loader.load()
    except Exception as e:
        logger.error(f"【Markdown文件加载】加载文件 {abs_file_path} 时出错: {e}")
        return []


def ppt_loader_sync(file_path: str) -> list[Document]:
    """
    同步加载PPT/PPTX文件内容（用于多线程场景）
    :param file_path: PPT文件路径
    :return: PPT文件内容
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    try:
        loader = UnstructuredPowerPointLoader(abs_file_path, mode="single")
        return loader.load()
    except Exception as e:
        logger.error(f"【PPT文件加载】加载文件 {abs_file_path} 时出错: {e}")
        return []
