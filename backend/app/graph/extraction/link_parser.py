"""[[双链]] 语法解析：扫描 Markdown 原文中的 [[目标]]，返回目标文本列表。"""
import re

_LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def parse_links(content: str) -> list[str]:
    """提取所有闭合的 [[目标]]；未闭合/嵌套忽略；去重保序。"""
    seen: set[str] = set()
    result: list[str] = []
    for m in _LINK_RE.finditer(content or ""):
        target = m.group(1).strip()
        if target and target not in seen:
            seen.add(target)
            result.append(target)
    return result