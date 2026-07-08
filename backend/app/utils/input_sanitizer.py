"""
Input sanitization for RAG content to prevent data poisoning.
"""
import re

# Maximum content length to prevent oversized inputs
MAX_CONTENT_LENGTH = 100_000  # 100KB

# Patterns that may indicate prompt injection attempts
SUSPICIOUS_PATTERNS = [
    re.compile(r'(?i)ignore\s+(all\s+)?previous\s+instructions'),
    re.compile(r'(?i)you\s+are\s+now\s+(a|an)\s+'),
    re.compile(r'(?i)system\s*:\s*'),
    re.compile(r'(?i)<\|(im_start|im_end|system|user|assistant)\|>'),
    re.compile(r'(?i)\[INST\]|\[/INST\]'),
]


def sanitize_content(content: str) -> str:
    """
    Sanitize user-provided content before storing in vector database.

    This function:
    1. Strips excessive whitespace
    2. Truncates to max length

    Note: We intentionally do NOT block content with suspicious patterns because:
    - Users may legitimately discuss AI topics
    - False positives would degrade user experience
    - The real defense is at retrieval time, not storage time

    Args:
        content: Raw user content

    Returns:
        Sanitized content safe for storage
    """
    if not content:
        return content

    # Strip excessive whitespace (newlines, spaces)
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = re.sub(r' {2,}', ' ', content)

    # Truncate if too long
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH]

    return content.strip()
