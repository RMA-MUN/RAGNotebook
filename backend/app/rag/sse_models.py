import json
from dataclasses import asdict, dataclass

EVENT_RESPONSE = "response"
EVENT_ERROR = "error"
EVENT_DONE = "done"


@dataclass
class SSEEvent:
    event_type: str
    message: str
    total_files: int = 0
    file_index: int | None = None
    filename: str | None = None
    step: str | None = None
    progress: int = 0
    success_count: int = 0
    failed_count: int = 0
    slice_success_count: int = 0
    error_message: str | None = None
    chunk_count: int | None = None

    def to_sse(self) -> str:
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class SliceResult:
    """切片结果数据结构"""

    def __init__(self):
        self.file_index: int = 0
        self.filename: str = ""
        self.documents: list = []
        self.md5: str = ""
        self.success: bool = False
        self.error: str | None = None
        self.chunk_count: int = 0

    @classmethod
    def success_result(cls, file_index: int, filename: str, documents: list, md5: str) -> 'SliceResult':
        result = cls()
        result.file_index = file_index
        result.filename = filename
        result.documents = documents
        result.md5 = md5
        result.success = True
        result.chunk_count = len(documents)
        return result

    @classmethod
    def error_result(cls, file_index: int, filename: str, error: str) -> 'SliceResult':
        result = cls()
        result.file_index = file_index
        result.filename = filename
        result.success = False
        result.error = error
        return result

    def to_dict(self) -> dict:
        return {
            'file_index': self.file_index,
            'filename': self.filename,
            'documents': self.documents,
            'md5': self.md5,
            'success': self.success,
            'error': self.error,
            'chunk_count': self.chunk_count
        }
