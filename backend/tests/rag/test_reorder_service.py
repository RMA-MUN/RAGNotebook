"""reorder_service.py — ReorderService 重排序逻辑测试（不加载真实模型）。"""
import pytest

from app.rag.reorder_service import ReorderService, check_and_download_reranker_model


class FakeCrossEncoder:
    """假 CrossEncoder：predict 返回预设分数。"""

    def __init__(self, scores):
        self.scores = scores

    def predict(self, pairs, batch_size=1):
        return self.scores

    def eval(self):
        return self


def _make_service(scores=None, predict_error=None) -> ReorderService:
    """跳过 __init__（避免导入 torch/真实模型路径），只装配假编码器。"""
    service = ReorderService.__new__(ReorderService)

    class _Enc:
        def predict(self, pairs, batch_size=1):
            if predict_error is not None:
                raise predict_error
            return scores if scores is not None else [1.0] * len(pairs)

        def eval(self):
            return self

    service._model = _Enc()
    return service


class TestReorderDocuments:
    async def test_success_returns_sorted_documents(self):
        service = _make_service(scores=[0.3, 0.9, 0.5])
        result = await service.reorder_documents("查询", ["docA", "docB", "docC"])
        assert result["success"] is True
        assert result["error"] == ""
        docs = result["documents"]
        # 按相似度降序排列
        assert [d["document"] for d in docs] == ["docB", "docC", "docA"]
        assert [d["similarity"] for d in docs] == [0.9, 0.5, 0.3]
        for d in docs:
            assert set(d.keys()) == {"document", "similarity"}

    async def test_success_with_thinking_callback(self):
        calls = []

        async def cb(data):
            calls.append(data)

        service = _make_service(scores=[0.8, 0.2])
        result = await service.reorder_documents("q", ["a", "b"], thinking_callback=cb)
        assert result["success"] is True
        # 两次 thinking：开始计算 + 完成详情
        assert len(calls) == 2
        assert calls[0]["stage"] == "reorder"
        assert "2" in calls[0]["content"]
        assert calls[1]["details"]["scores"][0]["score"] == 0.8

    async def test_empty_documents_short_circuits(self):
        service = _make_service(scores=[])
        result = await service.reorder_documents("q", [])
        assert result == {"success": True, "documents": [], "error": ""}

    async def test_failure_returns_error_shape(self):
        service = _make_service(predict_error=RuntimeError("模型崩溃"))
        result = await service.reorder_documents("q", ["a", "b"])
        assert result["success"] is False
        assert result["documents"] == []
        assert result["error"] == "模型崩溃"

    async def test_no_callback_when_none(self):
        service = _make_service(scores=[1.0])
        result = await service.reorder_documents("q", ["a"])
        assert result["success"] is True

    async def test_model_property_returns_lazy_model(self):
        service = _make_service(scores=[0.5])
        model = await service.model
        assert model is service._model


class TestFormatReorderResult:
    async def test_formats_scores_and_content(self):
        text = await ReorderService.format_reorder_result(
            [{"document": "内容A", "similarity": 0.9123}]
        )
        assert "0.9123" in text
        assert "内容A" in text


class TestCheckAndDownloadRerankerModel:
    def test_local_model_exists_skips_download(self, tmp_path, monkeypatch):
        model_dir = tmp_path / "local-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("RERANKER_MODEL_PATH", str(model_dir))

        called = []
        monkeypatch.setattr("modelscope.snapshot_download", lambda **kw: called.append(kw))
        check_and_download_reranker_model()
        assert called == []  # 本地存在 → 不下载

    def test_missing_model_downloads(self, tmp_path, monkeypatch):
        model_dir = tmp_path / "missing-model"
        monkeypatch.setenv("RERANKER_MODEL_PATH", str(model_dir))

        called = {}

        def fake_snapshot(**kw):
            called.update(kw)

        class DummyPbar:
            def __init__(self, *a, **k):
                self.updated = 0

            def update(self, n):
                self.updated += n

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("modelscope.snapshot_download", fake_snapshot)
        monkeypatch.setattr("tqdm.tqdm", DummyPbar)

        check_and_download_reranker_model()
        assert called["model_id"] == "BAAI/bge-reranker-v2-m3"
        assert called["revision"] == "master"
        assert called["cache_dir"] == str(model_dir)

    def test_download_failure_raises_runtime_error(self, tmp_path, monkeypatch):
        model_dir = tmp_path / "fail-model"
        monkeypatch.setenv("RERANKER_MODEL_PATH", str(model_dir))

        def boom(**kw):
            raise RuntimeError("网络错误")

        class DummyPbar:
            def update(self, n):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("modelscope.snapshot_download", boom)
        monkeypatch.setattr("tqdm.tqdm", DummyPbar)

        with pytest.raises(RuntimeError, match="重排序模型检查失败"):
            check_and_download_reranker_model()