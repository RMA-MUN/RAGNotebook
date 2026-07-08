"""
阿里云百炼 Tongyi 模型的 JSON 标准化补丁。
修复 Qwen3 模型返回非标准 JSON 格式的函数参数问题。
"""
import json
import re
from typing import Any


def normalize_function_arguments(arguments: Any) -> str:
    """
    标准化模型返回的函数参数，确保是合法 JSON。

    Qwen3 模型有时会返回：
    1. 不完整的 JSON（缺少闭合括号）
    2. 单引号而非双引号
    3. 带有额外文本的 JSON
    4. Python 字典格式（True/False/null）
    """
    if not arguments:
        return "{}"

    # 如果已经是 dict，直接序列化
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

    # 1. 提取 JSON 对象（找到第一个 { 和最后一个 }）
    json_match = re.search(r'\{.*\}', fixed, re.DOTALL)
    if json_match:
        fixed = json_match.group()

    # 2. 替换单引号为双引号
    fixed = re.sub(r"(?<!\\)'", '"', fixed)

    # 3. 修复 Python 布尔值和 None
    fixed = fixed.replace('True', 'true')
    fixed = fixed.replace('False', 'false')
    fixed = fixed.replace('None', 'null')

    # 4. 修复不完整的 JSON（添加缺失的闭合括号）
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

    # 6. 最后尝试：提取键值对重新构建
    return _extract_and_rebuild_json(arguments)


def _extract_and_rebuild_json(text: str) -> str:
    """从非结构化文本中提取键值对并重建 JSON。"""
    result = {}

    # 匹配 key: value 或 key="value" 模式
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


def patch_tongyi_model(model):
    """
    给 ChatTongyi 模型打补丁，在流式输出时标准化函数参数。
    """
    original_astream = model.astream

    async def patched_astream(*args, **kwargs):
        async for chunk in original_astream(*args, **kwargs):
            # 标准化工具调用的函数参数
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                for tool_call in chunk.tool_calls:
                    if isinstance(tool_call, dict):
                        func = tool_call.get('function', {})
                        if 'arguments' in func:
                            func['arguments'] = normalize_function_arguments(func['arguments'])
            yield chunk

    model.astream = patched_astream
    return model
