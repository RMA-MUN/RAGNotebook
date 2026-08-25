import re

from app.rag.agentic_rag.schemas import Evidence


_SOURCE_LABELS = {
    "note": "笔记",
    "knowledge_base": "知识库",
    "web": "外部搜索",
}


def _normalized_content(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip().lower()


def merge_evidence(evidences: list[Evidence], limit: int = 8) -> list[Evidence]:
    merged: list[Evidence] = []
    seen_source_ids: set[tuple[str, str]] = set()
    seen_content: set[str] = set()

    for evidence in evidences:
        source_id = (evidence.source, evidence.id)
        if source_id in seen_source_ids:
            continue

        content_key = _normalized_content(evidence.content)
        if content_key and content_key in seen_content:
            continue

        seen_source_ids.add(source_id)
        if content_key:
            seen_content.add(content_key)
        merged.append(evidence)

        if len(merged) >= limit:
            break

    return merged


def format_evidence_context(evidences: list[Evidence], max_chars: int = 6000) -> str:
    parts: list[str] = []
    used = 0

    for index, evidence in enumerate(evidences, start=1):
        label = _SOURCE_LABELS[evidence.source]
        header = f"[{index}] 来源：{label}《{evidence.title}》"
        lines = [header]
        if evidence.url:
            lines.append(f"URL：{evidence.url}")
        lines.append(evidence.content)
        block = "\n".join(lines)
        separator = "\n\n" if parts else ""

        if used + len(separator) + len(block) > max_chars:
            break

        parts.append(block)
        used += len(separator) + len(block)

    return "\n\n".join(parts)
