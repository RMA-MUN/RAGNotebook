"""sse_models.py — SSEEvent / SliceResult 数据类测试。"""
import json

from app.rag.sse_models import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_RESPONSE,
    SSEEvent,
    SliceResult,
)


class TestSSEEvent:
    def test_event_constants(self):
        assert EVENT_RESPONSE == "response"
        assert EVENT_ERROR == "error"
        assert EVENT_DONE == "done"

    def test_default_payload_only_includes_non_none(self):
        ev = SSEEvent(event_type="response", message="hello")
        s = ev.to_sse()
        assert s.startswith("event: progress\n")
        assert s.endswith("\n\n")
        data = s.split("data: ", 1)[1].strip()
        payload = json.loads(data)
        # 值为 None 的字段必须被过滤掉
        assert "file_index" not in payload
        assert "filename" not in payload
        assert "step" not in payload
        assert "error_message" not in payload
        assert "chunk_count" not in payload
        # 值为 0 的字段是合法值，必须保留
        assert payload["event_type"] == "response"
        assert payload["message"] == "hello"
        assert payload["total_files"] == 0
        assert payload["progress"] == 0
        assert payload["success_count"] == 0
        assert payload["failed_count"] == 0
        assert payload["slice_success_count"] == 0

    def test_full_payload(self):
        ev = SSEEvent(
            event_type="progress",
            message="正在处理",
            total_files=3,
            file_index=1,
            filename="a.pdf",
            step="loading",
            progress=33,
            success_count=1,
            failed_count=0,
            slice_success_count=2,
            error_message=None,
            chunk_count=5,
        )
        payload = json.loads(ev.to_sse().split("data: ", 1)[1].strip())
        assert payload == {
            "event_type": "progress",
            "message": "正在处理",
            "total_files": 3,
            "file_index": 1,
            "filename": "a.pdf",
            "step": "loading",
            "progress": 33,
            "success_count": 1,
            "failed_count": 0,
            "slice_success_count": 2,
            "chunk_count": 5,
        }

    def test_to_sse_uses_ensure_ascii_false(self):
        ev = SSEEvent(event_type="response", message="你好，世界")
        s = ev.to_sse()
        # 中文原样输出（ensure_ascii=False），且数据区为合法 JSON
        assert "你好，世界" in s
        payload = json.loads(s.split("data: ", 1)[1].strip())
        assert payload["message"] == "你好，世界"


class TestSliceResult:
    def test_default_instance(self):
        result = SliceResult()
        assert result.file_index == 0
        assert result.filename == ""
        assert result.documents == []
        assert result.md5 == ""
        assert result.success is False
        assert result.error is None
        assert result.chunk_count == 0

    def test_success_result(self):
        docs = ["doc1", "doc2"]
        result = SliceResult.success_result(2, "b.md", docs, "md5-abc")
        assert result.file_index == 2
        assert result.filename == "b.md"
        assert result.documents == docs
        assert result.md5 == "md5-abc"
        assert result.success is True
        assert result.error is None
        assert result.chunk_count == 2

    def test_error_result(self):
        result = SliceResult.error_result(3, "c.pdf", "加载失败")
        assert result.file_index == 3
        assert result.filename == "c.pdf"
        assert result.success is False
        assert result.error == "加载失败"
        assert result.documents == []
        assert result.chunk_count == 0

    def test_to_dict_success(self):
        docs = ["doc1"]
        result = SliceResult.success_result(1, "a.txt", docs, "m1")
        assert result.to_dict() == {
            "file_index": 1,
            "filename": "a.txt",
            "documents": docs,
            "md5": "m1",
            "success": True,
            "error": None,
            "chunk_count": 1,
        }

    def test_to_dict_error(self):
        result = SliceResult.error_result(1, "a.txt", "boom")
        assert result.to_dict()["success"] is False
        assert result.to_dict()["error"] == "boom"
        assert result.to_dict()["chunk_count"] == 0