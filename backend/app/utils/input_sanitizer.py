"""
RAG 内容输入清洗模块。
"""
import re


def sanitize_content(content: str) -> str:
    """
    在存入向量数据库之前，对用户提供的内容进行清洗。

    该函数执行以下操作：
    1. 去除多余空白字符（连续换行、连续空格）

    Args:
        content: 原始用户内容

    Returns:
        清洗后的内容
    """
    if not content:
        return content

    # 去除多余空白字符（换行、空格）
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = re.sub(r' {2,}', ' ', content)

    return content.strip()