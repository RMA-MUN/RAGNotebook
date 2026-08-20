"""vision_service.py — VisionService 测试（假视觉模型 + tmp 图片文件）。"""
import base64
import os

import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from app.core.background_init import init_manager
from app.utils.vision_service import VisionService


class FakeVisionModel:
    """假视觉模型：ainvoke/invoke 返回预设文本。"""

    def __init__(self, content):
        self.content = content

    async def ainvoke(self, messages):
        return AIMessage(content=self.content)

    def invoke(self, messages):
        return AIMessage(content=self.content)


def _make_png(path, size=(8, 8)):
    Image.new("RGB", size, (10, 20, 30)).save(path)
    return path


class TestModelResolution:
    def test_returns_injected_model(self):
        model = FakeVisionModel("x")
        svc = VisionService(model=model)
        assert svc._get_model() is model

    def test_raises_without_model(self, monkeypatch):
        monkeypatch.setattr(init_manager, "vision_model", None)
        with pytest.raises(RuntimeError, match="视觉模型尚未初始化完成"):
            VisionService()._get_model()


class TestEncodeImage:
    def test_png_mime_and_base64_roundtrip(self, tmp_path):
        path = _make_png(str(tmp_path / "img.png"))
        svc = VisionService(model=FakeVisionModel("x"))
        img_b64, mime = svc._encode_image(path)
        assert mime == "image/png"
        assert base64.b64decode(img_b64).startswith(b"\x89PNG")

    def test_jpg_mime(self, tmp_path):
        path = _make_png(str(tmp_path / "img.jpg"))
        svc = VisionService(model=FakeVisionModel("x"))
        _, mime = svc._encode_image(path)
        assert mime == "image/jpeg"

    def test_unknown_extension_defaults_png(self, tmp_path):
        # PIL 无法直接保存到未知扩展名 → 复制真实 PNG 字节
        src = _make_png(str(tmp_path / "src.png"))
        weird = tmp_path / "img.weird"
        weird.write_bytes(open(src, "rb").read())
        svc = VisionService(model=FakeVisionModel("x"))
        _, mime = svc._encode_image(str(weird))
        assert mime == "image/png"


class TestPromptBuilding:
    def test_build_prompt_with_existing_text_truncated_to_800(self):
        svc = VisionService(model=FakeVisionModel("x"))
        text = "A" * 1000
        prompt = svc._build_prompt(text)
        assert "A" * 800 in prompt
        assert "A" * 801 not in prompt
        assert "页面已有文本" in prompt

    def test_build_prompt_without_text(self):
        svc = VisionService(model=FakeVisionModel("x"))
        prompt = svc._build_prompt("   ")
        assert "该页没有提取到文本。" in prompt

    def test_build_batch_prompt_includes_refs(self):
        svc = VisionService(model=FakeVisionModel("x"))
        prompt = svc._build_batch_prompt([{"page": 1, "text": "第一页文本"}, {"page": 2, "text": ""}])
        assert "--- Page 1 已有文本 ---" in prompt
        assert "第一页文本" in prompt
        # 无文本的页面不生成 ref
        assert "--- Page 2" not in prompt

    def test_build_batch_prompt_without_refs(self):
        svc = VisionService(model=FakeVisionModel("x"))
        prompt = svc._build_batch_prompt([{"page": 1, "text": "  "}])
        assert "已有文本" not in prompt


class TestDescribePage:
    async def test_describe_page_success(self, tmp_path):
        path = _make_png(str(tmp_path / "p.png"))
        svc = VisionService(model=FakeVisionModel("页面描述内容"))
        out = await svc.describe_page(path, "已有文本")
        assert out == "页面描述内容"

    async def test_describe_page_missing_file(self, tmp_path):
        svc = VisionService(model=FakeVisionModel("x"))
        assert await svc.describe_page(str(tmp_path / "nope.png")) == ""

    def test_describe_page_sync_success(self, tmp_path):
        path = _make_png(str(tmp_path / "p.png"))
        svc = VisionService(model=FakeVisionModel("同步描述"))
        assert svc.describe_page_sync(path) == "同步描述"

    def test_describe_page_sync_missing_file(self, tmp_path):
        svc = VisionService(model=FakeVisionModel("x"))
        assert svc.describe_page_sync(str(tmp_path / "nope.png")) == ""

    async def test_describe_page_model_exception_returns_empty(self, tmp_path):
        path = _make_png(str(tmp_path / "p.png"))

        class BoomModel:
            async def ainvoke(self, messages):
                raise RuntimeError("vision down")

        svc = VisionService(model=BoomModel())
        assert await svc.describe_page(path) == ""


class TestDescribePagesBatch:
    def _two_pages(self, tmp_path):
        return [
            _make_png(str(tmp_path / "page1.png")),
            _make_png(str(tmp_path / "page2.png")),
        ]

    async def test_batch_parses_per_page(self, tmp_path):
        paths = self._two_pages(tmp_path)
        content = "--- Page 1 ---\n第一页描述\n\n--- Page 2 ---\n第二页描述"
        svc = VisionService(model=FakeVisionModel(content))
        result = await svc.describe_pages_batch(paths, [1, 2], ["t1", "t2"])
        assert result == {1: "第一页描述", 2: "第二页描述"}

    async def test_batch_missing_file_returns_empty(self, tmp_path):
        paths = [str(tmp_path / "nope.png"), str(tmp_path / "nope2.png")]
        svc = VisionService(model=FakeVisionModel("x"))
        result = await svc.describe_pages_batch(paths, [1, 2], ["t1", "t2"])
        assert result == {1: "", 2: ""}

    async def test_batch_exception_falls_back_to_existing_texts(self, tmp_path):
        paths = self._two_pages(tmp_path)

        class BoomModel:
            async def ainvoke(self, messages):
                raise RuntimeError("down")

        svc = VisionService(model=BoomModel())
        result = await svc.describe_pages_batch(paths, [1, 2], ["已有文本一", ""])
        assert result == {1: "已有文本一", 2: ""}

    def test_batch_sync_success(self, tmp_path):
        paths = self._two_pages(tmp_path)
        content = "--- Page 1 ---\n描述一\n--- Page 2 ---\n描述二"
        svc = VisionService(model=FakeVisionModel(content))
        result = svc.describe_pages_batch_sync(paths, [1, 2], ["", ""])
        assert result == {1: "描述一", 2: "描述二"}


class TestParseBatchResponse:
    def _svc(self):
        return VisionService(model=FakeVisionModel("x"))

    def test_strict_format(self):
        text = "--- Page 1 ---\n第一页\n\n--- Page 2 ---\n第二页"
        assert self._svc()._parse_batch_response(text, [1, 2]) == {1: "第一页", 2: "第二页"}

    def test_partial_results_filled_with_first(self):
        text = "--- Page 2 ---\n只有第二页"
        assert self._svc()._parse_batch_response(text, [1, 2]) == {2: "只有第二页", 1: "只有第二页"}

    def test_fallback_line_split_single_page(self):
        assert self._svc()._parse_batch_response("整页内容", [1]) == {1: "整页内容"}

    def test_fallback_line_split_multi_page(self):
        result = self._svc()._parse_batch_response("行一\n行二\n行三\n行四", [1, 2])
        assert result[1] == "行一\n行二"
        assert result[2] == "行三\n行四"


class TestImageHash:
    def test_compute_image_hash_returns_hex(self, tmp_path):
        path = _make_png(str(tmp_path / "h.png"))
        svc = VisionService(model=FakeVisionModel("x"))
        h = svc.compute_image_hash(path)
        assert h  # 非空
        assert len(h) == 16  # 64 位 phash 的 16 个十六进制字符

    def test_compute_image_hash_missing_file_returns_empty(self, tmp_path):
        svc = VisionService(model=FakeVisionModel("x"))
        assert svc.compute_image_hash(str(tmp_path / "nope.png")) == ""

    def test_hamming_distance(self):
        h1 = "0" * 16
        h2 = "0" * 16
        assert VisionService.hamming_distance(h1, h2) == 0
        assert VisionService.hamming_distance(h1, "f" * 16) == 64
        assert VisionService.hamming_distance("", h2) == 999
        assert VisionService.hamming_distance(None, None) == 999