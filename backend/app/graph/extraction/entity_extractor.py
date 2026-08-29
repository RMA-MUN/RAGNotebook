"""LLM 实体/关系抽取：优先 JSON mode（response_format），失败回落提示词+正则兜底。"""
import json
import re

from app.core.logger_handler import logger
from app.graph.schemas.graph import ExtractResult, ExtractedEntity, ExtractedRelation
from app.utils.prompt_loader import load_prompt


def _extract_json(raw: str) -> str:
    """从 LLM 输出中剥离前言/后缀/markdown 代码块，返回 JSON 子串（复用 note_service 同款思路）。"""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("未找到 JSON 对象")
    return raw[start:end + 1]


def _fallback_extract(text: str, raw: str) -> ExtractResult:
    """正则兜底：极端情况下从原始文本提取 [[...]] 与重复名词，保证不空手而归。"""
    entities = []
    for m in re.finditer(r"\[\[([^\[\]]+)\]\]", text or ""):
        name = m.group(1).strip()
        if name and all(e.name != name for e in entities):
            entities.append(ExtractedEntity(name=name))
    return ExtractResult(entities=entities, relations=[])


async def extract_entities(title: str, content: str, chat_model) -> ExtractResult:
    """调用 LLM 抽取实体与关系，返回结构化 ExtractResult。"""
    prompt = load_prompt("entity_extraction_prompt").replace("{title}", title or "").replace("{content}", (content or "")[:6000])
    result = None
    # 路径一：JSON mode（LangChain 模型均支持 bind(response_format)；hasattr 判断恒真，保留作兼容占位）
    if hasattr(chat_model, "with_config") or True:
        try:
            from langchain_core.messages import HumanMessage
            bound = chat_model.bind(response_format={"type": "json_object"})
            resp = await bound.ainvoke([HumanMessage(content=prompt)])
            raw = resp.content.strip()
            parsed = json.loads(_extract_json(raw))
            result = ExtractResult(
                entities=[ExtractedEntity(**e) for e in parsed.get("entities", [])],
                relations=[ExtractedRelation(**r) for r in parsed.get("relations", [])],
            )
        except Exception as e1:
            logger.warning(f"JSON mode 抽取失败，回落正则路径: {e1}")
            result = None
    # 路径二：正则兜底（含第二次原始调用）
    if result is None:
        try:
            from langchain_core.messages import HumanMessage
            resp = await chat_model.ainvoke([HumanMessage(content=prompt)])
            parsed = json.loads(_extract_json(resp.content.strip()))
            result = ExtractResult(
                entities=[ExtractedEntity(**e) for e in parsed.get("entities", [])],
                relations=[ExtractedRelation(**r) for r in parsed.get("relations", [])],
            )
        except Exception as e2:
            logger.error(f"实体抽取失败 title={title}: {e2}")
            result = _fallback_extract(content or "", "")
    return result