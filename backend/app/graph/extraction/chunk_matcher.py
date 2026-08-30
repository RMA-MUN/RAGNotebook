"""Chunk 构建与 MENTIONS 规则匹配。

按定稿决策，实体定位到 chunk 不再跑 LLM：来源级抽取结果（实体名+别名）
直接在 chunk 文本中做大小写无关子串匹配，零额外 LLM 成本。
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.utils.config import document_config

_splitter: RecursiveCharacterTextSplitter | None = None


def _get_splitter() -> RecursiveCharacterTextSplitter:
    """惰性单例：切分参数统一读 document.yaml，笔记与文档共用同一套切分配置。"""
    global _splitter
    if _splitter is None:
        _splitter = RecursiveCharacterTextSplitter(
            chunk_size=document_config["chunk_size"],
            chunk_overlap=document_config["chunk_overlap"],
            separators=document_config["separators"],
        )
    return _splitter


def build_text_chunks(text: str) -> list[str]:
    """纯文本切 chunk（笔记正文；文档在上传管线已带元数据切片，此处仅兜底）。"""
    if not text or not text.strip():
        return []
    return _get_splitter().split_text(text)


def match_entities_in_chunks(entities, chunk_texts: list[str]) -> dict[str, list[int]]:
    """把抽取出的实体匹配到 chunk 文本。

    entities: list[ExtractedEntity]（用 name + aliases 匹配）。
    返回 {实体名: [命中的 chunk_index,...]}；仅匹配长度 ≥2 的词项，避免单字泛匹配。
    """
    result: dict[str, list[int]] = {}
    lowered = [c.lower() for c in chunk_texts]
    for ent in entities:
        terms = {t.lower() for t in ({ent.name.strip()} | {a.strip() for a in (ent.aliases or [])})
                 if t and len(t) >= 2}
        if not terms:
            continue
        hits = [idx for idx, chunk in enumerate(lowered) if any(t in chunk for t in terms)]
        if hits:
            result[ent.name] = hits
    return result
