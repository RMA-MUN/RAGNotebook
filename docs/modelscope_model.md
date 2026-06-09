# ModelScope 模型下载配置

## 模型介绍

BAAI/bge-reranker-v2-m3 是北京智源研究院（BAAI）推出的多语言重排序模型，基于 XLMRobertaForSequenceClassification 架构：

- **原生 CrossEncoder 兼容**：BERT 式 Encoder-only 架构，sentence-transformers 原生支持
- **多语言**：支持 100+ 语言，中文和英文表现优秀
- **长上下文**：最大 8192 tokens
- **轻量级**：仅 568M 参数，适合本地笔记本部署

## 安装步骤

### 1. 安装依赖包

```bash
# 进入后端目录
cd backend

# 安装重排序模型依赖
uv add sentence-transformers torch
```

### 2. 模型获取方式

#### 方法一：自动下载（推荐）

系统支持自动检测和下载模型。当 FastAPI 服务器启动时：
1. 自动检查配置的模型路径是否存在
2. 如果不存在，自动从 ModelScope 下载模型到指定路径
3. 下载完成后在第一次使用时自动加载

**无需手动下载**，系统会在服务器启动时自动完成检查和下载。

#### 方法二：手动从 ModelScope 下载

如果需要手动下载：
1. 访问模型页面：[BAAI/bge-reranker-v2-m3 · 模型库](https://www.modelscope.cn/models/BAAI/bge-reranker-v2-m3)
2. 下载完整模型文件到本地目录，推荐路径：
   ```
   D:\Hugging_Face\models\bge-reranker-v2-m3
   ```

## 环境变量配置

在 `.env` 文件中配置模型路径：

```env
# 重排序模型配置（可选）
RERANKER_MODEL_PATH=D:\Hugging_Face\models\bge-reranker-v2-m3
```

### 硬件要求
- **CPU 模式**：任意现代 CPU（推荐 8GB+ 内存）
- **GPU 模式**：支持 CUDA 的 NVIDIA GPU（推荐，大幅提升性能）

### 软件要求
- Python 3.8+
- PyTorch 2.0+
- sentence_transformers 2.2.0+

## 性能优化建议

1. **GPU 加速**：确保安装了 CUDA 版本的 PyTorch 以获得最佳性能
2. **批量处理**：虽然当前设置 `batch_size=1` 避免 padding 错误，但在文档数量较少时可以尝试增加批次大小
3. **模型缓存**：模型会在服务启动时加载一次，后续请求无需重新加载

## 版本信息

- 模型版本：BAAI/bge-reranker-v2-m3（XLMRobertaForSequenceClassification）
- sentence-transformers：2.2.0+
- PyTorch：2.0+

---

[← 返回首页](../README.md)
