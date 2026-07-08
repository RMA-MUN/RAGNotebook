"""
阿里云百炼 Tongyi 模型的 JSON 标准化补丁。
修复 Qwen3 模型返回非标准 JSON 格式的函数参数问题。
"""
import json
import re
from typing import Any, AsyncIterator

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import AIMessageChunk


def normalize_function_arguments(arguments: Any) -> str:
    """
    标准化模型返回的函数参数，确保是合法 JSON。
    """
    if not arguments:
        return "{}"

    if isinstance(arguments, dict):
        return json.dumps(arguments, ensure_ascii=False)

    if not isinstance(arguments, str):
        return json.dumps(arguments, ensure_ascii=False)

    # 尝试直接解析
    try:
        json.loads(arguments)
        return arguments
    except json.JSONDecodeError:
        pass

    fixed = arguments.strip()

    # 1. 提取 JSON 对象
    json_match = re.search(r'\{.*\}', fixed, re.DOTALL)
    if json_match:
        fixed = json_match.group()

    # 2. 替换单引号为双引号
    fixed = re.sub(r"(?<!\\)'", '"', fixed)

    # 3. 修复 Python 布尔值和 None
    fixed = fixed.replace('True', 'true')
    fixed = fixed.replace('False', 'false')
    fixed = fixed.replace('None', 'null')

    # 4. 修复不完整的 JSON
    open_braces = fixed.count('{') - fixed.count('}')
    open_brackets = fixed.count('[') - fixed.count(']')
    if open_braces > 0:
        fixed += '}' * open_braces
    if open_brackets > 0:
        fixed += ']' * open_brackets

    # 5. 尝试解析
    try:
        parsed = json.loads(fixed)
        return json.dumps(parsed, ensure_ascii=False)
    except json.JSONDecodeError:
        pass

    # 6. 提取键值对重新构建
    return _extract_and_rebuild_json(arguments)


def _extract_and_rebuild_json(text: str) -> str:
    """从非结构化文本中提取键值对并重建 JSON。"""
    result = {}

    patterns = [
        r'(\w+)\s*:\s*"([^"]*)"',
        r"(\w+)\s*:\s*'([^']*)'",
        r'(\w+)\s*:\s*(\d+\.?\d*)',
        r'(\w+)\s*=\s*"([^"]*)"',
        r"(\w+)\s*=\s*'([^']*)'",
        r'(\w+)\s*=\s*(\d+\.?\d*)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text)
        for key, value in matches:
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass
            result[key] = value

    if result:
        return json.dumps(result, ensure_ascii=False)
    return "{}"


def _normalize_chunk(chunk: AIMessageChunk) -> AIMessageChunk:
    """标准化 AIMessageChunk 中的工具调用参数。"""
    if not hasattr(chunk, 'tool_calls') or not chunk.tool_calls:
        return chunk

    # 创建新的 tool_calls 列表
    normalized_calls = []
    for tool_call in chunk.tool_calls:
        if isinstance(tool_call, dict):
            func = tool_call.get('function', {})
            if 'arguments' in func:
                original = func['arguments']
                normalized = normalize_function_arguments(original)
                if original != normalized:
                    func['arguments'] = normalized
            normalized_calls.append(tool_call)
        else:
            normalized_calls.append(tool_call)

    # 创建新的 chunk（AIMessageChunk 是不可变的，需要重建）
    return AIMessageChunk(
        content=chunk.content,
        tool_calls=normalized_calls,
        response_metadata=chunk.response_metadata,
        id=chunk.id,
    )


class NormalizedChatTongyi(ChatTongyi):
    """
    包装 ChatTongyi，在流式输出时自动标准化函数参数。
    """

    class Config:
        arbitrary_types_allowed = True

    async def astream(
        self, input, config=None, *, stop=None, **kwargs
    ) -> AsyncIterator[AIMessageChunk]:
        """流式输出时标准化函数参数。"""
        async for chunk in super().astream(input, config, stop=stop, **kwargs):
            yield _normalize_chunk(chunk)


def create_normalized_tongyi(**kwargs) -> NormalizedChatTongyi:
    """创建标准化的 Tongyi 模型。"""
    return NormalizedChatTongyi(**kwargs)
